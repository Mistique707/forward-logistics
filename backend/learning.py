"""Self-learning loop: one shared model plus a per-site correction layer.

  inventory checks (counts, receipts, issues)
    -> observed daily usage per site and class
    -> per-site correction factor, recomputed on every check: observed / forecast over the last four
       weeks, shrunk toward 1 until the site has two weeks of data
    -> retraining of the shared model on all observed usage, champion/challenger: a new version is
       promoted only if it forecasts the most recent four weeks at least as well as the current one.

Demo posts learn from their issue registers (the `daily` table) plus any issues recorded after the demo
date; live sites (registered for real data collection) learn from what their inventory checks record,
with real weather for their location from Open-Meteo.
"""
import functools
import json
import re
import sqlite3
import threading
import urllib.parse
from datetime import date, timedelta

import lightgbm as lgb
import numpy as np
import pandas as pd

from . import forecast
from .config import CLASSES, DB_PATH, MODELS, POSTS

PRIOR_DAYS = 14          # a site's own data gets half the weight after two weeks
WINDOW = 28
AUTO_RETRAIN_DAYS = 28   # newly observed site-days that trigger a retrain
LIVE_WEIGHT = 3.0        # real observations count more than simulated history
PROMOTE_TOLERANCE = 1.02
EVAL_FILE = MODELS / "learning_eval.json"


def _db():
    return sqlite3.connect(DB_PATH)


def demo_clock():
    with _db() as con:
        return pd.Timestamp(con.execute("SELECT value FROM meta WHERE key='demo_date'").fetchone()[0])


# ---------- live sites ----------

def live_sites():
    with _db() as con:
        return pd.read_sql("SELECT * FROM sites ORDER BY created_at", con).to_dict("records")


def register_site(name, lat, lon, headcount):
    from .fetch_data import elevation
    sid = "LIVE-" + re.sub(r"[^A-Z0-9]+", "-", name.upper()).strip("-")[:24]
    try:
        alt = float(elevation([(lat, lon)])[0])
    except OSError:
        alt = 0.0  # offline: altitude only matters for heating and ration effects
    with _db() as con:
        con.execute("INSERT OR REPLACE INTO sites (id, name, lat, lon, alt_m, headcount) VALUES (?,?,?,?,?,?)",
                    (sid, name, lat, lon, alt, headcount))
    return sid


def _site(sid):
    return next((s for s in live_sites() if s["id"] == sid), None)


@functools.lru_cache(maxsize=64)
def _site_weather(lat, lon, day):
    """Daily weather around `day` for a live site: the last 92 days plus a 16-day forecast (Open-Meteo)."""
    from .fetch_data import _get
    url = "https://api.open-meteo.com/v1/forecast?" + urllib.parse.urlencode({
        "latitude": lat, "longitude": lon, "past_days": 92, "forecast_days": 16, "timezone": "auto",
        "daily": "temperature_2m_mean,temperature_2m_min,snowfall_sum"})
    d = json.loads(_get(url, timeout=30))["daily"]
    return pd.DataFrame({"t_mean": d["temperature_2m_mean"], "t_min": d["temperature_2m_min"],
                         "snow_cm": d["snowfall_sum"]}, index=pd.to_datetime(d["time"])).ffill().bfill()


def site_weather(site):
    try:
        return _site_weather(round(site["lat"], 3), round(site["lon"], 3), date.today().isoformat())
    except OSError:  # offline: a flat 15 C so cold-driven terms stay neutral; the per-site layer absorbs the rest
        idx = pd.date_range(date.today() - timedelta(days=92), periods=108)
        return pd.DataFrame({"t_mean": 15.0, "t_min": 10.0, "snow_cm": 0.0}, index=idx)


# ---------- observed usage ----------

def inventory(site_id=None):
    q = "SELECT * FROM inventory" + (" WHERE site_id = ?" if site_id else "") + " ORDER BY observed_at, id"
    with _db() as con:
        return pd.read_sql(q, con, params=(site_id,) if site_id else (), parse_dates=["observed_at"])


def usage_from_events(ev):
    """Daily usage per class from inventory events: recorded issues, plus whatever the counts imply
    (previous count + receipts - issues - this count) spread over the days between two counts."""
    out = {}
    for cls, g in ev.groupby("cls"):
        g = g.assign(day=g.observed_at.dt.normalize())
        daily = g[g.kind == "issue"].groupby("day").quantity.sum()
        counts = g[g.kind == "count"].sort_values("observed_at")
        for (_, a), (_, b) in zip(counts.iterrows(), counts.iloc[1:].iterrows()):
            between = g[(g.observed_at > a.observed_at) & (g.observed_at <= b.observed_at)]
            used = (a.quantity + between[between.kind == "receipt"].quantity.sum()
                    - between[between.kind == "issue"].quantity.sum() - b.quantity)
            days = pd.date_range(a.day + pd.Timedelta(days=1), b.day) if b.day > a.day else pd.DatetimeIndex([b.day])
            if used > 0:
                daily = daily.add(pd.Series(used / len(days), index=days), fill_value=0)
        if len(daily):
            out[cls] = daily.sort_index()
    return out


def live_rows():
    """Observed usage at live sites as training rows with drivers (real local weather, headcount)."""
    rows = []
    for site in live_sites():
        use = usage_from_events(inventory(site["id"]))
        if not use:
            continue
        wx = site_weather(site)
        for cls, s in use.items():
            w = wx.reindex(s.index).ffill().bfill()
            rows.append(pd.DataFrame({"post_id": site["id"], "date": s.index, "cls": cls,
                                      "troops": site["headcount"], "tempo": 1, "consumed": s.to_numpy(),
                                      "alt_m": site["alt_m"], **{c: w[c].to_numpy() for c in w}}))
    return pd.concat(rows, ignore_index=True) if rows else None


@functools.lru_cache(maxsize=4)
def _demo_history():
    return forecast.history()


# ---------- the per-site layer ----------

def _predict(df, version=None):
    out = np.empty(len(df))
    for cls, g in df.groupby("cls"):
        mean, _, smear = forecast.models(version)[cls]
        out[df.index.get_indexer(g.index)] = np.exp(mean.predict(g[forecast.FEATURES])) * smear * g.troops
    return out


def _factor(observed, predicted, n_days):
    r = observed / predicted if predicted > 0 else 1.0
    return (n_days * r + PRIOR_DAYS) / (n_days + PRIOR_DAYS)


def _window(df):
    """Each site's most recent WINDOW days of observed usage."""
    last = df.groupby("post_id").date.transform("max")
    return df[df.date > last - pd.Timedelta(days=WINDOW)]


_factor_cache = {}


def local_factors():
    """{site: {cls: {k, n_days, observed, predicted}}} for every site with observed usage."""
    key = (forecast.active_version(), _inventory_stamp())
    if key in _factor_cache:
        return _factor_cache[key]
    hist = _demo_history()
    parts = [hist]
    live = live_rows()
    if live is not None:
        parts.append(forecast.prepare(live))
    df = _window(pd.concat(parts, ignore_index=True)).reset_index(drop=True)
    df = df.assign(pred=_predict(df))
    out = {}
    for (site, cls), g in df.groupby(["post_id", "cls"]):
        n = g.date.nunique()
        out.setdefault(site, {})[cls] = {"k": round(_factor(g.consumed.sum(), g.pred.sum(), n), 3), "n_days": int(n),
                                         "observed": round(float(g.consumed.sum()), 1),
                                         "predicted": round(float(g.pred.sum()), 1)}
    _factor_cache.clear()
    _factor_cache[key] = out
    return out


def _inventory_stamp():
    with _db() as con:
        return con.execute("SELECT count(*), coalesce(max(id), 0) FROM inventory").fetchone()


# ---------- retraining (champion / challenger) ----------

_run = {"running": False, "last": None, "error": None}
_lock = threading.Lock()


def _training_frame():
    live = live_rows()
    df = forecast.history(live)
    df["w"] = np.where(df.post_id.str.startswith("LIVE-"), LIVE_WEIGHT, 1.0)
    return df, 0 if live is None else len(live)


def _wape(actual, pred):
    return float(np.abs(pred - actual).sum() / actual.sum() * 100)


def retrain(note="manual"):
    """Train a challenger on all observed usage; promote it if it is at least as good on the last 4 weeks."""
    with _lock:
        if _run["running"]:
            return False
        _run.update(running=True, error=None)
    try:
        df, n_live = _training_frame()
        recent = df.index.isin(_window(df).index)
        challenger = forecast.fit(df[~recent], weight="w")
        test = df[recent].dropna(subset=["pc"]).reset_index(drop=True)
        champ_pred = _predict(test)
        chall_pred = np.empty(len(test))
        for cls, g in test.groupby("cls"):
            mean, _, smear = challenger[cls]
            chall_pred[g.index] = np.exp(mean.predict(g[forecast.FEATURES])) * smear * g.troops
        ev = {cls: {"challenger": round(_wape(g.consumed, chall_pred[g.index]), 2),
                    "champion": round(_wape(g.consumed, champ_pred[g.index]), 2)} for cls, g in test.groupby("cls")}
        better = (np.mean([v["challenger"] for v in ev.values()])
                  <= np.mean([v["champion"] for v in ev.values()]) * PROMOTE_TOLERANCE)
        final = forecast.fit(df, weight="w") if better else challenger
        v = forecast.save_version(final, {"rows": int(len(df)), "live_rows": n_live, "note": note,
                                          "eval": {c: e["challenger"] for c, e in ev.items()},
                                          "champion_eval": {c: e["champion"] for c, e in ev.items()}}, promote=better)
        _run["last"] = {"version": v, "promoted": bool(better)}
        from . import scenarios
        scenarios.invalidate()
        return True
    except Exception as ex:  # surfaced in the status call; the champion keeps serving
        _run["error"] = str(ex)
        raise
    finally:
        _run["running"] = False


def retrain_in_background(note="manual"):
    if _run["running"]:
        return False
    threading.Thread(target=lambda: _safe(retrain, note), daemon=True).start()
    return True


def _safe(fn, *a):
    try:
        fn(*a)
    except Exception:
        pass  # already recorded in _run["error"]


def new_observed_days():
    """Site-days of inventory records received since the active version was trained."""
    reg = forecast.registry()
    v = next((x for x in reg["versions"] if x["version"] == reg["active"]), None)
    if v is None:
        return 0
    with _db() as con:
        return con.execute("SELECT count(DISTINCT site_id || cls || substr(observed_at, 1, 10)) FROM inventory "
                           "WHERE source != 'simulator' AND received_at > ?",
                           (v["trained_at"].replace("T", " "),)).fetchone()[0]


def maybe_retrain():
    if new_observed_days() >= AUTO_RETRAIN_DAYS:
        retrain_in_background("auto: new inventory records")


def status():
    reg = forecast.registry()
    sites = []
    for s in live_sites():
        ev = inventory(s["id"])
        use = usage_from_events(ev)
        sites.append({**s, "checks": int(len(ev)), "observed_days": int(max((len(v) for v in use.values()), default=0)),
                      "last_check": ev.observed_at.max().isoformat() if len(ev) else None})
    return {"active": reg["active"],
            "versions": [{k: v for k, v in x.items() if k != "smear"} for x in reg["versions"]],
            "running": _run["running"], "last_run": _run["last"], "error": _run["error"],
            "new_days": new_observed_days(), "auto_retrain_after": AUTO_RETRAIN_DAYS,
            "factors": local_factors(), "sites": sites,
            "evaluation": json.loads(EVAL_FILE.read_text()) if EVAL_FILE.exists() else None,
            "prior_days": PRIOR_DAYS, "window": WINDOW}


# ---------- how much the per-site layer helps a new site ----------

def evaluate_local_layer(rounds=100, learn_days=14):
    """Leave-one-post-out: train on the other posts, forecast the held-out post as a brand-new site (no usage
    history), then again after the per-site layer has seen its first `learn_days` of inventory records."""
    df = _demo_history()
    start, stop = forecast.holdout()
    res = {cls: {"new_site": [], "with_layer": [], "actual": []} for cls in CLASSES}
    for post in POSTS:
        train = forecast._with_dropout(df[(df.post_id != post) & (df.date < start)].dropna(subset=["pc"]))
        test = df[(df.post_id == post) & (df.date >= start) & (df.date <= stop)].copy()
        test[forecast.LAGS] = np.nan
        for cls, g in train.groupby("cls"):
            m = lgb.train(forecast.PARAMS, lgb.Dataset(g[forecast.FEATURES], np.log(g.pc.clip(lower=1e-4))), rounds)
            t = test[test.cls == cls].sort_values("date")
            pred = np.exp(m.predict(t[forecast.FEATURES])) * t.troops.to_numpy()
            first, rest = slice(0, learn_days), slice(learn_days, None)
            k = _factor(t.consumed.to_numpy()[first].sum(), pred[first].sum(), learn_days)
            res[cls]["new_site"].append(pred[rest])
            res[cls]["with_layer"].append(pred[rest] * k)
            res[cls]["actual"].append(t.consumed.to_numpy()[rest])
    out = {}
    for cls, r in res.items():
        a = np.concatenate(r["actual"])
        out[cls] = {"new_site": round(_wape(a, np.concatenate(r["new_site"])), 1),
                    "with_layer": round(_wape(a, np.concatenate(r["with_layer"])), 1)}
    EVAL_FILE.write_text(json.dumps({"method": "leave-one-post-out", "learn_days": learn_days, "wape": out}, indent=1))
    return out


if __name__ == "__main__":
    print(evaluate_local_layer())

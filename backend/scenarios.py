"""State engine and what-if simulator.

compute_state(overrides) runs: drivers (+ scenario overrides) -> forecast -> runout -> demand
-> constraints (passes, weather, fleet) -> dispatch plan -> alerts, KPIs, route risk.
The baseline and the presets are precomputed and cached; diff() compares any state to baseline.
"""
import copy
import functools
import json
import sqlite3
import threading

import numpy as np
import pandas as pd

from . import forecast, learning, network, planner
from .config import (CLASSES, DB_PATH, FORECAST_DAYS, MODES, PASSES, PLAN_DAYS, POSTS, SAFETY_DAYS,
                     WEATHER_FORECAST_DAYS)
from .simulator import climatology, transient_open, weather, zojila_rule

PRESETS = [
    {"id": "baseline", "name": "Baseline",
     "desc": "20 Feb 2025: a western disturbance is forecast to shut Zoji La in 6 days.", "overrides": {}},
    {"id": "what_happened", "name": "What really happened in 2025",
     "desc": "Zoji La actually shut on 28 Feb and reopened only on 1 Apr: two days later and 13 days later than "
             "forecast.", "overrides": {"pass_shift_days": 2, "reopen_shift_days": 13}},
    {"id": "early_closure", "name": "Zoji La closes 4 days early",
     "desc": "Heavy snow brings the closure forward to day 2.", "overrides": {"pass_shift_days": -4}},
    {"id": "closed_now", "name": "Zoji La closes 10 days early",
     "desc": "The pass is already shut: everything beyond it is air-maintained.", "overrides": {"pass_shift_days": -10}},
    {"id": "closed_grounded", "name": "Pass shut + helicopters grounded 3 days",
     "desc": "Zoji La shut and no helicopter sorties for the next 3 days.",
     "overrides": {"pass_shift_days": -10, "heli_grounded_days": 3}},
    {"id": "surge_bravo", "name": "Troop surge at Post Bravo (+60%)",
     "desc": "Reinforcements move into Post Bravo from today.", "overrides": {"troop_surge": {"post": "BRAVO", "pct": 60}}},
    {"id": "cold_snap", "name": "Cold snap: -8 °C for 10 days",
     "desc": "A western disturbance drops temperatures 8 °C below forecast.",
     "overrides": {"temp_offset_c": -8, "temp_days": 10}},
    {"id": "tempo_nubra", "name": "Operational tempo HIGH in Nubra",
     "desc": "Tempo rises to HIGH in the Nubra sector (Echo, Foxtrot).",
     "overrides": {"tempo": {"sector": "Nubra", "level": 3}}},
]
SEVERITY = {"critical": 0, "high": 1, "warning": 2}


# ---------- inputs ----------

@functools.cache
def base_inputs():
    with sqlite3.connect(DB_PATH) as con:
        demo = pd.Timestamp(con.execute("SELECT value FROM meta WHERE key='demo_date'").fetchone()[0])
        last = pd.read_sql("SELECT post_id, troops, tempo FROM daily WHERE cls='rations' AND date="
                           "(SELECT max(date) FROM daily)", con).set_index("post_id")
        events = pd.read_sql("SELECT * FROM events", con).to_dict("records")
        hist = pd.read_sql("SELECT post_id, cls, consumed / troops AS pc FROM daily WHERE date > ? ORDER BY date",
                           con, params=((demo - pd.Timedelta(days=60)).strftime("%Y-%m-%d"),))
    dates = pd.date_range(demo, periods=FORECAST_DAYS)
    clim = climatology(demo)
    wx = {}
    for node in weather():
        arch = weather()[node].reindex(dates[:WEATHER_FORECAST_DAYS])
        cl = clim[node].loc[dates[WEATHER_FORECAST_DAYS:].dayofyear].set_axis(dates[WEATHER_FORECAST_DAYS:])
        wx[node] = pd.concat([arch, cl])
    return {"demo": demo, "dates": dates, "troops": last.troops.to_dict(), "tempo": last.tempo.to_dict(),
            "events": events, "wx": wx,
            "hist_pc": {(p, c): g.pc.to_numpy() for (p, c), g in hist.groupby(["post_id", "cls"])}}


def current_stock():
    """Latest count per site and class, plus receipts and minus issues recorded after it."""
    with sqlite3.connect(DB_PATH) as con:
        ev = pd.read_sql("SELECT site_id, cls, kind, quantity, observed_at, id FROM inventory "
                         "ORDER BY observed_at, id", con)
    out = {}
    for (site, cls), g in ev.groupby(["site_id", "cls"]):
        counts = g[g.kind == "count"]
        if counts.empty:
            continue
        last = counts.iloc[-1]
        after = g[(g.observed_at > last.observed_at) | ((g.observed_at == last.observed_at) & (g.id > last.id))]
        out[site, cls] = max(0.0, last.quantity + after[after.kind == "receipt"].quantity.sum()
                             - after[after.kind == "issue"].quantity.sum())
    return out


def _drivers(ov):
    b = base_inputs()
    net = network.load()["by_id"]
    troops, tempo = dict(b["troops"]), dict(b["tempo"])
    if s := ov.get("troop_surge"):
        troops[s["post"]] = round(troops[s["post"]] * (1 + s["pct"] / 100))
    if t := ov.get("tempo"):
        for p in POSTS:
            if net[p]["sector"] == t["sector"]:
                tempo[p] = t["level"]
    wx = {k: v.copy() for k, v in b["wx"].items()}
    if ov.get("temp_offset_c"):
        n = ov.get("temp_days", PLAN_DAYS)
        for w in wx.values():
            w.iloc[:n, w.columns.get_indexer(["t_mean", "t_min"])] += ov["temp_offset_c"]
    return troops, tempo, wx


def _first(mask):
    idx = np.flatnonzero(mask)
    return int(idx[0]) if len(idx) else None


# ---------- state ----------

def compute_state(ov):
    b = base_inputs()
    net = network.load()["by_id"]
    troops, tempo, wx = _drivers(ov)
    stock = current_stock()

    # passes: predicted Zojila closure / reopening, transient closures inside the plan window
    zo = zojila_rule(wx["ZOJILA"]).to_numpy()
    close_day = _first(~zo)
    reopen_day = None if close_day is None else _first(zo[close_day:])
    reopen_day = None if reopen_day is None else close_day + reopen_day
    if close_day is not None:
        close_day = max(0, close_day + ov.get("pass_shift_days", 0))
    if reopen_day is not None:
        reopen_day = max((close_day or 0) + 1, reopen_day + ov.get("reopen_shift_days", 0))
    closures = {"ZOJILA": [(24 * close_day, 10 ** 6)] if close_day is not None and close_day < PLAN_DAYS else []}
    passes = [{"id": "ZOJILA", **PASSES["ZOJILA"], "close_day": close_day, "reopen_day": reopen_day,
               "status": "closed" if close_day == 0 else "closing" if close_day is not None and close_day < PLAN_DAYS
               else "open"}]
    for pid in ("KHARDUNG", "CHANG"):
        shut = ~transient_open(wx[pid].iloc[:PLAN_DAYS]).to_numpy()
        closures[pid] = [(24 * d, 24 * d + 24) for d in np.flatnonzero(shut)]
        passes.append({"id": pid, **PASSES[pid], "closed_days": [int(d) for d in np.flatnonzero(shut)],
                       "status": "closed" if shut[0] else "closing" if shut.any() else "open"})

    # forecast, runout and demand per post and class
    cover = (reopen_day if reopen_day is not None else FORECAST_DAYS - SAFETY_DAYS) + SAFETY_DAYS
    cover = min(cover, FORECAST_DAYS) - 1
    posts, demands, fc = [], [], {}
    X = {}
    for p in POSTS:
        x = wx[p][["t_mean", "t_min", "snow_cm"]].reset_index().rename(columns={"index": "date"})
        X[p] = forecast.add_features(x.assign(alt_m=net[p]["alt_m"], tempo=tempo[p], troops=troops[p]))
    factors = learning.local_factors()
    preds = {cls: forecast.forecast(cls, X, {p: b["hist_pc"][p, cls] for p in POSTS}) for cls in CLASSES}
    soon = PLAN_DAYS + SAFETY_DAYS - 1
    for p in POSTS:
        n = net[p]
        classes = {}
        for cls, meta in CLASSES.items():
            k = factors.get(p, {}).get(cls, {}).get("k", 1.0)
            mean_pc, p90_pc = preds[cls][p]
            daily, high = mean_pc * troops[p] * k, p90_pc * troops[p] * k
            cum, cum_hi = np.cumsum(daily), np.cumsum(high)
            s = stock[(p, cls)]
            runout, runout_hi = _first(cum > s), _first(cum_hi > s)
            target = cum[cover]
            # the next three weeks are planned on the P90 forecast: safety stock against a bad fortnight
            urgent = max(0.0, cum_hi[soon] - s)
            short = max(target - s, urgent, 0.0)
            fc[(p, cls)] = (daily, high)
            classes[cls] = {"stock": round(s), "unit": meta["unit"], "daily": round(float(daily[:7].mean()), 1),
                            "runout_day": runout, "runout_p90": runout_hi,
                            "days_of_stock": runout if runout is not None else FORECAST_DAYS,
                            "target": round(target), "shortfall": round(short), "urgent": round(urgent),
                            "local_factor": k}
            if short * meta["kg"] >= 50:
                first = runout_hi if runout_hi is not None else runout
                demands.append({"post": p, "cls": cls, "runout_day": first if urgent > 0 else None,
                                "urgent_kg": urgent * meta["kg"], "stocking_kg": (short - urgent) * meta["kg"]})
        drivers = {cls: [(name, round(v, 1)) for name, v in forecast.explain(cls, _explain_rows(X[p], b, p, cls))[:3]]
                   for cls in CLASSES}
        worst = min(c["days_of_stock"] for c in classes.values())
        posts.append({"id": p, "name": n["name"], "lat": n["lat"], "lon": n["lon"], "alt_m": n["alt_m"],
                      "access": n["access"], "sector": n["sector"], "troops": troops[p], "tempo": tempo[p],
                      "classes": classes, "drivers": drivers, "worst_days": worst})

    # weather limits for air modes and the mule leg, inside the plan window
    no_go = {"heli": {}, "airdrop": {}}
    mule_blocked = {}
    for p in POSTS:
        w = wx[p].iloc[:PLAN_DAYS]
        h, a = MODES["heli"], MODES["airdrop"]
        no_go["heli"][p] = set(np.flatnonzero((w.gust_kmh > h["max_gust"]) | (w.snow_cm > h["max_snow_cm"])
                                              | (w.cloud_pct > h["max_cloud"])).tolist())
        no_go["airdrop"][p] = set(np.flatnonzero((w.gust_kmh > a["max_gust"]) | (w.cloud_pct > a["max_cloud"])).tolist())
        mule_blocked[p] = set(np.flatnonzero(w.snow_cm > MODES["mule"]["max_snow_cm"]).tolist())
    grounded = {"heli": set(range(ov.get("heli_grounded_days", 0)))}
    plan = planner.plan(demands, {"closures": closures, "no_go": no_go, "grounded": grounded,
                                  "mule_blocked": mule_blocked, "trucks": ov.get("trucks", MODES["truck"]["fleet"])})

    state = {"demo_date": b["demo"].strftime("%Y-%m-%d"), "overrides": ov, "passes": passes, "posts": posts,
             "plan": plan, "events": b["events"], "weather_outlook": _outlook(wx)}
    state["alerts"] = _alerts(state)
    state["kpis"] = _kpis(state)
    state["segments"] = _risk(state, wx)
    state["_fc"] = fc
    return state


def _explain_rows(X, b, post, cls):
    """The first two weeks of forecast rows with lags filled from the post's own recent usage."""
    pc = b["hist_pc"][post, cls]
    rows = X.iloc[:PLAN_DAYS].copy()
    n = len(pc)
    for name, k in (("lag14", 14), ("lag21", 21), ("lag28", 28)):
        rows[name] = [pc[n - k + i] for i in range(PLAN_DAYS)]
    rows["r7_14"] = [pc[n - 20 + i:n - 13 + i].mean() for i in range(PLAN_DAYS)]
    rows["r28_14"] = [pc[n - 41 + i:n - 13 + i].mean() for i in range(PLAN_DAYS)]
    return rows


def _outlook(wx):
    return {p: {"t_mean": round(float(wx[p].t_mean.iloc[:7].mean()), 1),
                "snow_7d": round(float(wx[p].snow_cm.iloc[:7].sum()), 1)} for p in POSTS}


def day_label(day, hour=None):
    d = base_inputs()["demo"] + pd.Timedelta(days=day)
    return d.strftime("%a %d %b") + (f" {hour:02d}:00" if hour is not None else "")


def _alerts(state):
    plan = state["plan"]
    zo = state["passes"][0]
    cd = zo["close_day"]
    pass_txt = ("Zoji La is closed" if cd == 0 else f"Zoji La closes in {cd} d" if cd is not None else "Zoji La open")
    unmet = {(u["post"], u["cls"]): u for u in plan["unmet"]}
    deferred = {}
    for u in plan["deferred"]:
        deferred.setdefault(u["post"], []).append(u)
    alerts = []
    for post in state["posts"]:
        p = post["id"]
        trips = [t for t in plan["trips"] if any(s["post"] == p for s in t["stops"])]
        for cls, c in post["classes"].items():
            if c["urgent"] * CLASSES[cls]["kg"] < 50:  # below the planning threshold
                continue
            r = c["runout_day"]
            lab = CLASSES[cls]["label"].lower()
            if r is not None:
                when = f"runs out of {lab} in {r} days"
            elif c["runout_p90"] is not None:
                r = c["runout_p90"]
                when = f"could run out of {lab} in {r} days on a high-use fortnight"
            else:
                continue
            carry = [t for t in trips if any(s["post"] == p and cls in s["items"] for s in t["stops"])]
            if (p, cls) in unmet:
                action, sev = "NO feasible lift before runout: escalate for additional airlift", "critical"
            else:
                modes = {t["mode"] for t in carry}
                trucks = [t for t in carry if t["mode"] == "truck"]
                if trucks:
                    by = min(t["depart_by_h"] for t in trucks)
                    action = f"dispatch {len(trucks)} truck{'s' * (len(trucks) > 1)} by {day_label(by // 24)}"
                elif "heli" in modes:
                    first = min(t["depart_h"] for t in carry if t["mode"] == "heli")
                    action = (f"fly {sum(t['mode'] == 'heli' for t in carry)} helicopter sortie(s) from Onyx, first "
                              f"{day_label(first // 24)}")
                elif carry:
                    action = f"airdrop from Sapphire, {day_label(min(t['depart_h'] for t in carry) // 24)}"
                else:
                    action = "no lift planned for it"
                # the road window shutting before the runout is the winter-stocking danger
                sev = "critical" if r is not None and (r <= 3 or (cd is not None and cd < r)) else "high"
            alerts.append({"severity": sev, "post": p, "cls": cls, "days": r,
                           "title": f"{post['name']} {when}", "context": pass_txt, "action": action,
                           "text": f"{post['name']} {when}; {pass_txt}; {action}."})
        backlog = [(cls, c) for cls, c in post["classes"].items() if c["shortfall"] - c["urgent"] > 0
                   and (c["shortfall"] - c["urgent"]) * CLASSES[cls]["kg"] >= 50]
        if backlog:
            kg = sum((c["shortfall"] - c["urgent"]) * CLASSES[cls]["kg"] for cls, c in backlog)
            names = ", ".join(CLASSES[cls]["label"].lower() for cls, _ in backlog)
            dkg = sum(u["kg"] for u in deferred.get(p, []))
            if dkg:
                action = (f"{dkg / 1000:.1f} t cannot move by road in time and becomes winter air maintenance")
                sev = "high" if dkg > 1000 else "warning"
            else:
                tr = [t for t in trips if t["mode"] == "truck"]
                by = min((t["depart_by_h"] for t in tr), default=None)
                action = (f"covered by {len(tr)} truck{'s' * (len(tr) != 1)}"
                          + (f", dispatch by {day_label(by // 24)}" if by is not None else ""))
                sev = "warning"
            alerts.append({"severity": sev, "post": p, "cls": None, "days": post["worst_days"],
                           "title": f"{post['name']}: {kg / 1000:.1f} t short of winter target",
                           "context": f"{names}; {pass_txt}", "action": action,
                           "text": f"{post['name']} is {kg / 1000:.1f} t short of its winter target ({names}); "
                                   f"{pass_txt}; {action}."})
    alerts.sort(key=lambda a: (SEVERITY[a["severity"]], a["days"] if a["days"] is not None else 999))
    return alerts


def _kpis(state):
    t = state["plan"]["totals"]
    urgent_posts = {a["post"] for a in state["alerts"] if a["cls"]}
    runouts = [c["runout_day"] for p in state["posts"] for c in p["classes"].values() if c["runout_day"] is not None]
    bm = t["by_mode"]
    return {"posts_at_risk": len(urgent_posts), "zojila_close_day": state["passes"][0]["close_day"],
            "tonnes_planned": round(sum(m["kg"] for m in bm.values()) / 1000, 1),
            "cost_lakh": round(t["cost"] / 1e5, 1), "unmet_t": round(t["unmet_kg"] / 1000, 1),
            "air_liability_t": round(t["deferred_kg"] / 1000, 1),
            "air_liability_lakh": round(t["deferred_cost"] / 1e5, 1),
            "trucks": bm.get("truck", {}).get("trips", 0), "heli_sorties": bm.get("heli", {}).get("trips", 0),
            "airdrops": bm.get("airdrop", {}).get("trips", 0),
            "dispatch_by": next((day_label(x["depart_by_h"] // 24, x["depart_by_h"] % 24)
                                 for x in sorted(state["plan"]["trips"], key=lambda x: x["depart_by_h"])
                                 if x["mode"] == "truck"), None),
            "earliest_runout": min(runouts) if runouts else None}


def _risk(state, wx):
    net = network.load()
    pas = {p["id"]: p for p in state["passes"]}
    out = []
    for s in net["segments"]:
        pr = 0.0
        for pid in s["passes"]:
            p = pas[pid]
            if pid == "ZOJILA":
                cd = p["close_day"]
                pr = max(pr, 0.0 if cd is None or cd >= PLAN_DAYS else 1 - cd / PLAN_DAYS)
            else:
                pr = max(pr, min(1.0, len(p["closed_days"]) / 3))
        snow = min(1.0, float(wx[s["weather_node"]].snow_cm.iloc[:7].sum()) / 30)
        terrain = min(1.0, max(0.0, (s["max_alt_m"] - 3000) / 2500))
        risk = 0.5 * pr + 0.3 * snow + 0.2 * terrain
        out.append({"id": s["id"], "geom": s["geom"], "km": s["km"], "passes": s["passes"],
                    "max_alt_m": s["max_alt_m"], "risk": round(risk, 2), "pass_risk": round(pr, 2),
                    "snow_risk": round(snow, 2), "terrain_risk": round(terrain, 2)})
    return out


# ---------- cache, diff, series ----------

_cache, _lock = {}, threading.Lock()


def _key(ov):
    return json.dumps(ov, sort_keys=True)


def get_state(ov):
    k = _key(ov)
    with _lock:
        if k not in _cache:
            _cache[k] = compute_state(copy.deepcopy(ov))
        return _cache[k]


def invalidate():
    with _lock:
        _cache.clear()


def precompute():
    for p in PRESETS:
        get_state(p["overrides"])


def public(state):
    return {k: v for k, v in state.items() if not k.startswith("_")}


def diff(state, base):
    def modes(s):
        out = {}
        for t in s["plan"]["trips"]:
            for st in t["stops"]:
                for cls in st["items"]:
                    out.setdefault((st["post"], cls), set()).add(t["mode"])
        for u in s["plan"]["unmet"]:
            out.setdefault((u["post"], u["cls"]), set()).add("unmet")
        for u in s["plan"]["deferred"]:
            out.setdefault((u["post"], u["cls"]), set()).add("air maintenance")
        return out

    mb, ms = modes(base), modes(state)
    changes = [{"post": p, "cls": c, "before": sorted(mb.get((p, c), [])), "after": sorted(ms.get((p, c), []))}
               for p, c in sorted(set(mb) | set(ms)) if mb.get((p, c)) != ms.get((p, c))]
    runout = []
    bp = {p["id"]: p for p in base["posts"]}
    for p in state["posts"]:
        for cls, c in p["classes"].items():
            b0 = bp[p["id"]]["classes"][cls]["days_of_stock"]
            if c["days_of_stock"] != b0:
                runout.append({"post": p["id"], "cls": cls, "before": b0, "after": c["days_of_stock"]})
    kpis = {k: {"before": base["kpis"][k], "after": v} for k, v in state["kpis"].items()}
    return {"kpis": kpis, "mode_changes": changes, "runout_changes": runout}


def series(state, post_id, history_days=90):
    """History + forecast per class for one post, with projected stock without and with the plan."""
    b = base_inputs()
    with sqlite3.connect(DB_PATH) as con:
        h = pd.read_sql("SELECT date, cls, consumed, stock, received FROM daily WHERE post_id=? AND date>=?", con,
                        params=(post_id, (b["demo"] - pd.Timedelta(days=history_days)).strftime("%Y-%m-%d")))
    by_id = network.load()["by_id"]
    arrivals = {}
    for t in state["plan"]["trips"]:
        for s in t["stops"]:
            if s["post"] == post_id:
                day = -(-s["arrive_h"] // 24) + (planner._mule_days(s["kg"]) if s["mule"] else 0)
                for cls, q in s["items"].items():
                    arrivals.setdefault(cls, {}).setdefault(int(day), 0)
                    arrivals[cls][int(day)] += q
    post = next(p for p in state["posts"] if p["id"] == post_id)
    out = {"post": post_id, "name": by_id[post_id]["name"], "dates": [d.strftime("%Y-%m-%d") for d in b["dates"]],
           "classes": {}}
    for cls in CLASSES:
        g = h[h.cls == cls]
        daily, high = state["_fc"][(post_id, cls)]
        s0 = post["classes"][cls]["stock"]
        no_plan = s0 - np.cumsum(daily)
        add = np.zeros(len(daily))
        for d, q in arrivals.get(cls, {}).items():
            if d < len(add):
                add[d:] += q
        out["classes"][cls] = {
            "history": {"dates": g.date.tolist(), "consumed": g.consumed.round(1).tolist(),
                        "stock": g.stock.round(0).tolist()},
            "forecast": np.round(daily, 1).tolist(),
            "forecast_p90": np.round(high, 1).tolist(),
            "local_factor": post["classes"][cls]["local_factor"],
            "projected": np.round(np.maximum(no_plan, 0), 0).tolist(),
            "projected_with_plan": np.round(np.maximum(no_plan + add, 0), 0).tolist(),
            "deliveries": [{"day": d, "qty": round(q)} for d, q in sorted(arrivals.get(cls, {}).items())],
            "drivers": post["drivers"][cls]}
    return out

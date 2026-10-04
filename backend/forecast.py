"""Demand forecasting: one combined gradient-boosting model per supply class (+ a P90 model).

    python -m backend.forecast      # benchmark on the holdout year, then train and register version 1

The combined model merges two team designs:
  * ours: per-capita daily use from weather, altitude, tempo, season and troop strength (troops multiply
    it back up, so surges extrapolate), explained with SHAP;
  * Sujal's: lagged recent usage (14-28 days back, so no leakage inside a 14-day horizon), a log target
    and a P90 quantile model for safety stock.
Lags are hidden on a share of training rows so the same model also serves a brand-new site with no
usage history. Beyond 14 days the forecast runs recursively in 14-day blocks on its own predictions.
Versions live in models/ with a registry; learning.py retrains and promotes new versions.
"""
import json
import sqlite3
from datetime import datetime

import lightgbm as lgb
import numpy as np
import pandas as pd

from . import network
from .config import CLASSES, DB_PATH, MODELS, SEED
from .simulator import climatology, demo_date, weather

DRIVERS = ["t_mean", "t_min", "snow_cm", "hdd", "alt_m", "tempo", "doy_sin", "doy_cos", "troops", "dow"]
LAGS = ["lag14", "lag21", "lag28", "r7_14", "r28_14"]
FEATURES = DRIVERS + LAGS
SUJAL = ["alt_m", "troops", "dow", "doy_sin", "doy_cos", "t_mean", "tempo", "s_lag14", "s_lag21", "s_lag28", "s_r7",
         "s_r28"]
LABELS = {"t_mean": "Mean temperature", "t_min": "Night minimum", "snow_cm": "Snowfall", "hdd": "Heating demand",
          "alt_m": "Altitude", "tempo": "Operational tempo", "doy_sin": "Season", "doy_cos": "Season",
          "troops": "Troop strength", "dow": "Day of week", **{k: "Recent usage (2-4 weeks ago)" for k in LAGS}}
HORIZONS = range(1, 15)
BLOCK = 14
LAG_DROPOUT = 0.3
PARAMS = {"objective": "regression", "learning_rate": 0.05, "num_leaves": 15, "min_data_in_leaf": 40,
          "feature_fraction": 0.9, "seed": SEED, "deterministic": True, "force_row_wise": True,
          "num_threads": 4, "verbose": -1}
SUJAL_PARAMS = {**PARAMS, "num_leaves": 24}  # his HistGradientBoosting settings: 24 leaves, lr 0.05, 400 rounds
ROUNDS, SUJAL_ROUNDS = 300, 400


# ---------- data ----------

def add_features(df):
    """df needs t_mean, t_min, snow_cm, alt_m, tempo, troops and a datetime `date`."""
    doy = df["date"].dt.dayofyear
    return df.assign(hdd=np.maximum(0, 15 - df.t_mean), doy_sin=np.sin(2 * np.pi * doy / 365.25),
                     doy_cos=np.cos(2 * np.pi * doy / 365.25), dow=df["date"].dt.dayofweek)


def add_lags(df, col="pc", prefix=""):
    """Usage 14-28 days back, per site and class (df sorted by site, class, date; one row per day)."""
    g = df.groupby(["post_id", "cls"])[col]
    s14 = g.shift(14)
    out = {f"{prefix}lag14": s14, f"{prefix}lag21": g.shift(21), f"{prefix}lag28": g.shift(28)}
    key = [df.post_id, df.cls]
    out[f"{prefix}{'r7' if prefix else 'r7_14'}"] = s14.groupby(key).transform(lambda s: s.rolling(7).mean())
    out[f"{prefix}{'r28' if prefix else 'r28_14'}"] = s14.groupby(key).transform(lambda s: s.rolling(28).mean())
    return df.assign(**out)


def history(extra=None):
    """Usage history per site/class/day with drivers and lags. `extra`: more observed rows (live data)."""
    with sqlite3.connect(DB_PATH) as con:
        d = pd.read_sql("SELECT post_id, date, cls, troops, tempo, consumed FROM daily", con, parse_dates=["date"])
    wx = pd.concat([w.assign(post_id=k) for k, w in weather().items()]).reset_index()
    d = d.merge(wx, on=["post_id", "date"], how="left")
    d["alt_m"] = d.post_id.map({n["id"]: n["alt_m"] for n in network.load()["nodes"]})
    if extra is not None and len(extra):
        d = pd.concat([d, extra], ignore_index=True)
    return prepare(d)


def prepare(d):
    """Drivers, per-capita target and lags for daily usage rows of any site."""
    d = d.sort_values(["post_id", "cls", "date"]).reset_index(drop=True)
    d["pc"] = d.consumed / d.troops
    d = add_lags(add_features(d))
    return add_lags(d, "consumed", "s_")


def _with_dropout(df, rate=LAG_DROPOUT):
    """Append copies of a share of rows with lags hidden, so the model learns the no-history case too."""
    rng = np.random.default_rng(SEED)
    cold = df[rng.random(len(df)) < rate].copy()
    cold[LAGS] = np.nan
    return pd.concat([df, cold], ignore_index=True)


# ---------- models ----------

def fit(df, weight=None):
    """Combined mean + P90 models per class on log per-capita use. Returns {cls: (mean, p90, smear)}."""
    out = {}
    for cls, g in _with_dropout(df.dropna(subset=["pc"])).groupby("cls"):
        y = np.log(g.pc.clip(lower=1e-4))
        w = None if weight is None else g[weight]
        mean = lgb.train(PARAMS, lgb.Dataset(g[FEATURES], y, weight=w), ROUNDS)
        # alpha a touch above 0.9: the pinball fit under-covers slightly on held-out days
        p90 = lgb.train({**PARAMS, "objective": "quantile", "alpha": 0.92}, lgb.Dataset(g[FEATURES], y, weight=w),
                        ROUNDS)
        smear = float(g.pc.mean() / np.exp(mean.predict(g[FEATURES])).mean())  # log-target bias correction
        out[cls] = (mean, p90, smear)
    return out


def fit_drivers_only(df):
    """Our v1 model: drivers only, raw per-capita target."""
    return {cls: lgb.train(PARAMS, lgb.Dataset(g[DRIVERS[:-1]], g.pc), ROUNDS) for cls, g in df.groupby("cls")}


def fit_sujal(df):
    """Sujal's recipe: total-demand log target, his feature set (lags of total demand)."""
    return {cls: lgb.train(SUJAL_PARAMS, lgb.Dataset(g[SUJAL], np.log(g.consumed.clip(lower=1e-3))), SUJAL_ROUNDS)
            for cls, g in df.dropna(subset=["s_r28"]).groupby("cls")}


def _metrics(actual, pred):
    err = pred - actual
    return {"mae": float(np.mean(np.abs(err))), "wape": float(np.abs(err).sum() / actual.sum() * 100),
            "mape": float(np.mean(np.abs(err) / actual) * 100), "bias": float(err.sum() / actual.sum() * 100)}


def holdout():
    end = demo_date()
    return (end - pd.Timedelta(days=365)).strftime("%Y-%m-%d"), (end - pd.Timedelta(days=1)).strftime("%Y-%m-%d")


def evaluate(df):
    """Holdout benchmark (horizons 1-14, drivers frozen at the forecast origin) for both weather assumptions:
    our v1, Sujal's recipe, the combined model, the combined model on a site with no history, and baselines."""
    start, stop = holdout()
    train = df[df.date < start]
    v1, sj, comb = fit_drivers_only(train), fit_sujal(train), fit(train)
    clim = climatology(start)
    out = {"archived": {}, "climatology": {}}
    for cls, g in df.groupby("cls"):
        by = g.groupby("post_id")
        r7 = by.consumed.transform(lambda s: s.rolling(7).mean())
        acc = {k: [] for k in ("actual", "naive", "lag14", "r28", "p90")}
        for mode in ("archived", "climatology"):
            for k in ("v1", "sujal", "combined", "cold"):
                acc[f"{mode}_{k}"] = []
        for h in HORIZONS:
            x = g.assign(naive=r7.groupby(g.post_id).shift(h), troops=by.troops.shift(h), tempo=by.tempo.shift(h))
            x = x[(x.date >= start) & (x.date <= stop)].dropna(subset=["naive", "troops", "s_r28", "r28_14"])
            for mode in ("archived", "climatology"):
                if mode == "climatology":
                    x = x.copy()
                    for col in ("t_mean", "t_min", "snow_cm"):
                        x[col] = [clim[p].at[doy, col] for p, doy in zip(x.post_id, x.date.dt.dayofyear)]
                    x = add_features(x)
                mean, p90, smear = comb[cls]
                acc[f"{mode}_v1"].append(v1[cls].predict(x[DRIVERS[:-1]]) * x.troops)
                acc[f"{mode}_sujal"].append(np.exp(sj[cls].predict(x[SUJAL])))
                acc[f"{mode}_combined"].append(np.exp(mean.predict(x[FEATURES])) * smear * x.troops)
                cold = x.copy()
                cold[LAGS] = np.nan
                acc[f"{mode}_cold"].append(np.exp(mean.predict(cold[FEATURES])) * smear * x.troops)
                if mode == "archived":
                    acc["p90"].append(np.exp(p90.predict(x[FEATURES])) * x.troops)
            acc["actual"].append(x.consumed.to_numpy())
            acc["naive"].append(x.naive.to_numpy())
            acc["lag14"].append(x.s_lag14.to_numpy())
            acc["r28"].append(x.s_r28.to_numpy())
        a = {k: np.concatenate(v) for k, v in acc.items()}
        base = {k: _metrics(a["actual"], a[k]) for k in ("naive", "lag14", "r28")}
        for mode in ("archived", "climatology"):
            out[mode][cls] = {k: _metrics(a["actual"], a[f"{mode}_{k}"]) for k in ("v1", "sujal", "combined", "cold")}
            out[mode][cls].update(base)
        out["archived"][cls]["p90_coverage"] = float((a["actual"] <= a["p90"]).mean() * 100)
    return out


# ---------- registry ----------

REGISTRY = MODELS / "registry.json"


def registry():
    return json.loads(REGISTRY.read_text()) if REGISTRY.exists() else {"active": None, "versions": []}


def save_version(models, info, promote):
    reg = registry()
    v = 1 + max((x["version"] for x in reg["versions"]), default=0)
    d = MODELS / f"v{v}"
    d.mkdir(parents=True, exist_ok=True)
    for cls, (mean, p90, smear) in models.items():
        mean.save_model(str(d / f"{cls}_mean.txt"))
        p90.save_model(str(d / f"{cls}_p90.txt"))
        info.setdefault("smear", {})[cls] = smear
    reg["versions"].append({"version": v, "trained_at": datetime.now().isoformat(timespec="seconds"),
                            "promoted": promote, **info})
    if promote:
        reg["active"] = v
    REGISTRY.write_text(json.dumps(reg, indent=1))
    _loaded.clear()
    return v


_loaded = {}


def models(version=None):
    """{cls: (mean, p90, smear)} for a version (default: the active champion)."""
    reg = registry()
    v = version or reg["active"]
    if v not in _loaded:
        entry = next(x for x in reg["versions"] if x["version"] == v)
        d = MODELS / f"v{v}"
        _loaded[v] = {cls: (lgb.Booster(model_file=str(d / f"{cls}_mean.txt")),
                            lgb.Booster(model_file=str(d / f"{cls}_p90.txt")), entry["smear"][cls]) for cls in CLASSES}
    return _loaded[v]


def active_version():
    return registry()["active"]


def train_all():
    MODELS.mkdir(exist_ok=True)
    for f in MODELS.glob("v*/*.txt"):
        f.unlink()
    REGISTRY.unlink(missing_ok=True)
    df = history()
    bench = evaluate(df)
    final = fit(df)
    importance = {}
    for cls, (mean, _, _) in final.items():
        gain = {}
        for f, v in zip(FEATURES, mean.feature_importance("gain")):
            gain[LABELS[f]] = gain.get(LABELS[f], 0) + float(v)
        total = sum(gain.values())
        importance[cls] = {k: round(v / total, 4) for k, v in sorted(gain.items(), key=lambda kv: -kv[1])}
    start, stop = holdout()
    report = {"holdout": [start, stop], "horizons": [HORIZONS[0], HORIZONS[-1]], "train_rows": int(len(df)),
              "units": {c: CLASSES[c]["unit"] for c in CLASSES}, **bench, "importance": importance}
    (MODELS / "metrics.json").write_text(json.dumps(report, indent=1))
    save_version(final, {"rows": int(len(df)), "live_rows": 0, "note": "initial training on the issue registers",
                         "eval": {cls: round(bench["archived"][cls]["combined"]["wape"], 2) for cls in CLASSES}},
                 promote=True)
    for mode in ("archived", "climatology"):
        print(f"holdout WAPE %, {mode} weather   v1 | Sujal | combined | new site | naive | lag14 | r28")
        for cls, m in bench[mode].items():
            print(f"  {cls:11s} " + " | ".join(f"{m[k]['wape']:5.1f}" for k in
                                               ("v1", "sujal", "combined", "cold", "naive", "lag14", "r28")))
    print("P90 coverage %:", {c: round(bench["archived"][c]["p90_coverage"], 1) for c in CLASSES})
    from .learning import evaluate_local_layer
    print("new site WAPE %, without -> with the per-site layer:", evaluate_local_layer())
    return report


def metrics():
    return json.loads((MODELS / "metrics.json").read_text())


# ---------- forecasting ----------

def forecast(cls, drivers, hist_pc, version=None):
    """Recursive forecast of per-capita use for several sites at once.

    drivers: {site: DataFrame of future driver rows (FEATURES minus lags), one per day}
    hist_pc: {site: array of past per-capita daily use, oldest first; may be empty for a new site}
    Returns {site: (mean array, p90 array)}.
    """
    mean_m, p90_m, smear = models(version)[cls]
    sites = list(drivers)
    n = len(drivers[sites[0]])
    D = {s: drivers[s][DRIVERS].to_numpy(float) for s in sites}
    path = {s: np.asarray(hist_pc[s], float) for s in sites}
    out = {s: (np.empty(n), np.empty(n)) for s in sites}

    def window(series, ends, width):
        """Mean of `width` days ending 14 days before each target position (NaN if not enough history)."""
        res = np.full(len(ends), np.nan)
        for j, e in enumerate(ends):
            if e - width + 1 >= 0:
                res[j] = series[e - width + 1:e + 1].mean()
        return res

    for b in range(0, n, BLOCK):
        days = np.arange(b, min(b + BLOCK, n))
        blocks = []
        for s in sites:
            series = path[s]
            t = len(series) - b + days  # position of each target day in the series
            lag = [np.where(t - k >= 0, series[np.clip(t - k, 0, None)], np.nan) if len(series) else
                   np.full(len(days), np.nan) for k in (14, 21, 28)]
            blocks.append(np.column_stack([D[s][days], *lag, window(series, t - 14, 7), window(series, t - 14, 28)]))
        F = pd.DataFrame(np.vstack(blocks), columns=FEATURES)
        m = np.exp(mean_m.predict(F)) * smear
        q = np.maximum(np.exp(p90_m.predict(F)), m)
        for i, s in enumerate(sites):
            sl = slice(i * len(days), (i + 1) * len(days))
            out[s][0][days], out[s][1][days] = m[sl], q[sl]
            path[s] = np.concatenate([path[s], m[sl]])
    return out


def explain(cls, X, version=None):
    """Mean SHAP contribution per driver over the rows of X, as % change in use vs an average day."""
    mean_m = models(version)[cls][0]
    contrib = mean_m.predict(X[FEATURES], pred_contrib=True)[:, :-1].mean(axis=0)
    out = {}
    for f, v in zip(FEATURES, contrib):
        out[LABELS[f]] = out.get(LABELS[f], 0.0) + float(v)
    return sorted(((k, (np.exp(v) - 1) * 100) for k, v in out.items()), key=lambda kv: -abs(kv[1]))


if __name__ == "__main__":
    train_all()

"""Demand forecasting: one LightGBM model per supply class.

    python -m backend.forecast      # validate on the holdout year, then train final models

The model predicts per-capita daily consumption from weather, altitude, tempo, season and troop
strength; total = per-capita x troops, so troop surges extrapolate. Validation compares against
a naive trailing-7-day-mean baseline, with the future weather either replayed from the archive
(perfect forecast) or taken from climatology (no forecast at all).
"""
import functools
import json
import sqlite3

import lightgbm as lgb
import numpy as np
import pandas as pd

from . import network
from .config import CLASSES, DB_PATH, MODELS, SEED
from .simulator import climatology, weather

FEATURES = ["t_mean", "t_min", "snow_cm", "hdd", "alt_m", "tempo", "doy_sin", "doy_cos", "troops"]
LABELS = {"t_mean": "Mean temperature", "t_min": "Night minimum", "snow_cm": "Snowfall", "hdd": "Heating demand",
          "alt_m": "Altitude", "tempo": "Operational tempo", "doy_sin": "Season", "doy_cos": "Season",
          "troops": "Troop strength"}
HOLDOUT = ("2024-10-01", "2025-09-30")
HORIZONS = range(1, 15)
PARAMS = {"objective": "regression", "learning_rate": 0.05, "num_leaves": 15, "min_data_in_leaf": 40,
          "feature_fraction": 0.9, "seed": SEED, "deterministic": True, "force_row_wise": True,
          "num_threads": 4, "verbose": -1}
ROUNDS = 300


def add_features(df):
    """df needs t_mean, t_min, snow_cm, alt_m, tempo, troops and a datetime `date`."""
    doy = df["date"].dt.dayofyear
    return df.assign(hdd=np.maximum(0, 15 - df.t_mean), doy_sin=np.sin(2 * np.pi * doy / 365.25),
                     doy_cos=np.cos(2 * np.pi * doy / 365.25))


def history():
    with sqlite3.connect(DB_PATH) as con:
        d = pd.read_sql("SELECT post_id, date, cls, troops, tempo, consumed, stock, received FROM daily", con,
                        parse_dates=["date"])
    wx = pd.concat([w.assign(post_id=k) for k, w in weather().items()]).reset_index()
    d = d.merge(wx, on=["post_id", "date"], how="left")
    d["alt_m"] = d.post_id.map({n["id"]: n["alt_m"] for n in network.load()["nodes"]})
    return add_features(d)


def fit(df):
    return {cls: lgb.train(PARAMS, lgb.Dataset(g[FEATURES], g.consumed / g.troops), ROUNDS)
            for cls, g in df.groupby("cls")}


def _metrics(actual, pred):
    return {"mae": float(np.mean(np.abs(actual - pred))),
            "mape": float(np.mean(np.abs(actual - pred) / actual) * 100)}


def evaluate(df):
    """Holdout metrics, aggregated over horizons 1-14, for both future-weather assumptions."""
    train = df[df.date < HOLDOUT[0]]
    models = fit(train)
    clim = climatology(HOLDOUT[0])
    out = {"archived": {}, "climatology": {}}
    for cls, g in df.groupby("cls"):
        g = g.sort_values(["post_id", "date"])
        by_post = g.groupby("post_id")
        r7 = by_post.consumed.transform(lambda s: s.rolling(7).mean())
        acc = {"archived": [], "climatology": [], "naive": [], "actual": []}
        for h in HORIZONS:
            x = g.copy()
            x["naive"] = r7.groupby(g.post_id).shift(h)
            x["troops"] = by_post.troops.shift(h)  # drivers frozen at the forecast origin
            x["tempo"] = by_post.tempo.shift(h)
            x = x[(x.date >= HOLDOUT[0]) & (x.date <= HOLDOUT[1])].dropna(subset=["naive", "troops"])
            acc["archived"].append(models[cls].predict(x[FEATURES]) * x.troops)
            c = x.copy()
            for col in ("t_mean", "t_min", "snow_cm"):
                c[col] = [clim[p].at[doy, col] for p, doy in zip(c.post_id, c.date.dt.dayofyear)]
            c = add_features(c)
            acc["climatology"].append(models[cls].predict(c[FEATURES]) * c.troops)
            acc["naive"].append(x.naive.to_numpy())
            acc["actual"].append(x.consumed.to_numpy())
        a = {k: np.concatenate(v) for k, v in acc.items()}
        naive = _metrics(a["actual"], a["naive"])
        for mode in ("archived", "climatology"):
            m = _metrics(a["actual"], a[mode])
            out[mode][cls] = {"model_mae": m["mae"], "model_mape": m["mape"], "naive_mae": naive["mae"],
                              "naive_mape": naive["mape"], "mae_gain_pct": (1 - m["mae"] / naive["mae"]) * 100}
    return out


def train_all():
    df = history()
    metrics = evaluate(df)
    models = fit(df)
    MODELS.mkdir(exist_ok=True)
    importance = {}
    for cls, m in models.items():
        m.save_model(str(MODELS / f"{cls}.txt"))
        gain = dict(zip(FEATURES, m.feature_importance("gain")))
        gain["doy_sin"] += gain.pop("doy_cos")  # report season as one driver
        total = sum(gain.values())
        importance[cls] = {LABELS[k]: round(v / total, 4) for k, v in sorted(gain.items(), key=lambda kv: -kv[1])}
    report = {"holdout": HOLDOUT, "horizons": [HORIZONS[0], HORIZONS[-1]], "train_rows": int(len(df)),
              "units": {c: CLASSES[c]["unit"] for c in CLASSES}, **metrics, "importance": importance}
    (MODELS / "metrics.json").write_text(json.dumps(report, indent=1))
    for mode in ("archived", "climatology"):
        print(f"holdout, {mode} weather:")
        for cls, m in metrics[mode].items():
            print(f"  {cls:11s} MAE {m['model_mae']:8.2f} vs naive {m['naive_mae']:8.2f} ({m['mae_gain_pct']:+.0f}%)"
                  f"   MAPE {m['model_mape']:5.1f}% vs {m['naive_mape']:5.1f}%")
    return report


@functools.cache
def models():
    return {cls: lgb.Booster(model_file=str(MODELS / f"{cls}.txt")) for cls in CLASSES}


def metrics():
    return json.loads((MODELS / "metrics.json").read_text())


def predict(cls, X):
    """Per-capita consumption for feature frame X (already has FEATURES)."""
    return models()[cls].predict(X[FEATURES])


def explain(cls, X):
    """Mean SHAP contribution per driver (per-capita units) over the rows of X, largest first."""
    contrib = models()[cls].predict(X[FEATURES], pred_contrib=True)[:, :-1].mean(axis=0)
    out = {}
    for f, v in zip(FEATURES, contrib):
        out[LABELS[f]] = out.get(LABELS[f], 0.0) + float(v)
    return sorted(out.items(), key=lambda kv: -abs(kv[1]))


if __name__ == "__main__":
    train_all()

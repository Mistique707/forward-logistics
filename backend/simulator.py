"""Synthetic logistics history on real Ladakh weather.

    python -m backend.simulator

Daily, per post and supply class: troop strength, operational tempo, consumption driven by
cold/altitude/tempo, and stock with three resupply regimes:
  * open season: reorder-point convoys
  * Advance Winter Stocking (1 Jun - 31 Oct): weekly convoys building stock to the winter target
  * winter (Zojila closed): helicopter air maintenance when stock is low
Writes the SQLite DB. Deterministic for a given SEED.
"""
import functools
import sqlite3

import numpy as np
import pandas as pd

from . import network
from .config import (CLASSES, DB_PATH, POSTS, SAFETY_DAYS, SECTORS, SEED, SIM_START, TRANSIENT_CLOSE_SNOW_CM,
                     ZOJILA_CLOSE_SNOW_3D_CM, ZOJILA_LATEST_CLOSE, ZOJILA_REOPEN_TEMP_C, DATA)

AWS_START, AWS_END = (6, 1), (10, 31)
LEAD_DAYS = 3
DEMO_DAYS_BEFORE_CLOSURE = 6
ALPHA_KEROSENE_DAYS = 9


# ---------- weather ----------

@functools.cache
def weather():
    w = pd.read_csv(DATA / "weather.csv", parse_dates=["date"])
    return {k: g.drop(columns="node_id").set_index("date") for k, g in w.groupby("node_id")}


@functools.cache
def climatology():
    """Day-of-year mean weather per node (366 rows), used beyond the 16-day forecast."""
    out = {}
    for k, g in weather().items():
        c = g.groupby(g.index.dayofyear).mean()
        out[k] = c.reindex(range(1, 367)).interpolate().bfill().ffill()
    return out


# ---------- passes ----------

def zojila_open(w, closed=False):
    """Open/closed per day from snowfall and temperature (w: date-indexed weather)."""
    s3 = w.snow_cm.rolling(3, min_periods=1).sum().to_numpy()
    t14 = w.t_mean.rolling(14, min_periods=1).mean().to_numpy()
    out = np.empty(len(w), bool)
    for i, d in enumerate(w.index):
        md = (d.month, d.day)
        if not closed and d.month in (11, 12) and (s3[i] >= ZOJILA_CLOSE_SNOW_3D_CM or md >= ZOJILA_LATEST_CLOSE):
            closed = True
        elif closed and (3, 15) <= md < (7, 1) and t14[i] > ZOJILA_REOPEN_TEMP_C:
            closed = False
        out[i] = not closed
    return pd.Series(out, index=w.index)


def transient_open(w):
    """Kept-open passes shut the day of heavy snow and 1-2 days after."""
    snow = w.snow_cm.to_numpy()
    shut = np.zeros(len(w), bool)
    for i in np.flatnonzero(snow >= TRANSIENT_CLOSE_SNOW_CM):
        shut[i:i + (3 if snow[i] >= 2 * TRANSIENT_CLOSE_SNOW_CM else 2)] = True
    return pd.Series(~shut, index=w.index)


def pass_open(pid, w, closed=False):
    return zojila_open(w, closed) if pid == "ZOJILA" else transient_open(w)


def post_passes(post):
    """Passes on the road from the base depot to a post's roadhead."""
    return list(network.pair("SAPPHIRE", post)["passes"])


def zojila_season(year):
    """(close_date, reopen_date) of the winter starting in `year`, from real weather."""
    w = weather()["ZOJILA"]
    s = zojila_open(w.loc[f"{year}-10-01":f"{year + 1}-07-01"])
    close = s[~s].index.min()
    reopen = s[close:][s[close:]].index.min()
    return close, reopen


def demo_date():
    return zojila_season(2025)[0] - pd.Timedelta(days=DEMO_DAYS_BEFORE_CLOSURE)


# ---------- consumption physics (the simulator's ground truth; the forecaster never sees this) ----------

def per_capita(cls, t_mean, t_min, alt, tempo, troops):
    hdd = np.maximum(0, 15 - t_mean)
    alt_k = (alt - 3000) / 1000
    share = (troops / 60) ** -0.15  # shared heaters and generators
    if cls == "rations":
        return 1.4 * (1 + 0.15 * (alt > 3000)) * (1 + 0.01 * np.maximum(0, -t_mean))
    if cls == "kerosene":
        return 0.03 * hdd * (1 + 0.1 * alt_k) * share
    if cls == "diesel":
        return (0.25 + 0.012 * hdd) * share
    if cls == "ammunition":
        return 0.04 * tempo ** 2
    if cls == "medical":
        return 0.015 * (1 + 0.2 * alt_k + 0.01 * np.maximum(0, -t_min))
    raise ValueError(cls)


def winter_target(cls, post, troops, year):
    """Advance Winter Stocking target: expected use from 1 Nov to climatological reopening, +10%."""
    clim = climatology()[post]
    days = pd.date_range(f"{year}-11-01", f"{year + 1}-05-15")
    c = clim.loc[days.dayofyear]
    alt = network.load()["by_id"][post]["alt_m"]
    pc = per_capita(cls, c.t_mean.to_numpy(), c.t_min.to_numpy(), alt, 2, troops) * np.ones(len(days))  # plan at tempo 2
    return 1.1 * troops * pc.sum()


# ---------- drivers ----------

def _tempo(rng, n):
    P = np.array([[0.985, 0.015, 0], [0.02, 0.97, 0.01], [0, 0.03, 0.97]])
    s, out = 0, np.empty(n, int)
    for i in range(n):
        s = rng.choice(3, p=P[s])
        out[i] = s + 1
    return out


def _troops(rng, base, n):
    out = np.empty(n)
    level, surge_left, surge = base, 0, 1.0
    phase = rng.integers(90)
    for i in range(n):
        if (i + phase) % 90 == 0:
            level = round(base * rng.uniform(0.85, 1.15))
        if surge_left == 0 and rng.random() < 0.003:
            surge_left, surge = rng.integers(15, 40), rng.uniform(1.2, 1.4)
        out[i] = round(level * (surge if surge_left else 1.0))
        surge_left = max(0, surge_left - 1)
    return out


# ---------- stock ----------

def run_stock(dates, cons, route_open, W, aws_scale=1.0, block_from=None):
    """Daily stock and receipts for one post/class. W: {year: winter target}."""
    n = len(dates)
    stock = 45 * cons[:7].mean()
    pipe = np.zeros(n + 10)
    stock_out, recv_out = np.empty(n), np.empty(n)
    for i, d in enumerate(dates):
        got = pipe[i]
        stock += got
        recent = cons[max(0, i - 7):i].mean() if i else cons[0]
        pending = pipe[i + 1:].sum()
        aws = AWS_START <= (d.month, d.day) <= AWS_END
        scale = aws_scale if d.year == dates[-1].year else 1.0  # only this season's shortfall
        if block_from is None or d < block_from:
            if aws and d.weekday() == 0 and route_open[i]:
                weeks_left = (pd.Timestamp(d.year, *AWS_END) - d).days // 7 + 1
                q = max(0.0, W[d.year] - stock - pending) / weeks_left + 7 * recent
                pipe[i + LEAD_DAYS] += q * scale
            elif not aws and route_open[i] and stock + pending < 20 * recent:
                pipe[i + LEAD_DAYS] += 45 * recent - stock - pending
            elif not route_open[i] and stock + pending < 15 * recent:
                pipe[i + 2] += 30 * recent  # winter air maintenance by helicopter
        stock = max(0.0, stock - cons[i])
        stock_out[i], recv_out[i] = stock, got
    return stock_out, recv_out


def simulate():
    rng = np.random.default_rng(SEED)
    net = network.load()
    end = demo_date()
    dates = pd.date_range(SIM_START, end - pd.Timedelta(days=1))
    n = len(dates)
    wx = weather()
    tempo = {s: _tempo(rng, n) for s in SECTORS}
    landslide = pd.Timestamp(end.year, 10, 1)
    events = [(landslide.date().isoformat(), "ALPHA",
               "Landslide cuts the Drass-Alpha track; Advance Winter Stocking convoys to Post Alpha turned back."),
              ((end - pd.Timedelta(days=1)).date().isoformat(), "ALPHA",
               "Track to Post Alpha cleared. Winter-stocking backlog must move before Zoji La closes.")]

    passes_open = {p: pass_open(p, wx[p]).reindex(dates).to_numpy() for p in ("ZOJILA", "KHARDUNG", "CHANG")}
    rows = []
    for post in POSTS:
        node = net["by_id"][post]
        w = wx[post].reindex(dates)
        troops = _troops(rng, node["troops"], n)
        tp = tempo[node["sector"]]
        route_open = np.logical_and.reduce([passes_open[p] for p in post_passes(post)])
        for cls in CLASSES:
            pc = per_capita(cls, w.t_mean.to_numpy(), w.t_min.to_numpy(), node["alt_m"], tp, troops)
            noise = rng.lognormal(0, 0.12, n)
            if cls == "ammunition":  # occasional firing incidents when tempo is up
                noise *= np.where((tp >= 2) & (rng.random(n) < 0.02), rng.uniform(3, 6, n), 1)
            cons = pc * troops * noise
            W = {y: winter_target(cls, post, troops[dates.get_loc(pd.Timestamp(y, 6, 1))], y)
                 for y in range(dates[0].year, end.year + 1)}
            kw = {}
            if post == "ALPHA":
                kw["block_from"] = landslide
            if post == "FOXTROT" and cls == "diesel":
                kw["aws_scale"] = 0.6  # diesel convoys diverted this season
            stock, recv = run_stock(dates, cons, route_open, W, **kw)
            if post == "ALPHA" and cls == "kerosene":
                stock, recv = _calibrate_alpha(dates, cons, route_open, W, kw, troops[-1], tp[-1], node, end)
            rows.append(pd.DataFrame({"post_id": post, "date": dates.strftime("%Y-%m-%d"), "cls": cls,
                                      "troops": troops, "tempo": tp, "consumed": cons.round(2),
                                      "received": recv.round(1), "stock": stock.round(1)}))
    daily = pd.concat(rows)

    status = []
    for p in ("ZOJILA", "KHARDUNG", "CHANG"):
        s = pass_open(p, wx[p])
        status.append(pd.DataFrame({"pass_id": p, "date": s.index.strftime("%Y-%m-%d"), "open": s.to_numpy()}))

    last = daily[daily.date == dates[-1].strftime("%Y-%m-%d")]
    reports = pd.DataFrame({"post_id": last.post_id, "cls": last.cls, "quantity": last.stock,
                            "observed_at": end.strftime("%Y-%m-%dT06:00:00"), "source": "simulator"})

    DB_PATH.unlink(missing_ok=True)
    with sqlite3.connect(DB_PATH) as con:
        daily.to_sql("daily", con, index=False)
        con.execute("CREATE INDEX ix_daily ON daily(post_id, cls, date)")
        pd.concat(status).to_sql("pass_status", con, index=False)
        con.execute("CREATE TABLE stock_reports (id INTEGER PRIMARY KEY, post_id TEXT NOT NULL, cls TEXT NOT NULL,"
                    " quantity REAL NOT NULL, observed_at TEXT NOT NULL, source TEXT NOT NULL,"
                    " received_at TEXT DEFAULT CURRENT_TIMESTAMP)")
        reports.to_sql("stock_reports", con, index=False, if_exists="append")
        pd.DataFrame(events, columns=["date", "post_id", "text"]).to_sql("events", con, index=False)
        pd.DataFrame({"key": ["demo_date"], "value": [end.strftime("%Y-%m-%d")]}).to_sql("meta", con, index=False)
    print(f"simulated {n} days x {len(POSTS)} posts x {len(CLASSES)} classes -> {DB_PATH}; demo date {end.date()}")


def _calibrate_alpha(dates, cons, route_open, W, kw, troops, tempo, node, end):
    """Scale Alpha's last-season kerosene receipts so it holds ~ALPHA_KEROSENE_DAYS of stock on the demo date."""
    fut = weather()["ALPHA"].loc[end:end + pd.Timedelta(days=ALPHA_KEROSENE_DAYS)]
    need = troops * per_capita("kerosene", fut.t_mean.to_numpy(), fut.t_min.to_numpy(), node["alt_m"], tempo, troops)
    want = need[:ALPHA_KEROSENE_DAYS].sum() + 0.5 * need[ALPHA_KEROSENE_DAYS]
    lo, hi = 0.0, 1.0
    for _ in range(40):  # stock on the demo date is monotone in the AWS scale
        mid = (lo + hi) / 2
        stock, recv = run_stock(dates, cons, route_open, W, aws_scale=mid, **kw)
        lo, hi = (mid, hi) if stock[-1] < want else (lo, mid)
    return run_stock(dates, cons, route_open, W, aws_scale=(lo + hi) / 2, **kw)


if __name__ == "__main__":
    simulate()

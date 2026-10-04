"""Logistics history for the demo posts, anchored on real Ladakh data.

    python -m backend.simulator

Real inputs: daily weather at every post (Open-Meteo/ERA5), the Zoji La closures BRO actually made
(data/real/zojila_closures.csv), the published high-altitude ration scale. Post-level daily usage has no
public source, so it is simulated from those anchors, with a hidden "local habit" per post and class that
only the post's own inventory records reveal (that is what the per-post learning layer is for).

Stock follows three resupply regimes:
  * open season: reorder-point convoys
  * Advance Winter Stocking (1 Jun - 31 Oct): weekly convoys building stock to the winter target
  * winter (Zoji La closed): helicopter air maintenance when stock is low
Writes the SQLite DB. Deterministic for a given SEED.
"""
import functools
import sqlite3

import numpy as np
import pandas as pd

from . import network
from .config import (CLASSES, DATA, DB_PATH, HA_RATION_KCAL, POSTS, RATION_KCAL_PER_KG, SECTORS, SEED, SIM_START,
                     TRANSIENT_CLOSE_SNOW_CM, ZOJILA_CLOSE_SNOW_3D_CM, ZOJILA_EARLIEST_CLOSE, ZOJILA_MIN_CLOSED_DAYS,
                     ZOJILA_REOPEN_TEMP_C)

AWS_START, AWS_END = (6, 1), (10, 31)
LEAD_DAYS = 3
DEMO_WINTER = 2024              # the demo runs in winter 2024-25, just before the real February closure
DEMO_DAYS_BEFORE_CLOSURE = 6
ALPHA_KEROSENE_DAYS = 9
AVALANCHE = pd.Timestamp("2025-02-08")
AWS_MARGIN_DAYS = 21            # winter stock covers the expected reopening plus three weeks
REAL_CLOSURES = DATA / "real" / "zojila_closures.csv"


# ---------- weather ----------

@functools.cache
def weather():
    w = pd.read_csv(DATA / "weather.csv", parse_dates=["date"])
    return {k: g.drop(columns="node_id").set_index("date") for k, g in w.groupby("node_id")}


@functools.cache
def climatology(until):
    """Day-of-year mean weather per node (366 rows) from data before `until`; no peeking ahead."""
    out = {}
    for k, g in weather().items():
        g = g[g.index < until]
        c = g.groupby(g.index.dayofyear).mean()
        out[k] = c.reindex(range(1, 367)).interpolate().bfill().ffill()
    return out


# ---------- passes ----------

@functools.cache
def real_closures():
    r = pd.read_csv(REAL_CLOSURES, parse_dates=["closed_on", "reopened_on"])
    return list(zip(r.winter, r.closed_on, r.reopened_on))


def zojila_rule(w, closed=False):
    """Open/closed per day from snowfall and temperature (w: date-indexed weather), fitted to BRO's record."""
    s3 = w.snow_cm.rolling(3, min_periods=1).sum().to_numpy()
    t14 = w.t_mean.rolling(14, min_periods=1).mean().to_numpy()
    out, shut_for = np.empty(len(w), bool), 0
    for i, d in enumerate(w.index):
        closing_season = (d.month, d.day) >= ZOJILA_EARLIEST_CLOSE or d.month <= 3
        if not closed and closing_season and s3[i] >= ZOJILA_CLOSE_SNOW_3D_CM:
            closed, shut_for = True, 0
        elif closed:
            shut_for += 1
            if shut_for >= ZOJILA_MIN_CLOSED_DAYS and t14[i] > ZOJILA_REOPEN_TEMP_C:
                closed = False
        out[i] = not closed
    return pd.Series(out, index=w.index)


def zojila_history(index):
    """Real BRO closures for the winters on record, the fitted rule for any other day."""
    s = zojila_rule(weather()["ZOJILA"]).reindex(index).fillna(True).astype(bool)
    for _, closed, reopened in real_closures():
        start = pd.Timestamp(closed.year if closed.month >= 10 else closed.year - 1, 10, 1)
        season = (index >= start) & (index < start + pd.DateOffset(months=9))
        s[season] = ~((index[season] >= closed) & (index[season] < reopened))
    return s


def transient_open(w):
    """Kept-open passes shut the day of heavy snow and 1-2 days after."""
    snow = w.snow_cm.to_numpy()
    shut = np.zeros(len(w), bool)
    for i in np.flatnonzero(snow >= TRANSIENT_CLOSE_SNOW_CM):
        shut[i:i + (3 if snow[i] >= 2 * TRANSIENT_CLOSE_SNOW_CM else 2)] = True
    return pd.Series(~shut, index=w.index)


def post_passes(post):
    """Passes on the road from the base depot to a post's roadhead."""
    return list(network.pair("SAPPHIRE", post)["passes"])


def zojila_season(year):
    """(close, reopen) the fitted rule predicts on real weather for the winter starting in `year`."""
    s = zojila_rule(weather()["ZOJILA"].loc[f"{year}-10-01":f"{year + 1}-07-01"])
    close = s[~s].index.min()
    reopen = s[close:][s[close:]].index.min()
    return close, reopen


def closure_validation():
    """Fitted rule vs the real record, per winter (days late is positive)."""
    rows = []
    for winter, closed, reopened in real_closures():
        pc, pr = zojila_season(closed.year if closed.month >= 10 else closed.year - 1)
        rows.append({"winter": winter, "real_close": closed.date().isoformat(), "rule_close": pc.date().isoformat(),
                     "close_err_days": (pc - closed).days, "real_reopen": reopened.date().isoformat(),
                     "rule_reopen": pr.date().isoformat(), "reopen_err_days": (pr - reopened).days})
    return rows


def demo_date():
    return zojila_season(DEMO_WINTER)[0] - pd.Timedelta(days=DEMO_DAYS_BEFORE_CLOSURE)


def planned_reopening(year):
    """Reopening date a June planner expects: the mean of the real reopenings on record before then."""
    days = [r.dayofyear for _, _, r in real_closures() if r < pd.Timestamp(year, 6, 1)]
    doy = round(np.mean(days)) if days else 80
    return pd.Timestamp(year + 1, 1, 1) + pd.Timedelta(days=doy - 1)


# ---------- consumption physics (the simulator's ground truth; the forecaster never sees this) ----------

RATION_KG_HA = HA_RATION_KCAL / RATION_KCAL_PER_KG  # ~1.57 kg/man/day at the authorised HA scale


def per_capita(cls, t_mean, t_min, alt, tempo, troops):
    hdd = np.maximum(0, 15 - t_mean)
    alt_k = (alt - 3000) / 1000
    share = (troops / 60) ** -0.15  # shared heaters and generators
    if cls == "rations":
        return RATION_KG_HA * (0.87 + 0.13 * (alt > 2700)) * (1 + 0.01 * np.maximum(0, -t_mean))
    if cls == "kerosene":
        return (0.05 + 0.03 * hdd * (1 + 0.1 * alt_k)) * share  # cooking + heating (assumed norm)
    if cls == "diesel":
        return (0.25 + 0.012 * hdd) * share
    if cls == "ammunition":
        return 0.04 * tempo ** 2
    if cls == "medical":
        return 0.015 * (1 + 0.2 * alt_k + 0.01 * np.maximum(0, -t_min))
    raise ValueError(cls)


def local_habits(rng):
    """Hidden per-post, per-class usage factor (old heaters, cooking practice, generator wear)."""
    h = {(p, c): float(np.exp(rng.normal(0, 0.1))) for p in POSTS for c in CLASSES}
    h["BRAVO", "kerosene"] = 1.3   # ageing bukharis
    h["HOTEL", "diesel"] = 1.25    # generators de-rated at 5,200 m
    return h


def winter_target(cls, post, troops, year, habit=1.0):
    """Advance Winter Stocking target: expected use from 1 Nov to the planned reopening + margin, +10%."""
    clim = climatology(f"{year}-06-01")[post]
    days = pd.date_range(f"{year}-11-01", planned_reopening(year) + pd.Timedelta(days=AWS_MARGIN_DAYS))
    c = clim.loc[days.dayofyear]
    alt = network.load()["by_id"][post]["alt_m"]
    pc = per_capita(cls, c.t_mean.to_numpy(), c.t_min.to_numpy(), alt, 2, troops) * np.ones(len(days))  # plan at tempo 2
    return 1.1 * troops * pc.sum() * habit  # last winter's books already show the post's own habits


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

def run_stock(dates, cons, route_open, W, aws_scale=1.0, aws_year=None, block=None, loss=None):
    """Daily stock and receipts for one post/class.

    W: {year: winter target}; aws_scale applies to the `aws_year` build-up only; block: (from, to) dates with
    no deliveries; loss: (date, quantity) written off that day.
    """
    n = len(dates)
    stock = 45 * cons[:7].mean()
    pipe = np.zeros(n + 10)
    stock_out, recv_out = np.empty(n), np.empty(n)
    for i, d in enumerate(dates):
        got = pipe[i]
        stock += got
        if loss and d == loss[0]:
            stock = max(0.0, stock - loss[1])
        recent = cons[max(0, i - 7):i].mean() if i else cons[0]
        pending = pipe[i + 1:].sum()
        aws = AWS_START <= (d.month, d.day) <= AWS_END
        scale = aws_scale if d.year == aws_year else 1.0
        if block is None or not block[0] <= d < block[1]:
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
    habits = local_habits(rng)
    aws_year = end.year if end.month >= 6 else end.year - 1
    alpha_block = (AVALANCHE, end - pd.Timedelta(days=1))
    events = [(f"{aws_year}-10-31", "FOXTROT", "Foxtrot received only 60% of its winter-stocking diesel: convoys diverted."),
              (AVALANCHE.date().isoformat(), "ALPHA", "Avalanche at Post Alpha damages the kerosene store and blocks "
                                                      "the mule track from Drass."),
              (alpha_block[1].date().isoformat(), "ALPHA", "Mule track to Post Alpha cleared.")]

    passes_open = {"ZOJILA": zojila_history(dates).to_numpy(),
                   **{p: transient_open(wx[p]).reindex(dates).to_numpy() for p in ("KHARDUNG", "CHANG")}}
    rows = []
    for post in POSTS:
        node = net["by_id"][post]
        w = wx[post].reindex(dates)
        troops = _troops(rng, node["troops"], n)
        tp = tempo[node["sector"]]
        route_open = np.logical_and.reduce([passes_open[p] for p in post_passes(post)])
        for cls in CLASSES:
            habit = habits[post, cls]
            pc = per_capita(cls, w.t_mean.to_numpy(), w.t_min.to_numpy(), node["alt_m"], tp, troops) * habit
            noise = rng.lognormal(0, 0.12, n)
            if cls == "ammunition":  # occasional firing incidents when tempo is up
                noise *= np.where((tp >= 2) & (rng.random(n) < 0.02), rng.uniform(3, 6, n), 1)
            cons = pc * troops * noise
            W = {y: winter_target(cls, post, troops[dates.get_loc(pd.Timestamp(y, 6, 1))], y, habit)
                 for y in range(dates[0].year, end.year + 1) if pd.Timestamp(y, 6, 1) in dates}
            kw = {"aws_year": aws_year}
            if post == "ALPHA":
                kw["block"] = alpha_block
            if post == "FOXTROT" and cls == "diesel":
                kw["aws_scale"] = 0.6
            stock, recv = run_stock(dates, cons, route_open, W, **kw)
            if post == "ALPHA" and cls == "kerosene":
                loss = _alpha_loss(stock, troops[-1], tp[-1], node, end, habit)
                events[1] = (events[1][0], "ALPHA", events[1][2].replace("kerosene store", f"kerosene store "
                                                                           f"({loss:,.0f} L lost)"))
                stock, recv = run_stock(dates, cons, route_open, W, loss=(AVALANCHE, loss), **kw)
            rows.append(pd.DataFrame({"post_id": post, "date": dates.strftime("%Y-%m-%d"), "cls": cls,
                                      "troops": troops, "tempo": tp, "consumed": cons.round(2),
                                      "received": recv.round(1), "stock": stock.round(1)}))
    daily = pd.concat(rows)

    status = [pd.DataFrame({"pass_id": p, "date": dates.strftime("%Y-%m-%d"), "open": passes_open[p]})
              for p in passes_open]
    last = daily[daily.date == dates[-1].strftime("%Y-%m-%d")]
    counts = pd.DataFrame({"site_id": last.post_id, "cls": last.cls, "kind": "count", "quantity": last.stock,
                           "observed_at": end.strftime("%Y-%m-%dT06:00:00"), "source": "simulator"})

    DB_PATH.unlink(missing_ok=True)
    with sqlite3.connect(DB_PATH) as con:
        daily.to_sql("daily", con, index=False)
        con.execute("CREATE INDEX ix_daily ON daily(post_id, cls, date)")
        pd.concat(status).to_sql("pass_status", con, index=False)
        con.executescript(SCHEMA)
        counts.to_sql("inventory", con, index=False, if_exists="append")
        pd.DataFrame(events, columns=["date", "post_id", "text"]).to_sql("events", con, index=False)
        pd.DataFrame({"key": ["demo_date"], "value": [end.strftime("%Y-%m-%d")]}).to_sql("meta", con, index=False)
    print(f"simulated {n} days x {len(POSTS)} posts x {len(CLASSES)} classes -> {DB_PATH}; demo date {end.date()}")


# Inventory events (counts, receipts, issues) from the field app or any other source, and the live sites
# people register to collect real data.
SCHEMA = """
CREATE TABLE inventory (id INTEGER PRIMARY KEY, site_id TEXT NOT NULL, cls TEXT NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN ('count', 'receipt', 'issue')), quantity REAL NOT NULL CHECK (quantity >= 0),
  observed_at TEXT NOT NULL, source TEXT NOT NULL, received_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE INDEX ix_inventory ON inventory(site_id, cls, observed_at);
CREATE TABLE sites (id TEXT PRIMARY KEY, name TEXT NOT NULL, lat REAL NOT NULL, lon REAL NOT NULL,
  alt_m REAL NOT NULL, headcount INTEGER NOT NULL CHECK (headcount > 0), created_at TEXT DEFAULT CURRENT_TIMESTAMP);
"""


def _alpha_loss(stock, troops, tempo, node, end, habit):
    """Kerosene lost in the avalanche so Alpha holds ~ALPHA_KEROSENE_DAYS of stock on the demo date.

    Nothing new is ordered after the avalanche, so the closing stock falls one-for-one with the write-off.
    """
    fut = weather()["ALPHA"].loc[end:end + pd.Timedelta(days=ALPHA_KEROSENE_DAYS)]
    need = troops * habit * per_capita("kerosene", fut.t_mean.to_numpy(), fut.t_min.to_numpy(), node["alt_m"],
                                       tempo, troops)
    want = need[:ALPHA_KEROSENE_DAYS].sum() + 0.5 * need[ALPHA_KEROSENE_DAYS]
    return max(0.0, stock[-1] - want)


if __name__ == "__main__":
    simulate()

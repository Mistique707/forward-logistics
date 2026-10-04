import sqlite3

import numpy as np
import pandas as pd

from backend.config import DB_PATH
from backend.simulator import _tempo, closure_validation, demo_date, per_capita, zojila_history


def test_consumption_responds_to_drivers():
    cold, warm = per_capita("kerosene", -20, -28, 4500, 1, 60), per_capita("kerosene", 10, 2, 4500, 1, 60)
    assert cold > 3 * warm
    assert per_capita("kerosene", -10, -15, 5000, 1, 60) > per_capita("kerosene", -10, -15, 3500, 1, 60)
    assert per_capita("ammunition", 0, 0, 4000, 3, 60) > 4 * per_capita("ammunition", 0, 0, 4000, 1, 60)
    assert abs(per_capita("rations", 0, 0, 4000, 1, 60) - 4088 / 2600) < 0.01  # published HA ration scale


def test_simulated_history_is_cold_driven():
    with sqlite3.connect(DB_PATH) as con:
        d = pd.read_sql("SELECT date, consumed / troops AS pc FROM daily WHERE post_id='ALPHA' AND cls='kerosene'", con)
    month = pd.to_datetime(d.date).dt.month
    assert d[month.isin([12, 1, 2])].pc.mean() > 2 * d[month.isin([6, 7, 8])].pc.mean()


def test_closure_rule_matches_real_record():
    v = closure_validation()
    assert len(v) == 5
    assert np.mean([abs(r["close_err_days"]) for r in v]) <= 3
    assert np.mean([abs(r["reopen_err_days"]) for r in v]) <= 10
    # history uses the real closures: shut on 1 Mar 2024, open on 1 Feb 2025 (BRO kept it open)
    s = zojila_history(pd.DatetimeIndex(["2024-03-01", "2025-02-01"]))
    assert not s.iloc[0] and s.iloc[1]


def test_seeded_and_pinned():
    a, b = _tempo(np.random.default_rng(1), 200), _tempo(np.random.default_rng(1), 200)
    assert (a == b).all()
    assert demo_date() == pd.Timestamp("2025-02-20")  # six days before the forecast closure of 26 Feb 2025

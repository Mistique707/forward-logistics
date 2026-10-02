import sqlite3

import numpy as np
import pandas as pd

from backend.config import DB_PATH
from backend.simulator import _tempo, per_capita, zojila_season


def test_consumption_responds_to_drivers():
    cold, warm = per_capita("kerosene", -20, -28, 4500, 1, 60), per_capita("kerosene", 10, 2, 4500, 1, 60)
    assert cold > 3 * warm
    assert per_capita("kerosene", -10, -15, 5000, 1, 60) > per_capita("kerosene", -10, -15, 3500, 1, 60)
    assert per_capita("ammunition", 0, 0, 4000, 3, 60) > 4 * per_capita("ammunition", 0, 0, 4000, 1, 60)


def test_simulated_history_is_cold_driven():
    with sqlite3.connect(DB_PATH) as con:
        d = pd.read_sql("SELECT date, consumed / troops AS pc FROM daily WHERE post_id='ALPHA' AND cls='kerosene'", con)
    month = pd.to_datetime(d.date).dt.month
    assert d[month.isin([12, 1, 2])].pc.mean() > 2 * d[month.isin([6, 7, 8])].pc.mean()


def test_seeded_and_pinned():
    a, b = _tempo(np.random.default_rng(1), 200), _tempo(np.random.default_rng(1), 200)
    assert (a == b).all()
    assert zojila_season(2025)[0] == pd.Timestamp("2025-12-21")  # S = 15 cm / 3 days on real weather

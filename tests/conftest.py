import sqlite3

import pytest

from backend.config import DB_PATH, MODELS


def _stale():
    with sqlite3.connect(DB_PATH) as con:  # the newest table; a database without it predates the IoT schema
        return not con.execute("SELECT 1 FROM sqlite_master WHERE name = 'anomalies'").fetchone()


@pytest.fixture(scope="session", autouse=True)
def built():
    """Seed and train once if the demo has not been built yet, or was built before the current schema."""
    if not DB_PATH.exists() or _stale():
        from backend.simulator import simulate
        simulate()
    if not (MODELS / "registry.json").exists() or not (MODELS / "learning_eval.json").exists():
        from backend.forecast import train_all
        train_all()

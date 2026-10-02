import pytest

from backend.config import DB_PATH, MODELS


@pytest.fixture(scope="session", autouse=True)
def built():
    """Seed and train once if the demo has not been built yet."""
    if not DB_PATH.exists():
        from backend.simulator import simulate
        simulate()
    if not (MODELS / "metrics.json").exists():
        from backend.forecast import train_all
        train_all()

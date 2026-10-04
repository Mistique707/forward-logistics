import pandas as pd

from backend.learning import PRIOR_DAYS, _factor, usage_from_events


def ev(rows):
    return pd.DataFrame(rows, columns=["cls", "kind", "quantity", "observed_at"]).assign(
        observed_at=lambda d: pd.to_datetime(d.observed_at))


def test_usage_from_counts_receipts_and_issues():
    use = usage_from_events(ev([
        ("rations", "count", 100, "2026-01-01 08:00"),
        ("rations", "receipt", 50, "2026-01-02 10:00"),
        ("rations", "issue", 10, "2026-01-03 12:00"),
        ("rations", "count", 110, "2026-01-05 08:00"),
    ]))["rations"]
    # 100 + 50 - 10 - 110 = 30 unexplained over 2-5 Jan, plus the 10 recorded on 3 Jan
    assert abs(use.sum() - 40) < 1e-9
    assert len(use) == 4


def test_factor_is_shrunk_until_a_site_has_data():
    assert _factor(130, 100, 0) == 1.0
    assert abs(_factor(130, 100, PRIOR_DAYS) - 1.15) < 1e-9
    assert _factor(130, 100, 365) > 1.28

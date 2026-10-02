from backend.forecast import metrics


def test_forecast_beats_naive_baseline():
    m = metrics()
    for mode in ("archived", "climatology"):
        for cls, r in m[mode].items():
            assert r["model_mae"] < r["naive_mae"], (mode, cls, r)

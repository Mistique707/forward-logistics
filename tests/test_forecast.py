from backend.config import CLASSES
from backend.forecast import metrics
from backend.learning import EVAL_FILE
import json


def test_combined_model_beats_baselines_and_parents():
    m = metrics()
    for mode in ("archived", "climatology"):
        for cls, r in m[mode].items():
            best_baseline = min(r[k]["wape"] for k in ("naive", "lag14", "r28"))
            assert r["combined"]["wape"] < best_baseline, (mode, cls)
            # the merge keeps the better of the two parent designs (within half a point)
            assert r["combined"]["wape"] <= min(r["v1"]["wape"], r["sujal"]["wape"]) + 0.5, (mode, cls)


def test_p90_covers_most_days():
    m = metrics()
    for cls in CLASSES:
        assert 80 <= m["archived"][cls]["p90_coverage"] <= 96


def test_per_site_layer_helps_new_sites():
    ev = json.loads(EVAL_FILE.read_text())["wape"]
    for cls, r in ev.items():
        assert r["with_layer"] < r["new_site"], cls

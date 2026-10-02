from backend.config import MODES, heli_derate
from backend import network
from backend.planner import plan

CTX = {"closures": {}, "no_go": {}, "grounded": {}, "mule_blocked": {}}


def test_trips_respect_capacity():
    demands = [{"post": p, "cls": "rations", "runout_day": 5, "urgent_kg": 2500, "stocking_kg": 9000}
               for p in ("ALPHA", "BRAVO", "ECHO", "HOTEL")]
    out = plan(demands, CTX)
    assert out["trips"]
    by_id = network.load()["by_id"]
    for t in out["trips"]:
        assert t["load_kg"] <= t["capacity_kg"]
        if t["mode"] == "heli":  # payload falls with landing-site altitude
            assert sum(s["kg"] / heli_derate(by_id[s["post"]]["alt_m"]) for s in t["stops"]) <= MODES["heli"]["payload_kg"] + 1


def test_closed_pass_removes_trucks():
    demands = [{"post": "ALPHA", "cls": "kerosene", "runout_day": 9, "urgent_kg": 600, "stocking_kg": 4000}]
    open_ = plan(demands, CTX)
    shut = plan(demands, {**CTX, "closures": {"ZOJILA": [(0, 10 ** 6)]}})
    assert {t["mode"] for t in open_["trips"]} == {"truck"}
    assert "truck" not in {t["mode"] for t in shut["trips"]}
    assert shut["totals"]["deferred_kg"] > 0

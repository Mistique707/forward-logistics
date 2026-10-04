from backend.scenarios import PRESETS, diff, get_state

PRESET = {p["id"]: p["overrides"] for p in PRESETS}


def test_headline_alert():
    a = get_state({})["alerts"][0]
    assert a["severity"] == "critical"
    assert "Post Alpha runs out of kerosene in 9 days" in a["text"] and "Zoji La closes in 6 d" in a["text"]
    assert "dispatch" in a["text"] and "truck" in a["text"]


def test_pass_closure_changes_the_plan():
    base, shut = get_state({}), get_state(PRESET["closed_now"])
    assert base["kpis"]["trucks"] > 0 and shut["kpis"]["trucks"] == 0
    assert shut["kpis"]["air_liability_t"] > 0
    assert diff(shut, base)["mode_changes"]


def test_grounding_delays_air_lift():
    grounded = get_state(PRESET["closed_grounded"])
    air = [t for t in grounded["plan"]["trips"] if t["mode"] in ("heli", "airdrop")]
    assert air and all(t["mode"] == "airdrop" or t["depart_h"] >= 72 for t in air)


def test_late_reopening_and_surge_raise_demand():
    base = get_state({})["kpis"]["tonnes_planned"]
    assert get_state(PRESET["what_happened"])["kpis"]["tonnes_planned"] > base
    assert get_state(PRESET["surge_bravo"])["kpis"]["tonnes_planned"] > base

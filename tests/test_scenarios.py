from backend.scenarios import PRESETS, diff, get_state

PRESET = {p["id"]: p["overrides"] for p in PRESETS}


def test_headline_alert():
    a = get_state({})["alerts"][0]
    assert "Post Alpha runs out of kerosene in 9 days" in a["text"] and "Zoji La closes in 6 d" in a["text"]
    assert "dispatch" in a["text"] and "truck" in a["text"]


def test_pass_closure_changes_the_plan():
    base, shut = get_state({}), get_state(PRESET["closed_now"])
    assert base["kpis"]["trucks"] > 0 and shut["kpis"]["trucks"] == 0
    assert shut["kpis"]["air_liability_t"] > 0
    assert diff(shut, base)["mode_changes"]


def test_grounding_forces_airdrop_when_pass_shut():
    shut, grounded = get_state(PRESET["closed_now"]), get_state(PRESET["closed_grounded"])
    assert shut["kpis"]["heli_sorties"] > 0
    assert grounded["kpis"]["heli_sorties"] == 0 and grounded["kpis"]["airdrops"] > 0


def test_troop_surge_raises_demand():
    base, surge = get_state({}), get_state(PRESET["surge_bravo"])
    assert surge["kpis"]["tonnes_planned"] > base["kpis"]["tonnes_planned"]

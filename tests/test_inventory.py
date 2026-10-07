"""Ingestion paths: idempotent sync, demo clock, discrepancy flags, signed sensor telemetry, emergency lifts.

Each test works on a copy of the demo database, so the shared demo state is left untouched.
"""
import json
import shutil
import sqlite3

import pytest

from backend import config, inventory, learning, scenarios
from backend.planner import plan


@pytest.fixture()
def db(tmp_path, monkeypatch):
    copy = tmp_path / "logistics.db"
    shutil.copy(config.DB_PATH, copy)
    for mod in (config, inventory, learning, scenarios):
        monkeypatch.setattr(mod, "DB_PATH", copy, raising=False)
    monkeypatch.setattr(learning, "maybe_retrain", lambda: None)
    scenarios.invalidate()
    yield copy
    scenarios.invalidate()


def ev(**kw):
    return {"site_id": "ALPHA", "cls": "kerosene", "kind": "receipt", "quantity": 200.0, "observed_at": None,
            "source": "test", **kw}


def stock(site="ALPHA", cls="kerosene"):
    return scenarios.current_stock()[site, cls]


def test_retried_upload_is_stored_once(db):
    before = stock()
    first = inventory.ingest([ev(client_id="uid-0001-aaaa")])
    again = inventory.ingest([ev(client_id="uid-0001-aaaa")])
    assert (first["accepted"], again["accepted"], again["duplicates"]) == (1, 0, 1)
    assert stock() == pytest.approx(before + 200)


def test_demo_records_apply_in_arrival_order_whatever_the_wall_clock(db):
    from datetime import datetime
    before = stock()
    inventory.ingest([ev(observed_at=datetime(2026, 1, 1, 3, 0))])   # before the 06:00 snapshot, wall clock
    inventory.ingest([ev(observed_at=datetime(2026, 1, 1, 23, 0))])
    inventory.ingest([ev(observed_at=datetime(2026, 1, 2, 0, 30))])  # after midnight
    assert stock() == pytest.approx(before + 600)


def test_count_far_below_books_is_flagged(db):
    book = stock("ECHO", "diesel")
    ok = inventory.ingest([ev(site_id="ECHO", cls="diesel", kind="count", quantity=book * 0.97)])
    bad = inventory.ingest([ev(site_id="ECHO", cls="diesel", kind="count", quantity=book * 0.97 * 0.7)])
    assert not ok["anomalies"] and bad["anomalies"]
    alerts = scenarios.get_state({})["alerts"]
    assert any(a["kind"] == "anomaly" and a["post"] == "ECHO" for a in alerts)
    inventory.resolve_anomaly(inventory.anomalies()[0]["id"])
    assert not any(a["kind"] == "anomaly" for a in scenarios.get_state({})["alerts"])


def _reading(dev, secret, seq, fill):
    body = json.dumps({"device_id": dev, "readings": [{"seq": seq, "fill_pct": fill}]}).encode()
    return body, inventory.sign(secret, body)


def test_sensor_telemetry_is_signed_deadbanded_and_replay_safe(db):
    d = next(x for x in inventory.devices() if x["id"] == "TNK-ECHO-DSL")
    with sqlite3.connect(db) as con:
        secret = con.execute("SELECT secret FROM devices WHERE id = ?", (d["id"],)).fetchone()[0]
    seq = d["last_seq"] + 1  # the demo's simulated sensors may already have reported into this database
    body, sig = _reading(d["id"], secret, seq, d["book"] / d["capacity"] * 100)
    with pytest.raises(inventory.Rejected) as forged:
        inventory.telemetry(body, "0" * 64)
    assert forged.value.status == 401
    hb = inventory.telemetry(body, sig)
    assert (hb["heartbeats"], hb["accepted"]) == (1, 0)                 # matches the books: heartbeat only
    assert inventory.telemetry(body, sig)["replays"] == 1               # same sequence number again
    low = (d["book"] - 500) / d["capacity"] * 100
    res = inventory.telemetry(*_reading(d["id"], secret, seq + 1, low))
    assert res["accepted"] == 1
    assert stock("ECHO", "diesel") == pytest.approx(d["book"] - 500, abs=d["capacity"] * 0.0001 + 0.1)
    assert next(x for x in inventory.devices() if x["id"] == d["id"])["online"]


def test_site_names_never_collide(db, monkeypatch):
    monkeypatch.setattr("backend.fetch_data.elevation", lambda *a, **k: [500.0])
    a = learning.register_site("VIT Hostel Mess", 18.46, 73.87, 300)
    b = learning.register_site("vit hostel mess!!", 10, 10, 5)
    assert a != b
    assert {s["id"]: s["headcount"] for s in learning.live_sites()}[a] == 300


def test_post_already_out_gets_an_emergency_lift():
    demands = [{"post": "BRAVO", "cls": "kerosene", "runout_day": 0, "urgent_kg": 400, "stocking_kg": 0}]
    ctx = {"closures": {}, "no_go": {}, "grounded": {}, "mule_blocked": {}}
    out = plan(demands, ctx)
    assert not out["unmet"] and out["trips"]
    assert all(s["arrive_h"] <= 24 for t in out["trips"] for s in t["stops"])


def test_when_nothing_lands_in_time_the_earliest_lift_is_planned_and_flagged_late():
    # Alpha is behind a mule track, so no truck lands within a day; air is grounded for the first two days
    demands = [{"post": "ALPHA", "cls": "kerosene", "runout_day": 0, "urgent_kg": 900, "stocking_kg": 0}]
    ctx = {"closures": {}, "no_go": {}, "grounded": {"heli": {0, 1}, "airdrop": {0, 1}}, "mule_blocked": {}}
    out = plan(demands, ctx)
    assert not out["unmet"]
    stops = [s for t in out["trips"] for s in t["stops"]]
    assert stops and all("kerosene" in s["late"] for s in stops)
    assert len(out["trips"]) == 1  # one lift, as early as possible, not one per chunk

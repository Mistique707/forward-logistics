"""Inventory ingestion: field-app records, IoT sensor telemetry, book-vs-physical reconciliation.

Every record path ends in `ingest()`, which
  * stores a retried upload once (client_id is unique),
  * puts demo-post records on the demo clock, after the simulator's stock snapshot,
  * flags a physical count that is well below book stock (leak, pilferage, unrecorded issue),
  * refreshes the supply picture and lets the learning loop decide whether to retrain.

Sensors report by exception: a reading within the deadband of the book stock only updates the device's
heartbeat, so a healthy store costs a few bytes a minute and does not churn the plan.
"""
import hashlib
import hmac
import json
import sqlite3
from datetime import datetime, timedelta

from . import learning, scenarios
from .config import (ANOMALY_DAYS, ANOMALY_SHARE, CLASSES, DB_PATH, IOT_DEADBAND_PCT, IOT_ONLINE_S, POSTS)
from .simulator import SNAPSHOT_TIME


class Rejected(Exception):
    """Input the caller must fix (maps to HTTP 4xx)."""

    def __init__(self, status, detail):
        super().__init__(detail)
        self.status, self.detail = status, detail


def _db():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def site_ids():
    return set(POSTS) | {x["id"] for x in learning.live_sites()}


def _stamp(site_id, observed_at, now):
    """Demo posts live on a frozen simulated clock: every record is dated just after the demo day's snapshot count
    and records apply in arrival order (received_at keeps the wall-clock time). Using the wall-clock time of day
    instead would drop a record made before 06:00, or after midnight, behind older ones.
    Live sites keep the time the record was observed."""
    if site_id in POSTS:
        return f"{learning.demo_clock().date().isoformat()}T{_after(SNAPSHOT_TIME)}"
    return (observed_at or now).isoformat(timespec="seconds")


def _after(hms):
    t = datetime.strptime(hms, "%H:%M:%S") + timedelta(seconds=1)
    return t.strftime("%H:%M:%S")


def _daily_use():
    """Forecast daily use per demo post and class (baseline), for the anomaly threshold."""
    st = scenarios.get_state({})
    return {(p["id"], c): v["daily"] for p in st["posts"] for c, v in p["classes"].items()}


def ingest(events, operator=None):
    """events: dicts with site_id, cls, kind, quantity, observed_at (datetime|None), source, client_id (opt).
    Returns {accepted, duplicates, anomalies}."""
    if not events or len(events) > 500:
        raise Rejected(422, "send 1-500 records")
    if bad := sorted({e["site_id"] for e in events} - site_ids()):
        raise Rejected(422, f"unknown site: {', '.join(bad)}")
    now = datetime.now()
    book = scenarios.current_stock()
    use = _daily_use() if any(e["kind"] == "count" and e["site_id"] in POSTS for e in events) else {}
    accepted, dup, flagged = 0, 0, []
    with _db() as con:
        for e in events:
            at = _stamp(e["site_id"], e.get("observed_at"), now)
            src = e["source"] + (f":{operator}" if operator else "")
            cur = con.execute("INSERT OR IGNORE INTO inventory (site_id, cls, kind, quantity, observed_at, source, "
                              "client_id) VALUES (?,?,?,?,?,?,?)",
                              (e["site_id"], e["cls"], e["kind"], e["quantity"], at, src, e.get("client_id")))
            if not cur.rowcount:
                dup += 1
                continue
            accepted += 1
            key = (e["site_id"], e["cls"])
            # demo posts sit on a frozen clock, so their book stock is exact and any shortfall is unexplained
            if e["kind"] == "count" and e["site_id"] in POSTS and key in book:
                short = book[key] - e["quantity"]
                if short > max(ANOMALY_SHARE * book[key], ANOMALY_DAYS * use.get(key, 0)):
                    con.execute("INSERT INTO anomalies (site_id, cls, book, observed, source, observed_at) "
                                "VALUES (?,?,?,?,?,?)", (*key, round(book[key], 1), e["quantity"], src, at))
                    flagged.append({"site_id": key[0], "cls": key[1], "book": round(book[key]),
                                    "observed": round(e["quantity"])})
            # later records in the same batch build on this one
            if e["kind"] == "count":
                book[key] = e["quantity"]
            elif key in book:
                book[key] = max(0.0, book[key] + (e["quantity"] if e["kind"] == "receipt" else -e["quantity"]))
    if accepted:
        scenarios.invalidate()
        learning.maybe_retrain()
    return {"accepted": accepted, "duplicates": dup, "anomalies": flagged}


# ---------- book vs physical ----------

def anomalies(status="open"):
    with _db() as con:
        return [dict(r) for r in con.execute("SELECT * FROM anomalies WHERE status = ? ORDER BY id DESC", (status,))]


def resolve_anomaly(aid):
    with _db() as con:
        if not con.execute("UPDATE anomalies SET status = 'checked' WHERE id = ? AND status = 'open'", (aid,)).rowcount:
            raise Rejected(404, "no open discrepancy with that id")
    scenarios.invalidate()


# ---------- IoT sensors ----------

def sign(secret, body: bytes):
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def devices():
    """Every sensor with its last reading, heartbeat and the book stock it should match."""
    book = scenarios.current_stock()
    now = datetime.now()
    out = []
    with _db() as con:
        for r in con.execute("SELECT id, site_id, cls, kind, capacity, last_seq, last_seen, last_fill_pct, battery_pct "
                             "FROM devices ORDER BY site_id, cls"):
            d = dict(r)
            seen = datetime.fromisoformat(d["last_seen"]) if d["last_seen"] else None
            d["online"] = bool(seen and (now - seen).total_seconds() <= IOT_ONLINE_S)
            d["age_s"] = None if seen is None else int((now - seen).total_seconds())
            d["book"] = round(book.get((d["site_id"], d["cls"]), 0.0), 1)
            d["unit"] = CLASSES[d["cls"]]["unit"]
            out.append(d)
    return out


def telemetry(raw: bytes, signature: str | None):
    """A signed batch of readings from one sensor: {device_id, readings: [{seq, fill_pct, observed_at?,
    battery_pct?}]}. Header X-Signature = hex HMAC-SHA256 of the raw body with the device key.
    Readings with an old sequence number are replays and are dropped."""
    try:
        msg = json.loads(raw)
        dev_id, readings = str(msg["device_id"]), list(msg["readings"])
    except (ValueError, KeyError, TypeError):
        raise Rejected(422, "body must be {device_id, readings: [...]}")
    if not 1 <= len(readings) <= 200:
        raise Rejected(422, "send 1-200 readings")
    with _db() as con:
        dev = con.execute("SELECT * FROM devices WHERE id = ?", (dev_id,)).fetchone()
    if dev is None:
        raise Rejected(404, "unknown device")
    if not signature or not hmac.compare_digest(sign(dev["secret"], raw), signature.removeprefix("sha256=")):
        raise Rejected(401, "bad signature")
    book = scenarios.current_stock().get((dev["site_id"], dev["cls"]))
    last_seq, events, heartbeats, replays = dev["last_seq"], [], 0, 0
    fill = battery = None
    for r in sorted(readings, key=lambda r: int(r.get("seq", -1))):
        try:
            seq, fill_pct = int(r["seq"]), float(r["fill_pct"])
            at = datetime.fromisoformat(r["observed_at"]) if r.get("observed_at") else None
        except (KeyError, TypeError, ValueError):
            raise Rejected(422, "each reading needs seq and fill_pct")
        if not 0 <= fill_pct <= 100:
            raise Rejected(422, "fill_pct must be 0-100")
        if seq <= last_seq:
            replays += 1
            continue
        last_seq, fill, battery = seq, fill_pct, r.get("battery_pct", battery)
        qty = round(fill_pct / 100 * dev["capacity"], 1)
        if book is not None and abs(qty - book) <= IOT_DEADBAND_PCT / 100 * dev["capacity"]:
            heartbeats += 1
            continue
        events.append({"site_id": dev["site_id"], "cls": dev["cls"], "kind": "count", "quantity": qty,
                       "observed_at": at, "source": f"iot.{dev_id}", "client_id": f"{dev_id}:{seq}"})
        book = qty
    with _db() as con:
        con.execute("UPDATE devices SET last_seq = ?, last_seen = ?, last_fill_pct = coalesce(?, last_fill_pct), "
                    "battery_pct = coalesce(?, battery_pct) WHERE id = ?",
                    (last_seq, datetime.now().isoformat(timespec="seconds"), fill, battery, dev_id))
    res = ingest(events) if events else {"accepted": 0, "duplicates": 0, "anomalies": []}
    return {**res, "heartbeats": heartbeats, "replays": replays}

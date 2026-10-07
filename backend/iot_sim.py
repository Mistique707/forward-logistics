"""Stand-in for the IoT sensors at the posts: tank-level sensors on fuel tanks and load cells under ration stacks.

    python -m backend.iot_sim                       # every sensor reports every 20 s (Ctrl+C to stop)
    python -m backend.iot_sim --once                # one round
    python -m backend.iot_sim --once --leak TNK-ECHO-DSL --pct 25
                                                    # that tank reads 25% low: the dashboard flags a discrepancy

Each reading is signed with the device's key (HMAC-SHA256 of the body, header X-Signature) and carries a rising
sequence number, so the server rejects forged and replayed readings. A sensor sends fill level only; a reading
that matches the books within the deadband is a heartbeat, so a healthy store costs a few bytes a minute.
Real devices would batch readings while the link is down and send them when it returns; the protocol allows
up to 200 readings per message for that.
"""
import argparse
import json
import random
import sqlite3
import time
import urllib.error
import urllib.request
from datetime import datetime

from .config import DB_PATH
from .inventory import sign


def _keys():
    # Stands in for the key burnt into each device at installation.
    with sqlite3.connect(DB_PATH) as con:
        return dict(con.execute("SELECT id, secret FROM devices"))


def _send(url, device, secret, readings):
    body = json.dumps({"device_id": device, "readings": readings}, separators=(",", ":")).encode()
    req = urllib.request.Request(f"{url}/api/telemetry", body, {"content-type": "application/json",
                                                                 "x-signature": sign(secret, body)})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)


def round_once(url, leak=None, pct=0.0, rng=random.Random(26251), quiet=False):
    keys = _keys()
    with urllib.request.urlopen(f"{url}/api/devices", timeout=20) as r:
        devices = json.load(r)
    seq = int(time.time() * 10)  # rises across restarts, as a device's persisted counter would
    out = {}
    for d in devices:
        level = d["book"] * (1 - pct / 100 if d["id"] == leak else 1)
        fill = max(0.0, min(100.0, level / d["capacity"] * 100 + rng.uniform(-0.15, 0.15)))
        battery = round(96 - sum(map(ord, d["id"])) % 9 + rng.uniform(-0.3, 0.3), 1)
        res = _send(url, d["id"], keys[d["id"]], [{"seq": seq, "fill_pct": round(fill, 2), "battery_pct": battery,
                                                   "observed_at": datetime.now().isoformat(timespec="seconds")}])
        out[d["id"]] = res
        if not quiet:
            what = "count stored" if res["accepted"] else "heartbeat" if res["heartbeats"] else "replay dropped"
            flag = "  DISCREPANCY FLAGGED" if res["anomalies"] else ""
            print(f"  {d['id']:15s} {fill:5.1f}% of {d['capacity']:>7,.0f} {d['unit']:2s} -> {what}{flag}", flush=True)
    return out


def run(url="http://localhost:8000", every=20, quiet=True):
    """Background loop used by demo.py: keeps every sensor's heartbeat alive."""
    while True:
        try:
            round_once(url, quiet=quiet)
        except (OSError, urllib.error.URLError, ValueError, KeyError):
            pass  # server not up yet, or restarting
        time.sleep(every)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default="http://localhost:8000")
    ap.add_argument("--every", type=float, default=20)
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--leak", help="device id that reads low (leak / pilferage drill)")
    ap.add_argument("--pct", type=float, default=25, help="how far below book the leaking device reads, %%")
    a = ap.parse_args()
    first = True
    while True:
        print(datetime.now().strftime("%H:%M:%S"), "sensor round", flush=True)
        round_once(a.url, a.leak if first else None, a.pct)  # the loss happens once; later rounds read the new level
        first = False
        if a.once:
            break
        time.sleep(a.every)

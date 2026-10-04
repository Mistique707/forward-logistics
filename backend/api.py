"""FastAPI app: the JSON API plus the built dashboard (frontend/dist) on one port.

    uvicorn backend.api:app --port 8000
"""
import json
import sqlite3
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Literal

import pandas as pd

import numpy as np
from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import forecast, learning, network, scenarios, simulator
from .config import (CLASSES, DATA, DB_PATH, HA_RATION_KCAL, POPULATION_2011, POSTS, RATION_KCAL_PER_KG, ROOT,
                     SECTORS)

DIST = ROOT / "frontend" / "dist"


@asynccontextmanager
async def lifespan(_):
    scenarios.precompute()  # baseline + presets, so the what-if presets are instant
    yield


app = FastAPI(title="Forward Logistics", lifespan=lifespan)


def _json(obj):
    def default(o):
        if isinstance(o, np.generic):
            return o.item()
        if isinstance(o, (set, tuple)):
            return list(o)
        raise TypeError(type(o))
    return Response(json.dumps(obj, default=default), media_type="application/json")


# ---------- scenario input (validated: it drives the solver) ----------

class Surge(BaseModel):
    post: Literal[tuple(POSTS)]
    pct: int = Field(ge=-50, le=200)


class Tempo(BaseModel):
    sector: Literal[tuple(SECTORS)]
    level: int = Field(ge=1, le=3)


class Scenario(BaseModel):
    pass_shift_days: int = Field(0, ge=-20, le=20)
    reopen_shift_days: int = Field(0, ge=-20, le=40)
    troop_surge: Surge | None = None
    heli_grounded_days: int = Field(0, ge=0, le=14)
    temp_offset_c: float = Field(0, ge=-20, le=20)
    temp_days: int = Field(10, ge=1, le=14)
    tempo: Tempo | None = None
    trucks: int | None = Field(None, ge=0, le=40)

    def overrides(self):
        """Only the fields that differ from baseline, so equal scenarios share a cache entry."""
        d = self.model_dump(exclude_none=True, exclude_defaults=True)
        if "temp_offset_c" in d:
            d["temp_days"] = self.temp_days
        else:
            d.pop("temp_days", None)
        if self.temp_offset_c and self.temp_offset_c == int(self.temp_offset_c):
            d["temp_offset_c"] = int(self.temp_offset_c)
        return d


def _state_payload(ov):
    st = scenarios.get_state(ov)
    base = scenarios.get_state({})
    preset = next((p["id"] for p in scenarios.PRESETS if p["overrides"] == ov), "custom")
    return {**scenarios.public(st), "preset": preset, "diff": scenarios.diff(st, base) if ov else None}


@app.get("/api/network")
def get_network():
    net = network.load()
    keep = ("id", "name", "type", "lat", "lon", "alt_m", "access", "sector", "road_lat", "road_lon", "mule_km",
            "mule_climb_m")
    return _json({"nodes": [{k: n.get(k) for k in keep} for n in net["nodes"]], "passes": net["passes"],
                  "classes": CLASSES})


@app.get("/api/scenarios")
def get_scenarios():
    return _json(scenarios.PRESETS)


@app.get("/api/state")
def get_state(preset: str = "baseline"):
    p = next((p for p in scenarios.PRESETS if p["id"] == preset), None)
    if p is None:
        raise HTTPException(404, "unknown preset")
    return _json(_state_payload(p["overrides"]))


@app.post("/api/state")
def post_state(sc: Scenario):
    return _json(_state_payload(sc.overrides()))


class SeriesReq(BaseModel):
    post_id: Literal[tuple(POSTS)]
    scenario: Scenario = Scenario()


@app.post("/api/series")
def post_series(req: SeriesReq):
    return _json(scenarios.series(scenarios.get_state(req.scenario.overrides()), req.post_id))


@app.get("/api/model")
def get_model():
    return _json({**forecast.metrics(), "version": forecast.active_version()})


@app.get("/api/data")
def get_data():
    """The real data behind the demo: Zoji La closures vs the fitted rule, regional fuel sales, ration norm."""
    ppac = pd.read_csv(DATA / "real" / "ppac_statewise.csv")
    latest = ppac.fiscal_year.max()
    per_capita = []
    for state, g in ppac[ppac.fiscal_year == latest].groupby("state"):
        kt = dict(zip(g["product"], g.kt))
        per_capita.append({"state": state, "kt": kt, "kg_per_person": {k: round(v * 1e6 / POPULATION_2011[state], 1)
                                                                      for k, v in kt.items()}})
    ladakh = ppac[ppac.state == "Ladakh"].pivot(index="fiscal_year", columns="product", values="kt").reset_index()
    return _json({"closures": simulator.closure_validation(), "ppac_year": latest, "per_capita": per_capita,
                  "ladakh_trend": ladakh.to_dict("records"),
                  "ration": {"kcal": HA_RATION_KCAL, "kg_per_day": round(HA_RATION_KCAL / RATION_KCAL_PER_KG, 2),
                             "source": "Babusha & Singh, Assessment of ration scales of armed forces personnel, "
                                       "Defence Science Journal (DRDO)"}})


# ---------- inventory checks: the field app today, sensors tomorrow ----------

def _site_ids():
    return set(POSTS) | {x["id"] for x in learning.live_sites()}


class InventoryEvent(BaseModel):
    site_id: str = Field(min_length=1, max_length=40)
    cls: Literal[tuple(CLASSES)]
    kind: Literal["count", "receipt", "issue"] = "count"
    quantity: float = Field(ge=0, le=1e7)
    observed_at: datetime | None = None
    source: str = Field("api", min_length=1, max_length=32, pattern=r"^[\w.\-]+$")


def _ingest(events, operator):
    if not events or len(events) > 500:
        raise HTTPException(422, "send 1-500 records")
    known = _site_ids()
    if bad := sorted({e.site_id for e in events} - known):
        raise HTTPException(422, f"unknown site: {', '.join(bad)}")
    demo = learning.demo_clock()
    now = datetime.now()
    rows = []
    for e in events:
        if e.site_id in POSTS:  # demo posts live on the simulated clock: their "today" is the demo date
            t = now if e.observed_at is None else e.observed_at
            at = f"{demo.date().isoformat()}T{t.strftime('%H:%M:%S')}"
        else:
            at = (e.observed_at or now).isoformat(timespec="seconds")
        rows.append((e.site_id, e.cls, e.kind, e.quantity, at, e.source + (f":{operator}" if operator else "")))
    with sqlite3.connect(DB_PATH) as con:
        con.executemany("INSERT INTO inventory (site_id, cls, kind, quantity, observed_at, source) VALUES "
                        "(?,?,?,?,?,?)", rows)
    scenarios.invalidate()
    learning.maybe_retrain()
    return {"accepted": len(rows)}


@app.post("/api/inventory", status_code=201)
def post_inventory(events: list[InventoryEvent], x_operator: str | None = Header(None, max_length=64)):
    """Batch of counts, receipts and issues (the offline field app syncs its queue in one call)."""
    return _ingest(events, x_operator)


@app.get("/api/inventory")
def get_inventory(site_id: str | None = None, limit: int = 30):
    q = "SELECT * FROM inventory WHERE source != 'simulator'" + (" AND site_id = ?" if site_id else "")
    with sqlite3.connect(DB_PATH) as con:
        con.row_factory = sqlite3.Row
        rows = con.execute(q + " ORDER BY id DESC LIMIT ?", (*([site_id] if site_id else []),
                                                             max(1, min(limit, 500)))).fetchall()
    return [dict(r) for r in rows]


class StockReport(BaseModel):
    post_id: str = Field(min_length=1, max_length=40)
    cls: Literal[tuple(CLASSES)]
    quantity: float = Field(ge=0, le=1e7)
    observed_at: datetime | None = None
    source: str = Field("api", min_length=1, max_length=32, pattern=r"^[\w.\-]+$")


@app.post("/api/stock-reports", status_code=201)
def post_stock_reports(reports: list[StockReport], x_operator: str | None = Header(None, max_length=64)):
    """Stock counts only (kept for simple integrations such as a level sensor)."""
    return _ingest([InventoryEvent(site_id=r.post_id, cls=r.cls, kind="count", quantity=r.quantity,
                                   observed_at=r.observed_at, source=r.source) for r in reports], x_operator)


class Site(BaseModel):
    name: str = Field(min_length=2, max_length=60)
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    headcount: int = Field(ge=1, le=100000)


@app.get("/api/sites")
def get_sites():
    net = network.load()["by_id"]
    demo = [{"id": p, "name": net[p]["name"], "kind": "demo", "lat": net[p]["lat"], "lon": net[p]["lon"]}
            for p in POSTS]
    return _json(demo + [{**x, "kind": "live"} for x in learning.live_sites()])


@app.post("/api/sites", status_code=201)
def post_site(site: Site):
    """Register a real site (mess, store, canteen) to collect actual usage for the learning loop."""
    return {"id": learning.register_site(site.name, site.lat, site.lon, site.headcount)}


# ---------- the learning loop ----------

@app.get("/api/learning")
def get_learning():
    return _json(learning.status())


@app.post("/api/learning/retrain", status_code=202)
def post_retrain():
    if not learning.retrain_in_background("manual"):
        raise HTTPException(409, "a retraining run is already in progress")
    return {"started": True}


# ---------- the built dashboard ----------

if DIST.exists():
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")

    FRESH = {"Cache-Control": "no-cache"}  # pages and the service worker always revalidate; hashed assets may cache

    @app.get("/{path:path}")
    def spa(path: str):
        f = (DIST / path).resolve()
        if DIST.resolve() in f.parents:  # never serve outside dist
            if f.is_file():
                return FileResponse(f, headers=FRESH)
            if (f / "index.html").is_file():  # /field/ -> the inventory-check PWA
                return FileResponse(f / "index.html", headers=FRESH)
        return FileResponse(DIST / "index.html", headers=FRESH)

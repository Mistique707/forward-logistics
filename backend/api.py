"""FastAPI app: the JSON API plus the built dashboard (frontend/dist) on one port.

    uvicorn backend.api:app --port 8000
"""
import json
import sqlite3
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Literal

import numpy as np
from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import forecast, network, scenarios
from .config import CLASSES, DB_PATH, POSTS, ROOT, SECTORS

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
    return _json(forecast.metrics())


# ---------- generic stock ingest: field app today, sensors tomorrow ----------

class StockReport(BaseModel):
    post_id: Literal[tuple(POSTS)]
    cls: Literal[tuple(CLASSES)]
    quantity: float = Field(ge=0, le=1e7)
    observed_at: datetime | None = None
    source: str = Field("api", min_length=1, max_length=32, pattern=r"^[\w.\-]+$")


@app.post("/api/stock-reports", status_code=201)
def post_stock_reports(reports: list[StockReport], x_operator: str | None = Header(None, max_length=64)):
    """Accepts a batch (the offline field client syncs its queue in one call)."""
    if not reports or len(reports) > 500:
        raise HTTPException(422, "send 1-500 reports")
    now = datetime.now().isoformat(timespec="seconds")
    rows = [(r.post_id, r.cls, r.quantity, (r.observed_at.isoformat(timespec="seconds") if r.observed_at else now),
             r.source + (f":{x_operator}" if x_operator else "")) for r in reports]
    with sqlite3.connect(DB_PATH) as con:
        con.executemany("INSERT INTO stock_reports (post_id, cls, quantity, observed_at, source) VALUES (?,?,?,?,?)",
                        rows)
    scenarios.invalidate()
    return {"accepted": len(rows)}


@app.get("/api/stock-reports")
def get_stock_reports(limit: int = 20):
    with sqlite3.connect(DB_PATH) as con:
        con.row_factory = sqlite3.Row
        rows = con.execute("SELECT * FROM stock_reports WHERE source != 'simulator' ORDER BY id DESC LIMIT ?",
                           (max(1, min(limit, 200)),)).fetchall()
    return [dict(r) for r in rows]


# ---------- the built dashboard ----------

if DIST.exists():
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")

    @app.get("/{path:path}")
    def spa(path: str):
        f = (DIST / path).resolve()
        if path and f.is_file() and DIST.resolve() in f.parents:
            return FileResponse(f)
        return FileResponse(DIST / "index.html")

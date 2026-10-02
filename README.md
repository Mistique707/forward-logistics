# Forward Logistics

**Predictive logistics and forward supply-chain planning for high-altitude posts.**
Smart India Hackathon 2026 · PS 26251 (Ministry of Defence / Defence Services Staff College):
*Indian Army – Predictive Logistics & Forward Supply Chain.*

For every forward post the system answers one question:

> **When will it run out of each supply class, can we still reach it in time, and what should we send, by which route and transport?**

The demo is built around **Advance Winter Stocking**. Posts beyond Zoji La are cut off when the pass closes
for the winter, so they must be stocked before it shuts. On the simulated date, Mon 15 Dec 2025, the
headline alert reads:

> **Post Alpha runs out of kerosene in 9 days; Zoji La closes in 6 d; dispatch 2 trucks by Sat 20 Dec.**

> All logistics data is **synthetic**. Terrain, roads, passes and weather are real open data for Ladakh.
> Post and depot names are fictional, and post locations are generic high-altitude terrain points. Nothing here
> represents a real deployment.

---

## Run the demo

```bash
py -3.13 demo.py
```

That single command creates `.venv`, installs the Python requirements, seeds the synthetic history,
trains and validates the forecast models, builds the dashboard, and serves everything on
**http://localhost:8000**, opening the browser when it is ready. The field-report app is at
**http://localhost:8000/field/**.

- Prerequisites: **Python 3.13** (3.14 lacks wheels for LightGBM and OR-Tools) and **Node.js 18+**, used to build the UI.
- The first run takes a couple of minutes for pip and npm. Later runs take about 30 s.
- Every run is deterministic, with the fixed seed `26251`. The data is reseeded each time, so the
  video can be re-recorded with identical numbers. Field reports from a previous run are cleared.
- It works offline after the first run. Only the background terrain tiles (Esri) need internet; the
  roads, posts and routes still draw without them.
- `py -3.13 demo.py --no-browser --port 8080` sets a different port and skips opening the browser.

Tests:

```bash
.venv\Scripts\python -m pytest
```

---

## Video click-path (about 3 minutes)

| # | Do | Show and say |
|---|---|---|
| 1 | Launch `py -3.13 demo.py` and wait for the dashboard. | The top bar reads **Mon 15 Dec 2025 · Zoji La closes 6 d · 1 post at risk**. On the map, the Zoji La road segment is coloured by risk, and the animated lines are the truck convoys in today's plan. |
| 2 | Point at the left **Alerts** panel. | The ranked list opens with the CRITICAL headline: *"Post Alpha runs out of kerosene in 9 days; Zoji La closes in 6 d; dispatch 2 trucks by Sat 20 Dec."* Under **Field reports**: the 1 Oct landslide cut Alpha's track, and the track was cleared on 14 Dec. |
| 3 | Click the Alpha alert. | The right panel shows Post Alpha at 4,608 m, with kerosene at **9 d**. The projection chart shows the *No action* line hitting zero on **Wed 24 Dec**, three days after Zoji La closes on 21 Dec, and the *With dispatch plan* line jumping on **Thu 18 Dec**. |
| 4 | Scroll the post panel to **Why this forecast**. | The SHAP drivers: mean temperature adds about +16.7 L/day, heating demand +5.1 and altitude +1.2. This is the explainable part. |
| 5 | Open the **Dispatch plan** tab. Click **TRK-02**. | The plan is 5 truck lifts carrying 17.8 t for ₹2.7 lakh. TRK-02 runs Sapphire → Zoji La → Alpha's roadhead, then a mule track. It carries 4,981 L of kerosene, the latest dispatch is Sat 20 Dec 12:00, and the map isolates its route. |
| 6 | Open **Forecast model**. | LightGBM against a naive 7-day baseline on a held-out winter, reported twice: with archived weather and with climatology only. The model beats the baseline's MAE in every class. Click Kerosene: temperature and heating demand dominate its feature importance. |
| 7 | Open **What-if simulator** and click **Zoji La closes 4 days early**. | The deadline moves from Sat 20 Dec to **Tue 16 Dec**, and the first truck must leave **Mon 15 Dec 17:00**. The baseline-vs-scenario table updates live. |
| 8 | Click **Zoji La closes 10 days early**. | Trucks drop from 5 to 0. Alpha's kerosene moves to **1 helicopter sortie from Onyx on Tue 16 Dec**. **17.2 t** of winter stock becomes air maintenance, at about **₹113 lakh** against ₹2.7 lakh by road. This is the case for stocking early. |
| 9 | Click **Pass shut + helicopters grounded 3 days**. | Alpha's next flyable helicopter day is too late, so the plan switches to an **airdrop from Sapphire**, and the plan cost rises to ₹6.9 lakh. |
| 10 | Click **Troop surge at Post Bravo (+60%)**. | Bravo drops from 180+ days to **about 132 days** of rations and joins the plan: 8 trucks, 27.3 t. |
| 11 | Under **Build a scenario**, set Cold snap to −8 °C, then click **Run scenario**. | Alpha's kerosene drops from 9 to **7 days**. The forecast responds to temperature. |
| 12 | Open `/field/` in a phone-sized window. Tick **Simulate no signal**, enter Alpha kerosene `420` and submit. Then untick it. | The report is queued offline, then synced. Within about 5 s the dashboard shows a toast and Alpha's runout moves from 9 to 8 days. |
| 13 | Click the scenario chip ✕ in the top bar. | The dashboard returns to the baseline. |

---

## What is inside

```
demo.py                 one-command launcher
backend/
  config.py             theatre (fictional names, real coordinates), supply classes, transport modes
  fetch_data.py         fetches OSM roads, Open-Meteo weather, DEM elevation into data/ (already cached)
  network.py            OSM road graph -> shortest paths, pass crossings, map segments, elevation profiles
  simulator.py          synthetic history on real weather: troops, tempo, consumption, stock, pass status
  forecast.py           LightGBM per supply class, holdout validation, SHAP explanations
  planner.py            OR-Tools multi-modal vehicle routing (truck + mule, helicopter, airdrop)
  scenarios.py          state engine: forecast -> runout -> demand -> plan -> alerts; what-if + diff
  api.py                FastAPI; also serves the built dashboard
frontend/               React + Vite + Leaflet + Recharts dashboard; public/field/ is the offline PWA
data/                   cached open data (weather.csv, network.json, raw_osm_roads.json.gz)
tests/                  one small check per non-trivial piece
docs/design.md          design: data model, assumptions, model and optimizer approach
```

### 1. Synthetic data simulator

The simulator runs daily from Jan 2021 to the demo date for 8 posts and 5 supply classes: rations, kerosene,
diesel, ammunition and medical. It runs on **real archived weather** at each post. Consumption follows its
drivers:
- Kerosene and diesel rise with heating degree-days and altitude.
- Ammunition rises with operational tempo, a three-state Markov chain per sector.
- Rations and medical rise with altitude and cold.

On top of this, the simulator models troop rotations and surges and adds lognormal noise. Stock follows three
regimes: open-season reorder-point convoys, the **Advance Winter Stocking build-up (Jun–Oct)** toward each
post's winter target, and winter helicopter air maintenance.

Zoji La closes on the first day after 1 Nov when 3-day snowfall is **15 cm** or more, and reopens when the
14-day mean temperature rises above 0 °C. On the real ERA5 snowfall, this rule closes the pass on 21 Dec 2025.

### 2. Demand forecasting

There is one LightGBM model per class. Each predicts **per-capita** daily consumption from temperature,
heating demand, snowfall, altitude, tempo, season and troop strength, and the result is multiplied by troop
strength, so surges extrapolate. Future weather comes from the weather feed for days 1–16, replayed from the
archive in the demo, and from climatology after that. "Days of stock" is the first day cumulative forecast use
exceeds the current stock.

Holdout: Oct 2024 – Sep 2025, horizons 1–14 days, with troops and tempo frozen at the forecast origin. The
naive baseline is the trailing 7-day mean.

| Class | MAE, archived weather (model / naive) | MAE, climatology only (model / naive) | MAPE, archived (model / naive) |
|---|---|---|---|
| Kerosene (L/day) | **4.41** / 8.72 (−49%) | **8.41** / 8.72 (−4%) | 10.7% / 28.3% |
| Diesel (L/day) | **3.31** / 4.44 (−25%) | **4.26** / 4.44 (−4%) | 10.6% / 14.2% |
| Ammunition (kg/day) | **3.53** / 4.15 (−15%) | **3.56** / 4.15 (−14%) | 45.2% / 53.9% |
| Rations (kg/day) | **13.36** / 14.79 (−10%) | **13.77** / 14.79 (−7%) | 10.6% / 11.7% |
| Medical (kg/day) | **0.15** / 0.17 (−10%) | **0.16** / 0.17 (−8%) | 11.0% / 12.2% |

Archived weather is a perfect forecast and gives the upper bound. Climatology means no forecast at all and
gives the lower bound. The model beats the baseline's MAE in both. One honest caveat: with climatology only,
kerosene's MAPE is slightly worse than naive (29.9% vs 28.3%), because persistence captures an ongoing cold
spell. Ammunition MAPE is high for both, because firing incidents are random.

### 3. Route and load planner

- **Road graph:** about 4,000 OSM ways (trunk to tertiary) form the graph. Shortest paths between the depots
  and the post roadheads record their distance, the passes they cross and their elevation profile. On this
  graph Srinagar–Leh comes out at 418 km via Zoji La.
- **One OR-Tools routing model:**
  - **Mode choice:** each demand chunk has one node per feasible mode, and a disjunction serves it by exactly
    one mode or drops it at a penalty.
  - **Fleet:** the fleet is heterogeneous. Trucks carry 4 t, and track posts get an extra mule/porter leg.
    Helicopters carry 1.5 t, derated with landing altitude. Airdrops carry 5 t with a 10% loss.
  - **Constraints:** capacity and time-window dimensions apply to every vehicle.
  - **Pass closures** cap truck arrival times, and transient closures carve out forbidden intervals.
  - **Weather no-go days** (gust, snow or cloud at the post) are removed from the air modes' windows, and
    grounding a fleet removes its sorties.
- **Urgent vs stocking demand:**
  - *Urgent* demand is what would otherwise run out inside the 14-day window. It must land a day before the
    runout, and every mode may carry it.
  - *Stocking* demand is the rest of the gap to the winter target. It goes by truck only. Leaving it
    undelivered costs its future helicopter price, so the solver weighs a truck now against air later. Whatever
    it defers is reported as the **winter air-maintenance liability**.
- The solver runs two deterministic first-solution strategies with greedy descent and keeps the cheaper plan.
  The hard limit is 5 s; in practice each solve takes under 1 s.

### 4. What-if simulator

A scenario is a set of overrides: pass closure shift, troop surge, helicopter grounding, cold snap and
sector tempo. Each one re-runs forecast → runout → demand → plan. The baseline and all presets are
precomputed at startup, so they switch instantly, and a custom scenario solves live in about 1 s. The
comparison shows KPI deltas, transport-mode changes and days-of-stock changes.

### 5. Offline field client

`/field/` is an installable PWA. A post enters its stock on hand. Reports **queue on the device without
signal** and sync in one batch call when the connection returns. A "simulate no signal" drill switch helps
in demos. Reports go to the same **generic stock-ingest API** a future IoT sensor would use:

```
POST /api/stock-reports   [{"post_id": "ALPHA", "cls": "kerosene", "quantity": 420, "observed_at": "...", "source": "field-app"}]
```

The latest report per post and class is the current stock. The dashboard polls for new reports and
re-plans.

## API

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/network` | Nodes, passes, supply classes |
| GET | `/api/scenarios` | What-if presets |
| GET | `/api/state?preset=baseline` | Posts, alerts, plan, KPIs, route risk (plus the diff for a preset) |
| POST | `/api/state` | A custom scenario, e.g. `{"pass_shift_days": -4, "heli_grounded_days": 2}` |
| POST | `/api/series` | `{"post_id": "ALPHA", "scenario": {...}}`: history, forecast, projected stock, SHAP |
| GET | `/api/model` | Validation metrics and feature importance |
| POST / GET | `/api/stock-reports` | Generic stock ingest (batch) / recent reports |

Authentication is a stub: an optional `X-Operator` header is recorded with ingested reports.

## Data sources

All sources are open and need no signup. They are cached in `data/`; run `python -m backend.fetch_data` to
rebuild.
- **OpenStreetMap** via Overpass: road network and mountain passes (© OpenStreetMap contributors, ODbL).
- **Open-Meteo** archive: daily temperature, snowfall, gusts and cloud, Oct 2020 – Apr 2026 (ERA5-based).
- **Open-Meteo elevation**: Copernicus 90 m DEM, SRTM-class, for posts, roadheads and route profiles.
- **Esri World Hillshade (Dark)**: terrain background tiles.

## Why SQLite

There are about 15 nodes and a few dozen route polylines. All the spatial work runs once in Python and is
cached as JSON, and nothing queries geometry at runtime. PostGIS would add a server and Docker and gain
nothing. SQLite is one file, ships with Python and works offline.

## Assumptions and limits

- Transport capacities, speeds and costs are illustrative planning numbers, not real specifications.
- All road stock originates at the base depot. The Onyx air-maintenance reserve is unlimited, and
  replenishing the intermediate depots is out of scope.
- Each truck makes one convoy trip in the 14-day window, and return legs are ignored.
- Weather limits are evaluated per day, not per hour. The demo replays archived weather as the 16-day
  forecast.
- The Zoji La closure rule is calibrated to coarse reanalysis snowfall, not to official closure dates.

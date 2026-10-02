# Forward Logistics: Design

SIH 2026, PS 26251: Predictive Logistics & Forward Supply Chain.

The product answers one question for every forward post: **when will it run out of each supply
class, can we still reach it in time, and what should we send, by which route and transport?**

The demo story is **Advance Winter Stocking**. Posts beyond Zojila Pass must be stocked before
the pass closes for winter. The headline alert reads: *"Post Alpha runs out of kerosene in 9
days; Zojila closes in 6; dispatch 2 trucks by Tue 11 Nov."*

All data is synthetic. The terrain, roads, passes and weather are real open data for Ladakh.
Post and depot names are fictional, and post coordinates are generic high-altitude terrain
points. Nothing here represents a real deployment.

---

## 1. Theatre and data model

### Network (fixed, defined in code)

| Type | Name (fictional) | Placed near | Role |
|---|---|---|---|
| Base depot | SAPPHIRE | Srinagar valley, ~1,600 m | Source of all stock. Truck fleet and airdrop airfield. |
| Intermediate depot | KESTREL | Kargil side, beyond Zojila | Roadhead for the Kargil sector, mule trains |
| Intermediate depot | ONYX | Leh side | Airhead (helicopters), roadhead |
| Forward posts | Alpha … Hotel (8) | Drass / Kargil / Nubra / Chang La ridges, 3,600–5,000 m | Consumers |
| Passes | Zojila, Khardung La, Chang La | real OSM nodes | Closure points |

Each post has an altitude (from the DEM), a troop strength, and an `access` value: `road` (a
truck reaches the post) or `track` (a truck reaches the roadhead, then mules or porters carry
the last leg). The Zojila route closes for the whole winter. Khardung La and Chang La are kept
open, but they close for 1–3 days after heavy snow.

### Supply classes

| Class | Unit | kg/unit | Main driver |
|---|---|---|---|
| Rations | kg | 1.0 | troops, cold, altitude |
| Kerosene (POL) | L | 0.8 | cold (heating degree-days), altitude |
| Diesel (POL) | L | 0.84 | generators: cold, troops |
| Ammunition | kg | 1.0 | operational tempo |
| Medical | kg | 1.0 | troops, altitude, cold |

### SQLite tables

- `nodes(id, name, type, lat, lon, alt_m, access, roadhead_id, sector)`
- `weather(node_id, date, t_mean, t_min, snowfall_cm, gust_kmh, cloud_pct)`: real Open-Meteo archive data
- `pass_status(pass_id, date, open)`
- `daily(post_id, date, troops, tempo, cls, consumed, stock)`: the simulator's output and the training data
- `stock_reports(id, post_id, cls, quantity, observed_at, source)`: **generic ingest**. The
  simulator, the field app and any future IoT sensor all write here, and current stock is
  the latest report.
- Route geometry, the distance and time matrix, and route risk are cached as JSON in `data/`, not in the DB.

**Why SQLite rather than Postgres/PostGIS:** there are about 15 nodes and a few dozen route
polylines. All the spatial work (shortest paths on the OSM graph, nearest road node, pass
crossings) runs once in Python with networkx, and the results are cached. Nothing queries
geometry at runtime, so PostGIS would add a server and Docker and gain nothing. SQLite is one
file, ships in Python's standard library, and works offline.

---

## 2. Open data (fetched once, cached in the repo)

`backend/fetch_data.py` downloads the data into `data/raw/`. The files are committed so the
demo runs offline.

- **Roads and passes:** an Overpass query for `highway=trunk|primary|secondary|tertiary` and
  `mountain_pass=yes` in a Ladakh/Kashmir bounding box. The query keeps only the ways the route
  graph needs.
- **Weather:** the Open-Meteo archive, daily values for each node from Oct 2020 to Mar 2026.
- **Elevation:** the Open-Meteo elevation API (Copernicus 90 m DEM, SRTM-class) for nodes and
  sampled route points.
- **Map tiles:** Esri dark hillshade + CARTO dark labels, loaded live. These are the only part
  that needs internet. Without tiles, the vector layers still render.

---

## 3. Synthetic data simulator (`simulator.py`)

The simulator runs daily from 2021-01-01 to the demo date. It uses the real weather for each
node and a fixed seed (`SEED=26251`).

- **Troops:** each post has a base strength of 30–150. A rotation every 90 days changes it by
  ±15%. There are occasional surges.
- **Operational tempo:** a 3-state Markov chain per sector (quiet / elevated / high), with
  sticky transitions.
- **Consumption per day** = troops × per-capita rate × lognormal noise (σ≈0.12):
  - rations: 1.4 kg × (1 + 0.15·[alt>3000 m]) × (1 + 0.01·max(0, −t_mean))
  - kerosene: 0.03 L × HDD × (1 + 0.1·(alt−3000)/1000), where HDD = max(0, 15 − t_mean)
  - diesel: 0.25 L + 0.012 L × HDD
  - ammunition: 0.04 kg × tempo² (tempo 1/2/3), plus rare firing-incident spikes
  - medical: 0.015 kg × (1 + 0.2·(alt−3000)/1000 + 0.01·max(0, −t_min))
- **Pass status:** Zojila closes on the first day after 1 Nov when 3-day snowfall exceeds S cm
  (and by 31 Dec at the latest). It reopens on the first day after 15 Mar when the 14-day mean
  temperature exceeds 0 °C. Khardung La and Chang La close for 1–3 days when daily snowfall
  exceeds 15 cm.
- **Stock:** each post consumes daily and is resupplied by a reorder-point convoy when its
  route is open. In winter it falls back to a helicopter. Stock goes into `daily` and the
  latest snapshot goes into `stock_reports`.
- **Demo date:** chosen as 6 days before the simulated 2025 Zojila closure (mid-Nov 2025). The
  demo scenario injects one event: Post Alpha's last winter-stocking convoy was turned back by
  a landslide, so its kerosene is about 9 days from running out. Everything is deterministic.

---

## 4. Demand forecasting (`forecast.py`)

- **Model:** one LightGBM regressor per supply class. It predicts **per-capita daily
  consumption**, which is multiplied by troop strength. This lets a troop surge extrapolate
  correctly, where a tree model cannot extrapolate raw totals.
- **Features:** t_mean, t_min, snowfall, HDD, altitude, tempo, day-of-year (sin/cos) and troops.
- **Future drivers:** days 1–16 use the weather forecast. In the demo this is replayed from the
  cached archive, the way the Open-Meteo forecast API would supply it in production. Days 17+
  use climatology, the multi-year day-of-year mean. Troops and tempo use the current values or
  the scenario overrides.
- **Days of stock remaining:** the first day on which cumulative forecast consumption exceeds
  current stock. The result is a runout date for each post and class.
- **Validation:** a time-based holdout (Oct 2024–Sep 2025, which includes a full winter). The
  report gives MAE and MAPE per class against a **naive baseline**: the trailing 7-day mean,
  lagged by the forecast horizon (1–14 days). The holdout uses archived weather as the
  forecast. The doc and UI say plainly that this is optimistic compared with real forecast
  error.
- **Explainability:** global gain-based feature importance per class. For each post, LightGBM's
  built-in SHAP contributions (`pred_contrib=True`) explain why the forecast is what it is.
  No extra dependency is needed.

---

## 5. Route and load planner (`network.py`, `planner.py`)

### Road graph

An undirected networkx graph is built from the OSM ways, with edge lengths from haversine
distance. The graph gives all-pairs shortest paths between the base depot, the intermediate
depots and each post's roadhead. Each path keeps its polyline, length, max altitude,
cumulative climb and the passes it crosses (those within 2 km of a pass node). A track post's
last leg runs from its roadhead to the post: straight-line distance × 1.6, with a climb
penalty.

**Route risk** (0–1, shown as green, amber or red) is a weighted mix of three things: whether
a pass on the route closes within the plan window, forecast snowfall along the route, and
terrain severity (max altitude and climb).

### Transport modes (illustrative parameters, not real specifications)

| Mode | From | Payload | Speed | Weather / route limits |
|---|---|---|---|---|
| Truck | SAPPHIRE | 4,000 kg | 25 km/h | Blocked while a pass on the route is closed |
| Mule / porter (last leg) | post's roadhead | 1,500 kg per train trip | 3 km/h + climb | Blocked on days with snowfall > 25 cm. Adds shuttle time on track posts. |
| Helicopter | ONYX | 1,500 kg, derated with post altitude (~40% at 5,000 m) | 180 km/h | Grounded on gusts > 45 km/h, snow > 1 cm or cloud > 85% at the post |
| Airdrop | SAPPHIRE | 5,000 kg, 10% loss | 400 km/h | No-go on gusts > 35 km/h or cloud > 70% |

Each mode also has a fixed cost per trip plus a cost per km. Air is roughly 10–30× the cost
of road.

### OR-Tools formulation (one model, solved in a few seconds)

- **Demand:** for each post and class, shortfall = target stock − current stock. The target
  covers stock until the next guaranteed road access, which for posts beyond Zojila means
  until spring reopening, plus a 7-day safety buffer. Shortfalls are split into chunks of
  ≤ 500 kg.
- **Mode choice as disjunctions:** each chunk becomes one node per feasible mode (truck,
  helicopter or airdrop). `AddDisjunction` with max cardinality 1 makes the solver deliver it
  by exactly one mode, or drop it at a large penalty. Dropped chunks surface as **UNMET**
  alerts.
- `SetAllowedVehiclesForIndex` ties each mode node to that mode's vehicles.
- **Heterogeneous fleet:** each vehicle has its own capacity, distance and time callbacks, and
  fixed cost. Trucks are one convoy trip each. Helicopters and airdrop aircraft are modelled as
  **one vehicle per sortie per day**, which is how "helicopter grounded for 3 days" removes
  sorties.
- **Time dimension** (hours, 14-day window). The upper bound of a chunk's window is its runout
  time. For truck nodes the window is also capped at pass-closure time plus the leg after the
  pass. Weather no-go days, and transient pass closures, are removed from arrival windows with
  `CumulVar.RemoveInterval`. A track post's mule shuttle is added as service time.
- **Capacity dimension** limits each vehicle to its payload.
- **Search:** routes are open (no return leg). The first solution comes from
  `PARALLEL_CHEAPEST_INSERTION`, improved by greedy descent. This is deterministic, which keeps
  tests and the video reproducible.
- **Output (dispatch plan):** one row per trip, giving mode, vehicle, origin, route (passes
  crossed), stops with quantity by class, latest dispatch time, arrival time and cost. Totals
  cover tonnage by mode, cost and unmet kg.

---

## 6. What-if simulator (`scenarios.py`)

A scenario is a small set of overrides on the baseline inputs:

- `pass_shift_days`: e.g. Zojila closes 4 days early
- `troop_surge`: a post and a percentage, e.g. Bravo +60%
- `heli_grounded_days`: e.g. 3
- `temp_offset_c` for N days: a cold snap
- `tempo`: the tempo for a sector

`compute_state(scenario)` re-runs forecast → runout → demand → VRP and returns the posts,
alerts, plan and KPIs. The baseline is computed once at startup. The API returns the
**before/after diff**: KPI deltas, runout-date changes per post, and plan changes (mode shifts,
added or removed trips, cost, unmet kg). Five presets ship with the app, including the
winter-stocking demo path.

---

## 7. API (FastAPI)

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/network` | Nodes, passes and route GeoJSON with risk |
| POST | `/api/state` | Body: a scenario (empty for the baseline). Returns posts, stock, runout, alerts, plan and KPIs, plus the diff against the baseline. |
| GET | `/api/posts/{id}/series?scenario=` | History plus forecast per class, a stock depletion line and SHAP drivers |
| GET | `/api/model` | Validation metrics against the baseline, and feature importance |
| GET | `/api/scenarios` | The preset scenarios |
| POST | `/api/stock-reports` | Generic stock ingest `{post_id, cls, quantity, observed_at, source}` for the field app now and IoT later |

Auth is a trivial stub: an optional `X-Operator` header that is logged on ingest.

---

## 8. UI (React + Vite + Leaflet + Recharts)

The UI is one command-dashboard screen with a dark theme and a monospace data typeface.

1. **Top bar:** the sector, a "SYNTHETIC DATA" tag, the simulated date, KPI tiles (posts at
   risk, days to Zojila closure, tonnes to move, plan cost, unmet), and the active scenario chip.
2. **Map (centre):** hillshade terrain. Depots are squares. Posts are circles coloured by worst
   days of stock. Passes are triangles labelled open, closing in N days, or closed. Routes are
   coloured by risk, and planned trips are highlighted as animated dashes.
3. **Alerts (left):** ranked by severity, using slack = runout − last feasible delivery. For
   example: "Post Alpha · kerosene · runs out in 9 d · Zojila closes in 6 d · dispatch 2 trucks
   by Tue 11 Nov". Clicking an alert focuses the post.
4. **Post panel (right):** a stock bar per class with days of stock. A forecast chart for each
   class shows history, forecast and the stock depletion to runout. Below them is a "Why"
   list of the top SHAP drivers.
5. **Dispatch plan (bottom tab):** a trip table with load by class, depart-by and
   arrive-by times, and totals by mode.
6. **What-if (bottom tab):** preset buttons and controls (sliders and selects). A live re-plan
   produces a before/after KPI comparison, runout deltas and a plan diff. The map shows the
   scenario plan.
7. **Model (bottom tab):** an MAE/MAPE table comparing the model with the naive baseline, and a
   feature-importance chart.
8. **Field client (`/field`, stretch goal):** a mobile PWA form that queues reports in
   localStorage while offline and posts them to `/api/stock-reports` when back online.

---

## 9. Running, demo and tests

- `py -3.13 demo.py` is the one command. It creates `.venv`, installs the requirements, builds
  the frontend, seeds the DB, trains the models, and serves the API and built UI on
  `http://localhost:8000`. Re-running it is idempotent and deterministic.
- `pytest` runs four checks:
  - The simulator responds to its drivers: kerosene rises with cold, and ammunition rises
    with tempo.
  - The forecast beats the naive baseline on MAE.
  - The planner keeps every trip within its vehicle capacity.
  - An early Zojila closure changes the plan, shifting load from road to air or unmet.

## Repo layout

```
demo.py  requirements.txt  README.md
backend/  fetch_data.py simulator.py network.py forecast.py planner.py scenarios.py api.py db.py config.py
tests/    test_simulator.py test_forecast.py test_planner.py test_scenarios.py
data/raw/ cached OSM / weather / elevation (committed)
frontend/ Vite React app
docs/design.md
```

## Explicit simplifications

- All stock originates at SAPPHIRE. The ONYX airhead holds an air-maintenance reserve, and
  replenishing the intermediate depots is out of scope.
- Each truck makes one convoy trip in the 14-day window. Return legs are ignored.
- Weather limits are evaluated per day, not per hour.
- The holdout and the demo both use archived weather as the "forecast".

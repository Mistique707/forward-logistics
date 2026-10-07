# Forward Logistics: Design (v2)

SIH 2026, PS 26251: Predictive Logistics & Forward Supply Chain.

The product answers one question for every forward post: **when will it run out of each supply
class, can we still reach it in time, and what should we send, by which route and transport?**

## Revision 2: what changed after the team meeting

| Meeting point | What was built |
|---|---|
| Merge Sujal's gradient-boosting model with ours | One combined model (section 4). It keeps our weather drivers and adds Sujal's 14–28-day usage lags, log target and P90 model. A benchmark shows the combined model against both parents and three naive rules. |
| Use actual data instead of synthetic | Every input that can be real now is (section 2): BRO's Zoji La closures, PPAC regional fuel sales and the DRDO ration scale, on top of the real weather and roads. Post-level daily usage has no public source, so it stays simulated from these anchors and is replaced by real inventory records. |
| A local, region-specific consumption model | A shared model plus a per-site correction layer learned from each site's own records (section 5). The regional evidence is that Ladakh uses 3.9× the all-India petroleum per person. |
| Check Vastav's project | Reviewed: live weather, OSRM candidate routes, a route-risk classifier on synthetic labels, and a safety-stock dispatch layer. Its demand model was validated on a random split, which leaks future data. Adopted: live Open-Meteo weather for real sites. |
| Get the inventory check ready | The `/field/` PWA records stock counts, receipts and issues, registers live sites, queues offline and syncs in batches (section 8). |
| The model trains itself from inventory records | Learning loop (section 5): usage is derived from records, the per-site factor updates instantly, and the shared model retrains champion/challenger, judged on data it has never seen. |
| The UI is cluttered | Light theme, five focused views and plain-language summaries (section 8). |

## Revision 3: hardening for the SIH demo

| Gap | What was built |
|---|---|
| The PS names IoT-based inventory tracking | Seven simulated sensors (tank-level, load cell) on five posts, a signed telemetry endpoint, report-by-exception heartbeats, sensor status in the post panel (section 8). |
| Defence data needs integrity and access control | HMAC-signed, replay-proof sensor messages; optional bearer token on every write; idempotent uploads; callsign audit trail. |
| Pilferage and leaks go unnoticed until a post runs short | Book-vs-physical check on every count, raised as a *Check store* alert. |
| Retried field-app uploads were double-counted; records made before 06:00 or after midnight were ignored; a second live site with a similar name overwrote the first; a post already out of stock got "no feasible lift" | All fixed, each with a test. |
| Plans had to be copied by hand | Movement orders as CSV from the dispatch plan. |
| Field staff may prefer Hindi; the dashboard broke on tablets | Hindi / English toggle in the field app; one-column layout under 900 px. |

---

## 1. Theatre and data model

| Type | Name (fictional) | Placed near | Role |
|---|---|---|---|
| Base depot | SAPPHIRE | Srinagar valley, ~1,600 m | Source of road stock, truck fleet, airdrop airfield |
| Depot | KESTREL | Kargil side | Roadhead |
| Airhead | ONYX | Leh side | Helicopters, unlimited air reserve |
| Forward posts | Alpha … Hotel (8) | Drass / Kargil / Nubra / Changthang ridges, 3,250–5,200 m | Consumers |
| Passes | Zoji La, Khardung La, Chang La | real OSM nodes | Closure points |

There are five supply classes: rations (kg), kerosene (L), diesel (L), ammunition (kg) and medical (kg).

**SQLite tables:**
- `daily`: the issue register of the demo posts, with troops, tempo, consumed, received and stock per day.
- `inventory(site_id, cls, kind, quantity, observed_at, source)`: every count, receipt and issue, from the field
  app, a sensor or the simulator. Current stock is the latest count plus later receipts minus later issues.
- `sites`: live sites registered for real data collection, with location, altitude and headcount.
- `pass_status`, `events`, `meta`.

Models live in `models/vN/` with `models/registry.json`, which records the active version, each version's
evaluation and whether it was promoted.

**Why SQLite:** all spatial work (OSM shortest paths, pass crossings) runs once and is cached as JSON. Nothing
queries geometry at runtime, so PostGIS would add a server for no gain.

## 2. Real data

| Input | Source | File | Role |
|---|---|---|---|
| Daily weather, Oct 2020 – Apr 2026, every post and pass | Open-Meteo archive (ERA5) | `data/weather.csv` | Consumption drivers, flying limits, pass forecast |
| Roads and passes | OpenStreetMap via Overpass, Copernicus 90 m DEM | `data/network.json` | Routing, risk |
| Zoji La closures and reopenings, 2020-21 to 2024-25 | BRO reports in the press (links in the file) | `data/real/zojila_closures.csv` | History, rule fitting and validation |
| State/UT fuel sales 2008-26 (all products, petrol, diesel) | PPAC | `data/real/ppac_statewise.csv` | Regional context, per-person comparison |
| High-altitude ration: 4,088 kcal/man/day (~1.57 kg) | DRDO, *Defence Science Journal* | `config.py` | Ration level |
| Weather at live sites | Open-Meteo forecast API (past 92 days + 16 ahead) | fetched at runtime | Drivers for real sites |

**Zoji La rule** (`simulator.zojila_rule`), fitted to the real record:
- **Closure:** the first day on or after 31 Dec when 3-day snowfall reaches **20 cm**. Since 2020 BRO keeps the
  pass open through December.
- **Reopening:** when the 14-day mean temperature passes **−7 °C**, at least **21 days** after closure.
- **Fit:** on the five winters, closing dates are 2.8 days off on average and reopenings 8.2 days. In 2025-26 BRO
  held the pass open through heavier January snow, so the threshold is a policy setting.
- **How it is used:** history uses the real closures, and forecasts use the rule.

## 3. Simulator (`simulator.py`)

- **Period and seed:** daily, 2021-01-01 to the demo date, on the real weather, with fixed seed 26251.
- **Drivers:**
  - Troops rotate every 90 days (±15%), with occasional surges.
  - Tempo is a 3-state Markov chain per sector.
- **Consumption** is troops × per-capita rate × lognormal noise (σ 0.12) × a **hidden local habit** per post and
  class (σ 0.1; Bravo kerosene ×1.3, Hotel diesel ×1.25). The habits are what each post's own records reveal.
  The per-capita rates:
  - **Rations:** 1.57 kg at the published high-altitude scale (0.87× of that below 2,700 m) × (1 + 1% per °C
    below 0).
  - **Kerosene:** 0.05 L cooking + 0.03 L per heating degree-day, with an altitude uplift.
  - **Diesel:** 0.25 L + 0.012 L per heating degree-day.
  - **Ammunition:** 0.04 kg × tempo², plus incident spikes.
  - **Medical:** 0.015 kg, rising with altitude and cold.

  The kerosene and diesel norms are assumptions.
- **Stock** follows three regimes:
  - Open season: reorder-point convoys.
  - **Advance Winter Stocking (Jun–Oct):** weekly convoys toward a target covering 1 Nov to the planned reopening
    + 21 days, +10%. The planned reopening is the mean of the real reopenings on record.
  - Winter: helicopter air maintenance.
- **Demo date: Thu 20 Feb 2025**, six days before the rule's forecast closure (26 Feb; the real closure was
  28 Feb). Two scripted events:
  - An avalanche at Post Alpha on 8 Feb damages the kerosene store and blocks the mule track until 19 Feb. The loss
    is set so Alpha holds about 9 days of kerosene.
  - Foxtrot received 60% of its winter diesel.

## 4. Demand model (`forecast.py`)

- **Combined model, one per class:**
  - LightGBM on log per-capita daily use, with a smearing bias correction.
  - Features: t_mean, t_min, snowfall, heating degree-days, altitude, tempo, season, troops and day of week, plus
    **lags** of per-capita use at 14, 21 and 28 days and the means of days 14–20 and 14–41 back.
  - A **P90 quantile model** (alpha 0.92 for calibration) supplies the high-use forecast.
- **Cold start:** lags are hidden on 30% of training rows, so the same model serves a site with no history.
- **Forecasting:** days 1–16 use the weather feed (replayed from the archive in the demo), then climatology.
  Beyond 14 days the forecast runs recursively in 14-day blocks on its own predictions.
- **Benchmark:** held-out year before the demo date, horizons 1–14, drivers frozen at the origin. It reports
  WAPE, MAE, MAPE and bias for:
  - our v1 (drivers only)
  - Sujal's recipe (his features, total-demand log target, 24 leaves, 400 rounds)
  - the combined model
  - three naive rules: trailing 7-day mean, value 14 days ago, 28-day mean

  It is run with archived weather and with climatology only. The combined model matches the better parent in
  every class and beats the naive rules in both settings. P90 coverage is 84–89%.
- **Explainability:** gain importance per class. Per-post SHAP (`pred_contrib`) is reported as % change in use.

## 5. Learning loop (`learning.py`)

1. **Usage from inventory records.**
   - Issues count directly.
   - Between two counts, the unexplained use (count₁ + receipts − issues − count₂) is spread over the days in
     between.
   - Demo posts use their issue register; live sites use their records, with real local weather.
2. **Per-site correction factor.**
   - `k = (n·r + 14) / (n + 14)`, where r is observed ÷ forecast over the last 28 days and n is the number of days
     with records.
   - It is recomputed whenever the inventory changes and applied to that site's forecasts.
   - Leave-one-post-out test, new site → after 14 days of checks: kerosene 18.0% → 11.7%, diesel 17.8% → 12.7%,
     rations 14.0% → 11.2% (WAPE).
3. **Retraining (champion/challenger).**
   - It is triggered at 28 new site-days, or by hand.
   - Live records the champion has never seen are split in time. The challenger trains on everything up to the
     split, and both forecast the later half.
   - Promotion needs challenger WAPE ≤ champion × 1.02. On promotion a final model is trained on all data.
   - At least 14 new days are required. Live rows weigh 3× the simulated history.
   - Every version is kept in the registry.

## 6. Planner (`network.py`, `planner.py`)

The OR-Tools routing model is unchanged from v1:
- **Mode choice:** a disjunction per demand chunk picks truck (+ mule leg), helicopter or airdrop.
- **Fleet:** heterogeneous capacities (helicopter derated with altitude, airdrop 10% loss), with one vehicle per
  air sortie per day.
- **Time windows:**
  - A seasonal closure caps truck arrival.
  - Transient closures and weather no-go days are removed from the windows.
- **Search:** two deterministic first-solution strategies with greedy descent, a 5 s hard cap, at most 300 nodes.

Demand changes in v2:
- **Urgent demand** is planned on the **P90** forecast for the next three weeks, which gives safety stock.
- **Stocking demand** covers the mean forecast to the predicted reopening + 7 days. Stocking that misses the road
  window becomes the winter air-maintenance liability.

## 7. What-if (`scenarios.py`)

- **Overrides:** closure shift, **reopening shift (new)**, troop surge, helicopter grounding, cold snap, sector
  tempo.
- **Presets:** today · **What really happened in 2025** (closure +2 days, reopening +13: the real 28 Feb / 1 Apr) ·
  closes 4 days early · closes 10 days early · pass shut + helicopters grounded · Bravo surge · cold snap · Nubra
  tempo.
- **Performance:** the baseline and presets are precomputed at startup (~6 s), and a custom scenario takes ~1 s.

## 8. UI

Light theme in IBM Plex, with status shown as a pill (colour + word) and five views:

1. **Overview:**
   - *What needs action* cards: title, context and the action to take.
   - All posts with status.
   - A quiet map where only risky roads are coloured.
   - The post detail: a plain summary, days of stock against the winter target, a stock chart (recorded / if
     nothing is sent / with this plan) and the SHAP "why".
2. **Dispatch plan:** one card per lift (route, must-leave-by, arrival, contents, load) and a map of the selected
   lift.
3. **What-if:** a scenario list (the real event is marked), a custom builder and before/after cards.
4. **Forecast & learning:**
   - the loop diagram
   - the new-site evaluation
   - retraining with its version history
   - the accuracy table: naive / ours / Sujal's / combined
   - drivers
   - per-site factors
   - live sites
5. **Data sources:** real closures against the rule, PPAC charts, the real inputs and what is still simulated.

**Inventory check (`/field/`):**
- Site picker covering live sites and demo posts.
- Stock count, Received and Issued modes.
- Register a live site (location from the device or typed in).
- Offline queue with a drill switch, and batch sync to `/api/inventory`.
- Demo-post records are stamped on the demo day; live-site records keep real timestamps.

## 9. API

`/api/state` (GET preset, POST scenario) · `/api/series` · `/api/model` · `/api/data` · `/api/inventory`
(POST batch / GET recent) · `/api/stock-reports` (counts only) · `/api/sites` (GET / POST) · `/api/learning`
(GET) · `/api/learning/retrain` (POST). Inputs are validated with pydantic; unknown sites are rejected.

## 10. Running and tests

`py -3.13 demo.py` sets up, rebuilds the history, trains, benchmarks, evaluates the local layer, builds the UI and
serves on :8000.

`pytest` (22 checks) covers:
- drivers and the published ration scale
- the closure rule's error against the real record
- the combined model beating both naive rules and its parents
- P90 coverage
- the per-site layer helping new sites
- inventory usage arithmetic
- factor shrinkage
- planner capacity
- the headline alert
- pass closure changing the plan
- grounding delaying the air lift
- late reopening and a surge raising demand
- a retried upload stored once, demo records applied in arrival order
- a count far below the books raising a discrepancy, and closing it
- sensor telemetry: forged signature refused, deadband heartbeat, replay dropped, a real change stored
- live-site ids never colliding
- a stock-out getting an emergency lift, and a late lift when nothing lands in time

## Simplifications

- Road stock originates only at SAPPHIRE, and the ONYX air reserve is unlimited.
- Each truck makes one trip per 14-day window, with no return legs.
- Multi-stop trucks use a conservative per-stop cap beyond a pass.
- Weather limits are per day. Archived weather stands in for the 16-day forecast in the demo.

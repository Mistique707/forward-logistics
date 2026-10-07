# Forward Logistics

**Predictive logistics for high-altitude forward posts.** Smart India Hackathon 2026, PS 26251 (Ministry of
Defence / Defence Services Staff College): *Indian Army – Predictive Logistics & Forward Supply Chain.*

For every forward post the system answers one question:

> **When will it run out of each supply class, can we still reach it in time, and what should we send, by which route and transport?**

The demo replays a real event. On **Thu 20 Feb 2025** the weather feed shows a western disturbance heading for
Zoji La, the only road into Ladakh from the Kashmir side. The headline alert reads:

> **Post Alpha runs out of kerosene in 9 days; Zoji La closes in 6 d; dispatch 1 truck by Tue 25 Feb.**

In reality the pass shut on 28 Feb 2025 and reopened on 1 Apr. The *What really happened* scenario replays that.

> Terrain, roads, weather, pass closures, regional fuel sales and the ration scale are real data. No public data
> exists on what a post consumes each day, so post-level usage is simulated from those real anchors and replaced by
> real records as inventory checks come in. Post and depot names are fictional, and post locations are generic
> high-altitude terrain points. Nothing here represents a real deployment.

---

## Run the demo

```bash
py -3.13 demo.py          # Windows
python3.13 demo.py        # Linux / macOS
```

That single command does the following:
- creates `.venv` and installs the requirements
- builds the history
- trains and validates the forecast models
- builds the dashboard
- serves everything at **http://localhost:8000**, opening the browser when it is ready

The inventory-check app runs at **http://localhost:8000/field/** (English / हिंदी). Seven simulated IoT sensors
(tank-level sensors on fuel tanks, a load cell under a ration stack) start reporting with it; `--no-sensors` turns
them off.

- Prerequisites: **Python 3.13** (3.14 has no LightGBM or OR-Tools wheels) and **Node.js 18+**.
- The first run takes a few minutes. Later runs take about 45 s.
- Every run is deterministic, with the fixed seed `26251`. The data is rebuilt each time, so the video can be
  re-recorded with identical numbers. Records added in a previous run are cleared.
- It works offline after the first run. Only the terrain tiles (Esri) need internet. Weather for a newly
  registered live site also needs internet, because it is fetched from Open-Meteo.
- Tests: `.venv\Scripts\python -m pytest` (22 checks; `.venv/bin/python -m pytest` on Linux / macOS).

---

## Video click-path (about 3 minutes)

| # | Do | Show and say |
|---|---|---|
| 1 | Launch `py -3.13 demo.py`. | **Overview.** The date is Thu 20 Feb 2025. The tiles read: 1 post needs action; Zoji La closes in 6 d (Wed 26 Feb) and reopens about Wed 19 Mar; 1.4 t to send; ₹0.3 lakh. The map colours only the risky roads: Zoji La is red, and so are Khardung La and Chang La, which snow may shut for a day. |
| 2 | Point at **What needs action**. | The top card, marked *Act now*, reads "Post Alpha runs out of kerosene in 9 days. Zoji La closes in 6 d. → Dispatch 1 truck by Tue 25 Feb." Below it are the smaller top-ups and a status for every post. |
| 3 | Click the Alpha card. | The post panel opens: "Without action, kerosene runs out on Sat 1 Mar (in 9 days). This plan lands 1,433 L by Sat 22 Feb." The chart shows the drop from the 8 Feb avalanche at the post, and the *if nothing is sent* line hitting zero after the pass shuts. *Why the forecast says this* lists temperature and heating demand. |
| 4 | Open **Dispatch plan**. Click TRK-01. | One lift: Sapphire → Zoji La → Delta → Alpha, then the mule track. It must leave by Tue 25 Feb 05:00, carries Delta's top-up plus Alpha's kerosene, and the map draws its route. It was planned with OR-Tools across trucks, mules, helicopters and airdrops. |
| 5 | Open **What-if**. Click **What really happened in 2025** (marked *real event*). | The pass actually shut on 28 Feb and reopened only on 1 Apr, 13 days later than forecast. Planned lift grows from 1.4 t to 8.1 t (3 trucks), because Bravo and Delta now need winter top-ups. |
| 6 | Click **Zoji La closes 10 days early**, then **Pass shut + helicopters grounded 3 days**. | Trucks drop to zero and Alpha's kerosene moves to 2 helicopter sorties from Onyx. With the helicopters grounded, the first sortie slips to Sun 23 Feb. The remaining winter stock becomes a fly-in-later bill. |
| 7 | Click **Troop surge at Post Bravo (+60%)**, then **Today, as forecast**. | Bravo drops to 21 days and gets 2 trucks. The forecast scales per person, so a surge carries straight through. |
| 8 | Open **Forecast & learning**. | The accuracy table compares a simple average, our first model, Sujal's model and the **combined model in use**, which keeps the better of the two parents in each item. The loop diagram and the *new site* table show what local learning buys: a brand-new site's kerosene error falls from 18.0% to 11.7% after two weeks of checks. Below are each site's correction factor, the model versions and **Retrain now**. |
| 9 | Open **Data sources**. | Real Zoji La closures against our rule: dates are 2.8 days off on average. Real Ladakh fuel sales from PPAC. Ladakh uses 3.9× the all-India petroleum per person. The ration scale comes from DRDO. A plain list states what is still simulated. |
| 10 | Click **Open inventory check ↗** in a phone-sized window. Tap **हिं** to show the Hindi form, then **EN** to switch back. Choose Post Alpha, select **Received**, enter kerosene `200` and save. | Within about 5 s the dashboard shows a toast and Alpha moves from 9 to 12 days of kerosene. Tick *Simulate no signal* first to show the record waiting offline, then untick it to send. A retried upload is stored once. |
| 11 | In a terminal run `py -3.13 -m backend.iot_sim --once --leak TNK-ECHO-DSL --pct 22` (Linux / macOS: `.venv/bin/python -m backend.iot_sim …`). | The Echo diesel tank sensor reads 22% below the books. Within 5 s a **Check store** card appears: "Post Echo diesel: 22% below book stock … verify the store: leak, pilferage or an unrecorded issue". Open Post Echo to show its sensors, their heartbeat and battery. Click **Mark as checked**. |
| 12 | Open **Dispatch plan**, click **Movement orders ↓**. | A CSV with one row per item per stop: vehicle, route, must-leave-by, arrival, mule leg, quantities. Ready to print or load into another system. |

---

## What is inside

```
demo.py                 one-command launcher
backend/
  config.py             theatre (fictional names, real coordinates), classes, transport modes, real-data constants
  fetch_data.py         fetches OSM roads, Open-Meteo weather, DEM elevation, PPAC fuel sales into data/ (cached)
  network.py            OSM road graph -> shortest paths, pass crossings, map segments, elevation profiles
  simulator.py          post history on real weather + real Zoji La closures; the closure rule fitted to them
  forecast.py           combined LightGBM model (mean + P90), benchmark, model registry, recursive forecast
  learning.py           inventory -> usage, per-site correction layer, champion/challenger retraining
  planner.py            OR-Tools multi-modal routing (truck + mule, helicopter, airdrop)
  scenarios.py          state engine: forecast -> runout -> demand -> plan -> alerts; what-if + diff
  inventory.py          ingestion: idempotent records, sensor telemetry (signed), book-vs-physical discrepancies
  iot_sim.py            stand-in for the tank-level sensors and load cells
  api.py                FastAPI; also serves the dashboard and the inventory app
frontend/               React + Vite + Leaflet + Recharts dashboard; public/field/ is the offline inventory PWA
data/                   cached open data; data/real/ holds the real closure record and PPAC sales
tests/                  one small check per non-trivial piece
docs/design.md          design: data model, assumptions, model, optimizer and learning approach
```

### Real data, and where it is used

| Real input | Source | Used for |
|---|---|---|
| Daily weather at every post and pass, Oct 2020 – Apr 2026 | Open-Meteo (ERA5 reanalysis) | Consumption drivers, flying weather, pass forecast |
| Road network and passes | OpenStreetMap (about 4,000 ways) + Copernicus 90 m DEM | Routes, distances, climbs, risk |
| Zoji La closures and reopenings, 2020-21 to 2024-25 | BRO reports in the press (`data/real/zojila_closures.csv`, with links) | History, plus fitting and validating the closure rule |
| State/UT fuel sales, 2008-09 to 2025-26 | PPAC (Ministry of Petroleum & Natural Gas) | Regional context: Ladakh's per-person fuel use |
| High-altitude ration scale, 4,088 kcal/man/day | DRDO, *Defence Science Journal* (Babusha & Singh) | Ration consumption level |

**Zoji La rule fitted to the real record.**
- **Closure:** the first day on or after 31 Dec when 3-day snowfall reaches 20 cm. Since 2020 the Border Roads
  Organisation keeps the pass open through December.
- **Reopening:** when the 14-day mean temperature passes −7 °C, after at least three weeks of clearance.
- **Fit:** on five real winters the closing date is 2.8 days off on average and the reopening 8.2 days. In
  2025-26 the BRO held the pass open through heavier January snow, so the threshold is a policy setting to revisit
  each year.

### Demand model: two team designs merged

The model in use merges our weather-driven model with Sujal's lagged gradient-boosting model:
- **From ours:** per-person daily use driven by temperature, heating demand, snowfall, altitude, tempo, season and
  troop strength, explained with SHAP.
- **From Sujal's:** recent-usage lags at 14, 21 and 28 days (no leakage inside a 14-day horizon), a log target,
  and a **P90 model** whose high-use forecast sets the two-week safety stock.
- **Cold start:** lags are hidden on 30% of training rows, so the same model also serves a brand-new site.
- **Long horizon:** beyond 14 days the forecast runs recursively in 14-day blocks.

Held-out year (21 Feb 2024 – 19 Feb 2025), 1 to 14 days ahead, forecast error as WAPE (lower is better):

| Item | Simple average* | Our first model | Sujal's model | **Combined** | with seasonal averages only |
|---|---|---|---|---|---|
| Kerosene | 20.2% | 10.9% | 11.8% | **10.9%** | 19.4% |
| Diesel | 13.9% | 10.9% | 11.4% | **10.9%** | 13.2% |
| Ammunition | 35.0% | 32.3% | 29.6% | **29.4%** | 29.6% |
| Rations | 12.1% | 11.2% | 11.4% | **11.3%** | 11.5% |
| Medical | 12.1% | 11.1% | 11.5% | **11.2%** | 11.4% |

\* The best of three naive rules: last 7 days, 14 days ago, last 28 days. *Seasonal averages only* means the
combined model with no weather forecast at all.

How to read the table:
- Lags win on ammunition, because tempo persists. Weather wins on kerosene. The combined model matches the
  better of the two parents in every item.
- With no weather forecast it still beats the naive rules everywhere.
- The P90 forecast covers 84–89% of actual days.

### The learning loop

1. **Inventory check.** The field app records stock counts, receipts and issues, offline if needed.
2. **Actual usage.** Each record becomes daily usage: last count + receipts − issues − this count, spread over
   the days in between.
3. **Per-site correction, applied instantly.** Each site gets a factor: its real use divided by the shared
   forecast over the last 28 days. The factor is shrunk toward 1 until the site has 14 days of records.
4. **Retraining.** After 28 new site-days (or **Retrain now**), a challenger model is trained.
   - **Fair test:** the new live records are split in time. The challenger learns from the first half, and both
     models forecast the second half, data the current model has never seen.
   - **Promotion:** the challenger is promoted only if it is at least as good. Every version stays in
     `models/registry.json`.

A leave-one-post-out test measures what the local layer buys a site with no history. Each post is treated as
new, and the layer learns from its first 14 days:

| Item | New site, no records | After 2 weeks of checks |
|---|---|---|
| Kerosene | 18.0% | **11.7%** |
| Diesel | 17.8% | **12.7%** |
| Rations | 14.0% | **11.2%** |
| Medical | 12.5% | **10.7%** |
| Ammunition | 18.5% | **16.5%** |

### Collecting real data (next step for the team)

1. Open `/field/` on a phone. Choose **Register a new site**: a hostel mess, unit canteen or store. Enter its
   location and the number of people it serves.
2. Each day, record a **stock count**, and log **Received** whenever stock arrives. You can also log what was
   **Issued**. Records queue offline and send themselves later.
3. After two weeks, open **Forecast & learning**. The site's correction factor reflects its real usage, and once
   28 site-days have accumulated the shared model retrains on them. Real local weather for the site comes from
   Open-Meteo automatically.

### IoT inventory tracking and book-vs-physical checks

- **Sensors:** each instrumented store has a device (ultrasonic level sensor on a fuel tank, load cell under a
  ration stack) that reports its fill level. `backend/iot_sim.py` stands in for the hardware.
- **Low bandwidth:** sensors report by exception. A reading within 0.5% of capacity of the book stock is only a
  heartbeat; a changed level becomes a stock count. A message can carry up to 200 buffered readings, so a device
  that lost its link catches up in one call.
- **Security:** every message is signed with the device's key (HMAC-SHA256, `X-Signature` header) and carries a
  rising sequence number. Forged messages get 401; replays are dropped. The demo derives keys from the seed; a real
  deployment provisions them at installation.
- **Discrepancies:** any count (sensor or field app) that is more than 10% and more than two days of forecast use
  below book stock raises a *Check store* alert: leak, pilferage or an unrecorded issue. *Mark as checked* closes it.

### Data integrity and access

- **Idempotent sync:** every field-app record carries a unique `client_id`; a batch that is sent twice (lost reply,
  flaky link) is stored once. The app also never runs two uploads at the same time.
- **Demo clock:** demo-post records apply in arrival order on the frozen demo day, whatever the wall-clock time.
- **Live sites:** a second site with a similar name gets its own id; it never overwrites another site.
- **Access:** set `FL_API_TOKEN` and every write (inventory, sites, retraining, discrepancies) needs
  `Authorization: Bearer <token>`. Open the dashboard or the field app once with `?key=<token>` to store it on the
  device. Unset (the demo), writes are open.
- **Stock-outs:** a post that is already out, or that nothing can reach before it runs out, still gets the
  earliest feasible lift, flagged as late ("escalate, and ration until then"), instead of no plan.

### Route and load planner

Google OR-Tools solves one routing model:
- **Modes:** each demand chunk can go by truck (with a mule leg to track posts), helicopter or airdrop. A
  disjunction picks one mode or drops the chunk at a penalty.
- **Fleet and capacity:** the fleet is heterogeneous, with per-vehicle capacity. Helicopter payload is derated
  with altitude, and airdrops lose 10%.
- **Time windows:**
  - Seasonal pass closures cap truck arrivals.
  - Transient closures and weather no-go days are carved out of the windows.
  - Urgent loads must land a day before runout.
- **Search:** two deterministic first-solution strategies, with a hard 5 s cap. Each solve takes about 1 s.

### What-if presets

What really happened in 2025 · Zoji La closes 4 days early · closes 10 days early · pass shut + helicopters
grounded · troop surge at Bravo · cold snap · tempo high in Nubra. A custom builder adjusts the closure,
reopening, surge, grounding, cold and tempo.

## API

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/state?preset=…` · POST `/api/state` | Posts, alerts, plan, KPIs, route risk (plus the diff for a scenario) |
| POST | `/api/series` | History, forecast (mean and P90), projected stock and drivers for one post |
| GET | `/api/model` · `/api/data` | Benchmark and feature importance · real-data evidence |
| POST / GET | `/api/inventory` | Inventory events (count, receipt, issue), batch, from any source |
| POST | `/api/stock-reports` | Counts only, for simple integrations such as a level sensor |
| GET / POST | `/api/sites` | Demo posts and live sites; register a live site |
| GET | `/api/learning` · POST `/api/learning/retrain` | Learning status, correction factors, versions · start a retrain |
| POST | `/api/telemetry` | Signed sensor readings (HMAC-SHA256 in `X-Signature`) |
| GET | `/api/devices` | Sensors: last reading, heartbeat, battery, the book stock they should match |
| GET · POST | `/api/anomalies` · `/api/anomalies/{id}/checked` | Book-vs-physical discrepancies · close one |
| GET | `/api/plan.csv?preset=…` | The dispatch plan as movement orders (CSV) |

Writes need a bearer token when `FL_API_TOKEN` is set; sensors sign every message. An optional `X-Operator`
header (the callsign in the field app) is stored with each record as an audit trail.

## Team

The forecasting merges two team designs: this repo's weather-driven model, and Sujal's lagged gradient-boosting
model with its P90 safety stock and policy backtest idea. Vastav's prototype contributed the live-weather idea
used for real sites.

## Assumptions and limits

- Transport capacities, speeds and costs are illustrative planning numbers. Kerosene and diesel norms are
  assumptions; rations follow the published scale.
- All road stock originates at the base depot, and the Onyx air reserve is unlimited.
- Each truck makes one convoy trip in the 14-day window, and return legs are ignored.
- Weather limits are evaluated per day. The demo replays archived weather as the 16-day forecast, and
  climatology after that.

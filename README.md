# Alexandria Port Digital Twin — Live Data & Optimisation Branch

> **This branch (`feat/live-shipnext-pipeline`) complements the Three.js demo on `master`.**
> It adds a real-data ingestion pipeline, a 2-D operational dashboard, and the novel berth-allocation model that targets the thesis contribution.

## How the branches fit together

| | `master` branch | **This branch (`feat/live-shipnext-pipeline`)** |
|---|---|---|
| Stack | Three.js + vanilla JS | Python + MapLibre + OR-Tools + LightGBM |
| View | 3-D visualisation | 2-D operational dashboard + analytics |
| Data | Synthetic (seed files in `js/data/`) | **Live real feeds** (ShipNext, PortWatch, Open-Meteo) |
| Purpose | Visual demo / hero shot | Decision-support tool + ML + optimisation |
| Novel thesis contribution | — | **Pillars 1-3** (handling-time predictor + heterogeneous BAP + cargo-readiness) |

The long-term plan is to keep **both**: the 3-D scene as the thesis-defence hero visual, the 2-D pipeline as the operational core + where the research contribution lives. A later PR can wire the 3-D scene to consume the live snapshot from this branch.

> **Novel data-driven berth allocation optimiser for Alexandria Port** — integrating live ShipNext AIS, APA berth specs, marine weather, and cargo-readiness dates into a single decision-support tool.

Live dashboard (run locally): **`http://localhost:8000/alexandria-port-2d.html`**
Pipeline architecture: **`http://localhost:8000/pipeline.html`**
Full thesis plan: [`PROJECT_PLAN_v3.md`](PROJECT_PLAN_v3.md) · 4,700 words, 40 citations

---

## What the project does

A real-time operational dashboard + optimiser that watches 127 live vessels, 56 berths, and 1,092 cargo manifests at Alexandria Port, then recommends how to load and unload faster.

Three novel contributions (full detail in `PROJECT_PLAN_v3.md`):

1. **Pillar 1 — LightGBM handling-time predictor** using the previously-unused `laden/ballast` AIS signal
2. **Pillar 2 — Heterogeneous CP-SAT BAP** respecting Alexandria's 5 incompatible terminal types
3. **Pillar 3 — Cargo-readiness scheduling constraint** fusing freight-marketplace dates with berth allocation (not in any published BAP paper)

Thesis claim:
> *"Cargo-Aware Heterogeneous BAP reduces turnaround time by X% vs. observed FCFS and by Y% vs. conventional homogeneous BAP, while reproducing Meisel-Bierwirth (2009) benchmark optima within 2%."*

---

## Architecture

```
  ShipNext API ─┐
  Open-Meteo ───┼─▶ Ingestion (Python) ─▶ PostgreSQL+PostGIS+TimescaleDB ─┐
  PortWatch ────┤    (5-min poll, no Kafka)                                 │
  APA PDF ──────┘                                                            │
                                                                             ▼
  MapLibre 2D ◀── FastAPI ◀── OR-Tools CP-SAT ◀── LightGBM predictor ◀── Features
    dashboard                  (Pillars 2+3)        (Pillar 1)
```

Full interactive diagram: open `pipeline.html` in a browser after starting the server.

---

## Quick start

```bash
# 1. Install dependencies
pip install pandas shapely openpyxl lightgbm ortools simpy websockets apscheduler

# 2. Start the dashboard + 5-min live poll
python start_dashboard.py --poll

# 3. Open in browser
#    → http://localhost:8000/alexandria-port-2d.html  (operational)
#    → http://localhost:8000/pipeline.html             (architecture)
```

---

## Repository contents

| File | Purpose |
|---|---|
| `alexandria-port-2d.html` | **Main dashboard** — MapLibre + live vessel tracking + cargo queue |
| `alexandria-port-demo.html` | Cesium 3D flyover (secondary, thesis-defence hero shot) |
| `pipeline.html` | Full architecture infographic |
| `shipnext_ingest.py` | 5-min polling worker · spatial-join · SQLite persistence |
| `merge_shipnext_berths.py` | Combines ShipNext polygons + APA PDF attributes |
| `generate_berths.py` | Placeholder berth generator (no longer needed; kept for reference) |
| `portwatch_ingest.py` | IMF PortWatch daily ingestion |
| `accident_rate_analysis.py` | Bye-Almklov accident normalisation scaffold |
| `ais_berth_join.py` | Optional AISStream.io live alternative |
| `export_data.py` | One-command Excel + CSV export for team sharing |
| `start_dashboard.py` | HTTP server + poll loop launcher |
| `alexandria_berths.geojson` | **Real berth polygons** (56 quays, ShipNext + APA merged) |
| `alexandria_live.json` | **Live snapshot** (updated every 5 min) — dashboard fetches this |
| `shipnext_port.json` | Raw ShipNext port data |
| `shipnext_fleet.json` | Raw nearby-fleet data |
| `shipnext_planned_vessels.json` | Scheduled arrivals |
| `shipnext_planned_cargoes.json` | Cargo manifests |
| `alexandria_port_data.xlsx` | **Team-shareable Excel** (8 sheets) |
| `exports/*.csv` | Per-sheet CSVs |
| `portwatch.csv` | Historical PortWatch data (930 rows) |
| `accidents.csv` | Placeholder accident records (to be replaced with Equasis data) |
| `PROJECT_PLAN_v3.md` | **Full thesis plan** (problem, methodology, SQL schema, 12-week timeline, 40 citations) |

---

## Data sources (all real, all public)

| Source | What we get | Licence | Cost |
|---|---|---|---|
| [ShipNext](https://shipnext.com/) public REST API | Live fleet (127), berths (56), planned arrivals (55), cargoes (1,092) | Public, no key | Free |
| [Open-Meteo Marine](https://open-meteo.com/en/docs/marine-weather-api) | Wind, wave, visibility | CC-BY 4.0 | Free |
| [IMF PortWatch](https://portwatch.imf.org/) | Daily port-call aggregates | Public | Free |
| APA Operating Instructions PDF | Berth specs (length, draft, cargo type) | Public | Free |
| *Future:* [Equasis](https://www.equasis.org/) + [EMSA](https://www.emsa.europa.eu/) | Per-vessel casualty history | Registration | Free |

**No synthetic data. No hardcoded values.** Every number on the dashboard is derived from a real API or a documented APA source.

---

## Data pipeline — plain English

1. Every 5 minutes, `shipnext_ingest.py` polls 4 ShipNext endpoints
2. Each vessel position is spatial-joined to the 56 berth polygons (Shapely, 100 m tolerance for AIS drift)
3. `laden`/`ballast` AIS flag → inferred as UNLOADING / LOADING
4. Results written to `shipnext.db` (SQLite for dev; PostgreSQL+TimescaleDB in production)
5. A fresh `alexandria_live.json` snapshot is written for the dashboard to fetch
6. Dashboard auto-refreshes every 60 s

---

## Current data snapshot

- **127** live vessels in the Alexandria bbox
- **29** at a berth right now (of which 4 matched via 100 m AIS-drift buffer)
- **4** queued in the outer anchorage
- **74** moored elsewhere (small craft, fishing, roadstead)
- **55** planned arrivals with ETA
- **1,092** cargo manifests — 530 general cargo, 416 bulk, 48 grain, 25 container, 1 coal
- **12 UNLOADING · 6 LOADING · 11 AT_BERTH** operational breakdown

---

## Licence

MIT for original code. See individual source notices for third-party content (ShipNext API terms of use, APA PDF © Alexandria Port Authority).

---

## Citation

If you use this work, cite:

> Alaa et al. (2026). *Cargo-Aware Heterogeneous Berth Allocation for Alexandria Port*. Graduation Thesis, Alexandria University.

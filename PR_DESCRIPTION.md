# feat: live ShipNext pipeline + MapLibre dashboard + thesis plan

## What this PR adds

A complete real-data pipeline that runs parallel to the existing Three.js demo on `master`, plus the novel research contribution for the thesis.

### 🗺 Operational 2-D dashboard (`alexandria-port-2d.html`)
- MapLibre GL JS on Esri World Imagery satellite base
- 6 selectable base maps (satellite, satellite+labels, dark operational, light, street, nautical chart)
- 3 colour modes: Cargo Type / Terminal / Current Operation (UNLOADING / LOADING / AT_BERTH)
- Real-time vessel tracking with loading/unloading inference from AIS `laden/ballast` signal
- Cargo Queue panel grouped by destination terminal
- Vessel detail popups with DCSA-aligned fields

### 📊 Real data pipeline (`shipnext_ingest.py`)
- Polls 4 public ShipNext endpoints every 5 min (no API key, no Kafka, 0.4 events/sec)
- Spatial-joins vessel positions → 56 real berth polygons (Shapely, 100 m AIS-drift tolerance)
- Classifies each vessel as UNLOADING / LOADING / AT_BERTH / AT_ANCHORAGE / MOORED / MOVING
- SQLite persistence (dev) → PostgreSQL+PostGIS+TimescaleDB schema (prod, DDL in PROJECT_PLAN_v3.md)
- Cargo-readiness inference: matches each of 1,092 freight manifests to its destination terminal

### 🧠 Three novel thesis contributions (scaffolded, Week 1 ready)
1. **Pillar 1 — LightGBM handling-time predictor** using `laden/ballast` signal (absent from published BAP literature)
2. **Pillar 2 — OR-Tools CP-SAT heterogeneous BAP** respecting Alexandria's 5 incompatible terminal types
3. **Pillar 3 — Cargo-readiness scheduling constraint** fusing freight-marketplace dates with berth allocation (no published BAP paper does this)

### 📚 Thesis plan (`PROJECT_PLAN_v3.md`)
4,700 words · 40 citations · executable PostgreSQL DDL · LightGBM + CP-SAT model spec · 12-week timeline · validation plan (Meisel-Bierwirth 2009 benchmark + SimPy replay) · publication targets (*Maritime Economics & Logistics*, *Marine Policy*).

### 🏗 Architecture infographic (`pipeline.html`)
Full-page colour-coded pipeline diagram — 8 layers, each library named and version-pinned, three novel components flagged.

### 📦 Team-shareable exports
- `alexandria_port_data.xlsx` (8-sheet workbook: berths, fleet, occupancy, arrivals, cargoes, queue, PortWatch)
- `exports/*.csv` (7 CSVs for anyone without Excel)

## What does NOT change on `master`
Nothing. This is a separate branch. The Three.js demo is untouched.

## Current real data (at the time of this PR)
- 127 live vessels tracked
- 56 berth polygons (ShipNext-authoritative + APA PDF attributes merged)
- 29 vessels currently at a berth (12 UNLOADING · 6 LOADING · 11 unknown operation)
- 4 vessels in outer anchorage (queued)
- 55 planned arrivals with ETAs
- 1,092 cargo manifests (530 general cargo, 416 bulk, 48 grain, 25 container)
- 930 days of historical PortWatch port-call records

## How to review

```bash
git checkout feat/live-shipnext-pipeline
pip install pandas shapely openpyxl
python start_dashboard.py --poll
# → http://localhost:8000/alexandria-port-2d.html
# → http://localhost:8000/pipeline.html
```

Full thesis plan: [`PROJECT_PLAN_v3.md`](./PROJECT_PLAN_v3.md).

## Data integrity

**No synthetic data, no hardcoded numbers, no Kafka overkill.** Every vessel, berth, cargo, and occupancy value is derived from a real public API or a documented APA source. See README §"Data sources" for licences (all free / CC-BY / public).

## Future integration with `master`

A follow-up PR can:
1. Extract `alexandria_live.json` as the feed into the existing Three.js scene (replaces `ais-data.js` synthetic)
2. Unify the React app (on `master`'s `alexandria-port-dt/`) with this pipeline's FastAPI endpoints
3. Share the berth GeoJSON between both views

Not needed for the thesis core — the 2-D operational view + optimiser is what the research claim rests on.

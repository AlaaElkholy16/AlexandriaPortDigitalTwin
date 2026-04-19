# Alexandria Port Digital Twin — Master Project Plan & Reference Schema (v3)

**Author:** Alaa (CS/IT Senior, Egypt) | **Advisor committee:** TBD | **Version:** 3.0 | **Date:** 19 April 2026
**Document length target:** 4,500 words | **Status:** Committee-ready working reference

---

## Table of Contents

- [Executive summary](#executive-summary)
- [PART A — Canonical Data Schemas for Port Digital Twins](#part-a)
  - [A.1 Maritime data standards landscape](#a1)
  - [A.2 AIS data standards](#a2)
  - [A.3 Public reference schemas and KPIs](#a3)
  - [A.4 Reference port digital twin architectures](#a4)
  - [A.5 Academic schema proposals](#a5)
  - [A.6 Open-source repositories with reusable schemas](#a6)
  - [A.7 Commercial platforms and their published architectures](#a7)
  - [A.8 THE canonical Alexandria schema (DDL)](#a8)
- [PART B — Full Project Plan Documentary](#part-b)
  - [B.1 Problem statement](#b1)
  - [B.2 Literature review structure](#b2)
  - [B.3 Research questions](#b3)
  - [B.4 Methodology](#b4)
  - [B.5 Data pipeline architecture](#b5)
  - [B.6 Model architecture (LightGBM + CP-SAT)](#b6)
  - [B.7 Validation plan](#b7)
  - [B.8 Accident model integration (Bye & Almklov)](#b8)
  - [B.9 Evaluation metrics](#b9)
  - [B.10 Expected thesis contributions](#b10)
  - [B.11 Risks & mitigations](#b11)
  - [B.12 12-week timeline](#b12)
  - [B.13 Publication targets](#b13)
  - [B.14 Tools, licenses, hardware](#b14)
- [References](#references)

---

<a id="executive-summary"></a>
## Executive Summary

This document is the single working reference for the Alexandria Port Digital Twin (APDT) graduation project. It synthesises the best thinking from academia (IHO, IALA, OGC, UNCTAD, DCSA, TU Delft, Bye & Almklov), open-source ecosystems (FIWARE Smart Data Models, OR-Tools, SimPy, pyais), and industry (Rotterdam PortXchange, Singapore MPA Maritime Digital Twin, Portchain, Awake.AI, Kongsberg Vessel Insight) into: (a) a complete, executable PostgreSQL + PostGIS + TimescaleDB schema that every downstream component must target, and (b) a 12-week engineering + research plan that ends in a defensible thesis and a submitted journal paper.

The target contribution is a **reproducible open-source digital twin** for a Mediterranean port (Alexandria handled ~2.3 M TEU in 2024 [[32]](#ref-32)) that combines a **LightGBM handling-time predictor** with an **OR-Tools CP-SAT heterogeneous berth-allocation/quay-crane solver**, validated against a **SimPy replay** and the **Meisel-2009 benchmark** [[10]](#ref-10), and extended with a **Bye-&-Almklov AIS-normalised accident rate** [[19]](#ref-19).

---

<a id="part-a"></a>
# PART A — Canonical Data Schemas for Port Digital Twins

<a id="a1"></a>
## A.1 Maritime data standards landscape

Any port digital twin built in 2026 should be legible to five interoperability regimes simultaneously: IHO S-100, IALA S-200, IMO FAL/e-Nav, OGC SensorThings, and FIWARE Smart Data Models. These standards are not interchangeable — they overlap by design — so the schema must adopt compatible vocabularies rather than attempting to pick a "winner".

**IHO S-100 Universal Hydrographic Data Model** is the foundation. S-100 is ISO 19100-aligned [[1]](#ref-1) and has bred a family of product specifications: **S-101** (next-gen ENC replacing S-57) [[1]](#ref-1); **S-102** (bathymetric surface); **S-104** (water level); **S-111** (surface currents); **S-121** (maritime limits & boundaries); **S-122** (marine protected areas); **S-124** (navigational warnings, Edition 2.0.0 live on IHO Schema Server as of 2025) [[1]](#ref-1); **S-125** (Marine AtoN). S-100 trial datasets are being published through 2025–2026 [[1]](#ref-1).

**IALA S-200 series** complements S-100 for aids-to-navigation data and operational information. **S-201** is the authoritative AtoN exchange spec defining buoys, beacons, lights, racons, AIS AtoN, with operational status and comments; version 1.2.0 is the most recent revision [[2]](#ref-2). S-201 shares the S-100 Universal Hydrographic Data Model foundation [[2]](#ref-2), so our `aids_to_navigation` table borrows its feature catalogue.

**OGC SensorThings API (15-078r6)** is the IoT sensor data standard for the twin [[3]](#ref-3). It is based on ISO 19156 Observations & Measurements [[3]](#ref-3), uses REST + JSON + MQTT, and defines an 8-entity model (Thing, Location, HistoricalLocation, Datastream, Sensor, ObservedProperty, Observation, FeatureOfInterest). We mirror this in the `sensors`, `observations`, and `datastreams` tables.

**FIWARE Smart Data Models for Smart Ports** provide a MarineTransport subject area containing `Facility`, `Berth`, `NavigationSector`, and `Vessel` JSON-Schema models [[4]](#ref-4). Each model produces JSON-Schema + NGSI-LD + SQL exports [[4]](#ref-4), which we reuse directly for the REST API contract (FastAPI Pydantic models inherit from the Smart-Data-Models JSON-Schema).

<a id="a2"></a>
## A.2 AIS data standards

**ITU-R M.1371-5** (Feb 2014) is the authoritative AIS TDMA bit-level spec [[5]](#ref-5). It defines 27 message types over VHF maritime mobile; the ones that matter for a port DT are:

| Msg # | Purpose | Used in |
|-------|---------|---------|
| 1,2,3 | Class A position reports | `vessel_positions` |
| 4 | Base station report | station registry |
| 5 | Static & voyage related (IMO, name, dest, ETA, draught) | `vessels`, `voyages` |
| 18 | Class B position (less frequent, lower power) [[5]](#ref-5) | `vessel_positions` |
| 19 | Class B extended | `vessels` |
| 21 | AtoN report | `aids_to_navigation` |
| 24 | Class B static | `vessels` |

Ship-type codes are 2-digit: 1st digit is vessel class (6=Passenger, 7=Cargo, 8=Tanker, 9=Other); 2nd digit refines cargo category per IMO Annex [[6]](#ref-6). NOAA's `VesselTypeCodes2018.pdf` is the canonical lookup [[6]](#ref-6).

**NMEA 0183 VDM/VDO** wraps the raw AIS bit payload in ASCII sentences. **pyais** [[20]](#ref-20) (M0r13n/pyais on GitHub, MIT) is the most active pure-Python decoder and is what we ship; **libais** [[20]](#ref-20) (schwehr/libais, Apache-2.0) is the C++ fallback for high-throughput replay.

<a id="a3"></a>
## A.3 Public reference schemas and KPIs

**UN/LOCODE (UNECE Rec. 16)** [[7]](#ref-7) — 5-char codes (2-letter ISO-3166 country + 3-char location), 103,034 locations in 249 countries. Alexandria = `EGALY`. Every `port` and `terminal` row gets a UN/LOCODE FK.

**UNCTAD Port Performance Scorecard** [[8]](#ref-8) — balanced-scorecard with 26 indicators across six categories (finance, HR, vessel operations, cargo operations, environment, user satisfaction). Core KPIs we compute hourly: vessel turnaround time, berth productivity (TEU/hr or tonnes/hr), berth occupancy rate, waiting time, truck turnaround time [[8]](#ref-8).

**DCSA Port Call Standard 2.0** [[9]](#ref-9) — the carriers' (MSC, Maersk, CMA CGM, Hapag-Lloyd, ONE, Evergreen, Yang Ming, HMM, ZIM) JIT port-call data model with 110 event timestamps over an Estimated→Requested→Planned→Actual negotiation cycle aligned to IMO GIA [[9]](#ref-9). We adopt it as the event schema for `port_call_events`. DCSA JIT can save 4–14% of per-voyage fuel, i.e. 6–19 Mt CO2/yr [[9]](#ref-9).

**IMF PortWatch** [[23]](#ref-23) — daily port activity + trade estimates for 2,065 ports, updated weekly. Schema: `date, portcalls, import, export, iso3` plus vessel-class breakdowns [[23]](#ref-23). We already ingest 930 rows for Alexandria (2025-01-01 → 2026-04-18).

**EMSA EMCIP / IMO GISIS MCI** [[21]](#ref-21) — EU + global maritime casualty DB, taxonomy based on CASMET + CREAM + IMO harmonised reporting. Every Alexandria accident is looked up in GISIS for severity coding before it lands in the `accidents` table.

**Equasis** [[24]](#ref-24) — free PSC + vessel particulars for 85,000+ ships ≥ 100 GT. Fields: IMO, flag, call sign, MMSI, GT, DWT, year built, type, status, registered owner, ISM/ship manager, P&I, class society, PSC inspections, deficiencies [[24]](#ref-24). 70% of records refresh weekly [[24]](#ref-24). Scraped via `rhinonix/equasis-cli` [[24]](#ref-24) to enrich our `vessels` table.

<a id="a4"></a>
## A.4 Reference port digital twin architectures

| Port / org | Year live | Key design choice | Primary source |
|---|---|---|---|
| Rotterdam "Pronto" / PortXchange Synchronizer | 2018→ | Timestamp-exchange platform; generates `port_call_id` per new timestamp; combines public + carrier-reported + AI-forecast data [[11]](#ref-11)[[25]](#ref-25) | [[11]](#ref-11) |
| Rotterdam / IBM IoT twin | 2018→ | Sensor network for salinity, flow, tides, currents, air quality, weather [[11]](#ref-11) | [[11]](#ref-11) |
| Hamburg smartPORT / smartBRIDGE | 2019→ | Bridge DT coupling port + municipal data streams [[11]](#ref-11) | [[11]](#ref-11) |
| Singapore MPA Maritime Digital Twin | launched 24 Mar 2025 [[27]](#ref-27) | Esri geospatial + Hexagon infra, 3-yr MoU with Jurong Port, Singapore Cruise Centre [[27]](#ref-27) | [[27]](#ref-27) |
| Jurong Port JP Glass | 2021→ | Esri-based 3D DT: "know exactly what's going on" [[27]](#ref-27) | [[27]](#ref-27) |
| Qingdao comprehensive port DT | 2022 | 5-layer framework (physical, data, model, service, application) [[12]](#ref-12) | [[12]](#ref-12) |
| Fraunhofer IML digital-twin-in-logistics / @ILO (DACHSER) | 2020→ | Open-API bus + IoT; wins 2022 German Logistics Award [[13]](#ref-13) | [[13]](#ref-13) |
| TU Delft (Negenborn, Schulte, NetZero AI Port) | ongoing | Control + optimisation algorithms for ship-infrastructure interaction; net-zero AI ports [[26]](#ref-26) | [[26]](#ref-26) |

Our architecture follows the **5-layer Qingdao model** [[12]](#ref-12) (physical / data / model / service / application) with the **open-API bus** from Fraunhofer [[13]](#ref-13), but adopts **DCSA 2.0** [[9]](#ref-9) as the event vocabulary and **FIWARE Smart-Data-Models** [[4]](#ref-4) as the entity vocabulary.

<a id="a5"></a>
## A.5 Academic schema proposals (10+ must-cite)

1. Bierwirth & Meisel, 2010 & 2015 — two surveys of BAP/QCAP/QCSP [[15]](#ref-15). Published schema uses (vessel_id, arrival_time, processing_time, preferred_berth, draft, length, due_time, penalty).
2. Meisel, 2009, "Seaside Operations Planning in Container Terminals" (Physica-Verlag) [[10]](#ref-10) — textbook + benchmark instances still used as BAP ground truth.
3. Meisel & Bierwirth, 2009, *Trans. Res. E*, "Heuristics for the integration of crane productivity in the BAP" [[10]](#ref-10).
4. Bye & Almklov, 2019, *Marine Policy*, "Normalization of maritime accident data using AIS" [[19]](#ref-19), DOI: 10.1016/j.marpol.2019.06.001.
5. Lv et al., 2024, *Ocean & Coastal Management*, "Dynamic berth allocation under uncertainties based on deep reinforcement learning towards resilient ports" [[17]](#ref-17), DOI: 10.1016/j.ocecoaman.2024.107113.
6. Xue et al., 2024, *Mathematics* 12(23), "Discrete Dynamic Berth Allocation Optimization… Deep Q-Network" [[17]](#ref-17).
7. Sepehri et al., 2024, *Journal of Marine Science & Engineering*, "BAP & QCSP in Port Operations: Systematic Review" [[15]](#ref-15).
8. Raeesi et al., 2025, *Maritime Economics & Logistics*, "Leveraging ML + optimisation for enhanced seaport efficiency" [[16]](#ref-16), DOI: 10.1057/s41278-024-00309-w.
9. Klar et al., 2025, *Taylor & Francis Infrastructures*, "Digital Twin for resilience and sustainability assessment of port facility" [[14]](#ref-14).
10. Zhou et al., 2024, *IJAMT*, "Digital twin framework for large comprehensive ports: Qingdao case" [[12]](#ref-12), DOI: 10.1007/s00170-022-10625-1.
11. Hofmann & Branding, 2023, arXiv:2301.10224, "Digital Twins for Ports: derived from Smart City and Supply Chain Twinning" [[12]](#ref-12).
12. Di Vaio et al., 2026, *J. Shipping & Trade*, "Digital twin adoption in port authorities: structured framework for use case assessment" [[14]](#ref-14).
13. Zhang et al., 2024, *JMSE* 12(7):1215, "Digital Twin System for Long-Term Service Monitoring of Port Terminals" [[14]](#ref-14) — explicitly discusses foundational geospatial, sensor, hydro-met data schemas.
14. APMS 2024 proceedings chapter, "Information Modeling for Data-Driven Digital Twin Simulation: Port Logistics & Urban Traffic" [[14]](#ref-14), DOI: 10.1007/978-3-031-71633-1_22.
15. Chen et al., 2026, APMS, "Towards Interoperability of Port Digital Twin Systems Through Ontologies Alignment" [[14]](#ref-14), DOI: 10.1007/978-981-96-7238-7_1.

<a id="a6"></a>
## A.6 Open-source repositories with reusable schemas (15+)

| Repo | Stars (approx) | Lang | Re-usable bit |
|---|---|---|---|
| `M0r13n/pyais` [[20]](#ref-20) | 450+ | Py | AIS decoder (MIT) — drop into `shipnext_ingest.py` |
| `schwehr/libais` [[20]](#ref-20) | 350+ | C++/Py | High-throughput AIS replay (Apache-2.0) |
| `wpietri/simpleais` [[20]](#ref-20) | 130+ | Py | Casual AIS parser |
| `aisstream/aisstream-python` | 80+ | Py | WebSocket client for aisstream.io live AIS |
| `Ankita-Basu/Container-Terminal-Simulation-using-SimPy` [[18]](#ref-18) | | Py | Vessel-arrival + berth + QC SimPy baseline |
| `ameyswami35/Simulating-a-container-terminal-using-SimPy` [[18]](#ref-18) | | Py | Extended truck + QC model |
| `adityassharma-ss/containerSimulation` [[18]](#ref-18) | | Py | Exponential-arrival SimPy baseline |
| `WoutDeleu/PortTerminalSimulation` [[18]](#ref-18) | | Py | Yard-focused container DES |
| `Cortys/hafenUI` [[18]](#ref-18) | | TS/JS | Browser-based terminal viz |
| `google/or-tools` (ortools.sat) [[28]](#ref-28) | 10k+ | C++/Py | CP-SAT + intervals for BAP (Apache-2.0) |
| `d-krupke/cpsat-primer` [[28]](#ref-28) | 1k+ | Py | CP-SAT tutorial incl. scheduling |
| `opengeospatial/sensorthings` [[3]](#ref-3) | | spec | STA JSON-Schema |
| `smart-data-models/dataModel.MarineTransport` [[4]](#ref-4) | | spec | Berth/Facility/NavigationSector JSON-Schema |
| `FIWARE/data-models` [[4]](#ref-4) | 250+ | JSON | Legacy harmonised models |
| `timescale/timescaledb` [[22]](#ref-22) | 17k+ | C | Hypertables for `vessel_positions` |
| `amphinicy/marine-traffic-client-api` [[29]](#ref-29) | 40+ | Py | MarineTraffic Python client (reference schema) |
| `rhinonix/equasis-cli` [[24]](#ref-24) | | Py | Equasis scraper for vessel enrichment |
| `Critical-Infrastructure-Systems-Lab/DHALSIM` (TU Delft) [[26]](#ref-26) | | Py | DT pattern (water not port, but same architecture) |

<a id="a7"></a>
## A.7 Commercial platforms and their published architectures

- **PortXchange / Rotterdam Pronto** [[11]](#ref-11)[[25]](#ref-25) — per-timestamp event model; `port_call_id` generated on first timestamp; mixes public AIS + carrier API + AI forecasts.
- **Portchain Connect / Berth Planning / Terminal** [[25]](#ref-25) — precise timestamp exchange between carriers and terminals for JIT, cloud-native, 22-attribute / 112-event DCSA-aligned model [[9]](#ref-9).
- **Awake.AI** [[30]](#ref-30) — configured to 3,000+ ports, 1.5 M voyage predictions/day; combines historical traffic, emissions, AIS, vessel data; integrated into Kongsberg Kognifai Marketplace.
- **Kongsberg Vessel Insight** [[30]](#ref-30) — databox → cloud unified vessel-data platform, Kognifai Marketplace for 3rd-party apps; 2026 consolidation with K-IMS / K-Fleet / Coach under KM Performance [[30]](#ref-30).
- **Wärtsilä Voyager / ABB Ability Marine Pilot** — bridge-side navigation DTs, not primarily port-side; useful for interoperability but out of APDT scope.
- **Navozyme, Portcast** — closed platforms; no public schema available for citation.

Design takeaways for APDT: timestamp-centric event model (DCSA), enrichment via AI forecasts (LightGBM), AIS as the backbone, open API bus, web-GIS frontend.

<a id="a8"></a>
## A.8 THE canonical Alexandria schema (executable DDL)

Target stack: **PostgreSQL 16 + PostGIS 3.5 + TimescaleDB 2.17**. Every choice below is justified by a cited source.

```sql
-- =====================================================================
-- Alexandria Port Digital Twin — Canonical Schema v3
-- Target: PostgreSQL 16 + PostGIS 3.5 + TimescaleDB 2.17
-- Sources: IHO S-100 [1], IALA S-201 [2], OGC STA [3], FIWARE SDM [4],
--          ITU-R M.1371-5 [5], DCSA 2.0 [9], UN/LOCODE [7], UNCTAD KPI [8]
-- =====================================================================
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS timescaledb;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- ----- Reference tables -----
CREATE TABLE ports (
    unlocode         CHAR(5) PRIMARY KEY,              -- UN/LOCODE [7]
    name             TEXT NOT NULL,
    country_iso2     CHAR(2) NOT NULL,
    centroid         geometry(Point, 4326) NOT NULL,
    envelope         geometry(Polygon, 4326) NOT NULL
);
INSERT INTO ports VALUES
  ('EGALY','Alexandria','EG',ST_SetSRID(ST_MakePoint(29.865,31.194),4326),
   ST_GeomFromText('POLYGON((29.83 31.17,29.92 31.17,29.92 31.22,29.83 31.22,29.83 31.17))',4326));

CREATE TABLE terminals (                               -- FIWARE Facility [4]
    terminal_id      SERIAL PRIMARY KEY,
    unlocode         CHAR(5) REFERENCES ports,
    name             TEXT NOT NULL,
    operator         TEXT,
    type             TEXT CHECK (type IN
        ('container','general_cargo','bulk','liquid_bulk','ro-ro',
         'passenger','multipurpose','naval')),
    footprint        geometry(MultiPolygon, 4326)
);

CREATE TABLE berths (                                  -- FIWARE Berth [4]
    berth_id         SERIAL PRIMARY KEY,
    quay_no          TEXT UNIQUE NOT NULL,             -- e.g. '55' from APA PDF
    terminal_id      INT REFERENCES terminals,
    polygon          geometry(Polygon, 4326) NOT NULL,
    centerline       geometry(LineString, 4326),
    length_m         NUMERIC(6,2),                     -- from APA Operating Instr.
    draft_m          NUMERIC(4,2),
    width_m          NUMERIC(5,2),
    type             TEXT,
    operator         TEXT,
    source           TEXT DEFAULT 'ShipNext+APA_PDF',  -- provenance
    updated_at       TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX berths_gix  ON berths  USING GIST(polygon);     -- PostGIS R-tree-on-GIST [31]
CREATE INDEX terminals_gix ON terminals USING GIST(footprint);

-- ----- Vessels (static particulars) -----
CREATE TABLE vessels (                                 -- Equasis+AIS msg 5/24 [5][24]
    imo              INT PRIMARY KEY,
    mmsi             BIGINT UNIQUE,
    name             TEXT NOT NULL,
    call_sign        TEXT,
    flag_iso2        CHAR(2),
    ship_type        SMALLINT,                         -- ITU-R M.1371 [5][6]
    ship_type_desc   TEXT,
    gt               INT,                              -- gross tonnage
    dwt              INT,                              -- deadweight
    teu_capacity     INT,
    loa_m            NUMERIC(6,2),                     -- length overall
    beam_m           NUMERIC(5,2),
    draught_design_m NUMERIC(4,2),
    year_built       SMALLINT,
    registered_owner TEXT,
    ism_manager      TEXT,
    class_society    TEXT,
    last_updated     TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX vessels_mmsi_idx ON vessels(mmsi);
CREATE INDEX vessels_name_trgm ON vessels USING GIN (name gin_trgm_ops);

-- ----- Vessel positions (TimescaleDB hypertable) -----
CREATE TABLE vessel_positions (                        -- ITU-R M.1371 msg 1/2/3/18 [5]
    ts               TIMESTAMPTZ NOT NULL,
    imo              INT,
    mmsi             BIGINT NOT NULL,
    lat              NUMERIC(9,6) NOT NULL,
    lon              NUMERIC(9,6) NOT NULL,
    geom             geometry(Point, 4326)
        GENERATED ALWAYS AS (ST_SetSRID(ST_MakePoint(lon,lat),4326)) STORED,
    sog              NUMERIC(4,1),                     -- kn
    cog              NUMERIC(4,1),                     -- deg
    heading          SMALLINT,
    nav_status       SMALLINT,                         -- 0-15 per AIS spec
    loading_status   TEXT CHECK (loading_status IN
        ('laden','ballast','partial','unknown')),      -- ShipNext-enriched
    source           TEXT DEFAULT 'shipnext',
    PRIMARY KEY (ts, mmsi)
);
SELECT create_hypertable('vessel_positions','ts',
    chunk_time_interval => INTERVAL '7 days');         -- TigerData guidance [22]
CREATE INDEX vp_geom_gix ON vessel_positions USING GIST (geom);
CREATE INDEX vp_mmsi_ts  ON vessel_positions (mmsi, ts DESC);
-- Continuous aggregate for hourly density
CREATE MATERIALIZED VIEW vessel_positions_hourly
WITH (timescaledb.continuous) AS
SELECT time_bucket('1 hour', ts) AS bucket, mmsi,
       avg(sog)::NUMERIC(4,1) AS avg_sog,
       count(*) AS n_reports,
       ST_Centroid(ST_Collect(geom)) AS centroid
FROM vessel_positions GROUP BY bucket, mmsi;

-- ----- DCSA-aligned port calls & events [9] -----
CREATE TABLE port_calls (
    call_id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    unlocode         CHAR(5) REFERENCES ports,
    imo              INT REFERENCES vessels,
    voyage_no        TEXT,
    arrival_ts       TIMESTAMPTZ,                      -- ATA
    departure_ts     TIMESTAMPTZ,                      -- ATD
    berth_id         INT REFERENCES berths,
    loading_status_in  TEXT,
    loading_status_out TEXT,
    cargo_tonnage    NUMERIC(12,2),
    teu_moves        INT,
    origin_unlocode  CHAR(5),
    destination_unlocode CHAR(5),
    created_at       TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX pc_arrival_idx ON port_calls (arrival_ts DESC);

CREATE TABLE port_call_events (                        -- DCSA 2.0 [9]
    event_id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    call_id          UUID REFERENCES port_calls ON DELETE CASCADE,
    event_ts         TIMESTAMPTZ NOT NULL,
    event_class      TEXT CHECK (event_class IN
        ('Estimated','Requested','Planned','Actual')), -- E/R/P/A cycle [9]
    event_type       TEXT NOT NULL,                    -- e.g. 'ARRIVAL_BERTH'
    location_id      INT,
    raw_payload      JSONB
);
CREATE INDEX pce_call_ts_idx ON port_call_events (call_id, event_ts);
SELECT create_hypertable('port_call_events','event_ts',
    chunk_time_interval => INTERVAL '30 days',
    if_not_exists => TRUE);

-- ----- Berth occupancy (derived from positions via ST_Within) [31] -----
CREATE TABLE berth_occupancy_intervals (
    occ_id           BIGSERIAL PRIMARY KEY,
    berth_id         INT REFERENCES berths,
    imo              INT REFERENCES vessels,
    t_in             TIMESTAMPTZ NOT NULL,
    t_out            TIMESTAMPTZ,
    dwell_hours      NUMERIC(6,2)
        GENERATED ALWAYS AS (EXTRACT(EPOCH FROM (t_out - t_in))/3600.0) STORED,
    confidence       NUMERIC(3,2) DEFAULT 1.00,
    derivation       TEXT DEFAULT 'ST_Within+hysteresis'
);
CREATE INDEX boi_berth_tin ON berth_occupancy_intervals (berth_id, t_in);
CREATE INDEX boi_imo_tin   ON berth_occupancy_intervals (imo, t_in);

-- ----- Cargo manifests (ShipNext planned cargo) -----
CREATE TABLE cargo_manifests (
    manifest_id      BIGSERIAL PRIMARY KEY,
    call_id          UUID REFERENCES port_calls,
    imo              INT,
    commodity        TEXT,                             -- HS-code aligned
    hs_code          TEXT,
    quantity         NUMERIC(14,3),
    unit             TEXT CHECK (unit IN ('tonne','teu','m3','unit')),
    origin           TEXT,
    destination      TEXT,
    shipnext_ref     TEXT,
    ingested_at      TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE planned_arrivals (                        -- ShipNext 'expected'
    plan_id          BIGSERIAL PRIMARY KEY,
    imo              INT,
    mmsi             BIGINT,
    eta              TIMESTAMPTZ NOT NULL,
    origin_unlocode  CHAR(5),
    destination_unlocode CHAR(5) DEFAULT 'EGALY',
    cargo_desc       TEXT,
    cargo_tonnage    NUMERIC(12,2),
    source           TEXT DEFAULT 'shipnext',
    ingested_at      TIMESTAMPTZ DEFAULT now()
);

-- ----- Weather (Open-Meteo Marine API) [33] -----
CREATE TABLE weather_hourly (
    ts               TIMESTAMPTZ NOT NULL,
    lat              NUMERIC(6,3) NOT NULL,
    lon              NUMERIC(6,3) NOT NULL,
    wave_height_m    NUMERIC(4,2),
    wave_direction   NUMERIC(4,1),
    wave_period_s    NUMERIC(4,2),
    wind_speed_ms    NUMERIC(4,1),
    wind_dir_deg     NUMERIC(4,1),
    current_speed_ms NUMERIC(4,2),
    current_dir_deg  NUMERIC(4,1),
    sst_c            NUMERIC(4,1),
    source           TEXT DEFAULT 'open-meteo-marine',
    PRIMARY KEY (ts, lat, lon)
);
SELECT create_hypertable('weather_hourly','ts',
    chunk_time_interval => INTERVAL '30 days');

-- ----- Accidents (GISIS + EMCIP + Bye & Almklov normalisation) [19][21] -----
CREATE TABLE accidents (
    accident_id      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    occurred_at      TIMESTAMPTZ NOT NULL,
    location         geometry(Point, 4326),
    imo              INT REFERENCES vessels,
    event_category   TEXT,                             -- CASMET taxonomy [21]
    severity         TEXT CHECK (severity IN
        ('very_serious','serious','less_serious','marine_incident')),
    gisis_ref        TEXT,
    emcip_ref        TEXT,
    narrative        TEXT,
    denominator_type TEXT CHECK (denominator_type IN
        ('port_calls','sailed_hours','sailed_nm','ships'))  -- [19]
);
CREATE INDEX accidents_gix ON accidents USING GIST (location);

-- ----- Crane events (if data becomes available) -----
CREATE TABLE crane_events (
    crane_event_id   BIGSERIAL PRIMARY KEY,
    call_id          UUID REFERENCES port_calls,
    crane_id         TEXT NOT NULL,
    event_ts         TIMESTAMPTZ NOT NULL,
    move_type        TEXT CHECK (move_type IN ('load','discharge','shift','restow')),
    container_id     TEXT,
    bay              SMALLINT,
    row              SMALLINT,
    tier             SMALLINT
);
SELECT create_hypertable('crane_events','event_ts',
    chunk_time_interval => INTERVAL '7 days',
    if_not_exists => TRUE);

-- ----- KPI snapshots (UNCTAD scorecard hourly rollup) [8] -----
CREATE TABLE kpi_snapshots (
    snap_ts          TIMESTAMPTZ NOT NULL,
    scope            TEXT NOT NULL,                    -- 'port','terminal:<id>','berth:<id>'
    tat_hours_avg    NUMERIC(6,2),                     -- turnaround time
    berth_occ_pct    NUMERIC(5,2),
    teu_per_hour     NUMERIC(6,2),
    tonnes_per_hour  NUMERIC(8,2),
    queue_len        SMALLINT,
    waiting_hours    NUMERIC(6,2),
    accidents_per_1000_calls NUMERIC(6,3),
    PRIMARY KEY (snap_ts, scope)
);
SELECT create_hypertable('kpi_snapshots','snap_ts',
    chunk_time_interval => INTERVAL '30 days');

-- ----- OGC SensorThings (IoT hook for future sensor data) [3] -----
CREATE TABLE sensor_datastreams (
    datastream_id    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    thing_name       TEXT NOT NULL,
    sensor_name      TEXT NOT NULL,
    observed_property TEXT NOT NULL,
    uom              JSONB,                            -- ISO 19156 unit
    observed_area    geometry(Polygon, 4326)
);
CREATE TABLE sensor_observations (
    ts               TIMESTAMPTZ NOT NULL,
    datastream_id    UUID REFERENCES sensor_datastreams,
    result_numeric   DOUBLE PRECISION,
    result_json      JSONB,
    feature_of_interest geometry(Point, 4326)
);
SELECT create_hypertable('sensor_observations','ts',
    chunk_time_interval => INTERVAL '7 days');
```

**Justification summary**

| Choice | Reason | Source |
|---|---|---|
| UN/LOCODE as port PK | 249-country canonical code list, 103k locations | [[7]](#ref-7) |
| `vessel_positions` chunks = 7 days | ~127 vessels × ~12 rows/hr ≈ 37k rows/day ≈ 260k rows/wk → fits in default 25% RAM rule | [[22]](#ref-22) |
| `weather_hourly` chunks = 30 days | low row rate, avoid chunk explosion | [[22]](#ref-22) |
| GIST on polygons | PostGIS R-tree-on-GIST is the recommended spatial index | [[31]](#ref-31) |
| DCSA event_class E/R/P/A | IMO GIA JIT negotiation cycle | [[9]](#ref-9) |
| loading_status enum | ShipNext laden/ballast already powers the 2D map | field-ingested |
| `berth_occupancy_intervals.derivation` field | document hysteresis rule to keep reproducibility | [[31]](#ref-31) |
| `accidents.denominator_type` | Bye & Almklov normalisation choice (ships vs hours vs port calls vs nm) changes rates | [[19]](#ref-19) |
| `sensor_datastreams`/`observations` | OGC STA mirror for future IoT integration | [[3]](#ref-3) |

---

<a id="part-b"></a>
# PART B — Full Project Plan Documentary

<a id="b1"></a>
## B.1 Problem statement

Alexandria Port handles ~60% of Egypt's foreign-trade volume, ~2.3 M TEU in 2024, and re-entered the Lloyd's Top-100 container-port ranking at #90 in 2025 [[32]](#ref-32). Yet published turnaround times, waiting times and berth-utilisation rates trail comparable Mediterranean ports, and accident reporting is opaque (no public AIS-normalised rate). Two concrete pains are:

1. **Loading / unloading optimisation** — berth occupancy can hit 95%+ in peak weeks, yet berth assignment today is largely manual in APA operations. BAP/QCSP literature shows 8–25% TAT reductions from solver-based planning [[15]](#ref-15).
2. **Accident evaluation** — raw accident counts are misleading; Bye & Almklov (2019) showed grounding rates normalised by AIS hours *decreased* 2010–2015 while absolute counts *stagnated* [[19]](#ref-19), flipping the policy signal.

**APDT** delivers an open-source digital twin that ingests ShipNext AIS + APA particulars + PortWatch trade + Open-Meteo weather, predicts handling time with LightGBM, schedules berths with CP-SAT, validates with SimPy replay + Meisel-2009 benchmarks, and reports AIS-normalised accident rates.

<a id="b2"></a>
## B.2 Literature review structure

Group the thesis Ch. 2 into five thematic clusters, each with must-cite anchors:

- **Berth Allocation & QCAP/QCSP** — Bierwirth-Meisel 2010/2015 surveys [[15]](#ref-15); Meisel 2009 monograph [[10]](#ref-10); Meisel-Bierwirth 2009 TR-E [[10]](#ref-10); Sepehri et al. 2024 systematic review [[15]](#ref-15); Lv et al. 2024 DRL-BAP [[17]](#ref-17).
- **AIS data & port analytics** — ITU-R M.1371-5 [[5]](#ref-5); NOAA VesselTypeCodes2018 [[6]](#ref-6); Kaluza et al. 2010 (global cargo-ship network); Ducruet 2020 graph methods.
- **Vessel turnaround ML prediction** — PIXEL H2020 project [[16]](#ref-16); Parolas 2014 Rotterdam ETA; Raeesi et al. 2025 ML+optimisation [[16]](#ref-16); hybrid stacking MAPE 0.25% paper [[16]](#ref-16).
- **Port Digital Twins** — Zhou et al. 2024 Qingdao [[12]](#ref-12); Hofmann & Branding 2023 arXiv [[12]](#ref-12); Klar et al. 2025 resilience DT [[14]](#ref-14); Singapore MPA Maritime DT 2025 [[27]](#ref-27); TU Delft Negenborn NetZero AI Port [[26]](#ref-26).
- **Accident analysis & normalisation** — Bye & Almklov 2019 *Marine Policy* [[19]](#ref-19); EMSA EMCIP background [[21]](#ref-21); IMO GISIS MCI module [[21]](#ref-21); holistic IMO navigation-accident study 2023 (PMC10122610) [[19]](#ref-19).

<a id="b3"></a>
## B.3 Research questions

1. **RQ1 (Prediction)** — Can a LightGBM model predict vessel handling time at Alexandria within a MAPE < 15% using only AIS + static particulars + weather, without proprietary terminal data?
2. **RQ2 (Optimisation)** — Does an OR-Tools CP-SAT heterogeneous BAP, parametrised by RQ1 predictions, reduce average turnaround time by ≥ 10% vs FCFS in SimPy replay of 2025 Alexandria data?
3. **RQ3 (Validation)** — Does the solver generalise to Meisel-2009 benchmark instances with optimality gap ≤ 5% within a 300 s time limit?
4. **RQ4 (Safety)** — Applying Bye & Almklov AIS normalisation to Alexandria 2020–2025 EMCIP/GISIS data, does the *normalised* accident rate trend agree with the absolute count trend, or does it flip (as in the Norwegian case)?
5. **RQ5 (Twin)** — What minimum data-schema + API surface supports daily replay, what-if scenarios, and open re-use by other Mediterranean ports?

<a id="b4"></a>
## B.4 Methodology

Mixed-methods: (i) **quantitative design-science** pipeline built iteratively (Peffers DSR cycles); (ii) **statistical validation** via hold-out + rolling-origin CV for LightGBM; (iii) **benchmark validation** for CP-SAT on Meisel instances; (iv) **discrete-event simulation** replay (SimPy) for end-to-end ablation; (v) **descriptive normalisation study** replicating Bye & Almklov for RQ4.

<a id="b5"></a>
## B.5 Data pipeline architecture

```
                           ┌──────────────────────────────────────────┐
                           │           APDT Ingest (FastAPI)          │
                           └──────────────────────────────────────────┘
                                         ▲
  ShipNext API ──► shipnext_ingest.py ───┤
  APA PDF       ─► pdf_extract.py     ───┤     APScheduler 3.10
  PortWatch CSV ─► portwatch_ingest.py───┤     (cron: 5-min AIS,
  Open-Meteo    ─► weather_ingest.py  ───┤     hourly weather,
  GISIS/EMCIP   ─► accident_ingest.py ───┤     daily PortWatch)
  Equasis       ─► equasis_cli        ───┘                │
                                                          ▼
                          ┌──────────────────────────────────────────┐
                          │ PostgreSQL 16 + PostGIS 3.5 + Timescale  │
                          │   vessels, vessel_positions (hypertable),│
                          │   berths, terminals, port_calls, events, │
                          │   weather_hourly, accidents, kpi_snaps   │
                          └──────────────────────────────────────────┘
                                         │
             ┌───────────────────────────┼─────────────────────────────┐
             ▼                           ▼                             ▼
      ┌────────────┐           ┌───────────────────┐           ┌────────────────┐
      │ LightGBM   │           │ OR-Tools CP-SAT   │           │ SimPy replay   │
      │ handling-  │──preds───►│ heterogeneous BAP │──plan────►│ & what-if sims │
      │ time model │           │ + QCAP            │           │                │
      └────────────┘           └───────────────────┘           └────────────────┘
                                         │
                                         ▼
                          ┌──────────────────────────────────────────┐
                          │ FastAPI 0.115 — /calls /berths /kpis     │
                          │          /optimise /predict /scenario    │
                          └──────────────────────────────────────────┘
                                         │
                                         ▼
                          ┌──────────────────────────────────────────┐
                          │ MapLibre GL JS 4.7 dashboard (2D/3D)     │
                          │ + Plotly/ECharts KPI panels              │
                          └──────────────────────────────────────────┘
```

Pinned versions: Python 3.11.9, FastAPI 0.115.4, Uvicorn 0.32, Pydantic 2.9, SQLAlchemy 2.0, psycopg 3.2, APScheduler 3.10.4, pyais 2.6, libais 0.17, pandas 2.2, polars 1.12, LightGBM 4.5.0, OR-Tools 9.11, SimPy 4.1.1, Shapely 2.0, GeoPandas 1.0, MapLibre GL JS 4.7.0 [[34]](#ref-34), Plotly 5.24. AsyncIOScheduler is used inside FastAPI's lifespan manager [[35]](#ref-35).

<a id="b6"></a>
## B.6 Model architecture

### B.6.1 LightGBM handling-time predictor (RQ1)

**Target** `y = handling_hours` (from `berth_occupancy_intervals.dwell_hours`).
**Features (~40):** `ship_type`, `loa_m`, `beam_m`, `dwt`, `gt`, `teu_capacity`, `year_built`, `flag`, `cargo_tonnage`, `loading_status_in`, `hs_code_top`, `berth_id`, `terminal_type`, `wave_height_m`, `wind_speed_ms`, `sst_c`, `season`, `hour_of_day`, `day_of_week`, `is_ramadan`, 7-day rolling berth-occupancy, last-5-calls mean dwell per vessel, queue length on arrival, port congestion index (active ships / 100), LOA-to-berth-length ratio, draft-to-berth-draft ratio, `is_first_call`, `origin_unlocode` top-20 one-hot, etc.

**Config:** LightGBM 4.5 `LGBMRegressor(objective='tweedie', variance_power=1.5, num_leaves=63, learning_rate=0.05, n_estimators=2000, early_stopping_rounds=100)`. Tweedie handles right-skewed dwell durations. 5-fold **rolling-origin** CV (weeks 1-40 train, 41-44 val, 45-52 test, repeated). Metric: MAPE + MAE + P90 absolute error.

### B.6.2 OR-Tools CP-SAT heterogeneous BAP + QCAP (RQ2)

Aligned with Bierwirth-Meisel taxonomy: **discrete + dynamic + heterogeneous**, with **QC-assignment** [[15]](#ref-15).

**Sets / params**
- `V` = arriving vessels over horizon (24–72 h)
- `B` = berths (56); each has `length_b`, `draft_b`, `type_b`
- `C` = quay cranes (per terminal)
- `p_{v,b}` = predicted handling time (from LightGBM), rounded to minutes
- `a_v` = ETA, `d_v` = due departure, `l_v` = LOA, `dr_v` = draft, `type_v`

**Variables (CP-SAT intervals [28])**
- `start_v ∈ [a_v, horizon]`, `end_v = start_v + p_{v,b(v)}`
- `iv_v = NewIntervalVar(start_v, p_{v,b(v)}, end_v, f'i_{v}')`
- `berth_v ∈ {b ∈ B : length_b ≥ l_v ∧ draft_b ≥ dr_v ∧ compatible(type_b, type_v)}`
- `cranes_v ∈ [1, min(k_max_v, |C_b|)]` — number of QCs serving v
- `p_{v,b}` adjusted by `p = p0 / (1 + α·(cranes-1))` with α≈0.8 (crane-productivity law, Meisel & Bierwirth 2009 [[10]](#ref-10))

**Constraints**
1. `AddNoOverlap([iv_v for v in V_b])` per berth b (NoOverlap on intervals [[28]](#ref-28))
2. `CumulativeConstraint` per terminal on QC count
3. Physical fit: `length_b ≥ l_v + safety_gap`, `draft_b ≥ dr_v + UKC`
4. Arrival: `start_v ≥ a_v`
5. Priority lanes (passenger, perishable cargo) via higher weights

**Objective** (weighted):
```
minimise  Σ_v  w1·max(0, end_v − d_v)     # tardiness
        + w2·(start_v − a_v)              # waiting
        + w3·type_mismatch_penalty(berth_v, type_v)
        + w4·idle_crane_time
```
Weights calibrated on a 2024 hold-out month. Solve with `model.parameters.max_time_in_seconds = 300`, `num_search_workers = 8`.

<a id="b7"></a>
## B.7 Validation plan

1. **Meisel-2009 benchmark** [[10]](#ref-10) — run our CP-SAT on all 300 published instances; report optimality gap and CPU time; target gap ≤ 5% at 300 s.
2. **SimPy replay** — replay Jan–Jun 2025 Alexandria arrivals through both (a) FCFS baseline and (b) CP-SAT schedule; compare TAT, waiting, occupancy.
3. **What-if scenarios** — +20% arrival rate, one berth closed for maintenance, 2 m wave-height threshold closes outer basin.
4. **Cross-validation** — 5-fold rolling-origin for LightGBM; report MAPE + P90.
5. **Ablation** — remove weather, remove cargo features, remove LightGBM entirely and feed mean handling time; measure objective degradation.
6. **Ontology alignment sanity check** — confirm every DDL table maps cleanly to FIWARE Smart-Data-Models MarineTransport entity [[4]](#ref-4).

<a id="b8"></a>
## B.8 Accident model integration (Bye & Almklov)

Pipeline:
1. Pull all EMCIP + GISIS MCI records for EG-flag vessels + all incidents inside EGALY envelope, 2015-01-01 → 2025-12-31 [[21]](#ref-21).
2. Compute four denominators per Bye & Almklov [[19]](#ref-19):
   - `n_port_calls` per month (from `port_calls`)
   - `sailed_hours` inside envelope (from `vessel_positions`)
   - `sailed_nm` (great-circle between consecutive position reports)
   - `ship-days` (unique IMO × days present)
3. Compute rate per 1,000 port calls and per 10,000 sailed hours, broken down by ship_type (cargo / tanker / passenger / other) and by event_category (grounding / collision / fire / contact).
4. Compare with absolute counts — replicate the "flip" test that drove Bye & Almklov's key finding.
5. Publish normalised rates as a dashboard tile and a KPI in `kpi_snapshots.accidents_per_1000_calls`.

<a id="b9"></a>
## B.9 Evaluation metrics

Per UNCTAD PPS [[8]](#ref-8):

| KPI | Unit | Target improvement | How computed |
|---|---|---|---|
| Vessel turnaround time (TAT) | hours | ≥ 10% reduction vs FCFS | mean `departure_ts − arrival_ts` |
| Waiting time | hours | ≥ 15% reduction | mean `first_berth_event − arrival_ts` |
| Berth occupancy | % | within target band 65–80% | total berth-hrs occupied / available |
| QC productivity | TEU/hr | ≥ 5% lift | TEU moves / crane-hours |
| Queue length | vessels | ≤ 3 at p95 | `count(waiting @ t)` |
| Accident rate | per 1,000 port calls | report + normalise | Bye-Almklov [[19]](#ref-19) |
| Prediction MAPE | % | ≤ 15% | LightGBM hold-out |
| Solver optimality gap | % | ≤ 5% at 300 s | Meisel-2009 [[10]](#ref-10) |

<a id="b10"></a>
## B.10 Expected thesis contributions

1. **First public digital twin of Alexandria Port** with 56 real berth polygons, ShipNext live AIS, PortWatch trade, Open-Meteo weather.
2. **Open-source Python pipeline + PostGIS/TimescaleDB schema** reusable by any Mediterranean port (MIT-licensed).
3. **Quantified TAT/wait-time improvement** from a LightGBM + CP-SAT pipeline validated on both real Alexandria data and Meisel-2009 benchmarks.
4. **First AIS-normalised accident-rate analysis of Alexandria** (Bye-Almklov methodology applied to an African port, filling an explicit gap in the 2019 paper [[19]](#ref-19) which covered only Norway).
5. **Interoperability map** of DCSA 2.0 [[9]](#ref-9) ↔ FIWARE Smart-Data-Models MarineTransport [[4]](#ref-4) ↔ IHO S-100 [[1]](#ref-1) for a port DT, with conflicts resolved.

<a id="b11"></a>
## B.11 Risks & mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| ShipNext API changes / rate-limits | M | H | Cache every 5-min snapshot to SQLite; fall back to aisstream.io + pyais [[20]](#ref-20) |
| Crane event data never obtained | H | M | Treat QC productivity as latent; model only BAP, not QCSP (keep QCAP) |
| GISIS/EMCIP access limitation | M | M | Use public IMO MCI + news-scraper triangulation; narrative-only if counts unavailable |
| LightGBM overfits with small N (~5k calls/yr) | M | M | Rolling-origin CV, Tweedie objective, conservative leaves |
| CP-SAT timeout at 300 s | L | M | Warm-start from heuristic greedy; tune `num_search_workers` |
| APA PDF schema changes | L | L | Version-pin PDF hash; re-run `pdf_extract.py` |
| Ramadan / Christmas break eats timeline | H | M | Buffer weeks 7, 11 as catch-up |
| Thesis advisor unavailable | L | H | Weekly written status note; escalate after 2 weeks silence |
| Plagiarism / dual-use of ShipNext data | L | M | Terms-of-service review; attribute all data sources in the paper |

<a id="b12"></a>
## B.12 12-week detailed timeline

Start 20 April 2026. Ramadan ended 30 Mar 2026, so no major religious break. Eid Al-Adha is ~26–30 May → **buffer week 6**.

| Wk | Dates | Deliverable |
|---|---|---|
| 1 | Apr 20–26 | Freeze schema v3; migrate SQLite → PostgreSQL 16 + PostGIS + TimescaleDB; hypertables live |
| 2 | Apr 27–May 3 | Port `shipnext_ingest.py`; add `weather_ingest.py` + `accident_ingest.py`; APScheduler jobs |
| 3 | May 4–10 | Derive `berth_occupancy_intervals` via ST_Within + hysteresis; back-fill 2025 |
| 4 | May 11–17 | LightGBM baseline on handling-time; first rolling-origin CV; feature audit |
| 5 | May 18–24 | CP-SAT BAP prototype on toy instance; wire LightGBM → CP-SAT |
| 6 | May 25–31 | **Buffer (Eid)** + Meisel-2009 benchmark runs, gap analysis |
| 7 | Jun 1–7 | Full BAP + QCAP objective; weight calibration on 2024 hold-out |
| 8 | Jun 8–14 | SimPy replay framework; end-to-end FCFS vs CP-SAT comparison |
| 9 | Jun 15–21 | Accident pipeline; Bye-Almklov normalisation; rate report |
| 10 | Jun 22–28 | FastAPI endpoints + MapLibre dashboard finalised; what-if scenarios |
| 11 | Jun 29–Jul 5 | Ablation study; write Ch. 4 (Results) + Ch. 5 (Discussion) |
| 12 | Jul 6–12 | Thesis polish, defence slides, journal-paper draft submission |

<a id="b13"></a>
## B.13 Publication targets

| Venue | Type | IF / rank | Deadline(s) | Page limit | Fit |
|---|---|---|---|---|---|
| **Maritime Economics & Logistics** (Springer, Palgrave) [[36]](#ref-36) | Q1 journal | IF ≈ 2.3 | rolling | ~30 pp | High: port performance + ML optimisation |
| **Marine Policy** (Elsevier) | Q1 journal | IF ≈ 4.2 | rolling | ~10 pp | Accident-normalisation piece only |
| **Ocean & Coastal Management** (Elsevier) | Q1 journal | IF ≈ 5.0 | rolling | 30 pp | DRL-BAP vs CP-SAT; [[17]](#ref-17) already fits |
| **European Journal of Operational Research** [[15]](#ref-15) | Q1 journal | IF ≈ 6.0 | rolling | 40 pp | Pure BAP contribution, high bar |
| **Journal of Marine Science & Engineering** (MDPI) | Q2 | IF ≈ 2.7 | rolling | open | Fast turnaround, APC |
| **IEEE Trans. Intelligent Transportation Systems** | Q1 journal | IF ≈ 8.5 | rolling | 14 pp | DT angle; high bar |
| **IEEE ITSC 2026** conference | conf | — | May 2026 abstract | 6 pp | Stretch: submit at week 5 |
| **INFORMS TSL / ILS 2026** | conf | — | varies | 8 pp | Transportation & logistics fit |

Primary target: **Maritime Economics & Logistics** (main thesis article). Secondary: **Marine Policy** (accident-normalisation short paper).

<a id="b14"></a>
## B.14 Tools, licenses, hardware

**Software — all free-for-academic / OSI-approved:**
- Python 3.11.9 (PSF)
- PostgreSQL 16 (PostgreSQL License); PostGIS 3.5 (GPL-2.0); TimescaleDB 2.17 Community (Apache-2.0 / TSL)
- OR-Tools 9.11 (Apache-2.0); LightGBM 4.5 (MIT); SimPy 4.1.1 (MIT)
- FastAPI 0.115 (MIT); Uvicorn (BSD-3); Pydantic 2.9 (MIT); SQLAlchemy 2.0 (MIT); APScheduler 3.10 (MIT)
- pyais 2.6 (MIT); libais 0.17 (Apache-2.0)
- MapLibre GL JS 4.7.0 (BSD-3) [[34]](#ref-34); Plotly 5.24 (MIT); ECharts (Apache-2.0)
- GeoPandas 1.0 (BSD-3); Shapely 2.0 (BSD-3); Rasterio (BSD-3)
- Docker CE (Apache-2.0); Git + GitHub Student (free)
- VS Code (MIT); JupyterLab (BSD-3)

**Data sources (terms reviewed):**
- ShipNext public API — terms permit research use
- IMF PortWatch — CC-BY
- Open-Meteo — free, CC-BY
- APA Operating Instructions PDF — public document
- Equasis — free, non-commercial
- IMO GISIS MCI — public
- EMSA EMCIP — aggregated public reports

**Hardware — minimum:**
- Laptop: 16 GB RAM, 8-core CPU, 512 GB NVMe, Windows 11 / Linux (OP already has)
- Chunk 7-day rule means working set fits in 4 GB [[22]](#ref-22)
- **Optional** GPU (stretch RL experiment): RTX 4060 or free Colab T4 — DRL-BAP paper used similar [[17]](#ref-17)

---

<a id="references"></a>
## References

<a id="ref-1"></a>**[1]** IHO. *S-100 Universal Hydrographic Data Model, Edition 5.0.0.* IHO Publications. <https://iho.int/en/s-100-universal-hydrographic-data-model> and <https://iho.int/uploads/user/pubs/standards/s-100/S-100_5.0.0_Final_Clean_Web.pdf>. IHO S-100 Schema Server: <https://schemas.s100dev.net/>. Also: NOAA S-100 <https://marinenavigation.noaa.gov/s100.html>.

<a id="ref-2"></a>**[2]** IALA. *S-201 Aids to Navigation Information Product Specification, v1.2.0.* <https://www.iala.int/technical/data-modelling/iala-s-200-development-status/s-201/>; draft 0.0.7 full text <https://www.iala.int/content/uploads/2018/09/2-S-201-Product-Specification-draft-0-0-7-July-2017-main-document.pdf>. Council report C80-10.8.1 (2024).

<a id="ref-3"></a>**[3]** OGC. *OGC SensorThings API — Part 1: Sensing (OGC 15-078r6).* <https://docs.ogc.org/is/15-078r6/15-078r6.html>. Data model: <https://ogc-iot.github.io/ogc-iot-api/datamodel.html>. GitHub: <https://github.com/opengeospatial/sensorthings>.

<a id="ref-4"></a>**[4]** FIWARE Foundation / Smart Data Models. *Smart Data Models — MarineTransport subject (Berth, Facility, NavigationSector).* <https://smartdatamodels.org/>, <https://github.com/smart-data-models/>. FIWARE data-models repo: <https://github.com/FIWARE/data-models>.

<a id="ref-5"></a>**[5]** ITU. *Recommendation ITU-R M.1371-5 (02/2014) — Technical characteristics for an automatic identification system using TDMA in the VHF maritime mobile band.* <https://www.itu.int/rec/R-REC-M.1371-5-201402-I/en>. AIVDM/AIVDO protocol decoding: <https://gpsd.gitlab.io/gpsd/AIVDM.html>. USCG Encoding Guide v.25: <https://www.navcen.uscg.gov/sites/default/files/pdf/AIS/AISGuide.pdf>.

<a id="ref-6"></a>**[6]** NOAA MarineCadastre. *AIS Vessel Type and Group Codes 2018.* <https://coast.noaa.gov/data/marinecadastre/ais/VesselTypeCodes2018.pdf>. MarineTraffic Shiptype: <https://support.marinetraffic.com/en/articles/9552866-what-is-the-significance-of-the-ais-shiptype-or-vessel-type-number>.

<a id="ref-7"></a>**[7]** UNECE. *Recommendation 16 — UN/LOCODE Code for Ports and Other Locations.* <https://unece.org/trade/uncefact/unlocode>; text <https://unece.org/sites/default/files/2023-10/rec16_ece-trd-205E.pdf>.

<a id="ref-8"></a>**[8]** UNCTAD. *Port Performance Scorecard — Port Management Series Vol. 11 (2023).* <https://unctad.org/system/files/official-document/dtltlb2023d2_en.pdf>. Chapter 4 RMT 2022 KPIs: <https://unctad.org/system/files/official-document/rmt2022ch4_en.pdf>. TrainForTrade PPS: <https://tft.unctad.org/thematic-areas/port-management/port-performance-scorecard/>.

<a id="ref-9"></a>**[9]** DCSA. *Port Call Standard 2.0 (supersedes JIT 1.2 Beta).* <https://dcsa.org/standards/just-in-time-port-call-> and <https://dcsa.org/newsroom/port-call-standard-update>.

<a id="ref-10"></a>**[10]** Meisel, F. (2009). *Seaside Operations Planning in Container Terminals*. Physica-Verlag, Heidelberg. DOI: 10.1007/978-3-7908-2191-8. Meisel & Bierwirth (2009). "Heuristics for the integration of crane productivity in the berth allocation problem", *Trans. Res. Part E* 45(1):196–209. DOI: 10.1016/j.tre.2008.03.001.

<a id="ref-11"></a>**[11]** Port of Rotterdam. *PortXchange Synchronizer / Pronto.* <https://www.portofrotterdam.com/en/services/online-tools/portxchange>. IBM digital-twin case: <https://www.ibm.com/blog/iot-digital-twin-rotterdam/>. Esri case study: <https://ditto-oceandecade.org/use-cases/a-digital-twin-for-the-port-of-rotterdam-by-esri/>.

<a id="ref-12"></a>**[12]** Yang, F. et al. (2024). "A digital twin framework for large comprehensive ports and a case study of Qingdao Port." *Int. J. Adv. Manuf. Tech.* DOI: 10.1007/s00170-022-10625-1. Hofmann, W. & Branding, F. (2023). "Digital Twins for Ports: Derived from Smart City and Supply Chain Twinning Experience." arXiv:2301.10224. <https://arxiv.org/pdf/2301.10224>.

<a id="ref-13"></a>**[13]** Fraunhofer IML. *Digital twin in logistics.* <https://www.iml.fraunhofer.de/en/fields_of_activity/material-flow-systems/software_engineering/digital-twin-in-logistics.html>. @ILO / DACHSER German Logistics Award: <https://www.dachser.us/en/mediaroom/DACHSER-and-Fraunhofer-IML-receive-German-Logistics-Award-for-digital-twin-23296>.

<a id="ref-14"></a>**[14]** (a) Klar et al. (2025). "Digital Twin for resilience and sustainability assessment of port facility." *Sustainable & Resilient Infrastructure.* <https://www.tandfonline.com/doi/full/10.1080/23789689.2025.2526928>. (b) Zhang et al. (2024). "Digital Twin System for Long-Term Service Monitoring of Port Terminals." *JMSE* 12(7):1215. <https://www.mdpi.com/2077-1312/12/7/1215>. (c) Chen et al. (2026). "Interoperability of Port Digital Twin Systems Through Ontologies Alignment." DOI: 10.1007/978-981-96-7238-7_1. (d) Di Vaio et al. (2026). "Digital twin adoption in port authorities." *J. Shipping & Trade.*

<a id="ref-15"></a>**[15]** Bierwirth, C. & Meisel, F. (2010). "A survey of berth allocation and quay crane scheduling problems in container terminals." *EJOR* 202(3):615–627. Bierwirth & Meisel (2015). "A follow-up survey of BAP and QCSP." *EJOR* 244(3):675–689. <https://ideas.repec.org/a/eee/ejores/v244y2015i3p675-689.html>. Sepehri et al. (2024). "BAP and QCSP: Systematic Review." *JMSE* 13(7):1339. <https://www.mdpi.com/2077-1312/13/7/1339>.

<a id="ref-16"></a>**[16]** Parolas, I. et al. (2021). "Machine Learning based System for Vessel Turnaround Time Prediction." arXiv:2104.14980. <https://arxiv.org/pdf/2104.14980>. PIXEL H2020: <https://pixel-ports.eu/>. Raeesi et al. (2025). "Leveraging ML and optimisation models for enhanced seaport efficiency." *Maritime Economics & Logistics.* DOI: 10.1057/s41278-024-00309-w. Hybrid stacking: "High-accuracy prediction of vessels' ETA in seaports" DOI: 10.1016/j.iotjb.2025.100xxx.

<a id="ref-17"></a>**[17]** Lv, G., Zou, X. et al. (2024). "Dynamic berth allocation under uncertainties based on deep reinforcement learning towards resilient ports." *Ocean & Coastal Management.* DOI: 10.1016/j.ocecoaman.2024.107113. Xue et al. (2024). "Discrete Dynamic Berth Allocation Optimization … Deep Q-Network." *Mathematics* 12(23):3742. <https://www.mdpi.com/2227-7390/12/23/3742>. "Deep RL for Dynamic BAP with Random Ship Arrivals," IEEE <https://ieeexplore.ieee.org/document/10704490/>.

<a id="ref-18"></a>**[18]** Open-source SimPy container-terminal repos: Basu <https://github.com/Ankita-Basu/Container-Terminal-Simulation-using-SimPy>; Swami <https://github.com/ameyswami35/Simulating-a-container-terminal-using-SimPy>; Sharma <https://github.com/adityassharma-ss/containerSimulation>; Deleu <https://github.com/WoutDeleu/PortTerminalSimulation>; Cortys <https://github.com/Cortys/hafenUI>.

<a id="ref-19"></a>**[19]** Bye, R. J. & Almklov, P. G. (2019). "Normalization of maritime accident data using AIS." *Marine Policy* 109:103675. DOI: 10.1016/j.marpol.2019.06.001. <https://www.sciencedirect.com/science/article/pii/S0308597X1930154X>. SINTEF open archive: <https://sintef.brage.unit.no/sintef-xmlui/handle/11250/2640009>. Follow-up: "Holistic view of maritime navigation accidents and risk indicators: examining IMO reports 2011–2021" PMC10122610.

<a id="ref-20"></a>**[20]** M0r13n. *pyais.* <https://github.com/M0r13n/pyais>. schwehr. *libais.* <https://github.com/schwehr/libais>. Pietri. *simpleais.* <https://github.com/wpietri/simpleais>.

<a id="ref-21"></a>**[21]** EMSA. *EMCIP — European Marine Casualty Information Platform.* <https://emsa.europa.eu/emcip.html>. Background: <https://portal.emsa.europa.eu/web/emcip/background>. IMO GISIS MCI: <https://www.imo.org/en/OurWork/MSAS/Pages/Casualties.aspx>. CASMET taxonomy.

<a id="ref-22"></a>**[22]** Timescale / TigerData. *About hypertables and chunk intervals.* <https://docs.timescale.com/use-timescale/latest/hypertables/about-hypertables/>. Chunk sizing: <https://www.tigerdata.com/docs/use-timescale/latest/hypertables/improve-query-performance>. Forum guidance: <https://forum.tigerdata.com/forum/t/choosing-the-right-chunk-time-interval-value-for-timescaledb-hypertables/116>.

<a id="ref-23"></a>**[23]** IMF. *PortWatch — Daily Port Activity Data and Trade Estimates.* <https://portwatch.imf.org/pages/data-and-methodology>. Dataset page: <https://portwatch.imf.org/datasets/959214444157458aad969389b3ebe1a0_0/about>. R tutorial: <https://bookr.basbakker.us/5-Data/7_PortWatch.html>.

<a id="ref-24"></a>**[24]** Equasis. <https://equasis.bureauveritas.com/>. EMSA Equasis statistics: <https://www.emsa.europa.eu/equasis-statistics.html>. CLI: <https://github.com/rhinonix/equasis-cli>.

<a id="ref-25"></a>**[25]** PortXchange. <https://port-xchange.com/>. Portchain. <https://portchain.com/home>. "How Portchain Works": <https://canvasbusinessmodel.com/blogs/how-it-works/portchain-how-it-works>.

<a id="ref-26"></a>**[26]** TU Delft. "The port of the future: smart, clean and autonomous." <https://www.tudelft.nl/en/innovation-impact/pioneering-tech/articles/the-port-of-the-future-smart-clean-and-autonomous>. Negenborn research page: <http://negenborn.net/rudy/projects.html>. AI in port and maritime research (Leiden-Delft-Rotterdam): <https://www.tudelft.nl/en/ai/research-innovation/innovation-ecosystem/ai-in-port-and-maritime-research-in-leiden-delft-and-rotterdam>.

<a id="ref-27"></a>**[27]** Maritime and Port Authority of Singapore. *Maritime Digital Twin launched 24 March 2025.* <https://www.mpa.gov.sg/maritime-singapore/innovation-and-r-d/maritime-digital-twin>. Press release: <https://www.mpa.gov.sg/media-centre/details/singapore-advances-maritime-innovation-with-geospatial-partnerships-and-launches-maritime-digital-twin>. Jurong Port JP Glass Esri case: <https://www.esri.com/about/newsroom/blog/singapore-jurong-port-jpglass-digital-twin>.

<a id="ref-28"></a>**[28]** Google OR-Tools. CP-SAT scheduling docs: <https://github.com/google/or-tools/blob/stable/ortools/sat/docs/scheduling.md>. CP-SAT Primer (Krupke): <https://d-krupke.github.io/cpsat-primer/>. Pganalyze tutorial: <https://pganalyze.com/blog/a-practical-introduction-to-constraint-programming-using-cp-sat>.

<a id="ref-29"></a>**[29]** MarineTraffic AIS API documentation. <https://servicedocs.marinetraffic.com/> and <https://www.marinetraffic.com/en/ais-api-services/documentation/>. Python client Amphinicy: <https://github.com/amphinicy/marine-traffic-client-api>.

<a id="ref-30"></a>**[30]** Awake.AI. <https://www.awake.ai/>. Awake-Kongsberg partnership: <https://www.awake.ai/post/awake-ai-partners-with-kongsberg-digital>. Kongsberg Vessel Insight datasheet: <https://www.kongsberg.com/contentassets/1fcaa7b8c3ad44c1be76cfbd6d007673/vessel-insight-data-technical-sheet.pdf>. 2026 KM Performance: <https://thedigitalship.com/news/maritime-software/kongsberg-maritime-rolls-out-unified-digital-suite/>.

<a id="ref-31"></a>**[31]** PostGIS. *Spatial Indexing.* <https://postgis.net/workshops/postgis-intro/indexing.html>. Spatial queries: <https://postgis.net/docs/using_postgis_query.html>. Crunchy Data: <https://www.crunchydata.com/blog/the-many-spatial-indexes-of-postgis>.

<a id="ref-32"></a>**[32]** Alexandria Port Authority. <https://apa.gov.eg/en/>. WFP LCA Egypt-Alexandria: <https://lca.logcluster.org/211-egypt-port-alexandria>. UNISCO Alexandria overview: <https://www.unisco.com/international-ports/alexandria-egypt>. Lloyd's Top-100 Container Ports 2025 (re-entry at #90). EgyptToday expansion coverage 2024–2025: <https://www.egypttoday.com/Article/3/138588/Egypt-expands-logistics-sector-with-new-Alexandria-port-terminal>.

<a id="ref-33"></a>**[33]** Open-Meteo Marine Weather API. <https://open-meteo.com/en/docs/marine-weather-api>. Wave-model ICON Wave (DWD) + 5 km European model; wave height, period, direction, SST.

<a id="ref-34"></a>**[34]** MapLibre. *MapLibre GL JS 4.7.* <https://maplibre.org/projects/gl-js/>. Releases: <https://github.com/maplibre/maplibre-gl-js/releases>. 3D-terrain example: <https://maplibre.org/maplibre-gl-js/docs/examples/3d-terrain/>.

<a id="ref-35"></a>**[35]** FastAPI + APScheduler integration patterns. "Scheduled Jobs with FastAPI and APScheduler": <https://ahaw021.medium.com/scheduled-jobs-with-fastapi-and-apscheduler-5a4c50580b0e>. <https://pypi.org/project/fastapi-apscheduler/>. Sentry guide: <https://sentry.io/answers/schedule-tasks-with-fastapi/>.

<a id="ref-36"></a>**[36]** *Maritime Economics & Logistics* (Palgrave Macmillan / Springer Nature). <https://link.springer.com/journal/41278>. Metrics: <https://researcher.life/journal/maritime-economics-and-logistics/17262>.

<a id="ref-37"></a>**[37]** ISO 23247 *Digital Twin Framework for Manufacturing* — Parts 1–6. <https://www.iso.org/standard/78743.html> (Part 2 reference architecture). NIST use-case scenarios: <https://www.nist.gov/publications/use-case-scenarios-digital-twin-implementation-based-iso-23247>.

<a id="ref-38"></a>**[38]** Digital Twins for Ports review with hydrogen focus (2025). *MDPI Applied System Innovation* 8(6):165. <https://www.mdpi.com/2571-5577/8/6/165>. Big-Data + DT for dry cargo ports (2025): <https://ojs.publisher.agency/index.php/FTR/article/view/7031>.

<a id="ref-39"></a>**[39]** AI in Digital Twins — Systematic Literature Review (2024). Data & Knowledge Engineering. DOI: 10.1016/j.datak.2024.102304. Digital Twin in Transportation Systematic Review (2024). *Sensors* 24(18):6069. <https://www.mdpi.com/1424-8220/24/18/6069>.

<a id="ref-40"></a>**[40]** IMO FAL Convention & e-Navigation. IMO GISIS public portal: <https://gisis.imo.org/>. IMO Casualty: <https://www.imo.org/en/ourwork/iiis/pages/casualty.aspx>. IALA Guideline 1082 "AN OVERVIEW OF AIS": <https://www.navcen.uscg.gov/sites/default/files/pdf/IALA_Guideline_1082_An_Overview_of_AIS.pdf>.

---

*End of document. Word count ≈ 4,700. Maintainer: Alaa. License: document is CC-BY-4.0 for the prose, MIT for code snippets and DDL.*

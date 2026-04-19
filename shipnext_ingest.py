"""
ShipNext ingestion & berth-occupancy engine for the Alexandria Port DT.

SOURCES (all public, no auth, Mozilla User-Agent required):
    /api/v1/ports/public/alexandria-egaly-egy   — 71-feature GeoJSON (berths + terminals)
    /api/v1/ports/{portId}/nearby-fleet         — live vessel positions
    /api/v1/ports/{portId}/planned-vessels      — scheduled arrivals
    /api/v1/ports/{portId}/planned-cargoes      — cargo manifest (readiness, deadlines)

CAPABILITIES:
    1. Periodic polling of all four endpoints (default 5 min).
    2. Spatial-join live vessel positions → berth polygons for occupancy.
    3. Dwell-time tracking: first_seen_at_berth → last_seen_at_berth.
    4. SQLite persistence: every poll appended, so post-hoc analysis can
       replay the full history.
    5. Emits a live JSON snapshot (alexandria_live.json) the frontend fetches.

USAGE:
    pip install shapely
    python shipnext_ingest.py snapshot          # one-shot fetch + join + save snapshot
    python shipnext_ingest.py poll --interval 300   # continuous polling loop
    python shipnext_ingest.py dwell              # replay sqlite -> per-vessel dwell stats

PORT ID: 581fd25b54e6080aa866a838 (Alexandria MongoDB ObjectId in ShipNext)
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

BASE = Path(__file__).parent
DB   = BASE / "shipnext.db"
BERTHS_PATH = BASE / "alexandria_berths.geojson"
LIVE_SNAPSHOT = BASE / "alexandria_live.json"

PORT_ID = "581fd25b54e6080aa866a838"
ROOT = "https://shipnext.com/api/v1/ports"
HDRS = {"User-Agent": "Mozilla/5.0 (alex-port-dt research)"}

ENDPOINTS = {
    "port":     f"{ROOT}/public/alexandria-egaly-egy",
    "fleet":    f"{ROOT}/{PORT_ID}/nearby-fleet",
    "planned":  f"{ROOT}/{PORT_ID}/planned-vessels",
    "cargoes":  f"{ROOT}/{PORT_ID}/planned-cargoes",
}

MOORED_SOG_KTS = 0.5

# ── Cargo → terminal inference (keyword match, case-insensitive) ──
# Maps any substring present in a cargo description to the likely terminal
# type the cargo would be handled at. Based on APA Operating Instructions +
# ShipNext's 14 terminals for Alexandria/Dekheila.
CARGO_KEYWORD_TO_TERMINAL = [
    # (keyword,           terminal_type)
    ("container",         "container"),
    ("20'",               "container"),
    ("teu",               "container"),
    ("crude",             "petroleum"),
    ("diesel",            "petroleum"),
    ("gasoline",          "petroleum"),
    ("fuel oil",          "petroleum"),
    ("fuel",              "petroleum"),
    ("bunker",            "petroleum"),
    ("petrol",            "petroleum"),
    ("lng",               "petroleum"),
    ("lpg",               "petroleum"),
    ("naphtha",           "petroleum"),
    ("chemical",          "petroleum"),
    ("molasses",          "molasses"),
    ("wheat",             "grains"),
    ("corn",              "grains"),
    ("maize",             "grains"),
    ("barley",            "grains"),
    ("soy",               "grains"),
    ("soybean",           "grains"),
    ("raw sugar",         "grains"),
    ("sugar",             "grains"),
    ("rice",              "grains"),
    ("grain",             "grains"),
    ("timber",            "grain_timber"),
    ("wood",              "grain_timber"),
    ("logs",              "grain_timber"),
    ("livestock",         "livestock"),
    ("sheep",             "livestock"),
    ("cattle",            "livestock"),
    ("coal",              "coal"),
    ("iron ore",          "mining"),
    ("phosphate",         "mining"),
    ("bauxite",           "mining"),
    ("ore",               "mining"),
    ("cement",            "general_cargo_bulk"),
    ("salt",              "general_cargo_bulk"),
    ("gypsum",            "general_cargo_bulk"),
    ("calcium",           "general_cargo_bulk"),
    ("rocks",             "general_cargo_bulk"),
    ("aggregates",        "general_cargo_bulk"),
    ("fertilizer",        "general_cargo_bulk"),
    ("urea",              "general_cargo_bulk"),
    ("scrap",             "general_cargo_bulk"),
    ("wire rod",          "general_cargo"),
    ("steel coil",        "general_cargo"),
    ("steel sheet",       "general_cargo"),
    ("steel rebar",       "general_cargo"),
    ("steel plate",       "general_cargo"),
    ("hot rolled",        "general_cargo"),
    ("cold rolled",       "general_cargo"),
    ("steel",             "general_cargo"),
    ("pipe",              "general_cargo"),
    ("machinery",         "general_cargo"),
    ("project",           "general_cargo"),
    ("general cargo",     "general_cargo"),
    ("vehicles",          "roro"),
    ("vehicle",           "roro"),
    ("cars",              "roro"),
    ("trucks",            "roro"),
    ("passengers",        "passenger"),
    ("cruise",            "passenger"),
]


def infer_terminal_from_cargo(cargo_items) -> str | None:
    """Given a list like ['Wire Rod', 'Steel Coils'], return the best-match terminal type."""
    if not cargo_items:
        return None
    blob = " ".join(cargo_items).lower()
    for keyword, term in CARGO_KEYWORD_TO_TERMINAL:
        if keyword in blob:
            return term
    return None


# ──────────────────────────────────────────────────────────────────────────
# HTTP helper
# ──────────────────────────────────────────────────────────────────────────
def fetch(url: str) -> dict:
    req = urllib.request.Request(url, headers=HDRS)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


# ──────────────────────────────────────────────────────────────────────────
# SQLite schema
# ──────────────────────────────────────────────────────────────────────────
def init_db(conn: sqlite3.Connection):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS fleet_snapshots (
            ts TEXT, imo INTEGER, name TEXT, vtype TEXT,
            lat REAL, lon REAL, sog REAL, heading INTEGER,
            dwt INTEGER, capacity INTEGER,
            route_from TEXT, route_to TEXT, progress REAL,
            berth_id TEXT,     -- spatial-join result
            PRIMARY KEY (ts, imo)
        );
        CREATE INDEX IF NOT EXISTS idx_fleet_imo ON fleet_snapshots(imo);
        CREATE INDEX IF NOT EXISTS idx_fleet_berth ON fleet_snapshots(berth_id);

        CREATE TABLE IF NOT EXISTS berth_occupancy (
            berth_id TEXT, imo INTEGER, name TEXT, vtype TEXT,
            first_seen TEXT, last_seen TEXT,
            dwell_minutes REAL,
            is_current INTEGER,
            PRIMARY KEY (berth_id, imo, first_seen)
        );

        CREATE TABLE IF NOT EXISTS planned_vessels (
            ts TEXT, imo INTEGER, name TEXT, vtype TEXT,
            eta TEXT, origin TEXT,
            PRIMARY KEY (ts, imo)
        );

        CREATE TABLE IF NOT EXISTS planned_cargoes (
            ts TEXT, cargo_id TEXT, cargo TEXT,
            readiness_date TEXT, cancelling_date TEXT,
            weight_tonnes REAL, volume_cbm REAL,
            unloading_port TEXT,
            PRIMARY KEY (ts, cargo_id)
        );
    """)
    conn.commit()


# ──────────────────────────────────────────────────────────────────────────
# Berth index (lazy shapely import)
# ──────────────────────────────────────────────────────────────────────────
def load_berth_index():
    from shapely.geometry import shape, Point
    fc = json.loads(BERTHS_PATH.read_text(encoding="utf-8"))
    polys = []
    for f in fc["features"]:
        p = f["properties"]
        polys.append({
            "id":       "B" + (p.get("quay_no") or p.get("berthID", "?")).replace("/", "_"),
            "quay_no":  p.get("quay_no"),
            "name":     p.get("name"),
            "type":     p.get("type"),
            "terminal": p.get("terminal"),
            "poly":     shape(f["geometry"]),
        })
    return polys, Point


import math

# Alexandria outer anchorage bounding box (where vessels queue for a berth).
# Rough extent: 1–2 NM north of the port, where AIS traffic clusters at 0 kn.
ANCHORAGE_BBOX = {"min_lat": 31.195, "max_lat": 31.260,
                  "min_lon": 29.770, "max_lon": 29.920}

def in_anchorage(lat: float, lon: float) -> bool:
    return (ANCHORAGE_BBOX["min_lat"] <= lat <= ANCHORAGE_BBOX["max_lat"]
            and ANCHORAGE_BBOX["min_lon"] <= lon <= ANCHORAGE_BBOX["max_lon"])


def locate(polys, Point, lon: float, lat: float, buffer_m: float = 100) -> tuple[str | None, str]:
    """Return (berth_id, match_type) where match_type is 'exact', 'buffered', or ''.

    'exact'    — point lies inside the berth polygon
    'buffered' — within `buffer_m` metres of a berth polygon (handles AIS drift,
                 ships moored slightly off the quay, GPS antenna at bow, etc.)
    ''         — no match; caller may want to classify as AT_ANCHORAGE instead
    """
    pt = Point(lon, lat)
    for p in polys:
        if p["poly"].contains(pt):
            return p["id"], "exact"
    # Buffered pass — distance-to-polygon, converted from degrees to metres
    # at Alexandria latitude (~31°, so 1° longitude ≈ 95.3 km).
    m_per_deg_lat = 110540
    m_per_deg_lon = 111320 * math.cos(math.radians(lat))
    best_d, best_id = None, None
    for p in polys:
        d_deg = p["poly"].distance(pt)
        # Approx: treat east-west and north-south scale uniformly via mean
        d_m = d_deg * ((m_per_deg_lat + m_per_deg_lon) / 2)
        if best_d is None or d_m < best_d:
            best_d, best_id = d_m, p["id"]
    if best_d is not None and best_d <= buffer_m:
        return best_id, "buffered"
    return None, ""


# ──────────────────────────────────────────────────────────────────────────
# Poll once — fetch all 4 endpoints + spatial-join + persist
# ──────────────────────────────────────────────────────────────────────────
def poll_once(conn: sqlite3.Connection) -> dict:
    ts = datetime.now(timezone.utc).isoformat()
    print(f"[poll {ts}]")
    polys, Point = load_berth_index()

    # 1. Fleet (now captures loadingStatus — 'laden' = UNLOADING, 'ballast' = LOADING)
    fleet = fetch(ENDPOINTS["fleet"])["data"]
    fleet_rows, occupancy, fleet_full = [], [], []
    for v in fleet:
        pos = v.get("lastPos") or {}
        coords = pos.get("coords") or [None, None]
        lon, lat = coords[0], coords[1]
        if lat is None or lon is None:
            continue
        sog = pos.get("speed", 0) or 0
        det = v.get("details") or {}
        rt  = v.get("route") or {}
        frm = (rt.get("from") or {}).get("name", "")
        to  = (rt.get("to")   or {}).get("name", "")
        lstatus = det.get("loadingStatus")    # 'laden' / 'ballast' / None
        berth_id, match_type, waiting = None, "", False
        if sog < MOORED_SOG_KTS:
            berth_id, match_type = locate(polys, Point, lon, lat)
            if berth_id:
                occupancy.append({"berth_id": berth_id, "imo": v.get("imo"),
                                  "name": v.get("name"), "vtype": det.get("type"),
                                  "loading_status": lstatus,
                                  "match_type": match_type,   # 'exact' or 'buffered'
                                  "operation": ("UNLOADING" if lstatus == "laden"
                                                else "LOADING" if lstatus == "ballast"
                                                else "AT_BERTH")})
            else:
                # Not at a berth — is this ship waiting in the outer anchorage?
                waiting = in_anchorage(lat, lon)
        fleet_rows.append((
            ts, v.get("imo"), v.get("name"), det.get("type"),
            lat, lon, sog, pos.get("angle"),
            det.get("dwt"), det.get("capacity"),
            frm, to, rt.get("progress"),
            berth_id,
        ))
        fleet_full.append({
            "imo": v.get("imo"), "name": v.get("name"),
            "type": det.get("type"), "dwt": det.get("dwt"), "capacity": det.get("capacity"),
            "built": det.get("blt"), "gears": det.get("gears"),
            "lat": lat, "lon": lon, "sog": sog, "heading": pos.get("angle"),
            "from": frm, "to": to, "progress": rt.get("progress"),
            "berth": berth_id,
            "match_type": match_type,      # 'exact' / 'buffered' / ''
            "loading_status": lstatus,
            "operation": ("UNLOADING" if berth_id and lstatus == "laden"
                          else "LOADING" if berth_id and lstatus == "ballast"
                          else "AT_BERTH" if berth_id
                          else "AT_ANCHORAGE" if waiting
                          else "MOORED" if sog < MOORED_SOG_KTS
                          else "MOVING"),
        })
    conn.executemany("""INSERT OR REPLACE INTO fleet_snapshots VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", fleet_rows)

    # 2. Planned vessels
    planned = fetch(ENDPOINTS["planned"])["data"]
    plan_rows = []
    for v in planned:
        det = v.get("details") or {}
        rt  = v.get("route") or {}
        to  = rt.get("to") or {}
        frm = rt.get("from") or {}
        plan_rows.append((
            ts, v.get("imo"), v.get("name"), det.get("type"),
            to.get("date"), frm.get("name", ""),
        ))
    conn.executemany("""INSERT OR REPLACE INTO planned_vessels VALUES (?,?,?,?,?,?)""", plan_rows)

    # 3. Planned cargoes + terminal inference
    cargoes = fetch(ENDPOINTS["cargoes"])["data"]
    cargo_rows = []
    cargo_queue = {}   # terminal_type -> {count, weight_t, top_cargo}
    cargo_enriched = []
    for c in cargoes:
        tv = c.get("totalValues") or {}
        up = c.get("unloadingPort") or {}
        cargo_list = c.get("cargo") or []
        terminal  = infer_terminal_from_cargo(cargo_list)
        weight_kg = tv.get("weight") or 0
        weight_t  = weight_kg / 1000 if weight_kg else 0
        cargo_rows.append((
            ts, c.get("_id"),
            ", ".join(cargo_list),
            c.get("readinessDate"), c.get("cancellingDate"),
            weight_kg, tv.get("volume"),
            up.get("name", ""),
        ))
        # Build terminal queue aggregation
        if terminal:
            q = cargo_queue.setdefault(terminal, {"count": 0, "weight_t": 0, "cargo_types": {}})
            q["count"] += 1
            q["weight_t"] += weight_t
            for item in cargo_list:
                q["cargo_types"][item] = q["cargo_types"].get(item, 0) + 1
        cargo_enriched.append({
            "id": c.get("_id"),
            "cargo": cargo_list,
            "weight_t": round(weight_t, 1),
            "readiness": c.get("readinessDate"),
            "cancelling": c.get("cancellingDate"),
            "unload_port": up.get("name", ""),
            "inferred_terminal": terminal,
        })
    # Convert cargo_queue top_cargos to sorted lists
    for tname, q in cargo_queue.items():
        q["top_cargos"] = sorted(q["cargo_types"].items(), key=lambda kv: -kv[1])[:5]
        q["weight_t"] = round(q["weight_t"], 1)
        del q["cargo_types"]
    conn.executemany("""INSERT OR REPLACE INTO planned_cargoes VALUES (?,?,?,?,?,?,?,?)""", cargo_rows)

    conn.commit()

    # 4. Dwell-time update: compare this poll with previous
    update_berth_occupancy(conn, ts, occupancy)

    # 5. Live JSON snapshot for the dashboard
    snapshot = {
        "ts": ts,
        "berths_occupied": len(set(o["berth_id"] for o in occupancy)),
        "berths_total":    len(polys),
        "vessels_nearby":  len(fleet),
        "vessels_moored":  sum(1 for r in fleet_rows if r[6] is not None and r[6] < MOORED_SOG_KTS),
        "vessels_at_berth":    sum(1 for v in fleet_full if v["operation"] in ("UNLOADING","LOADING","AT_BERTH")),
        "vessels_at_anchorage": sum(1 for v in fleet_full if v["operation"] == "AT_ANCHORAGE"),
        "vessels_moored_other": sum(1 for v in fleet_full if v["operation"] == "MOORED"),
        "vessels_buffered":    sum(1 for o in occupancy if o.get("match_type") == "buffered"),
        "planned_arrivals": len(planned),
        "planned_cargoes":  len(cargoes),
        "cargo_queue_by_terminal": cargo_queue,
        "occupancy": occupancy,
        "fleet": fleet_full,
    }
    LIVE_SNAPSHOT.write_text(json.dumps(snapshot, indent=2, default=str), encoding="utf-8")
    print(f"  fleet={snapshot['vessels_nearby']}  moored={snapshot['vessels_moored']}  "
          f"at_berth={snapshot['vessels_at_berth']}  planned_arrivals={snapshot['planned_arrivals']}  "
          f"cargoes={snapshot['planned_cargoes']}")
    print(f"  snapshot -> {LIVE_SNAPSHOT.name}")
    return snapshot


def update_berth_occupancy(conn: sqlite3.Connection, ts: str, occupancy: list):
    """Maintain first_seen / last_seen / dwell per (berth_id, imo)."""
    now_keys = {(o["berth_id"], o["imo"]) for o in occupancy}
    # Mark previously-current rows that are no longer present as final (is_current=0)
    cur = conn.execute("SELECT berth_id, imo, first_seen FROM berth_occupancy WHERE is_current = 1")
    for berth, imo, first in cur.fetchall():
        if (berth, imo) not in now_keys:
            conn.execute("""UPDATE berth_occupancy
                            SET is_current = 0,
                                dwell_minutes = (julianday(last_seen) - julianday(first_seen)) * 1440
                            WHERE berth_id = ? AND imo = ? AND first_seen = ?""",
                         (berth, imo, first))
    # Upsert currents
    for o in occupancy:
        conn.execute("""
            INSERT INTO berth_occupancy (berth_id, imo, name, vtype, first_seen, last_seen, dwell_minutes, is_current)
            VALUES (?,?,?,?,?,?,0,1)
            ON CONFLICT(berth_id, imo, first_seen) DO UPDATE SET
                last_seen = ?,
                dwell_minutes = (julianday(?) - julianday(first_seen)) * 1440
        """, (o["berth_id"], o["imo"], o["name"], o["vtype"], ts, ts, ts, ts))
    conn.commit()


# ──────────────────────────────────────────────────────────────────────────
# Dwell report
# ──────────────────────────────────────────────────────────────────────────
def dwell_report(conn: sqlite3.Connection):
    print("\n" + "=" * 70)
    print(" BERTH DWELL TIME REPORT")
    print("=" * 70)
    rows = conn.execute("""
        SELECT berth_id, vtype, name, first_seen, last_seen, dwell_minutes, is_current
        FROM berth_occupancy
        ORDER BY dwell_minutes DESC NULLS LAST
        LIMIT 50
    """).fetchall()
    print(f"{'BERTH':8} {'TYPE':6} {'VESSEL':30} {'DWELL (h)':10} {'STATUS':10}")
    print("-" * 70)
    for berth, vtype, name, f, l, dwell, curr in rows:
        d = f"{(dwell or 0)/60:7.2f}"
        status = "ACTIVE" if curr else "FINAL"
        print(f"{berth:8} {vtype or '?':6} {(name or '?')[:30]:30} {d:10} {status}")
    print()

    print("=" * 70)
    print(" AVG DWELL BY VESSEL TYPE (FINAL intervals only)")
    print("=" * 70)
    rows = conn.execute("""
        SELECT vtype, COUNT(*) AS n, AVG(dwell_minutes)/60 AS avg_h, MIN(dwell_minutes)/60 AS min_h, MAX(dwell_minutes)/60 AS max_h
        FROM berth_occupancy WHERE is_current = 0 GROUP BY vtype ORDER BY avg_h DESC
    """).fetchall()
    print(f"{'TYPE':6} {'N':5} {'AVG (h)':10} {'MIN (h)':10} {'MAX (h)':10}")
    for vtype, n, avg_h, min_h, max_h in rows:
        print(f"{vtype or '?':6} {n:5} {avg_h or 0:10.2f} {min_h or 0:10.2f} {max_h or 0:10.2f}")


# ──────────────────────────────────────────────────────────────────────────
# Entrypoint
# ──────────────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["snapshot", "poll", "dwell"])
    ap.add_argument("--interval", type=int, default=300,
                    help="seconds between polls (mode=poll)")
    args = ap.parse_args()

    conn = sqlite3.connect(DB)
    init_db(conn)

    if args.mode == "snapshot":
        poll_once(conn)
    elif args.mode == "poll":
        print(f"[poll loop] every {args.interval}s — Ctrl-C to stop")
        while True:
            try:
                poll_once(conn)
            except Exception as e:
                print(f"[warn] poll failed: {e}")
            time.sleep(args.interval)
    elif args.mode == "dwell":
        dwell_report(conn)

    conn.close()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n!! Interrupted.")
        sys.exit(1)

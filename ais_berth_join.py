"""
AIS ⇄ berth spatial-join tool for the Alexandria Port Digital Twin.

Three modes, selectable on the command line:

    python ais_berth_join.py harvest           # run for hours/days, collect moored vessel pos
    python ais_berth_join.py cluster           # DBSCAN harvested points -> real berth polygons
    python ais_berth_join.py live              # live per-berth occupancy feed, ws broadcast

DATA FLOW

    AISStream.io  ─▶  harvest mode  ─▶  ais_harvest.db (sqlite)
                                           │
                                           ▼
                                     cluster mode
                                           │
                                           ▼
                      alexandria_berths.geojson  (overwrites placeholder)
                                           │
                                           ▼
    AISStream.io  ─▶  live mode    ─▶  ws://localhost:8008   (demo connects here)

CITATION
    Method follows UN Datathon 2023 "Cookbook for Creating Berth Polygons based
    on AIS data" (https://unstats.un.org/wiki/display/UNDatathon2023/...).
    Normalization of accidents per berth follows Bye & Almklov (2019),
    Marine Policy 109: 103675.

SETUP
    pip install websockets pandas scikit-learn shapely
    Get a FREE API key at https://aisstream.io → paste into AISSTREAM_API_KEY
    or set environment variable AISSTREAM_API_KEY.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

BASE = Path(__file__).parent
DB = BASE / "ais_harvest.db"
BERTHS_PATH = BASE / "alexandria_berths.geojson"

# Alexandria + Dekheila bounding box (generous; trims done in code)
BBOX = [[31.10, 29.75], [31.25, 29.95]]   # [[min_lat, min_lon], [max_lat, max_lon]]

AIS_WS = "wss://stream.aisstream.io/v0/stream"
API_KEY = os.environ.get("AISSTREAM_API_KEY", "PASTE_YOUR_KEY_HERE")

# Moored threshold: SOG < 0.5 kn counts as stationary
MOORED_SOG_KTS = 0.5


# ──────────────────────────────────────────────────────────────────────────
# harvest — record moored AIS positions for N hours/days
# ──────────────────────────────────────────────────────────────────────────
def init_harvest_db():
    conn = sqlite3.connect(DB)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS ais_positions (
            mmsi     INTEGER, ts TEXT,
            lat      REAL,    lon REAL,
            sog      REAL,    cog REAL,
            heading  INTEGER, ship_type INTEGER,
            name     TEXT,
            PRIMARY KEY (mmsi, ts)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ts ON ais_positions(ts)")
    conn.commit()
    return conn


async def harvest_mode(duration_hours: float):
    try:
        import websockets
    except ImportError:
        sys.exit("pip install websockets")

    if API_KEY == "PASTE_YOUR_KEY_HERE":
        sys.exit("Set AISSTREAM_API_KEY env var or edit this file.")

    conn = init_harvest_db()
    subscribe = {
        "APIKey": API_KEY,
        "BoundingBoxes": [BBOX],
        "FilterMessageTypes": ["PositionReport", "ShipStaticData"],
    }
    print(f"[harvest] bbox={BBOX}  duration={duration_hours}h")
    deadline = time.time() + duration_hours * 3600
    saved = 0

    async with websockets.connect(AIS_WS) as ws:
        await ws.send(json.dumps(subscribe))
        print("[harvest] subscribed; Ctrl-C to stop early.")

        while time.time() < deadline:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=30)
            except asyncio.TimeoutError:
                print("[harvest] idle 30s, still listening...")
                continue
            msg = json.loads(raw)
            mtype = msg.get("MessageType")
            meta  = msg.get("MetaData", {})
            body  = msg.get("Message", {}).get(mtype, {})

            if mtype == "PositionReport":
                sog = body.get("Sog", 0) or 0
                conn.execute("""
                    INSERT OR REPLACE INTO ais_positions
                    (mmsi, ts, lat, lon, sog, cog, heading, ship_type, name)
                    VALUES (?,?,?,?,?,?,?,?,?)
                """, (
                    meta.get("MMSI"),
                    meta.get("time_utc", datetime.now(timezone.utc).isoformat()),
                    body.get("Latitude"),  body.get("Longitude"),
                    sog, body.get("Cog"),  body.get("TrueHeading"),
                    None, meta.get("ShipName"),
                ))
                saved += 1
                if saved % 50 == 0:
                    conn.commit()
                    print(f"[harvest] saved={saved}")

    conn.commit()
    conn.close()
    print(f"[harvest] done. {saved} position rows in {DB}")


# ──────────────────────────────────────────────────────────────────────────
# cluster — DBSCAN stationary points -> real berth polygons
# ──────────────────────────────────────────────────────────────────────────
def cluster_mode(eps_m: float = 30.0, min_samples: int = 10):
    try:
        import pandas as pd
        from sklearn.cluster import DBSCAN
        from shapely.geometry import MultiPoint, mapping
    except ImportError:
        sys.exit("pip install pandas scikit-learn shapely")

    if not DB.exists():
        sys.exit(f"{DB} not found — run 'harvest' mode first.")

    conn = sqlite3.connect(DB)
    df = pd.read_sql_query(f"""
        SELECT mmsi, lat, lon, sog, ship_type, name FROM ais_positions
        WHERE sog < {MOORED_SOG_KTS}
          AND lat BETWEEN {BBOX[0][0]} AND {BBOX[1][0]}
          AND lon BETWEEN {BBOX[0][1]} AND {BBOX[1][1]}
    """, conn)
    conn.close()
    print(f"[cluster] {len(df)} moored positions loaded")
    if len(df) < min_samples:
        sys.exit("not enough moored positions — harvest longer.")

    # Convert lat/lon to local metres (equirectangular, good enough for port-scale)
    lat0 = df["lat"].mean()
    mx = (df["lon"] - df["lon"].mean()) * 111320 * math.cos(math.radians(lat0))
    my = (df["lat"] - df["lat"].mean()) * 110540
    coords = list(zip(mx, my))

    db = DBSCAN(eps=eps_m, min_samples=min_samples).fit(coords)
    df["cluster"] = db.labels_
    n_clusters = len(set(db.labels_)) - (1 if -1 in db.labels_ else 0)
    print(f"[cluster] DBSCAN found {n_clusters} clusters "
          f"(eps={eps_m}m, min_samples={min_samples})")

    # Emit GeoJSON — one Feature per cluster, convex-hull polygon
    features = []
    for cid, group in df[df["cluster"] >= 0].groupby("cluster"):
        pts = list(zip(group["lon"], group["lat"]))
        hull = MultiPoint(pts).convex_hull
        if hull.geom_type != "Polygon":
            # Degenerate (line/point) — buffer a bit
            hull = hull.buffer(0.0003)
        features.append({
            "type": "Feature",
            "geometry": mapping(hull),
            "properties": {
                "cluster_id":      int(cid),
                "n_positions":     int(len(group)),
                "n_unique_vessels": int(group["mmsi"].nunique()),
                "dominant_ship_type": int(group["ship_type"].mode()[0]) if group["ship_type"].notna().any() else None,
                "coords_status":   "ais_derived",
            },
        })
    out = BASE / "alexandria_berths_ais.geojson"
    out.write_text(json.dumps({"type": "FeatureCollection", "features": features},
                              indent=2), encoding="utf-8")
    print(f"[cluster] wrote {out}  ({len(features)} berth clusters)")
    print("  NOTE: these are AIS-DERIVED clusters, not yet matched to the 79 "
          "PDF quays. Next step: match each cluster to a quay_no by type+length.")


# ──────────────────────────────────────────────────────────────────────────
# live — match incoming AIS to berth polygons, emit per-berth occupancy
# ──────────────────────────────────────────────────────────────────────────
class BerthIndex:
    """Spatial index over the current berth polygons."""
    def __init__(self, path: Path):
        from shapely.geometry import shape
        fc = json.loads(path.read_text(encoding="utf-8"))
        self.polys = []
        for feat in fc["features"]:
            self.polys.append({
                "id":     "B" + feat["properties"].get("quay_no", str(feat["properties"].get("cluster_id"))),
                "type":   feat["properties"].get("type"),
                "length": feat["properties"].get("length_m"),
                "poly":   shape(feat["geometry"]),
            })
        print(f"[live] indexed {len(self.polys)} berth polygons from {path.name}")

    def locate(self, lon: float, lat: float):
        from shapely.geometry import Point
        pt = Point(lon, lat)
        for p in self.polys:
            if p["poly"].contains(pt):
                return p["id"]
        return None


async def live_mode(ws_port: int = 8008):
    try:
        import websockets
    except ImportError:
        sys.exit("pip install websockets")
    if API_KEY == "PASTE_YOUR_KEY_HERE":
        sys.exit("Set AISSTREAM_API_KEY.")
    if not BERTHS_PATH.exists():
        sys.exit(f"{BERTHS_PATH} not found — run generate_berths.py first.")

    idx = BerthIndex(BERTHS_PATH)
    occupancy: dict[str, dict] = {}   # berth_id -> {mmsi, name, since, last_seen}
    clients: set = set()

    async def handler(websocket):
        clients.add(websocket)
        try:
            await websocket.send(json.dumps({"type": "snapshot", "occupancy": occupancy}))
            await websocket.wait_closed()
        finally:
            clients.discard(websocket)

    async def broadcast(msg):
        if not clients:
            return
        payload = json.dumps(msg)
        await asyncio.gather(
            *(c.send(payload) for c in list(clients)),
            return_exceptions=True,
        )

    server = await websockets.serve(handler, "localhost", ws_port)
    print(f"[live] ws server listening on ws://localhost:{ws_port}")

    subscribe = {
        "APIKey": API_KEY,
        "BoundingBoxes": [BBOX],
        "FilterMessageTypes": ["PositionReport"],
    }
    async with websockets.connect(AIS_WS) as ws:
        await ws.send(json.dumps(subscribe))
        print("[live] subscribed to AISStream — waiting for Alexandria traffic...")

        async for raw in ws:
            msg = json.loads(raw)
            if msg.get("MessageType") != "PositionReport":
                continue
            meta = msg["MetaData"]
            body = msg["Message"]["PositionReport"]
            mmsi = meta.get("MMSI")
            sog  = body.get("Sog", 0) or 0
            lat, lon = body.get("Latitude"), body.get("Longitude")
            if lat is None or lon is None:
                continue

            # Only interested in moored vessels for occupancy
            if sog >= MOORED_SOG_KTS:
                # Moving: clear from occupancy if previously there
                for bid, rec in list(occupancy.items()):
                    if rec["mmsi"] == mmsi:
                        del occupancy[bid]
                        await broadcast({"type": "vacated", "berth": bid, "mmsi": mmsi})
                continue

            berth_id = idx.locate(lon, lat)
            if berth_id is None:
                continue

            now = datetime.now(timezone.utc).isoformat()
            rec = occupancy.get(berth_id)
            if rec and rec["mmsi"] == mmsi:
                rec["last_seen"] = now
            else:
                occupancy[berth_id] = {
                    "mmsi": mmsi,
                    "name": meta.get("ShipName") or f"MMSI {mmsi}",
                    "since": now,
                    "last_seen": now,
                }
                await broadcast({
                    "type":    "berth_occupied",
                    "berth":   berth_id,
                    "vessel":  occupancy[berth_id],
                })
                print(f"[live] {berth_id:6s} <- {occupancy[berth_id]['name']}")


# ──────────────────────────────────────────────────────────────────────────
# entrypoint
# ──────────────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("mode", choices=["harvest", "cluster", "live"])
    ap.add_argument("--hours", type=float, default=4.0,
                    help="harvest duration (mode=harvest)")
    ap.add_argument("--eps", type=float, default=30.0,
                    help="DBSCAN eps in metres (mode=cluster)")
    ap.add_argument("--min-samples", type=int, default=10,
                    help="DBSCAN min_samples (mode=cluster)")
    ap.add_argument("--port", type=int, default=8008,
                    help="WebSocket port (mode=live)")
    args = ap.parse_args()

    if args.mode == "harvest":
        asyncio.run(harvest_mode(args.hours))
    elif args.mode == "cluster":
        cluster_mode(args.eps, args.min_samples)
    elif args.mode == "live":
        asyncio.run(live_mode(args.port))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n!! Interrupted.")

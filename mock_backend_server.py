"""
Mock Backend Server for Alexandria Port Digital Twin

Simulates a port backend by broadcasting WebSocket events to the Cesium frontend.
This lets the frontend show "live" data without needing the full backend ready.

Run:  python mock_backend_server.py
Then open the Cesium demo HTML — it will auto-connect.

Requires:  pip install websockets
"""
import asyncio
import json
import random
import math
from datetime import datetime

try:
    import websockets
except ImportError:
    print("Missing package. Install with: pip install websockets")
    raise SystemExit(1)

# ----------------------------------------------------------------------------
# ALEXANDRIA PORT STATE
# ----------------------------------------------------------------------------

VESSELS = [
    {"mmsi": "477553000", "name": "EVER GIVEN", "lon": 29.8650, "lat": 31.1874, "status": "loading"},
    {"mmsi": "248921000", "name": "MSC ISABELLA", "lon": 29.8710, "lat": 31.1871, "status": "loading"},
    {"mmsi": "228399600", "name": "CMA CGM MARCO POLO", "lon": 29.8870, "lat": 31.1860, "status": "docked"},
    {"mmsi": "440451000", "name": "HMM ALGECIRAS", "lon": 29.8980, "lat": 31.1856, "status": "loading"},
    {"mmsi": "636092000", "name": "MAERSK DETROIT", "lon": 29.8520, "lat": 31.1810, "status": "approaching"},
    {"mmsi": "563030000", "name": "ONE TRIUMPH", "lon": 29.8400, "lat": 31.1760, "status": "approaching"},
    {"mmsi": "211281000", "name": "BARBARA", "lon": 29.8200, "lat": 31.1600, "status": "anchored"},
]

BERTHS = [
    {"id": "B49", "status": "occupied"},
    {"id": "B50", "status": "occupied"},
    {"id": "B51", "status": "free"},
    {"id": "B52", "status": "maintenance"},
    {"id": "B53", "status": "occupied"},
    {"id": "B54", "status": "free"},
    {"id": "B55", "status": "occupied"},
    {"id": "B56", "status": "free"},
]

YARDS = [
    {"id": "Y-A", "occupancy": 54},
    {"id": "Y-B", "occupancy": 78},
    {"id": "Y-C", "occupancy": 92},
    {"id": "Y-D", "occupancy": 77},
]

CONNECTED_CLIENTS = set()


# ----------------------------------------------------------------------------
# EVENT BROADCAST
# ----------------------------------------------------------------------------

async def broadcast(event: dict):
    if not CONNECTED_CLIENTS:
        return
    msg = json.dumps(event)
    disconnected = []
    for ws in list(CONNECTED_CLIENTS):
        try:
            await ws.send(msg)
        except websockets.exceptions.ConnectionClosed:
            disconnected.append(ws)
    for ws in disconnected:
        CONNECTED_CLIENTS.discard(ws)


def now():
    return datetime.utcnow().isoformat() + "Z"


# ----------------------------------------------------------------------------
# SIMULATION LOOPS
# ----------------------------------------------------------------------------

async def move_approaching_vessels():
    """Simulate approaching vessels moving toward the port."""
    port_lon, port_lat = 29.8700, 31.1870
    while True:
        for v in VESSELS:
            if v["status"] in ("approaching", "anchored"):
                # Move slightly toward port
                v["lon"] += (port_lon - v["lon"]) * 0.003
                v["lat"] += (port_lat - v["lat"]) * 0.003
                # If close enough, dock
                dist = math.hypot(v["lon"] - port_lon, v["lat"] - port_lat)
                if dist < 0.005 and v["status"] != "docked":
                    v["status"] = "docked"
                    await broadcast({
                        "event_type": "VESSEL_ARRIVE",
                        "entity_id": v["mmsi"],
                        "entity_type": "VESSEL",
                        "timestamp": now(),
                        "details": {"name": v["name"], "status": "docked"},
                    })
                await broadcast({
                    "event_type": "VESSEL_POSITION",
                    "entity_id": v["mmsi"],
                    "entity_type": "VESSEL",
                    "timestamp": now(),
                    "position": {"longitude": v["lon"], "latitude": v["lat"], "heading": 45},
                })
        await asyncio.sleep(2)


async def toggle_berth_status():
    """Occasionally flip a berth status to simulate operations."""
    while True:
        await asyncio.sleep(random.uniform(15, 30))
        b = random.choice(BERTHS)
        if b["status"] == "free":
            b["status"] = "occupied"
        elif b["status"] == "occupied":
            b["status"] = "free"
        else:
            continue
        await broadcast({
            "event_type": "BERTH_STATUS_CHANGED",
            "entity_id": b["id"],
            "entity_type": "BERTH",
            "timestamp": now(),
            "details": {"status": b["status"]},
        })
        print(f"→ Berth {b['id']} is now {b['status']}")


async def update_yard_occupancy():
    """Slowly change yard occupancy."""
    while True:
        await asyncio.sleep(10)
        for y in YARDS:
            # Drift occupancy up/down by up to 2%
            y["occupancy"] = max(30, min(98, y["occupancy"] + random.uniform(-2, 2)))
        await broadcast({
            "event_type": "YARD_UPDATE",
            "entity_type": "YARDS",
            "timestamp": now(),
            "details": {"yards": YARDS},
        })


async def broadcast_kpis():
    """Broadcast overall KPIs every few seconds."""
    while True:
        await asyncio.sleep(5)
        occupied = sum(1 for b in BERTHS if b["status"] == "occupied")
        await broadcast({
            "event_type": "KPI_UPDATE",
            "entity_type": "SYSTEM",
            "timestamp": now(),
            "details": {
                "vessels": len(VESSELS),
                "berths_total": len(BERTHS),
                "berths_occupied": occupied,
                "berth_utilization_pct": round(occupied / len(BERTHS) * 100, 1),
                "avg_yard_occupancy_pct": round(sum(y["occupancy"] for y in YARDS) / len(YARDS), 1),
            },
        })


async def spawn_new_vessel_occasionally():
    """Spawn a new approaching vessel now and then."""
    while True:
        await asyncio.sleep(random.uniform(60, 120))
        mmsi = str(random.randint(100000000, 999999999))
        names = ["MAERSK HONG KONG", "APL SINGAPORE", "HYUNDAI DREAM",
                 "COSCO GALAXY", "YANG MING", "WAN HAI", "OOCL BERLIN"]
        new_vessel = {
            "mmsi": mmsi,
            "name": random.choice(names) + " " + str(random.randint(1, 99)),
            "lon": 29.80 + random.uniform(-0.05, 0.05),
            "lat": 31.15 + random.uniform(-0.05, 0),
            "status": "approaching",
        }
        VESSELS.append(new_vessel)
        print(f"→ New vessel spawned: {new_vessel['name']}")
        await broadcast({
            "event_type": "VESSEL_SPAWN",
            "entity_id": mmsi,
            "entity_type": "VESSEL",
            "timestamp": now(),
            "details": new_vessel,
        })


# ----------------------------------------------------------------------------
# WEBSOCKET HANDLER
# ----------------------------------------------------------------------------

async def handler(ws):
    CONNECTED_CLIENTS.add(ws)
    client = getattr(ws, "remote_address", ("?", "?"))
    print(f"✓ Client connected ({client[0]}). Total: {len(CONNECTED_CLIENTS)}")

    # Send initial state snapshot
    await ws.send(json.dumps({
        "event_type": "INITIAL_STATE",
        "timestamp": now(),
        "details": {
            "vessels": VESSELS,
            "berths": BERTHS,
            "yards": YARDS,
        },
    }))

    try:
        async for msg in ws:
            try:
                data = json.loads(msg)
                print(f"← {data}")
            except json.JSONDecodeError:
                pass
    except websockets.exceptions.ConnectionClosed:
        pass
    finally:
        CONNECTED_CLIENTS.discard(ws)
        print(f"✗ Client disconnected. Total: {len(CONNECTED_CLIENTS)}")


# ----------------------------------------------------------------------------
# MAIN
# ----------------------------------------------------------------------------

async def main():
    host = "localhost"
    port = 8008

    print("=" * 60)
    print(" ALEXANDRIA PORT — MOCK BACKEND SERVER")
    print("=" * 60)
    print(f" WebSocket listening on:  ws://{host}:{port}/ws")
    print(" Open alexandria-port-demo.html in browser")
    print(" Events broadcast:")
    print("   • Vessel positions (every 2s)")
    print("   • Berth status changes (every 15-30s)")
    print("   • Yard occupancy (every 10s)")
    print("   • KPI updates (every 5s)")
    print("   • New vessel spawns (every 60-120s)")
    print(" Press Ctrl+C to stop")
    print("=" * 60)

    # Start background tasks
    tasks = [
        asyncio.create_task(move_approaching_vessels()),
        asyncio.create_task(toggle_berth_status()),
        asyncio.create_task(update_yard_occupancy()),
        asyncio.create_task(broadcast_kpis()),
        asyncio.create_task(spawn_new_vessel_occasionally()),
    ]

    async with websockets.serve(handler, host, port):
        await asyncio.Future()  # run forever


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n✗ Server stopped.")

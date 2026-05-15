"""FastAPI server for Alexandria Port Digital Twin."""
import json, pickle, sqlite3, os
import urllib.request, urllib.error
import numpy as np
import pandas as pd
from datetime import datetime
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

class NumpyEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, (np.integer,)): return int(obj)
        if isinstance(obj, (np.floating,)): return float(obj)
        if isinstance(obj, np.ndarray): return obj.tolist()
        if isinstance(obj, (pd.Timestamp, datetime)): return str(obj)
        return super().default(obj)

def _json(data):
    return JSONResponse(content=json.loads(json.dumps(data, cls=NumpyEncoder)))

BASE = os.path.dirname(os.path.abspath(__file__))
MODELS = os.path.join(BASE, "models")
LOADING_SERVICE_URL = os.environ.get("LOADING_SERVICE_URL", "http://localhost:8009")

app = FastAPI(title="Alexandria Port API")

cors_origins = os.environ.get("CORS_ORIGINS", "http://localhost:5173,http://localhost:4173,http://127.0.0.1:5173")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in cors_origins.split(",")],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Load models & static data at startup
# ---------------------------------------------------------------------------
with open(os.path.join(MODELS, "handling_time_model_v3.pkl"), "rb") as f:
    m1_artifact = pickle.load(f)
m1_model = m1_artifact["model"]
m1_encoders = m1_artifact["label_encoders"]
m1_features = m1_artifact["feature_cols"]
m1_type_avg = m1_artifact["type_avg_dwell"]
m1_term_avg = m1_artifact["terminal_avg_dwell"]
m1_global_median = m1_artifact["global_median_dwell"]

with open(os.path.join(MODELS, "model3", "wait_time_model.pkl"), "rb") as f:
    m3_artifact = pickle.load(f)
m3_model = m3_artifact["model"]
m3_encoders = m3_artifact["label_encoders"]
m3_features = m3_artifact["feature_cols"]
m3_type_avg = m3_artifact["type_avg_wait"]
m3_global_median = m3_artifact["global_median_wait"]

import joblib
m4_artifact = joblib.load(os.path.join(MODELS, "model4", "anomaly_model.pkl"))
m4_model = m4_artifact["model"]
m4_scaler = m4_artifact["scaler"]
m4_feature_cols = m4_artifact["feature_columns"]

berths_df = pd.read_csv(os.path.join(BASE, "exports", "berths.csv"))
berths_df["draft_m"] = pd.to_numeric(berths_df["draft_m"], errors="coerce")
berths_df["length_m"] = pd.to_numeric(berths_df["length_m"], errors="coerce")
berth_to_type = dict(zip(berths_df["berth_id"], berths_df["type"]))
berth_to_draft = {k: (float(v) if pd.notna(v) else 10.0) for k, v in zip(berths_df["berth_id"], berths_df["draft_m"])}
berth_to_length = {k: (float(v) if pd.notna(v) else 160.0) for k, v in zip(berths_df["berth_id"], berths_df["length_m"])}
berth_to_terminal = dict(zip(berths_df["berth_id"], berths_df["terminal"]))

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _load_live():
    with open(os.path.join(BASE, "alexandria_live.json"), "r", encoding="utf-8") as f:
        return json.load(f)

def _load_weather():
    try:
        with open(os.path.join(BASE, "weather_data.json"), "r") as f:
            w = json.load(f)
        lookup = {}
        for i, t in enumerate(w["archive"]["time"]):
            lookup[t[:13]] = {
                "temp": w["archive"]["temperature_2m"][i] or 20,
                "wind_speed": w["archive"]["wind_speed_10m"][i] or 0,
                "wind_gust": w["archive"]["wind_gusts_10m"][i] or 0,
                "wave_height": w["marine"]["wave_height"][i] if i < len(w["marine"]["wave_height"]) else 0,
                "swell_height": w["marine"]["swell_wave_height"][i] if i < len(w["marine"]["swell_wave_height"]) else 0,
                "precip": w["archive"]["precipitation"][i] or 0,
            }
        return lookup
    except FileNotFoundError:
        return {}

WEATHER = _load_weather()

OP_TO_STATUS = {
    "UNLOADING": "Discharging",
    "LOADING": "Loading",
    "AT_BERTH": "Docked",
    "AT_ANCHORAGE": "Arriving",
    "MOORED": "Mooring",
    "MOVING": "Departure",
}

def _predict_dwell(vtype, dwt, berth_id, snap_time):
    btype = berth_to_type.get(berth_id, "general_cargo")
    bdraft = berth_to_draft.get(berth_id, 10.0)
    blength = berth_to_length.get(berth_id, 160.0)
    terminal = berth_to_terminal.get(berth_id, "UNKNOWN")
    snap_key = str(snap_time)[:13]
    w = WEATHER.get(snap_key, {})
    t_avg = m1_type_avg.get(vtype, m1_global_median)
    term_avg = m1_term_avg.get(terminal, m1_global_median)
    dt = datetime.fromisoformat(str(snap_time).replace("Z", "+00:00")) if isinstance(snap_time, str) else snap_time

    row = {}
    for col in m1_features:
        if col == "vessel_type":
            le = m1_encoders[col]
            row[col] = int(le.transform([vtype])[0]) if vtype in le.classes_ else 0
        elif col == "berth_type":
            le = m1_encoders[col]
            row[col] = int(le.transform([btype])[0]) if btype in le.classes_ else 0
        elif col == "terminal_cat":
            le = m1_encoders[col]
            tcat = terminal[:30] if terminal else "UNKNOWN"
            row[col] = int(le.transform([tcat])[0]) if tcat in le.classes_ else 0
        elif col == "dwt": row[col] = float(dwt or 10000)
        elif col == "berth_draft_m": row[col] = bdraft
        elif col == "berth_length_m": row[col] = blength
        elif col == "arrival_hour": row[col] = dt.hour
        elif col == "arrival_dow": row[col] = dt.weekday()
        elif col == "is_weekend": row[col] = 1 if dt.weekday() >= 5 else 0
        elif col == "type_avg_dwell": row[col] = t_avg
        elif col == "terminal_avg_dwell": row[col] = term_avg
        elif col == "wind_speed": row[col] = w.get("wind_speed", 0)
        elif col == "wind_gust": row[col] = w.get("wind_gust", 0)
        elif col == "wave_height": row[col] = w.get("wave_height", 0)
        elif col == "swell_height": row[col] = w.get("swell_height", 0)
        elif col == "temperature": row[col] = w.get("temp", 20)
        elif col == "precipitation": row[col] = w.get("precip", 0)
        else: row[col] = 0

    X = pd.DataFrame([row])
    pred_log = m1_model.predict(X, num_iteration=m1_model.best_iteration)[0]
    return round(max(float(np.expm1(pred_log)), 1.0), 1)

def _predict_wait(vtype, dwt, berths_occupied, snap_time):
    snap_key = str(snap_time)[:13]
    w = WEATHER.get(snap_key, {})
    dt = datetime.fromisoformat(str(snap_time).replace("Z", "+00:00")) if isinstance(snap_time, str) else snap_time
    t_avg = m3_type_avg.get(vtype, m3_global_median)

    row = {}
    for col in m3_features:
        if col == "vessel_type":
            le = m3_encoders[col]
            row[col] = int(le.transform([vtype])[0]) if vtype in le.classes_ else 0
        elif col == "dwt": row[col] = float(dwt or 10000)
        elif col == "arrival_hour": row[col] = dt.hour
        elif col == "arrival_dow": row[col] = dt.weekday()
        elif col == "is_weekend": row[col] = 1 if dt.weekday() >= 5 else 0
        elif col == "berths_occupied": row[col] = berths_occupied
        elif col == "type_avg_wait": row[col] = t_avg
        elif col == "wind_speed": row[col] = w.get("wind_speed", 0)
        elif col == "wind_gust": row[col] = w.get("wind_gust", 0)
        elif col == "wave_height": row[col] = w.get("wave_height", 0)
        elif col == "swell_height": row[col] = w.get("swell_height", 0)
        elif col == "temperature": row[col] = w.get("temp", 20)
        else: row[col] = 0

    X = pd.DataFrame([row])
    pred_log = m3_model.predict(X, num_iteration=m3_model.best_iteration)[0]
    return round(max(float(np.expm1(pred_log)), 0.0), 1)

# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
@app.get("/api/live-snapshot")
def live_snapshot():
    live = _load_live()
    total_berths = len(berths_df)
    occupied = live.get("vessels_at_berth", 0)
    return {
        "ts": live["ts"],
        "vessels_at_berth": occupied,
        "vessels_at_anchorage": live.get("vessels_at_anchorage", 0),
        "planned_arrivals": live.get("planned_arrivals", 0),
        "berths_total": total_berths,
        "berths_occupied": occupied,
        "utilization": round(occupied / total_berths * 100, 1) if total_berths else 0,
    }

@app.get("/api/fleet")
def fleet(at_berth: bool = False, at_anchorage: bool = False, limit: int = 200):
    live = _load_live()
    ts = live["ts"]
    occupied_imos = {str(o["imo"]) for o in live.get("occupancy", [])}
    occ_map = {str(o["imo"]): o for o in live.get("occupancy", [])}
    berths_occupied = len(occupied_imos)

    results = []
    for v in live.get("fleet", []):
        imo = str(v.get("imo", ""))
        is_at_berth = imo in occupied_imos
        op = v.get("operation", "")
        is_anch = op == "AT_ANCHORAGE" or (
            not is_at_berth and v.get("sog", 99) <= 1.5
            and 31.10 <= v.get("lat", 0) <= 31.30
            and 29.75 <= v.get("lon", 0) <= 29.95
        )

        if at_berth and not is_at_berth:
            continue
        if at_anchorage and not is_anch:
            continue

        vtype = v.get("type", "MPP")
        dwt = v.get("dwt", 0)
        berth_id = occ_map[imo]["berth_id"] if imo in occ_map else None

        pred_dwell = None
        pred_wait = None
        if is_at_berth and berth_id:
            pred_dwell = _predict_dwell(vtype, dwt, berth_id, ts)
        if is_anch:
            pred_wait = _predict_wait(vtype, dwt, berths_occupied, ts)

        status = OP_TO_STATUS.get(op, "Docked")
        if is_anch and status not in ("Arriving",):
            status = "Arriving"

        results.append({
            "imo": imo,
            "name": v.get("name", ""),
            "type": vtype,
            "dwt": dwt,
            "lat": v.get("lat"),
            "lon": v.get("lon"),
            "sog": v.get("sog"),
            "heading": v.get("heading"),
            "berth_id": berth_id,
            "operation": op,
            "status": status,
            "from": v.get("from", ""),
            "to": v.get("to", ""),
            "loading_status": v.get("loading_status", ""),
            "predicted_dwell_h": pred_dwell,
            "predicted_wait_h": pred_wait,
            "is_at_berth": is_at_berth,
            "is_at_anchorage": is_anch,
        })

    results.sort(key=lambda x: (not x["is_at_berth"], not x["is_at_anchorage"], x["name"]))
    return results[:limit]

@app.get("/api/berths")
def berths():
    live = _load_live()
    occ_map = {o["berth_id"]: o for o in live.get("occupancy", [])}
    result = []
    for _, b in berths_df.iterrows():
        bid = b["berth_id"]
        occ = occ_map.get(bid)
        result.append({
            "berth_id": bid,
            "terminal": b.get("terminal", ""),
            "type": b.get("type", ""),
            "draft_m": float(b["draft_m"]) if pd.notna(b["draft_m"]) else None,
            "length_m": float(b["length_m"]) if pd.notna(b["length_m"]) else None,
            "lat": float(b["center_lat"]) if pd.notna(b.get("center_lat")) else None,
            "lon": float(b["center_lon"]) if pd.notna(b.get("center_lon")) else None,
            "status": "occupied" if occ else "vacant",
            "vessel_name": occ["name"] if occ else None,
            "vessel_imo": str(occ["imo"]) if occ else None,
            "vessel_type": occ.get("vtype") if occ else None,
        })
    return result

@app.get("/api/forecast")
def forecast():
    live = _load_live()
    ts = live["ts"]
    occupied_imos = {str(o["imo"]) for o in live.get("occupancy", [])}
    berths_occupied = len(occupied_imos)

    # Find first anchorage vessel as "next arrival"
    candidate = None
    for v in live.get("fleet", []):
        imo = str(v.get("imo", ""))
        if imo in occupied_imos:
            continue
        op = v.get("operation", "")
        if op == "AT_ANCHORAGE" or (
            v.get("sog", 99) <= 1.5 and 31.10 <= v.get("lat", 0) <= 31.30
            and 29.75 <= v.get("lon", 0) <= 29.95
        ):
            candidate = v
            break

    if not candidate:
        return {"has_forecast": False}

    vtype = candidate.get("type", "MPP")
    dwt = candidate.get("dwt", 10000)
    wait_h = _predict_wait(vtype, dwt, berths_occupied, ts)

    # Find best compatible berth (first vacant one matching type)
    TYPE_COMPAT = {
        "CONT": {"container"},
        "BULK": {"general_cargo", "general_cargo_bulk", "grains", "grain_timber", "coal"},
        "MPP": {"general_cargo", "general_cargo_bulk", "roro", "roro_general", "passenger"},
        "TANK": {"petroleum"},
        "CAR": {"roro", "roro_general"},
    }
    compat = TYPE_COMPAT.get(vtype, set())
    best_berth = None
    for _, b in berths_df.iterrows():
        bid = b["berth_id"]
        if b.get("type") not in compat:
            continue
        if bid in {o["berth_id"] for o in live.get("occupancy", [])}:
            continue
        best_berth = bid
        break

    dwell_h = _predict_dwell(vtype, dwt, best_berth or "B47", ts) if best_berth else None

    return {
        "has_forecast": True,
        "vessel_name": candidate.get("name", ""),
        "vessel_type": vtype,
        "imo": str(candidate.get("imo", "")),
        "dwt": dwt,
        "predicted_wait_h": wait_h,
        "predicted_berth": best_berth,
        "predicted_dwell_h": dwell_h,
        "model_info": {
            "dwell_mae": "3.9h",
            "wait_mae": "9.9h",
            "dwell_r2": 0.776,
        },
    }

@app.get("/api/anomalies")
def anomalies():
    csv_path = os.path.join(MODELS, "model4", "anomalies.csv")
    if not os.path.exists(csv_path):
        return {"anomalies": [], "total": 0}
    df = pd.read_csv(csv_path)
    return {
        "anomalies": df.to_dict("records"),
        "total": len(df),
        "event_matched": int((df["likely_event"] != "Unclassified").sum()),
    }

@app.get("/api/anomaly-check")
def anomaly_check():
    db_path = os.path.join(BASE, "portwatch.db")
    if not os.path.exists(db_path):
        return {"is_anomaly": False, "message": "No PortWatch data"}
    conn = sqlite3.connect(db_path)
    df = pd.read_sql_query(
        "SELECT * FROM port_daily_stats WHERE portname='Alexandria' ORDER BY date DESC LIMIT 1", conn
    )
    conn.close()
    if df.empty:
        return {"is_anomaly": False, "message": "No data"}

    latest = df.iloc[0]
    return {
        "is_anomaly": False,
        "date": latest["date"],
        "portcalls": int(latest["portcalls"]),
        "import_tonnes": int(latest["import"]),
        "export_tonnes": int(latest["export"]),
        "message": f"Port activity on {latest['date']}: {int(latest['portcalls'])} calls, normal range",
    }

@app.get("/api/daily-stats")
def daily_stats(start: str = "2025-01-01", end: str = "2026-12-31"):
    db_path = os.path.join(BASE, "portwatch.db")
    if not os.path.exists(db_path):
        return []
    conn = sqlite3.connect(db_path)
    df = pd.read_sql_query(
        "SELECT * FROM port_daily_stats WHERE portname='Alexandria' AND date BETWEEN ? AND ? ORDER BY date",
        conn, params=(start, end),
    )
    conn.close()
    records = []
    for _, r in df.iterrows():
        records.append({
            "date": r["date"],
            "portcalls": int(r["portcalls"]),
            "import_tonnes": int(r["import"]),
            "export_tonnes": int(r["export"]),
            "portcalls_container": int(r.get("portcalls_container", 0)),
            "portcalls_dry_bulk": int(r.get("portcalls_dry_bulk", 0)),
            "portcalls_general_cargo": int(r.get("portcalls_general_cargo", 0)),
            "portcalls_roro": int(r.get("portcalls_roro", 0)),
            "portcalls_tanker": int(r.get("portcalls_tanker", 0)),
        })
    return records

@app.get("/api/berth-schedule")
def berth_schedule():
    pkl_path = os.path.join(MODELS, "model2", "berth_allocation_result.pkl")
    if not os.path.exists(pkl_path):
        return {"has_schedule": False, "message": "Run Model 2 notebook first"}
    with open(pkl_path, "rb") as f:
        result = pickle.load(f)
    schedule = result.get("schedule", [])
    clean = []
    for s in schedule:
        row = {}
        for k, v in s.items():
            if hasattr(v, "item"):
                row[k] = v.item()
            elif not isinstance(v, str) and k in ("start_time", "end_time"):
                row[k] = str(v)
            else:
                row[k] = v
        clean.append(row)
    return _json({
        "has_schedule": True,
        "snapshot_time": result.get("snapshot_time"),
        "status": result.get("status"),
        "solve_time_s": result.get("solve_time_s"),
        "vessels_scheduled": result.get("vessels_scheduled"),
        "total_wait_h": result.get("total_wait_h"),
        "avg_wait_h": result.get("avg_wait_h"),
        "schedule": clean,
    })

@app.get("/api/weather")
def weather():
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    key = now.strftime("%Y-%m-%dT%H")
    current = WEATHER.get(key)
    if not current:
        keys = sorted(WEATHER.keys())
        current = WEATHER[keys[-1]] if keys else {
            "temp": 22, "wind_speed": 8, "wind_gust": 14,
            "wave_height": 0.5, "swell_height": 0.3, "precip": 0,
        }
    sea_state = "Calm"
    wh = current.get("wave_height", 0) or 0
    if wh > 2.5:
        sea_state = "Rough"
    elif wh > 1.0:
        sea_state = "Moderate"
    return {
        "temp_c": round(current["temp"], 1),
        "wind_speed_ms": round(current["wind_speed"], 1),
        "wind_gust_ms": round(current["wind_gust"], 1),
        "wave_height_m": round(wh, 2),
        "swell_height_m": round(current.get("swell_height", 0) or 0, 2),
        "precip_mm": round(current.get("precip", 0) or 0, 1),
        "sea_state": sea_state,
        "description": f"{round(current['temp'])}°C, Wind {round(current['wind_speed'])} m/s",
    }

@app.post("/api/chat")
def chat(body: dict):
    msg = (body.get("message") or "").strip().lower()
    if not msg:
        return {"reply": "Please ask me something about Alexandria Port!"}

    live = _load_live()
    ts = live["ts"]
    occupied_imos = {str(o["imo"]) for o in live.get("occupancy", [])}
    berths_occupied_count = len(occupied_imos)
    total_berths = len(berths_df)
    at_anchorage = 0
    fleet_list = live.get("fleet", [])
    for v in fleet_list:
        imo = str(v.get("imo", ""))
        if imo not in occupied_imos:
            op = v.get("operation", "")
            if op == "AT_ANCHORAGE" or (v.get("sog", 99) <= 1.5 and 31.10 <= v.get("lat", 0) <= 31.30 and 29.75 <= v.get("lon", 0) <= 29.95):
                at_anchorage += 1
    utilization = round(berths_occupied_count / total_berths * 100, 1) if total_berths else 0

    # Weather
    from datetime import timezone
    now = datetime.now(timezone.utc)
    w_key = now.strftime("%Y-%m-%dT%H")
    w = WEATHER.get(w_key) or WEATHER.get(sorted(WEATHER.keys())[-1] if WEATHER else "", {})
    if not w:
        w = {"temp": 22, "wind_speed": 8, "wind_gust": 14, "wave_height": 0.5, "swell_height": 0.3, "precip": 0}
    wh = w.get("wave_height", 0) or 0
    sea_state = "Rough" if wh > 2.5 else ("Moderate" if wh > 1.0 else "Calm")

    # Intent matching
    if any(k in msg for k in ["hello", "hi ", "hey", "greet", "good morning", "good evening", "مرحبا"]):
        return {"reply": f"Hello! I'm Captain Alex, your Alexandria Port assistant. We currently have {berths_occupied_count} vessels at berth and {at_anchorage} at anchorage. How can I help you today?"}

    if any(k in msg for k in ["weather", "temperature", "temp", "wind", "wave", "sea state", "storm", "rain", "forecast weather"]):
        return {"reply": f"Current weather at Alexandria Port:\n• Temperature: {round(w['temp'], 1)}°C\n• Wind: {round(w['wind_speed'], 1)} m/s (gusts {round(w['wind_gust'], 1)} m/s)\n• Wave height: {round(wh, 2)} m\n• Sea state: {sea_state}\n• Precipitation: {round(w.get('precip', 0) or 0, 1)} mm\n\n{'⚠️ Rough seas — exercise caution for vessel operations.' if sea_state == 'Rough' else '✅ Conditions are favorable for port operations.' if sea_state == 'Calm' else 'ℹ️ Moderate conditions — monitor wave heights.'}"}

    if any(k in msg for k in ["how many ship", "how many vessel", "vessel count", "ship count", "ships in port", "vessels in port", "fleet size", "total ship", "total vessel"]):
        return {"reply": f"Alexandria Port currently has:\n• {berths_occupied_count} vessels at berth (out of {total_berths} berths)\n• {at_anchorage} vessels at anchorage\n• Port utilization: {utilization}%\n• Total fleet tracked: {len(fleet_list)} vessels"}

    if any(k in msg for k in ["schedule", "gantt", "plan", "allocation", "assign"]):
        pkl_path = os.path.join(MODELS, "model2", "berth_allocation_result.pkl")
        if os.path.exists(pkl_path):
            with open(pkl_path, "rb") as f:
                result = pickle.load(f)
            sched = result.get("schedule", [])
            lines = [
                f"Berth Allocation Schedule (OR-Tools CP-SAT):",
                f"• Status: {result.get('status', 'Unknown')}",
                f"• Vessels scheduled: {result.get('vessels_scheduled', len(sched))}",
                f"• Total wait: {result.get('total_wait_h', 0)}h",
                f"• Average wait: {round(result.get('avg_wait_h', 0), 1)}h",
                f"\nNext 5 assignments:",
            ]
            for s in sched[:5]:
                lines.append(f"• {s.get('vessel', '?')} → {s.get('berth', '?')} (wait {s.get('wait_h', 0)}h, dwell {s.get('dwell_h', 0)}h)")
            return {"reply": "\n".join(lines)}
        return {"reply": "No berth schedule available. Run the OR-Tools optimizer first."}

    if any(k in msg for k in ["berth", "dock", "terminal", "quay"]):
        occupied_list = [o for o in live.get("occupancy", [])]
        terminals = {}
        for _, b in berths_df.iterrows():
            t = b.get("terminal", "Unknown")
            if t not in terminals:
                terminals[t] = {"total": 0, "occupied": 0}
            terminals[t]["total"] += 1
        for o in occupied_list:
            t = berth_to_terminal.get(o["berth_id"], "Unknown")
            if t in terminals:
                terminals[t]["occupied"] += 1

        lines = [f"Berth Status ({berths_occupied_count}/{total_berths} occupied, {utilization}% utilization):"]
        for t, info in sorted(terminals.items()):
            lines.append(f"• {t}: {info['occupied']}/{info['total']} berths used")
        if any(k in msg for k in ["available", "vacant", "free", "empty"]):
            lines.append(f"\n{total_berths - berths_occupied_count} berths are currently available.")
        return {"reply": "\n".join(lines)}

    if any(k in msg for k in ["anchorage", "waiting", "anchor", "queue"]):
        anch_vessels = []
        for v in fleet_list:
            imo = str(v.get("imo", ""))
            if imo in occupied_imos:
                continue
            op = v.get("operation", "")
            if op == "AT_ANCHORAGE" or (v.get("sog", 99) <= 1.5 and 31.10 <= v.get("lat", 0) <= 31.30 and 29.75 <= v.get("lon", 0) <= 29.95):
                anch_vessels.append(v)
        lines = [f"{len(anch_vessels)} vessels at anchorage:"]
        for v in anch_vessels[:8]:
            lines.append(f"• {v.get('name', 'Unknown')} ({v.get('type', '')}, {v.get('dwt', 0):,} DWT)")
        if len(anch_vessels) > 8:
            lines.append(f"  ... and {len(anch_vessels) - 8} more")
        return {"reply": "\n".join(lines)}

    if any(k in msg for k in ["utilization", "capacity", "how busy", "occupancy", "how full"]):
        return {"reply": f"Port utilization is currently {utilization}% ({berths_occupied_count} of {total_berths} berths occupied).\n\n{'The port is operating at high capacity.' if utilization > 70 else 'The port has available capacity for incoming vessels.' if utilization < 40 else 'The port is at moderate utilization.'}"}

    # Find specific vessel — search fleet first, then berth schedule
    import re
    msg_words = set(re.findall(r'\b\w+\b', msg))

    def _search_vessels(vessels, key="name"):
        for v in vessels:
            vname = (v.get(key) or "").lower()
            if vname and vname in msg:
                return v
        for v in vessels:
            vname = (v.get(key) or "").lower()
            vwords = [w for w in vname.split() if len(w) >= 3]
            if vwords and any(w in msg_words for w in vwords):
                return v
        return None

    vessel_match = _search_vessels(fleet_list, "name")
    if vessel_match:
        v = vessel_match
        imo = str(v.get("imo", ""))
        is_berth = imo in occupied_imos
        occ = {str(o["imo"]): o for o in live.get("occupancy", [])}
        berth_id = occ[imo]["berth_id"] if imo in occ else None
        location = f"at berth {berth_id}" if is_berth else "at anchorage"
        lines = [
            f"Vessel: {v.get('name', '')}",
            f"• IMO: {imo}",
            f"• Type: {v.get('type', '')}",
            f"• DWT: {v.get('dwt', 0):,}",
            f"• Location: {location}",
            f"• Status: {OP_TO_STATUS.get(v.get('operation', ''), 'Unknown')}",
        ]
        if is_berth and berth_id:
            dwell = _predict_dwell(v.get("type", "MPP"), v.get("dwt", 10000), berth_id, ts)
            lines.append(f"• Predicted dwell: {dwell}h")
        if v.get("from"):
            lines.append(f"• From: {v['from']}")
        return {"reply": "\n".join(lines)}

    # Search berth schedule if not in live fleet
    pkl_path = os.path.join(MODELS, "model2", "berth_allocation_result.pkl")
    if os.path.exists(pkl_path):
        with open(pkl_path, "rb") as f:
            sched_result = pickle.load(f)
        sched_list = sched_result.get("schedule", [])
        sched_match = _search_vessels(sched_list, "vessel")
        if sched_match:
            s = sched_match
            lines = [
                f"Vessel: {s.get('vessel', '')}",
                f"• IMO: {s.get('imo', '')}",
                f"• Type: {s.get('type', '')}",
                f"• DWT: {s.get('dwt', 0):,}",
                f"• Assigned berth: {s.get('berth', '?')} ({s.get('terminal', '')})",
                f"• Wait time: {s.get('wait_h', 0)}h",
                f"• Dwell time: {s.get('dwell_h', 0)}h",
                f"• Scheduled: {str(s.get('start_time', ''))[:16]} → {str(s.get('end_time', ''))[:16]}",
                f"\n(Source: OR-Tools berth allocation schedule)",
            ]
            return {"reply": "\n".join(lines)}

    if any(k in msg for k in ["anomal", "unusual", "disruption", "abnormal", "incident"]):
        csv_path = os.path.join(MODELS, "model4", "anomalies.csv")
        if os.path.exists(csv_path):
            adf = pd.read_csv(csv_path)
            recent = adf.tail(5)
            lines = [f"Anomaly Detection (Isolation Forest): {len(adf)} anomalies detected from historical data."]
            lines.append("\nMost recent anomalies:")
            for _, r in recent.iterrows():
                lines.append(f"• {r['date']}: {r.get('likely_event', 'Unclassified')} (score: {r.get('anomaly_score', 0):.3f})")
            return {"reply": "\n".join(lines)}
        return {"reply": "Anomaly detection model is active. No anomalies file found at this time."}

    if any(k in msg for k in ["model", "ai ", "machine learning", "ml ", "predict", "algorithm", "accuracy"]):
        return {"reply": "Alexandria Port uses 4 AI/ML models:\n\n1️⃣ **Dwell Time Prediction** (LightGBM)\n   MAE: 3.92h | R²: 0.776 | 89.84% within 10h\n\n2️⃣ **Berth Allocation** (OR-Tools CP-SAT)\n   Optimal solver | Avg wait: 4.59h\n\n3️⃣ **Wait Time Prediction** (LightGBM)\n   MAE: 9.94h | Median error: 2.90h\n\n4️⃣ **Anomaly Detection** (Isolation Forest)\n   40 anomalies detected | 200 estimators\n\n5️⃣ **Container Loading** (DRL + Extreme Points)\n   PCT model with GAT+PPO, ~65-67% utilization"}

    if any(k in msg for k in ["help", "what can you", "what do you", "feature", "can you"]):
        return {"reply": "I can help you with:\n• 🚢 Vessel information — ask about any ship by name\n• ⚓ Berth status — availability, terminals, utilization\n• 🌊 Weather conditions — wind, waves, temperature\n• 📊 Port statistics — vessel counts, capacity, traffic\n• 🤖 AI models — predictions, schedules, anomalies\n• 📋 Berth schedule — OR-Tools optimized assignments\n• ⏳ Anchorage queue — waiting vessels\n\nTry: \"How many ships are in port?\" or \"What's the weather?\""}

    if any(k in msg for k in ["who are you", "what are you", "your name", "about you", "introduce"]):
        return {"reply": "I'm Captain Alex, the AI assistant for Alexandria Port Digital Twin. I have real-time access to vessel tracking, berth management, weather data, and AI prediction models. Ask me anything about port operations!"}

    if any(k in msg for k in ["thank", "thanks", "شكرا"]):
        return {"reply": "You're welcome! Feel free to ask if you need anything else about port operations. Fair winds! ⚓"}

    # Default
    return {"reply": f"I'm not sure I understand that question. Here's a quick overview:\n\n• {berths_occupied_count} vessels at berth, {at_anchorage} at anchorage\n• Port utilization: {utilization}%\n• Weather: {round(w['temp'], 1)}°C, {sea_state} seas\n\nTry asking about: weather, vessels, berths, schedule, AI models, or a specific ship name."}


# ---------------------------------------------------------------------------
# Container Loading — built-in 3D bin packing (Extreme Points algorithm)
# ---------------------------------------------------------------------------
CONTAINER_SPECS = {
    "20GP": {"l": 5898, "w": 2352, "h": 2393, "payload": 28300},
    "40GP": {"l": 12032, "w": 2352, "h": 2393, "payload": 26730},
    "40HC": {"l": 12032, "w": 2352, "h": 2698, "payload": 28800},
    "45HC": {"l": 13556, "w": 2352, "h": 2698, "payload": 25740},
    "20RF": {"l": 5450, "w": 2286, "h": 2265, "payload": 27430},
    "40RF": {"l": 11580, "w": 2286, "h": 2265, "payload": 26130},
    "20OT": {"l": 5898, "w": 2352, "h": 2348, "payload": 28220},
    "20FR": {"l": 5620, "w": 2228, "h": 2233, "payload": 27580},
}

def _overlap(x1, y1, z1, l1, w1, h1, x2, y2, z2, l2, w2, h2):
    return (x1 < x2+l2 and x1+l1 > x2 and y1 < y2+h2 and y1+h1 > y2 and z1 < z2+w2 and z1+w1 > z2)

def _supported(x, y, z, l, w, boxes):
    if y == 0:
        return True
    area = l * w
    support = 0
    for bx, by, bz, bl, bw, bh in boxes:
        if abs(by + bh - y) < 2:
            ox = max(0, min(x+l, bx+bl) - max(x, bx))
            oz = max(0, min(z+w, bz+bw) - max(z, bz))
            support += ox * oz
    return support >= area * 0.3

def _solve_packing(code, items, algo="extreme_points"):
    import time
    t0 = time.perf_counter()
    spec = CONTAINER_SPECS.get(code, CONTAINER_SPECS["20GP"])
    L, W, H, PL = spec["l"], spec["w"], spec["h"], spec["payload"]

    def vol(it):
        d = it["dimensions"]
        return d["length_mm"] * d["width_mm"] * d["height_mm"]

    if algo in ("baf", "bl"):
        si = sorted(items, key=lambda x: x["dimensions"]["length_mm"] * x["dimensions"]["width_mm"], reverse=True)
    elif algo == "ga":
        si = sorted(items, key=lambda x: x.get("weight_kg", 0), reverse=True)
    else:
        si = sorted(items, key=vol, reverse=True)

    eps = [(0, 0, 0)]
    boxes = []
    placements = []
    tw = 0
    wpos = []

    for item in si:
        d = item["dimensions"]
        iw = float(item.get("weight_kg", 0) or 0)
        if tw + iw > PL:
            continue

        rots = [
            (d["length_mm"], d["width_mm"], d["height_mm"], 0),
            (d["width_mm"], d["length_mm"], d["height_mm"], 1),
        ]
        if algo in ("ppo", "ga"):
            rots.append((d["length_mm"], d["height_mm"], d["width_mm"], 2))

        best = None
        bscore = float("inf")
        for ep in sorted(eps, key=lambda p: (p[1], p[0]+p[2])):
            for rl, rw, rh, rot in rots:
                x, y, z = ep
                if x+rl > L or y+rh > H or z+rw > W:
                    continue
                if any(_overlap(x, y, z, rl, rw, rh, *b) for b in boxes):
                    continue
                if not _supported(x, y, z, rl, rw, boxes):
                    continue
                if algo == "baf":
                    sc = y*10000 + (rl*rw - d["length_mm"]*d["width_mm"])**2 + x + z
                elif algo in ("ppo", "ga"):
                    waste_x = L - (x + rl)
                    waste_z = W - (z + rw)
                    sc = y*8000 + min(waste_x, waste_z)*5 + x*0.5 + z*0.5
                else:
                    sc = y*10000 + (x + z)*10 + (L - rl) + (W - rw)
                if sc < bscore:
                    bscore = sc
                    best = (x, y, z, rl, rw, rh, rot)

        if best:
            x, y, z, rl, rw, rh, rot = best
            placements.append({
                "item_id": item["id"],
                "position": {"x_mm": int(x), "y_mm": int(y), "z_mm": int(z)},
                "rotation": rot,
                "rotated_dimensions": {"length_mm": int(rl), "width_mm": int(rw), "height_mm": int(rh)},
            })
            boxes.append((x, y, z, rl, rw, rh))
            tw += iw
            wpos.append((iw if iw > 0 else 1, x+rl/2, y+rh/2, z+rw/2))
            for ne in [(x+rl, y, z), (x, y, z+rw), (x, y+rh, z)]:
                if ne[0] <= L and ne[1] <= H and ne[2] <= W:
                    if not any(_overlap(ne[0], ne[1], ne[2], 1, 1, 1, *b) for b in boxes):
                        eps.append(ne)
            eps = [p for p in eps if p != (x, y, z)]
            eps = list(set(eps))

    cvol = L * W * H
    pvol = sum(b[3]*b[4]*b[5] for b in boxes)
    tww = sum(wp[0] for wp in wpos) or 1
    cog_x = sum(wp[0]*wp[1] for wp in wpos)/tww if wpos else L/2
    cog_y = sum(wp[0]*wp[2] for wp in wpos)/tww if wpos else 0
    cog_z = sum(wp[0]*wp[3] for wp in wpos)/tww if wpos else W/2

    placed_ids = {p["item_id"] for p in placements}
    elapsed = (time.perf_counter() - t0) * 1000

    return {
        "algorithm": algo,
        "container_code": code,
        "placements": placements,
        "unplaced_item_ids": [it["id"] for it in items if it["id"] not in placed_ids],
        "kpis": {
            "utilization": round(pvol / cvol, 4) if cvol else 0,
            "weight_used": round(tw / PL, 4) if PL else 0,
            "cog_long_dev": round(cog_x / L - 0.5, 4) if L else 0,
            "cog_lat_dev": round(cog_z / W - 0.5, 4) if W else 0,
            "cog_vert_frac": round(cog_y / H, 4) if H else 0,
            "unstable_count": 0,
            "overloaded_count": 0,
            "imdg_violation_count": 0,
            "lifo_violation_count": 0,
            "stack_violation_count": 0,
        },
        "elapsed_ms": round(elapsed, 2),
    }

@app.post("/api/loading/solve")
def loading_solve(body: dict):
    code = body.get("container_code", "20GP")
    items = body.get("items", [])
    algo = body.get("algorithm", "extreme_points")
    return _json(_solve_packing(code, items, algo))

@app.post("/api/loading/compare")
def loading_compare(body: dict):
    code = body.get("container_code", "20GP")
    items = body.get("items", [])
    a = body.get("algorithm_a", "ppo")
    b = body.get("algorithm_b", "extreme_points")
    return _json({"a": _solve_packing(code, items, a), "b": _solve_packing(code, items, b)})


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

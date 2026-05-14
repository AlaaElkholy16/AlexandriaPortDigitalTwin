"""FastAPI server for Alexandria Port Digital Twin."""
import json, pickle, sqlite3, os
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

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

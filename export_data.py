"""
Exports every real dataset the project has pulled into a single Excel
workbook + companion CSV files. Designed to be shareable with a graduation
team (each sheet is self-describing, every column has a header).

Run:
    pip install pandas openpyxl
    python export_data.py

Produces:
    alexandria_port_data.xlsx        — 8-sheet workbook for team review
    exports/berths.csv               — individual CSVs for anyone without Excel
    exports/live_fleet.csv
    exports/planned_arrivals.csv
    exports/planned_cargoes.csv
    exports/occupancy.csv
    exports/cargo_queue_by_terminal.csv
    exports/portwatch.csv            — historical daily port calls (existing)
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pandas as pd

BASE = Path(__file__).parent
OUT_XLSX = BASE / "alexandria_port_data.xlsx"
OUT_DIR  = BASE / "exports"
OUT_DIR.mkdir(exist_ok=True)


# ──────────────────────────────────────────────────────────────────────────
def load_berths() -> pd.DataFrame:
    fc = json.loads((BASE / "alexandria_berths.geojson").read_text(encoding="utf-8"))
    rows = []
    for f in fc["features"]:
        p = f["properties"]
        ring = f["geometry"]["coordinates"][0]
        center_lon = sum(c[0] for c in ring[:-1]) / (len(ring) - 1)
        center_lat = sum(c[1] for c in ring[:-1]) / (len(ring) - 1)
        rows.append({
            "berth_id":       "B" + (p.get("quay_no") or "").replace("/", "_"),
            "quay_no":        p.get("quay_no"),
            "name":           p.get("name"),
            "terminal":       p.get("terminal"),
            "type":           p.get("type"),
            "shipnext_class": p.get("shipnext_type"),   # B-Dry / B-Wet
            "length_m":       p.get("length_m"),
            "draft_m":        p.get("draft_m"),
            "operator":       p.get("operator"),
            "port":           p.get("port"),
            "center_lat":     round(center_lat, 6),
            "center_lon":     round(center_lon, 6),
            "coords_status":  p.get("coords_status"),
        })
    return pd.DataFrame(rows).sort_values(["port", "terminal", "quay_no"])


def load_live() -> dict[str, pd.DataFrame]:
    s = json.loads((BASE / "alexandria_live.json").read_text(encoding="utf-8"))

    fleet = pd.DataFrame(s["fleet"])
    fleet = fleet[[
        "imo", "name", "type", "dwt", "capacity", "built", "gears",
        "lat", "lon", "sog", "heading",
        "from", "to", "progress",
        "berth", "match_type", "loading_status", "operation",
    ]].sort_values(["operation", "name"], na_position="last")

    occ = pd.DataFrame(s["occupancy"])

    cq_rows = []
    for terminal_type, q in s.get("cargo_queue_by_terminal", {}).items():
        top = ", ".join(f"{c} × {n}" for c, n in (q.get("top_cargos") or [])[:5])
        cq_rows.append({
            "terminal_type":         terminal_type,
            "queued_cargoes":        q.get("count"),
            "total_weight_tonnes":   q.get("weight_t"),
            "top_cargo_types":       top,
        })
    cq = pd.DataFrame(cq_rows).sort_values("queued_cargoes", ascending=False)

    summary = pd.DataFrame([{
        "snapshot_timestamp":      s["ts"],
        "berths_total":            s["berths_total"],
        "berths_occupied":         s["berths_occupied"],
        "vessels_nearby":          s["vessels_nearby"],
        "vessels_at_berth":        s["vessels_at_berth"],
        "vessels_at_anchorage":    s["vessels_at_anchorage"],
        "vessels_moored_other":    s["vessels_moored_other"],
        "vessels_buffered":        s["vessels_buffered"],
        "planned_arrivals":        s["planned_arrivals"],
        "planned_cargoes":         s["planned_cargoes"],
    }])
    return {"summary": summary, "fleet": fleet, "occupancy": occ, "cargo_queue": cq}


def load_planned() -> pd.DataFrame:
    raw = json.loads((BASE / "shipnext_planned_vessels.json").read_text(encoding="utf-8"))
    rows = []
    for v in raw["data"]:
        det = v.get("details") or {}
        rt  = v.get("route") or {}
        pos = v.get("lastPos") or {}
        coords = pos.get("coords") or [None, None]
        rows.append({
            "imo":    v.get("imo"),
            "name":   v.get("name"),
            "type":   det.get("type"),
            "dwt":    det.get("dwt"),
            "capacity": det.get("capacity"),
            "built":  det.get("blt"),
            "loading_status": det.get("loadingStatus"),
            "origin": (rt.get("from") or {}).get("name"),
            "eta":    (rt.get("to")   or {}).get("date"),
            "lat":    coords[1],
            "lon":    coords[0],
            "progress": rt.get("progress"),
        })
    return pd.DataFrame(rows).sort_values("eta", na_position="last")


def load_cargoes() -> pd.DataFrame:
    raw = json.loads((BASE / "shipnext_planned_cargoes.json").read_text(encoding="utf-8"))
    rows = []
    for c in raw["data"]:
        tv = c.get("totalValues") or {}
        up = c.get("unloadingPort") or {}
        rows.append({
            "cargo_id":         c.get("_id"),
            "cargo":            ", ".join(c.get("cargo") or []),
            "readiness_date":   c.get("readinessDate"),
            "cancelling_date":  c.get("cancellingDate"),
            "weight_tonnes":    (tv.get("weight") or 0) / 1000,
            "volume_cbm":       tv.get("volume"),
            "unloading_port":   up.get("name"),
        })
    return pd.DataFrame(rows).sort_values(["cargo", "readiness_date"])


def load_portwatch() -> pd.DataFrame:
    db = BASE / "portwatch.db"
    if not db.exists():
        return pd.DataFrame()
    conn = sqlite3.connect(db)
    df = pd.read_sql_query("""
        SELECT date, portid, portname,
               portcalls, portcalls_container, portcalls_dry_bulk,
               portcalls_general_cargo, portcalls_roro, portcalls_tanker,
               import, export
        FROM port_daily_stats ORDER BY date, portid
    """, conn)
    conn.close()
    return df


def load_accident_rates() -> dict[str, pd.DataFrame]:
    out = {}
    for name in ("accident_rates_by_type", "accident_rates_monthly", "accident_severity_by_type"):
        p = BASE / f"{name}.csv"
        if p.exists():
            out[name] = pd.read_csv(p)
    return out


# ──────────────────────────────────────────────────────────────────────────
def main():
    print("[1/4] Loading all datasets...")
    berths = load_berths()
    live   = load_live()
    planned = load_planned()
    cargoes = load_cargoes()
    portwatch = load_portwatch()
    accidents = load_accident_rates()

    print(f"      {len(berths)} berths")
    print(f"      {len(live['fleet'])} live vessels")
    print(f"      {len(planned)} planned arrivals")
    print(f"      {len(cargoes)} planned cargoes")
    print(f"      {len(portwatch)} PortWatch daily records")

    print("[2/4] Writing CSVs to ./exports/ ...")
    berths.to_csv(OUT_DIR / "berths.csv", index=False)
    live["fleet"].to_csv(OUT_DIR / "live_fleet.csv", index=False)
    live["occupancy"].to_csv(OUT_DIR / "occupancy.csv", index=False)
    live["cargo_queue"].to_csv(OUT_DIR / "cargo_queue_by_terminal.csv", index=False)
    planned.to_csv(OUT_DIR / "planned_arrivals.csv", index=False)
    cargoes.to_csv(OUT_DIR / "planned_cargoes.csv", index=False)
    if not portwatch.empty:
        portwatch.to_csv(OUT_DIR / "portwatch_daily.csv", index=False)

    print("[3/4] Writing single multi-sheet Excel workbook...")
    with pd.ExcelWriter(OUT_XLSX, engine="openpyxl") as w:
        live["summary"].to_excel(w, sheet_name="00_Summary", index=False)
        berths.to_excel(w,              sheet_name="01_Berths", index=False)
        live["fleet"].to_excel(w,       sheet_name="02_Live_Fleet", index=False)
        live["occupancy"].to_excel(w,   sheet_name="03_Occupancy_Now", index=False)
        planned.to_excel(w,             sheet_name="04_Planned_Arrivals", index=False)
        cargoes.to_excel(w,             sheet_name="05_Planned_Cargoes", index=False)
        live["cargo_queue"].to_excel(w, sheet_name="06_Cargo_Queue_Terminal", index=False)
        if not portwatch.empty:
            portwatch.to_excel(w,       sheet_name="07_PortWatch_Daily", index=False)
        for name, df in accidents.items():
            df.to_excel(w, sheet_name=name[:31], index=False)

    print(f"[4/4] Done.")
    print(f"      Excel:  {OUT_XLSX}  ({OUT_XLSX.stat().st_size/1024:.1f} KB)")
    print(f"      CSVs:   {OUT_DIR}/")
    for p in sorted(OUT_DIR.glob("*.csv")):
        print(f"              {p.name}  ({p.stat().st_size/1024:.1f} KB)")


if __name__ == "__main__":
    main()

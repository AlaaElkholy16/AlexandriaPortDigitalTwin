"""
PortWatch IMF ingest — pulls daily port-call and trade-volume stats
for Alexandria + El Dekheila from the IMF/Oxford PortWatch dataset,
and writes them into a local SQLite database for the Digital Twin.

Dataset:  https://portwatch.imf.org/datasets/4a3facf6df3542b09dbe48d5556b45fa_0
Service:  Daily_Ports_Data FeatureServer/0  (ArcGIS REST)

Fields include:
  date, year, month, day, portid, portname, country, ISO3,
  portcalls_{container,dry_bulk,general_cargo,roro,tanker,cargo}, portcalls,
  import_{...}, import, export_{...}, export

Egyptian ports of interest:
  port23    Alexandria
  port2044  El Dekheila   (twin port adjacent to Alexandria — handle them together)

Usage:
    python portwatch_ingest.py              # incremental: last 30 days
    python portwatch_ingest.py --full       # full history since 2019
    python portwatch_ingest.py --since 2024-01-01
    python portwatch_ingest.py --export csv # dump SQLite -> CSV
"""
import argparse
import sqlite3
import sys
import time
import urllib.parse
import urllib.request
import json
from datetime import date, datetime, timedelta
from pathlib import Path

SERVICE = ("https://services9.arcgis.com/weJ1QsnbMYJlCHdG/arcgis/rest/"
           "services/Daily_Ports_Data/FeatureServer/0/query")

PORTS = {
    "port23":   "Alexandria",
    "port2044": "El Dekheila",
}

FIELDS = [
    "date", "year", "month", "day",
    "portid", "portname", "country", "ISO3",
    "portcalls_container", "portcalls_dry_bulk", "portcalls_general_cargo",
    "portcalls_roro", "portcalls_tanker", "portcalls_cargo", "portcalls",
    "import_container", "import_dry_bulk", "import_general_cargo",
    "import_roro", "import_tanker", "import_cargo", "import",
    "export_container", "export_dry_bulk", "export_general_cargo",
    "export_roro", "export_tanker", "export_cargo", "export",
]

DB_PATH = Path(__file__).parent / "portwatch.db"
PAGE_SIZE = 1000  # service max per request


def fetch_page(where_sql: str, offset: int) -> dict:
    params = {
        "where": where_sql,
        "outFields": ",".join(FIELDS),
        "orderByFields": "date ASC",
        "resultOffset": str(offset),
        "resultRecordCount": str(PAGE_SIZE),
        "returnGeometry": "false",
        "f": "json",
    }
    url = SERVICE + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "alex-port-dt/1.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())


def fetch_all(where_sql: str) -> list:
    rows, offset = [], 0
    while True:
        payload = fetch_page(where_sql, offset)
        features = payload.get("features", [])
        if not features:
            break
        rows.extend(f["attributes"] for f in features)
        print(f"  +{len(features):4d} rows  (total {len(rows)})")
        if len(features) < PAGE_SIZE:
            break
        offset += PAGE_SIZE
        time.sleep(0.3)  # be polite
    return rows


def init_db(conn: sqlite3.Connection):
    cols_sql = ",\n    ".join(f"{f} {_sql_type(f)}" for f in FIELDS)
    conn.execute(f"""
        CREATE TABLE IF NOT EXISTS port_daily_stats (
            {cols_sql},
            PRIMARY KEY (portid, date)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_pds_date ON port_daily_stats(date)")
    conn.commit()


def _sql_type(field: str) -> str:
    if field in ("portid", "portname", "country", "ISO3"):
        return "TEXT"
    if field == "date":
        return "TEXT"  # ISO date
    return "INTEGER"


def upsert(conn: sqlite3.Connection, rows: list):
    if not rows:
        return 0
    # Normalize 'date' (epoch ms -> YYYY-MM-DD)
    for r in rows:
        if isinstance(r.get("date"), (int, float)):
            r["date"] = datetime.utcfromtimestamp(r["date"] / 1000).date().isoformat()
    placeholders = ",".join("?" * len(FIELDS))
    cols = ",".join(FIELDS)
    updates = ",".join(f"{f}=excluded.{f}" for f in FIELDS if f not in ("portid", "date"))
    sql = (f"INSERT INTO port_daily_stats ({cols}) VALUES ({placeholders}) "
           f"ON CONFLICT(portid, date) DO UPDATE SET {updates}")
    conn.executemany(sql, [tuple(r.get(f) for f in FIELDS) for r in rows])
    conn.commit()
    return len(rows)


def latest_date_in_db(conn: sqlite3.Connection) -> str | None:
    cur = conn.execute("SELECT MAX(date) FROM port_daily_stats")
    row = cur.fetchone()
    return row[0] if row else None


def build_where(since: str | None) -> str:
    port_list = ",".join(f"'{p}'" for p in PORTS)
    clauses = [f"portid IN ({port_list})"]
    if since:
        clauses.append(f"date >= DATE '{since}'")
    return " AND ".join(clauses)


def export_csv(conn: sqlite3.Connection, path: Path):
    import csv
    cur = conn.execute(f"SELECT {','.join(FIELDS)} FROM port_daily_stats ORDER BY date, portid")
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(FIELDS)
        w.writerows(cur.fetchall())
    print(f"OK CSV written: {path}  ({path.stat().st_size/1024:.1f} KB)")


def summary(conn: sqlite3.Connection):
    print("\n" + "=" * 60)
    print(" PORTWATCH LOCAL DB SUMMARY")
    print("=" * 60)
    for pid, pname in PORTS.items():
        cur = conn.execute(
            "SELECT COUNT(*), MIN(date), MAX(date), "
            "ROUND(AVG(portcalls),1), ROUND(AVG(\"import\"),1) "
            "FROM port_daily_stats WHERE portid=?", (pid,))
        n, d0, d1, avg_calls, avg_imp = cur.fetchone()
        print(f" {pname:14s} ({pid:8s})  rows={n:5d}  "
              f"{d0} -> {d1}  "
              f"avg_calls={avg_calls}  avg_import={avg_imp}")
    print("=" * 60)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--full", action="store_true",
                    help="Pull full history (since 2019-01-01)")
    ap.add_argument("--since", type=str, default=None,
                    help="Pull from this ISO date (overrides incremental)")
    ap.add_argument("--export", choices=["csv"], default=None,
                    help="After ingest, export table to CSV")
    args = ap.parse_args()

    conn = sqlite3.connect(DB_PATH)
    init_db(conn)

    if args.full:
        since = "2019-01-01"
    elif args.since:
        since = args.since
    else:
        latest = latest_date_in_db(conn)
        if latest:
            since = (date.fromisoformat(latest) - timedelta(days=2)).isoformat()
        else:
            since = (date.today() - timedelta(days=30)).isoformat()

    print(f"-> Fetching PortWatch since {since} for {list(PORTS.values())}")
    where = build_where(since)
    rows = fetch_all(where)
    n = upsert(conn, rows)
    print(f"OK Upserted {n} rows into {DB_PATH}")

    summary(conn)

    if args.export == "csv":
        export_csv(conn, DB_PATH.with_suffix(".csv"))

    conn.close()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n!! Interrupted.", file=sys.stderr)
        sys.exit(1)

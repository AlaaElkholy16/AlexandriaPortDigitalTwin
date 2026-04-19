"""
Accident rate analysis for Alexandria Port — applying the methodology of
Bye & Almklov (2019), "Normalization of maritime accident data using AIS",
Marine Policy 109: 103675.

Core idea:
    accident_rate = number_of_accidents / activity_measure

Activity measures (from AIS / port statistics):
    - port calls       (directly from PortWatch)
    - sailed distance  (from AIS trails — stub here, ready for AISStream)
    - hours of operation
    - number of vessels

This script:
    1. Loads PortWatch daily port-calls for Alexandria (port23) from
       portwatch.db (populated by portwatch_ingest.py).
    2. Loads accident records from accidents.csv if it exists; otherwise
       generates a realistic SYNTHETIC dataset representative of Alexandria
       approach-channel incidents (groundings, allisions, collisions) for
       2025-2026. Replace with EMSA / GISIS exports when available.
    3. Joins on date + vessel_type, computes accident rates per vessel
       category, and writes:
         - accident_rates_by_type.csv
         - accident_rates_monthly.csv
         - plots/   (matplotlib, PNG)

Run:
    pip install pandas matplotlib
    python accident_rate_analysis.py
"""
from __future__ import annotations

import csv
import random
import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path

try:
    import pandas as pd
except ImportError:
    print("Missing pandas. Install with:  pip install pandas")
    sys.exit(1)

# matplotlib is optional — CSV output always works
try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    HAVE_MPL = True
except Exception as e:
    print(f"[warn] matplotlib unavailable, skipping plots: {e.__class__.__name__}")
    HAVE_MPL = False

BASE = Path(__file__).parent
DB_PATH = BASE / "portwatch.db"
ACCIDENTS_CSV = BASE / "accidents.csv"
PLOTS_DIR = BASE / "plots"
PLOTS_DIR.mkdir(exist_ok=True)

VESSEL_TYPES = ["container", "dry_bulk", "general_cargo", "roro", "tanker"]

# Representative yearly incident baseline rates per vessel category
# (incidents per 1000 port calls). These are placeholder values used only
# when no real accident CSV is present. Replace with EMSA/GISIS figures.
SYNTHETIC_BASE_RATE_PER_1000 = {
    "container":     2.8,
    "dry_bulk":      4.1,
    "general_cargo": 3.3,
    "roro":          1.9,
    "tanker":        2.2,
}
SYNTHETIC_ACCIDENT_TYPES = ["grounding", "allision", "collision", "machinery_failure"]


# ---------------------------------------------------------------------------
# Load PortWatch activity data (the "denominator" of accident rate)
# ---------------------------------------------------------------------------
def load_portcalls() -> pd.DataFrame:
    if not DB_PATH.exists():
        sys.exit(f"[FATAL] {DB_PATH} not found. Run portwatch_ingest.py first.")
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql_query("""
        SELECT date, portcalls_container, portcalls_dry_bulk,
               portcalls_general_cargo, portcalls_roro, portcalls_tanker,
               portcalls
        FROM port_daily_stats
        WHERE portid = 'port23'
        ORDER BY date
    """, conn)
    conn.close()
    df["date"] = pd.to_datetime(df["date"])
    # Long-format: one row per (date, vessel_type)
    long = df.melt(
        id_vars=["date", "portcalls"],
        value_vars=[f"portcalls_{v}" for v in VESSEL_TYPES],
        var_name="vessel_type", value_name="calls",
    )
    long["vessel_type"] = long["vessel_type"].str.replace("portcalls_", "")
    return long[["date", "vessel_type", "calls"]]


# ---------------------------------------------------------------------------
# Load or synthesize accident data (the "numerator")
# ---------------------------------------------------------------------------
def load_or_synthesize_accidents(portcalls_long: pd.DataFrame) -> pd.DataFrame:
    if ACCIDENTS_CSV.exists():
        print(f"[OK] Real accident data found: {ACCIDENTS_CSV}")
        df = pd.read_csv(ACCIDENTS_CSV, parse_dates=["date"])
        return df
    print(f"[!] No {ACCIDENTS_CSV.name} present — generating SYNTHETIC accidents.")
    print("    Replace with EMSA / GISIS / national-registry data for final thesis.")
    random.seed(42)
    rows = []
    by_type = portcalls_long.groupby("vessel_type")["calls"].sum().to_dict()
    for vtype in VESSEL_TYPES:
        total_calls = by_type.get(vtype, 0)
        rate = SYNTHETIC_BASE_RATE_PER_1000[vtype] / 1000
        n = int(total_calls * rate)
        # Distribute n accidents across days weighted by daily call count
        daily = portcalls_long[portcalls_long["vessel_type"] == vtype]
        if daily["calls"].sum() == 0 or n == 0:
            continue
        weights = daily["calls"].values / daily["calls"].sum()
        chosen = random.choices(daily["date"].tolist(), weights=weights, k=n)
        for d in chosen:
            rows.append({
                "date": d,
                "vessel_type": vtype,
                "accident_type": random.choices(
                    SYNTHETIC_ACCIDENT_TYPES, weights=[0.35, 0.30, 0.20, 0.15])[0],
                "severity": random.choices(["minor", "serious", "major"],
                                           weights=[0.70, 0.25, 0.05])[0],
            })
    df = pd.DataFrame(rows)
    df.to_csv(ACCIDENTS_CSV, index=False)
    print(f"[OK] Synthetic accidents written to {ACCIDENTS_CSV} ({len(df)} rows)")
    return df


# ---------------------------------------------------------------------------
# Bye & Almklov normalization
# ---------------------------------------------------------------------------
def compute_rates(portcalls_long: pd.DataFrame, accidents: pd.DataFrame):
    # Per vessel type
    by_type_calls = portcalls_long.groupby("vessel_type")["calls"].sum()
    by_type_acc   = accidents.groupby("vessel_type").size()
    by_type = pd.DataFrame({
        "port_calls":        by_type_calls,
        "accidents":         by_type_acc,
    }).fillna(0)
    by_type["accidents"] = by_type["accidents"].astype(int)
    by_type["rate_per_1000_calls"] = (
        by_type["accidents"] / by_type["port_calls"] * 1000
    ).round(2)
    by_type = by_type.sort_values("rate_per_1000_calls", ascending=False)

    # Monthly time series
    portcalls_long["ym"] = portcalls_long["date"].dt.to_period("M").astype(str)
    accidents["ym"]      = pd.to_datetime(accidents["date"]).dt.to_period("M").astype(str)
    monthly_calls = portcalls_long.groupby("ym")["calls"].sum()
    monthly_acc   = accidents.groupby("ym").size()
    monthly = pd.DataFrame({
        "port_calls":  monthly_calls,
        "accidents":   monthly_acc,
    }).fillna(0)
    monthly["accidents"] = monthly["accidents"].astype(int)
    monthly["rate_per_1000_calls"] = (
        monthly["accidents"] / monthly["port_calls"] * 1000
    ).round(2)
    monthly = monthly.sort_index()

    # By severity
    by_sev = accidents.groupby(["vessel_type", "severity"]).size().unstack(fill_value=0)

    return by_type, monthly, by_sev


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------
def report(by_type, monthly, by_sev):
    by_type.to_csv(BASE / "accident_rates_by_type.csv")
    monthly.to_csv(BASE / "accident_rates_monthly.csv")
    by_sev.to_csv(BASE / "accident_severity_by_type.csv")

    print()
    print("=" * 60)
    print(" ACCIDENT RATES BY VESSEL CATEGORY (Bye & Almklov method)")
    print("=" * 60)
    print(by_type.to_string())
    print()
    print("=" * 60)
    print(" ACCIDENT COUNT BY VESSEL TYPE AND SEVERITY")
    print("=" * 60)
    print(by_sev.to_string())
    print()
    print("=" * 60)
    print(" MONTHLY RATE  (tail)")
    print("=" * 60)
    print(monthly.tail(8).to_string())

    # Plots
    if not HAVE_MPL:
        print("\n[info] Skipping plots (matplotlib unavailable). CSVs written.")
        return
    fig, ax = plt.subplots(figsize=(8, 4.5))
    by_type["rate_per_1000_calls"].plot(kind="bar", ax=ax,
        color=["#00d4ff", "#ffa726", "#ef5350", "#00ff9d", "#ab47bc"])
    ax.set_title("Accident rate per 1,000 port calls — Alexandria (Bye & Almklov)")
    ax.set_ylabel("Accidents per 1,000 calls")
    ax.set_xlabel("Vessel category")
    plt.xticks(rotation=30, ha="right")
    plt.tight_layout()
    p1 = PLOTS_DIR / "rate_by_vessel_type.png"
    plt.savefig(p1, dpi=130)
    plt.close()

    fig, ax = plt.subplots(figsize=(9, 4.5))
    monthly["rate_per_1000_calls"].plot(ax=ax, marker="o", color="#00d4ff")
    ax.set_title("Monthly accident rate — Alexandria")
    ax.set_ylabel("Accidents per 1,000 calls")
    ax.grid(alpha=.3)
    plt.xticks(rotation=45, ha="right")
    plt.tight_layout()
    p2 = PLOTS_DIR / "rate_monthly.png"
    plt.savefig(p2, dpi=130)
    plt.close()

    print(f"\n[OK] Plots -> {p1}, {p2}")


def main():
    print("[1/3] Loading PortWatch port calls (activity data)...")
    portcalls_long = load_portcalls()
    total_calls = int(portcalls_long["calls"].sum())
    print(f"      {len(portcalls_long)} rows, total port calls = {total_calls}")

    print("[2/3] Loading accident data...")
    accidents = load_or_synthesize_accidents(portcalls_long)
    print(f"      {len(accidents)} accident records")

    print("[3/3] Normalizing (Bye & Almklov)...")
    by_type, monthly, by_sev = compute_rates(portcalls_long, accidents)
    report(by_type, monthly, by_sev)


if __name__ == "__main__":
    main()

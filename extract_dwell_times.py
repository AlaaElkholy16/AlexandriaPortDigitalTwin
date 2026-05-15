"""
Extract vessel dwell times from 648 git snapshots of alexandria_live.json.

For each commit on origin/main that contains an auto-refresh snapshot:
  1. Read alexandria_live.json via `git show <hash>:alexandria_live.json`
  2. Record which vessels are at which berths
  3. Track each vessel across snapshots to compute actual dwell duration

Output:
  dwell_times.csv  — training dataset for the Handling Time Predictor (Model 1)
  anchorage_waits.csv — training dataset for ETA/Wait Prediction (Model 3)
"""
from __future__ import annotations

import csv
import json
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

BASE = Path(__file__).parent


def get_commit_hashes() -> list[str]:
    result = subprocess.run(
        ["git", "log", "origin/main", "--format=%H", "--grep=auto-refresh", "--reverse"],
        capture_output=True, text=True, cwd=str(BASE),
    )
    return [h.strip() for h in result.stdout.strip().splitlines() if h.strip()]


def read_snapshot(commit_hash: str) -> dict | None:
    result = subprocess.run(
        ["git", "show", f"{commit_hash}:alexandria_live.json"],
        capture_output=True, text=True, cwd=str(BASE),
    )
    if result.returncode != 0:
        return None
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return None


def parse_ts(ts_str: str) -> datetime:
    ts_str = ts_str.replace("Z", "+00:00")
    return datetime.fromisoformat(ts_str)


def main():
    print("Fetching commit hashes from origin/main ...")
    hashes = get_commit_hashes()
    print(f"Found {len(hashes)} auto-refresh commits to process.\n")

    if not hashes:
        print("ERROR: No commits found. Run 'git fetch origin' first.")
        sys.exit(1)

    # --- State tracking ---
    # berth_visits[imo] = {berth_id, first_seen, last_seen, vessel_info}
    berth_visits: dict[int, dict] = {}
    # anchorage_tracking[imo] = {first_seen_anchorage, vessel_info}
    anchorage_tracking: dict[int, dict] = {}

    completed_visits: list[dict] = []
    completed_waits: list[dict] = []

    prev_ts = None
    processed = 0

    for i, commit in enumerate(hashes):
        snap = read_snapshot(commit)
        if not snap:
            continue

        ts_str = snap.get("ts")
        if not ts_str:
            continue

        try:
            ts = parse_ts(ts_str)
        except (ValueError, TypeError):
            continue

        fleet = snap.get("fleet", [])

        current_at_berth: dict[int, dict] = {}
        current_at_anchorage: set[int] = set()

        for v in fleet:
            imo = v.get("imo")
            if not imo:
                continue
            imo = int(imo)

            berth = v.get("berth")
            operation = v.get("operation", "")
            loading_status = v.get("loading_status")

            if berth:
                current_at_berth[imo] = {
                    "berth_id": berth,
                    "name": v.get("name", ""),
                    "type": v.get("type", ""),
                    "dwt": v.get("dwt"),
                    "capacity": v.get("capacity"),
                    "loading_status": loading_status,
                    "operation": operation,
                    "from": v.get("from", ""),
                    "to": v.get("to", ""),
                }
            elif operation == "AT_ANCHORAGE":
                current_at_anchorage.add(imo)
                if imo not in anchorage_tracking:
                    anchorage_tracking[imo] = {
                        "first_seen": ts,
                        "name": v.get("name", ""),
                        "type": v.get("type", ""),
                        "dwt": v.get("dwt"),
                        "loading_status": loading_status,
                        "from": v.get("from", ""),
                    }

        # Check for vessels that LEFT a berth (were tracked, now gone)
        departed_imos = set(berth_visits.keys()) - set(current_at_berth.keys())
        for imo in departed_imos:
            visit = berth_visits.pop(imo)
            first = visit["first_seen"]
            last = visit["last_seen"]
            dwell_minutes = (last - first).total_seconds() / 60.0
            if dwell_minutes >= 30:  # at least 1 snapshot interval
                completed_visits.append({
                    "imo": imo,
                    "name": visit["name"],
                    "vessel_type": visit["type"],
                    "dwt": visit["dwt"],
                    "capacity": visit["capacity"],
                    "loading_status": visit["loading_status"],
                    "operation": visit["operation"],
                    "berth_id": visit["berth_id"],
                    "origin_port": visit["from"],
                    "dest_port": visit["to"],
                    "first_seen": first.isoformat(),
                    "last_seen": last.isoformat(),
                    "dwell_hours": round(dwell_minutes / 60.0, 2),
                })

        # Check for vessels that moved from anchorage to berth
        for imo in current_at_berth:
            if imo in anchorage_tracking:
                anch = anchorage_tracking.pop(imo)
                wait_minutes = (ts - anch["first_seen"]).total_seconds() / 60.0
                if wait_minutes >= 30:
                    completed_waits.append({
                        "imo": imo,
                        "name": anch["name"],
                        "vessel_type": anch["type"],
                        "dwt": anch["dwt"],
                        "loading_status": anch["loading_status"],
                        "origin_port": anch["from"],
                        "berth_assigned": current_at_berth[imo]["berth_id"],
                        "first_seen_anchorage": anch["first_seen"].isoformat(),
                        "docked_at": ts.isoformat(),
                        "wait_hours": round(wait_minutes / 60.0, 2),
                    })

        # Update / start tracking for vessels currently at berth
        for imo, info in current_at_berth.items():
            if imo in berth_visits:
                if berth_visits[imo]["berth_id"] == info["berth_id"]:
                    berth_visits[imo]["last_seen"] = ts
                else:
                    # Vessel moved to a different berth — close old visit
                    old = berth_visits[imo]
                    dwell_minutes = (old["last_seen"] - old["first_seen"]).total_seconds() / 60.0
                    if dwell_minutes >= 30:
                        completed_visits.append({
                            "imo": imo,
                            "name": old["name"],
                            "vessel_type": old["type"],
                            "dwt": old["dwt"],
                            "capacity": old["capacity"],
                            "loading_status": old["loading_status"],
                            "operation": old["operation"],
                            "berth_id": old["berth_id"],
                            "origin_port": old["from"],
                            "dest_port": old["to"],
                            "first_seen": old["first_seen"].isoformat(),
                            "last_seen": old["last_seen"].isoformat(),
                            "dwell_hours": round(dwell_minutes / 60.0, 2),
                        })
                    berth_visits[imo] = {**info, "first_seen": ts, "last_seen": ts}
            else:
                berth_visits[imo] = {**info, "first_seen": ts, "last_seen": ts}

        # Clean up anchorage tracking for vessels no longer visible
        gone_from_anchorage = set(anchorage_tracking.keys()) - current_at_anchorage - set(current_at_berth.keys())
        for imo in gone_from_anchorage:
            anchorage_tracking.pop(imo, None)

        processed += 1
        if processed % 50 == 0:
            print(f"  Processed {processed}/{len(hashes)} snapshots — "
                  f"{len(completed_visits)} dwell times, {len(completed_waits)} anchorage waits so far")

    # Write dwell times CSV
    dwell_path = BASE / "dwell_times.csv"
    if completed_visits:
        fields = list(completed_visits[0].keys())
        with open(dwell_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fields)
            writer.writeheader()
            writer.writerows(completed_visits)
        print(f"\nWrote {len(completed_visits)} dwell time records to {dwell_path}")
    else:
        print("\nWARNING: No completed dwell times found!")

    # Write anchorage waits CSV
    wait_path = BASE / "anchorage_waits.csv"
    if completed_waits:
        fields = list(completed_waits[0].keys())
        with open(wait_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fields)
            writer.writeheader()
            writer.writerows(completed_waits)
        print(f"Wrote {len(completed_waits)} anchorage wait records to {wait_path}")
    else:
        print("WARNING: No completed anchorage waits found!")

    # Summary stats
    if completed_visits:
        dwells = [v["dwell_hours"] for v in completed_visits]
        types = defaultdict(list)
        for v in completed_visits:
            types[v["vessel_type"]].append(v["dwell_hours"])
        ops = defaultdict(list)
        for v in completed_visits:
            ops[v["operation"]].append(v["dwell_hours"])

        print(f"\n{'='*60}")
        print(f"DWELL TIME SUMMARY")
        print(f"{'='*60}")
        print(f"  Total completed visits:  {len(completed_visits)}")
        print(f"  Unique vessels:          {len(set(v['imo'] for v in completed_visits))}")
        print(f"  Min dwell:               {min(dwells):.1f} hours")
        print(f"  Max dwell:               {max(dwells):.1f} hours")
        print(f"  Mean dwell:              {sum(dwells)/len(dwells):.1f} hours")
        print(f"  Median dwell:            {sorted(dwells)[len(dwells)//2]:.1f} hours")
        print(f"\n  By vessel type:")
        for t, vals in sorted(types.items()):
            print(f"    {t:6s}: {len(vals):3d} visits, avg {sum(vals)/len(vals):.1f}h")
        print(f"\n  By operation:")
        for op, vals in sorted(ops.items()):
            print(f"    {op:12s}: {len(vals):3d} visits, avg {sum(vals)/len(vals):.1f}h")

    if completed_waits:
        waits = [w["wait_hours"] for w in completed_waits]
        print(f"\n{'='*60}")
        print(f"ANCHORAGE WAIT SUMMARY")
        print(f"{'='*60}")
        print(f"  Total completed waits:   {len(completed_waits)}")
        print(f"  Min wait:                {min(waits):.1f} hours")
        print(f"  Max wait:                {max(waits):.1f} hours")
        print(f"  Mean wait:               {sum(waits)/len(waits):.1f} hours")

    # Still at berth (ongoing, not yet departed)
    print(f"\n  Still at berth (ongoing): {len(berth_visits)} vessels")
    print(f"  Still at anchorage:       {len(anchorage_tracking)} vessels")


if __name__ == "__main__":
    main()

"""Re-run OR-Tools CP-SAT berth allocation using current alexandria_live.json."""
import json, csv, pickle, time, os
from datetime import datetime, timedelta, timezone
from collections import defaultdict
from ortools.sat.python import cp_model

BASE = os.path.dirname(os.path.abspath(__file__))

TYPE_TERMINALS = {
    "CONT": ["ALEXANDRIA INTERNATIONAL CONTAINER TERMINAL",
             "ALEXANDRIA HANDLINGCARGOES AND CONTAINERSTERMINAL"],
    "BULK": ["COAL TERMINAL", "GRAIN TERMINAL", "FERTILIZERS TERMINAL"],
    "TANK": ["E.G.P.C.", "PETROLEUM TERMINAL"],
    "MPP":  ["FIRST ZONE TERMINAL", "SECOND ZONE - CRUISE TERMINAL",
             "SECOND ZONE - GENERAL CARGO TERMINAL",
             "ALEXANDRIA HANDLINGCARGOES AND CONTAINERSTERMINAL"],
    "CAR":  ["SECOND ZONE - CRUISE TERMINAL", "SECOND ZONE - GENERAL CARGO TERMINAL"],
}

def estimate_dwell(vtype, dwt):
    base = {"CONT": 18, "BULK": 28, "TANK": 20, "MPP": 22, "CAR": 16}
    h = base.get(vtype, 20)
    if dwt > 50000:
        h += 8
    elif dwt > 20000:
        h += 4
    return h

def main():
    now = datetime.now(timezone.utc)

    data = json.load(open(os.path.join(BASE, "alexandria_live.json"), encoding="utf-8"))
    with open(os.path.join(BASE, "exports", "berths.csv"), encoding="utf-8") as f:
        berths_raw = list(csv.DictReader(f))

    berth_terminal = {b["berth_id"]: b["terminal"] for b in berths_raw}

    occ = data.get("occupancy", [])
    at_berth_imos = {str(v.get("imo", "")) for v in occ}

    fleet = data.get("fleet", [])
    incoming = []
    for v in fleet:
        if str(v.get("imo", "")) in at_berth_imos:
            continue
        to = (v.get("to") or "").lower()
        if "alex" in to or "dekh" in to:
            progress = v.get("progress", 0) or 0
            eta_hours = max(1, int((1.0 - min(progress, 0.99)) * 48))
            incoming.append({
                "name": v["name"],
                "type": v.get("type", "MPP"),
                "imo": str(v["imo"]),
                "dwt": v.get("dwt", 0) or 0,
                "eta_hours": eta_hours,
            })

    incoming.sort(key=lambda x: x["eta_hours"])
    incoming = incoming[:20]

    if not incoming:
        print("No incoming vessels to schedule.")
        return

    print(f"Scheduling {len(incoming)} incoming vessels...")

    occupied_berths = {}
    for v in occ:
        bid = v["berth_id"]
        vtype = v.get("vtype", "MPP")
        free_in = {"CONT": 8, "BULK": 16, "TANK": 12, "MPP": 10, "CAR": 6}.get(vtype, 12)
        if bid not in occupied_berths or occupied_berths[bid] < free_in:
            occupied_berths[bid] = free_in

    model = cp_model.CpModel()
    HORIZON = 120

    starts = {}
    berth_assignments = {}
    for i, v in enumerate(incoming):
        terminals = TYPE_TERMINALS.get(v["type"], TYPE_TERMINALS["MPP"])
        compatible = [b["berth_id"] for b in berths_raw if b["terminal"] in terminals]
        if not compatible:
            compatible = [b["berth_id"] for b in berths_raw]
        v["_compatible"] = compatible
        v["_dwell"] = estimate_dwell(v["type"], v["dwt"])
        starts[i] = model.NewIntVar(v["eta_hours"], HORIZON, f"start_{i}")
        berth_assignments[i] = model.NewIntVarFromDomain(
            cp_model.Domain.FromValues(list(range(len(compatible)))), f"berth_{i}"
        )

    berth_intervals = defaultdict(list)
    for i, v in enumerate(incoming):
        for b_idx, berth_id in enumerate(v["_compatible"]):
            is_assigned = model.NewBoolVar(f"assign_{i}_{b_idx}")
            model.Add(berth_assignments[i] == b_idx).OnlyEnforceIf(is_assigned)
            model.Add(berth_assignments[i] != b_idx).OnlyEnforceIf(is_assigned.Not())
            interval = model.NewOptionalIntervalVar(
                starts[i], v["_dwell"], starts[i] + v["_dwell"],
                is_assigned, f"interval_{i}_{b_idx}"
            )
            berth_intervals[berth_id].append(interval)
            if berth_id in occupied_berths:
                model.Add(starts[i] >= occupied_berths[berth_id]).OnlyEnforceIf(is_assigned)

    for berth_id, intervals in berth_intervals.items():
        if len(intervals) > 1:
            model.AddNoOverlap(intervals)

    total_wait = []
    for i, v in enumerate(incoming):
        wait = model.NewIntVar(0, HORIZON, f"wait_{i}")
        model.Add(wait == starts[i] - v["eta_hours"])
        total_wait.append(wait)
    model.Minimize(sum(total_wait))

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = 10
    t0 = time.time()
    status = solver.Solve(model)
    solve_time = time.time() - t0

    status_name = {0: "UNKNOWN", 1: "MODEL_INVALID", 2: "FEASIBLE", 3: "INFEASIBLE", 4: "OPTIMAL"}
    print(f"Status: {status_name.get(status, status)}, Solve time: {solve_time:.2f}s")

    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        print("Solver failed — keeping previous schedule.")
        return

    schedule = []
    total_wait_h = 0
    for i, v in enumerate(incoming):
        start_h = solver.Value(starts[i])
        b_idx = solver.Value(berth_assignments[i])
        berth_id = v["_compatible"][b_idx]
        terminal = berth_terminal.get(berth_id, "Unknown")
        wait_h = start_h - v["eta_hours"]
        dwell_h = v["_dwell"]
        total_wait_h += wait_h
        start_time = now + timedelta(hours=start_h)
        end_time = start_time + timedelta(hours=dwell_h)
        schedule.append({
            "vessel": v["name"],
            "type": v["type"],
            "imo": v["imo"],
            "dwt": v["dwt"],
            "berth": berth_id,
            "terminal": terminal,
            "wait_h": wait_h,
            "dwell_h": dwell_h,
            "start_time": start_time,
            "end_time": end_time,
        })

    schedule.sort(key=lambda x: x["start_time"])
    result = {
        "snapshot_time": now,
        "status": status_name.get(status, "UNKNOWN"),
        "solve_time_s": round(solve_time, 2),
        "vessels_scheduled": len(schedule),
        "total_wait_h": round(total_wait_h, 1),
        "avg_wait_h": round(total_wait_h / len(schedule), 1) if schedule else 0,
        "schedule": schedule,
    }

    pkl_path = os.path.join(BASE, "models", "model2", "berth_allocation_result.pkl")
    with open(pkl_path, "wb") as f:
        pickle.dump(result, f)

    print(f"Done — {len(schedule)} vessels, avg wait {result['avg_wait_h']}h")

if __name__ == "__main__":
    main()

"""
Merge ShipNext's REAL berth polygons with the APA Operating Instructions PDF
attributes (length, draft, cargo type, operator).

Input:
    shipnext_port.json     — 71 Polygon features (terminals + berths) with real coords
    generate_berths.py     — PIERS dict with PDF attributes keyed by quay_no

Output:
    alexandria_berths.geojson  — 57 berth polygons with:
        geometry:   from ShipNext (real coordinates)
        properties: quay_no, name, type, length_m, draft_m, operator, terminal,
                    terminalID, berthID, coords_status="shipnext_authoritative"

This replaces the placeholder version generated from pier anchors.
"""
import json
import re
from pathlib import Path

BASE = Path(__file__).parent
IN_SHIPNEXT = BASE / "shipnext_port.json"
OUT = BASE / "alexandria_berths.geojson"

# ── PDF attributes (verbatim from generate_berths.py) ─────────────────────
# quay_no -> (length_m, draft_m, type, operator)
PDF_ATTR = {}
for group in [
    # Alexandria
    [("1/5",  103, 6.5, "general_cargo", None),
     ("2/5",  104, 6.5, "general_cargo", None),
     ("3/5",  103, 6.5, "general_cargo", None),
     ("4/5",  100, 6.5, "general_cargo", None)],    # ShipNext has '5/4' not in PDF
    [("6",    97,  6.5, "maintenance",   None),
     ("7",    98,  6.5, "maintenance",   None),
     ("8",    98,  6.5, "maintenance",   None)],
    [("9",    70,  6.5, "general_cargo", None)],
    [("10",  135,  8.0, "general_cargo", None)],
    [("11",  127,  9.0, "general_cargo", None)],
    [("12",  100,  9.0, "general_cargo", None)],
    [("13",  144,  9.0, "general_cargo", None)],
    [("14",  183, 10.0, "general_cargo", None)],
    [("16",  160, 12.0, "passenger",    None),
     ("18",  160, 12.0, "passenger",    None),
     ("20",  160, 12.0, "passenger",    None),
     ("22",  160, 12.0, "passenger",    None),
     ("24",  158, 12.0, "passenger",    None)],
    [("25",  165, 10.0, "roro_general", None),
     ("26",  165, 10.0, "roro_general", None)],
    [("27",  150, 12.0, "roro",         None),
     ("28",  150, 12.0, "roro",         None)],
    [("35",  114, 10.0, "general_cargo", None),
     ("36",  114, 10.0, "general_cargo", None),
     ("37",  113, 10.0, "general_cargo", None)],
    [("38",  114, 10.0, "general_cargo", None)],
    [("39",  155, 10.0, "roro",         None),
     ("40",  155, 10.0, "roro",         None)],
    [("41",  175, 10.0, "general_cargo", None)],
    [("42",  115,  7.5, "general_cargo", None),
     ("43",  115,  7.5, "general_cargo", None)],
    [("44",  154,  6.5, "general_cargo", None)],
    [("45",  116,  6.5, "military",     None)],
    [("46",  204, 10.0, "military",     None),
     ("47",  203, 10.0, "military",     None)],
    [("49",  177, 14.0, "container",    "APMT"),
     ("51",  177, 14.0, "container",    "APMT"),
     ("53",  176, 14.0, "container",    "APMT")],
    [("54",  160, 14.0, "container",    "APMT")],
    [("55",  294, 15.0, "general_cargo", "under_development"),
     ("56",  294, 15.0, "general_cargo", "under_development"),
     ("57",  294, 15.0, "general_cargo", "under_development"),
     ("58",  294, 15.0, "general_cargo", "under_development"),
     ("59",  294, 16.0, "general_cargo", "under_development"),
     ("60",  294, 17.0, "general_cargo", "under_development"),
     ("61",  294, 17.5, "general_cargo", "under_development"),
     ("62",  292, 17.5, "general_cargo", "under_development")],
    [("63",  150, 10.0, "coal",         None),
     ("64",  150, 10.0, "coal",         None)],
    [("65",  161, 10.0, "general_cargo_bulk", None),
     ("66",  161, 11.0, "general_cargo_bulk", None),
     ("67",  161, 11.0, "general_cargo_bulk", None),
     ("68",  162, 12.0, "general_cargo_bulk", None)],
    [("71",  280,  9.0, "container",    "HBH")],
    [("72",  380, 12.0, "container",    "HBH")],
    [("73",  280, 12.0, "container",    "HBH")],
    [("81",  175, 10.0, "container",    "HBH")],
    [("82",  190, 10.0, "general_cargo", None)],
    [("84",  160, 10.0, "grains",       None)],
    [("85",  250, 13.0, "grain_timber", None)],
    [("85/1", 360, 13.0, "grains",      None)],
    [("85/2",  75, 10.0, "grains",      None)],
    [("86",   133,  7.0, "livestock",   None)],
    [("87/1", 178, 10.0, "petroleum",   None),
     ("87/2", 177, 10.0, "petroleum",   None)],
    [("87/3",  80, 12.0, "petroleum",   None)],
    [("87/4",  80, 12.0, "petroleum",   None)],
    [("87/5", 350, 12.0, "petroleum",   None)],
]:
    for qno, length, draft, btype, op in group:
        PDF_ATTR[qno] = (length, draft, btype, op)


def normalize_shipnext_name(name: str) -> str | None:
    """Turn 'BERTH NO 49' / 'BERTH NO.85/1' / 'BERTH 24' into '49', '85/1', '24'.

    Notation normalisation: ShipNext writes '5/1, 5/2, 5/3, 5/4' where the
    APA PDF writes '1/5, 2/5, 3/5'. Same physical berths, different ordering
    convention ('group 5 / berth N' vs 'berth N / of group 5'). Flip them
    so both match.
    """
    m = re.search(r"BERTH\s*(?:NO\.?\s*)?([\d/]+)", name.upper())
    if not m:
        return None
    qno = m.group(1)
    # Flip '5/N' -> 'N/5' to match PDF ordering
    if qno.startswith("5/") and qno[2:].isdigit() and int(qno[2:]) <= 9:
        return f"{qno[2:]}/5"
    return qno


def main():
    sn = json.loads(IN_SHIPNEXT.read_text(encoding="utf-8"))["data"]
    fc = sn["featureCollection"]

    # Build terminal lookup (features without berthID are terminals)
    terminal_name_by_id = {
        f["properties"]["terminalID"]: f["properties"]["name"]
        for f in fc["features"]
        if f["properties"].get("terminalID") and not f["properties"].get("berthID")
    }

    features_out = []
    matched, unmatched = 0, []
    for f in fc["features"]:
        p = f["properties"]
        # Only include actual BERTHS (have berthID), skip terminal polygons + port boundary
        if not p.get("berthID"):
            continue

        quay_no = normalize_shipnext_name(p["name"])
        attr = PDF_ATTR.get(quay_no) if quay_no else None
        if attr:
            length, draft, btype, operator = attr
            matched += 1
        else:
            length, draft, btype, operator = None, None, "general_cargo", None
            unmatched.append((p["name"], quay_no))

        terminal_name = terminal_name_by_id.get(p["terminalID"], "")
        is_dekheila = "DEKHEILA" in (terminal_name.upper() + p["name"].upper())

        features_out.append({
            "type": "Feature",
            "geometry": f["geometry"],
            "properties": {
                "quay_no":        quay_no or p["name"],
                "name":           p["name"].title(),
                "shipnext_type":  p.get("type"),          # B-Dry / B-Wet
                "type":           btype,
                "length_m":       length,
                "draft_m":        draft,
                "operator":       operator,
                "terminal":       terminal_name,
                "terminalID":     p.get("terminalID"),
                "berthID":        p.get("berthID"),
                "port":           "Dekheila" if is_dekheila else "Alexandria",
                "coords_status":  "shipnext_authoritative",
            },
        })

    out = {
        "type": "FeatureCollection",
        "name": "alexandria_port_berths",
        "crs": {"type": "name", "properties": {"name": "EPSG:4326"}},
        "meta": {
            "source": "ShipNext public API (https://shipnext.com/api/v1/ports/public/alexandria-egaly-egy) + APA Operating Instructions PDF",
            "note":   "Geometry is authoritative from ShipNext (Leaflet polygons). Attributes merged from APA PDF by quay-number match.",
            "berth_count":     len(features_out),
            "matched_to_pdf":  matched,
            "unmatched":       len(unmatched),
        },
        "features": features_out,
    }

    OUT.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"OK wrote {OUT}  ({len(features_out)} berths, {OUT.stat().st_size/1024:.1f} KB)")
    print(f"  Matched to PDF attributes: {matched}")
    print(f"  Unmatched (missing from PDF): {len(unmatched)}")
    for name, qno in unmatched:
        print(f"    - {name}  (normalized={qno})")


if __name__ == "__main__":
    main()

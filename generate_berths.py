"""
Generator for alexandria_berths.geojson — 75 quay polygons sourced verbatim
from the Alexandria Port Authority Operating Instructions PDF (pp. 5-7).

Coordinates are PLACEHOLDER: each pier has an approximate anchor (eyeballed
on satellite imagery), a bearing, and berths are laid out sequentially along
it. Refine later in SASPlanet / QGIS by dragging each polygon to its real
quay location. Every feature has `coords_status = "placeholder"` so downstream
code (demo, AIS join) can flag unrefined berths.

Output is GeoJSON FeatureCollection with one rectangular polygon per berth:
    length_m along the pier bearing
    25m perpendicular (quay apron width)

Usage:
    python generate_berths.py
"""
import json
import math
from pathlib import Path

OUT = Path(__file__).parent / "alexandria_berths.geojson"

# ---------------------------------------------------------------------------
# Helpers: WGS-84 offset math (good enough for berth-scale placeholders)
# ---------------------------------------------------------------------------
R = 6371000  # Earth radius m

def offset(lon, lat, d_east_m, d_north_m):
    """Return (lon, lat) offset by given metres east/north."""
    dlat = d_north_m / R
    dlon = d_east_m / (R * math.cos(math.radians(lat)))
    return (lon + math.degrees(dlon), lat + math.degrees(dlat))

def rect_polygon(start_lon, start_lat, bearing_deg, length_m, width_m):
    """Build a rectangle starting at (start_lon,start_lat), extending
    length_m along bearing_deg, width_m perpendicular (to port side)."""
    br = math.radians(bearing_deg)
    # Along-pier unit vector (east, north)
    fx, fy = math.sin(br), math.cos(br)
    # Perpendicular to the LEFT (quay apron is landward side; sign is arbitrary)
    px, py = -math.cos(br), math.sin(br)

    p0 = (start_lon, start_lat)
    p1 = offset(start_lon, start_lat, fx * length_m, fy * length_m)
    p2 = offset(p1[0],   p1[1],       px * width_m,  py * width_m)
    p3 = offset(p0[0],   p0[1],       px * width_m,  py * width_m)
    return [list(p0), list(p1), list(p2), list(p3), list(p0)]

# ---------------------------------------------------------------------------
# Pier layout — anchor, bearing, and ordered berth list
# Berth tuple: (quay_no, length_m, draft_m, type, operator_or_notes)
# ---------------------------------------------------------------------------
PIERS = [
    # ---- ALEXANDRIA MAIN HARBOR ----------------------------------------
    {
        "name": "East Container Pier",
        "zone": "Z1_EAST_CONTAINER",
        "anchor": (29.8830, 31.1905),
        "bearing": 250,  # WSW along the eastern container quay
        "berths": [
            ("49",  177, 14.0, "container", "APMT"),
            ("51",  177, 14.0, "container", "APMT"),
            ("53",  176, 14.0, "container", "APMT"),
            ("54",  160, 14.0, "container", "APMT"),
        ],
    },
    {
        "name": "Passenger & RORO Pier",
        "zone": "Z2_PASSENGER_RORO",
        "anchor": (29.8820, 31.1882),
        "bearing": 260,
        "berths": [
            ("16", 160, 12.0, "passenger",     None),
            ("18", 160, 12.0, "passenger",     None),
            ("20", 160, 12.0, "passenger",     None),
            ("22", 160, 12.0, "passenger",     None),
            ("24", 158, 12.0, "passenger",     None),
            ("25", 165, 10.0, "roro_general",  None),
            ("26", 165, 10.0, "roro_general",  None),
            ("27", 150, 12.0, "roro",          None),
            ("28", 150, 12.0, "roro",          None),
        ],
    },
    {
        "name": "Central General Cargo Pier",
        "zone": "Z3_GENERAL_CENTRAL",
        "anchor": (29.8800, 31.1858),
        "bearing": 255,
        "berths": [
            ("35", 114, 10.0, "general_cargo", None),
            ("36", 114, 10.0, "general_cargo", None),
            ("37", 113, 10.0, "general_cargo", None),
            ("38", 114, 10.0, "general_cargo", None),
            ("39", 155, 10.0, "roro",          None),
            ("40", 155, 10.0, "roro",          None),
            ("41", 175, 10.0, "general_cargo", None),
            ("42", 115,  7.5, "general_cargo", None),
            ("43", 115,  7.5, "general_cargo", None),
            ("44", 154,  6.5, "general_cargo", None),
        ],
    },
    {
        "name": "South Quay (Old General Cargo)",
        "zone": "Z4_OLD_GENERAL",
        "anchor": (29.8780, 31.1835),
        "bearing": 260,
        "berths": [
            ("1/5",  103,  6.5, "general_cargo", None),
            ("2/5",  104,  6.5, "general_cargo", None),
            ("3/5",  103,  6.5, "general_cargo", None),
            ("6",     97,  6.5, "maintenance",   None),
            ("7",     98,  6.5, "maintenance",   None),
            ("8",     98,  6.5, "maintenance",   None),
            ("9",     70,  6.5, "general_cargo", None),
            ("10",   135,  8.0, "general_cargo", None),
            ("11",   127,  9.0, "general_cargo", None),
            ("12",   100,  9.0, "general_cargo", None),
            ("13",   144,  9.0, "general_cargo", None),
            ("14",   183, 10.0, "general_cargo", None),
        ],
    },
    {
        "name": "Military Wharf",
        "zone": "Z5_MILITARY",
        "anchor": (29.8620, 31.1820),
        "bearing": 270,
        "berths": [
            ("45", 116,  6.5, "military", None),
            ("46", 204, 10.0, "military", None),
            ("47", 203, 10.0, "military", None),
        ],
    },
    {
        "name": "New Western Quays (under development)",
        "zone": "Z6_NEW_WEST",
        "anchor": (29.8580, 31.1790),
        "bearing": 270,
        "berths": [
            ("55", 294, 15.0, "general_cargo", "under_development"),
            ("56", 294, 15.0, "general_cargo", "under_development"),
            ("57", 294, 15.0, "general_cargo", "under_development"),
            ("58", 294, 15.0, "general_cargo", "under_development"),
            ("59", 294, 16.0, "general_cargo", "under_development"),
            ("60", 294, 17.0, "general_cargo", "under_development"),
            ("61", 294, 17.5, "general_cargo", "under_development"),
            ("62", 292, 17.5, "general_cargo", "under_development"),
        ],
    },
    {
        "name": "Coal & Dirty-Bulk Terminal",
        "zone": "Z7_BULK",
        "anchor": (29.8550, 31.1760),
        "bearing": 270,
        "berths": [
            ("63", 150, 10.0, "coal",            None),
            ("64", 150, 10.0, "coal",            None),
            ("65", 161, 10.0, "general_cargo_bulk", None),
            ("66", 161, 11.0, "general_cargo_bulk", None),
            ("67", 161, 11.0, "general_cargo_bulk", None),
            ("68", 162, 12.0, "general_cargo_bulk", None),
        ],
    },
    {
        "name": "HBH Container Terminal",
        "zone": "Z8_HBH_CONTAINER",
        "anchor": (29.8500, 31.1740),
        "bearing": 270,
        "berths": [
            ("71", 280,  9.0, "container", "HBH"),
            ("72", 380, 12.0, "container", "HBH"),
            ("81", 175, 10.0, "container", "HBH"),
            ("82", 190, 10.0, "general_cargo", None),
        ],
    },
    {
        "name": "Grain & Livestock Quays",
        "zone": "Z9_GRAIN",
        "anchor": (29.8470, 31.1715),
        "bearing": 270,
        "berths": [
            ("84",   160, 10.0, "grains",   None),
            ("85",   250, 13.0, "grain_timber", None),
            ("85/1", 360, 13.0, "grains",   None),
            ("85/2",  75, 10.0, "grains",   None),
            ("86",   133,  7.0, "livestock", None),
        ],
    },
    {
        "name": "Petroleum Quays",
        "zone": "Z10_PETROLEUM",
        "anchor": (29.8430, 31.1685),
        "bearing": 270,
        "berths": [
            ("87/1", 178, 10.0, "petroleum", None),
            ("87/2", 177, 10.0, "petroleum", None),
            ("87/3",  80, 12.0, "petroleum", None),
            ("87/4",  80, 12.0, "petroleum", None),
            ("87/5", 350, 12.0, "petroleum", None),
        ],
    },
    {
        "name": "Molasses Berth",
        "zone": "Z11_SPECIALTY",
        "anchor": (29.8415, 31.1700),
        "bearing": 0,  # short stub
        "berths": [
            ("MOL", 38, 36.0, "molasses", None),
        ],
    },
    # ---- DEKHEILA PORT -------------------------------------------------
    {
        "name": "Dekheila Mining Pier",
        "zone": "DK1_MINING",
        "anchor": (29.8270, 31.1465),
        "bearing": 270,
        "berths": [
            ("90/1", 300, 17.0, "mining", None),
            ("90/2", 300, 20.0, "mining", None),
        ],
    },
    {
        "name": "Dekheila Medetab Wharf",
        "zone": "DK2_MEDETAB",
        "anchor": (29.8240, 31.1445),
        "bearing": 270,
        "berths": [
            ("91A", 200, 14.0, "general_cargo", "Medetab"),
            ("91/B", 200, 14.0, "general_cargo", "Medetab"),
            ("91/C", 200, 14.0, "general_cargo", "Medetab"),
        ],
    },
    {
        "name": "Dekheila Grain Terminals",
        "zone": "DK3_GRAIN",
        "anchor": (29.8200, 31.1420),
        "bearing": 270,
        "berths": [
            ("92",  330, 15.0, "grains",       "Venus"),
            ("94a", 350, 13.0, "grains",       "Venus"),
            ("94b", 350, 13.0, "grains",       "Uni Green"),
            ("94c", 350, 13.0, "grains",       "Cisco Trans"),
        ],
    },
    {
        "name": "Dekheila Container Terminal (HPH / ACCHC)",
        "zone": "DK4_CONTAINER",
        "anchor": (29.8170, 31.1395),
        "bearing": 270,
        "berths": [
            ("96a", 500, 13.0, "container", "ACCHC"),
            ("96b", 500, 13.0, "container", "ACCHC"),
            ("98",  580, 12.0, "container", "AICT (HPH)"),
        ],
    },
]


def build_feature(quay_no, pier, length_m, draft_m, btype, operator,
                  start_lon, start_lat):
    poly = rect_polygon(start_lon, start_lat, pier["bearing"],
                        length_m, 25.0)
    return {
        "type": "Feature",
        "geometry": {"type": "Polygon", "coordinates": [poly]},
        "properties": {
            "quay_no":       quay_no,
            "name":          f"Berth {quay_no}",
            "pier":          pier["name"],
            "zone":          pier["zone"],
            "type":          btype,
            "length_m":      length_m,
            "draft_m":       draft_m,
            "operator":      operator,
            "port":          "Dekheila" if pier["zone"].startswith("DK") else "Alexandria",
            "coords_status": "placeholder",
        },
    }


def main():
    features = []
    for pier in PIERS:
        lon, lat = pier["anchor"]
        for (qno, length, draft, btype, op) in pier["berths"]:
            features.append(build_feature(qno, pier, length, draft, btype, op, lon, lat))
            # Advance along the pier for the next berth (1m gap)
            br = math.radians(pier["bearing"])
            lon, lat = offset(lon, lat,
                              math.sin(br) * (length + 1),
                              math.cos(br) * (length + 1))

    fc = {
        "type": "FeatureCollection",
        "name": "alexandria_port_berths",
        "crs": {"type": "name", "properties": {"name": "EPSG:4326"}},
        "meta": {
            "source": "Alexandria Port Authority Operating Instructions PDF, pp. 5-7",
            "note":   "Quay attributes (length, draft, type) are authoritative. "
                      "Coordinates are placeholder — refine in SASPlanet/QGIS.",
            "berth_count": len(features),
        },
        "features": features,
    }

    OUT.write_text(json.dumps(fc, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"OK wrote {OUT}  ({len(features)} berths, {OUT.stat().st_size/1024:.1f} KB)")

    # Summary by type
    from collections import Counter
    by_type = Counter(f["properties"]["type"] for f in features)
    by_port = Counter(f["properties"]["port"] for f in features)
    print("\nBy port:", dict(by_port))
    print("By type:")
    for t, n in sorted(by_type.items(), key=lambda kv: -kv[1]):
        print(f"  {t:22s} {n}")


if __name__ == "__main__":
    main()

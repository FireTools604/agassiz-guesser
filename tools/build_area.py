#!/usr/bin/env python3
"""Build area.json for Agassiz Guesser from Overpass output (tools/area.overpassql).

    python3 tools/build_area.py tools/osm.json area.json

area.json:
  outline: [[ring, hole, ...], ...]   playable polygons as [lat, lng] (District minus exclusions)
  roads:   [[[lat, lng], ...], ...]    drivable road lines clipped to the playable area
  weights: [w, ...]                    sampling weight per road line (by road class)
"""
import json, math, re, sys, unicodedata
import shapely
from shapely.geometry import LineString, Polygon, MultiPolygon
from shapely.ops import linemerge, polygonize, unary_union

# Areas cut out of the District. Each entry: label, regex on the normalized OSM name.
EXCLUDE = [
    ("Village of Harrison Hot Springs", r"harrison hot springs"),
    ("Seabird Island IR",              r"seabird island"),
    ("Tseatah IR 2",                   r"tseatah\b.*\b2\b"),
    ("Scowlitz IR 1",                  r"scowlitz\b.*\b1\b"),
    ("Sts'ailes IR 6",                 r"sts ?ailes\b.*\b6\b"),
    ("Wahleach Island IR 2",           r"wahleach island\b.*\b2\b"),
    ("Lukseetsissum IR 9",             r"lukseetsissum\b.*\b9\b"),
]
# Relative chance of a round landing on each road class (per metre of road).
# Service roads include driveways and yards that rarely have Street View.
ROAD_WEIGHT = {"service": 0.25}
SIMPLIFY_M = 8            # outline/road simplification tolerance
DEG_LAT_M = 111_320


def norm(name):
    s = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode().lower()
    s = s.replace("indian reserve", "").replace(" ir ", " ")
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def rel_polygon(el):
    """Assemble a relation (or closed way) from `out body geom` into a (Multi)Polygon in lng/lat."""
    if el["type"] == "way":
        pts = [(g["lon"], g["lat"]) for g in el["geometry"]]
        return Polygon(pts) if len(pts) >= 4 else None
    lines = {"outer": [], "inner": []}
    for m in el.get("members", []):
        if m["type"] == "way" and m.get("geometry"):
            role = "inner" if m.get("role") == "inner" else "outer"
            lines[role].append(LineString([(g["lon"], g["lat"]) for g in m["geometry"]]))
    outer = unary_union(list(polygonize(linemerge(lines["outer"])))) if lines["outer"] else None
    if outer is None or outer.is_empty:
        return None
    if lines["inner"]:
        outer = outer.difference(unary_union(list(polygonize(linemerge(lines["inner"])))))
    return outer


def to_metres(geom, lat0):
    """Equirectangular projection around the District's latitude; plenty accurate at this scale."""
    k, c = DEG_LAT_M, math.cos(math.radians(lat0))
    return shapely.transform(geom, lambda a: a * [k * c, k]), (k, c)


def from_metres(geom, kc):
    k, c = kc
    return shapely.transform(geom, lambda a: a / [k * c, k])


def latlng(coords):
    return [[round(y, 5), round(x, 5)] for x, y in coords]


def main(src, dst):
    els = json.load(open(src))["elements"]
    district, exclusions, roads = None, {}, []
    print("Boundary features found:")
    for el in els:
        tags = el.get("tags", {})
        if "highway" in tags:
            roads.append(el)
            continue
        name = tags.get("name", "")
        n = norm(name)
        if tags.get("boundary") == "administrative" and re.fullmatch(r"(district of )?kent", n):
            district = rel_polygon(el)
            print(f"  DISTRICT  {el['type']}/{el['id']}  {name}")
            continue
        hit = next((label for label, rx in EXCLUDE if re.search(rx, n)), None)
        print(f"  {'EXCLUDE ' if hit else 'keep    '}  {el['type']}/{el['id']}  {name}" + (f"  -> {hit}" if hit else ""))
        if hit:
            poly = rel_polygon(el)
            if poly is not None:
                exclusions.setdefault(hit, []).append(poly)

    if district is None or district.is_empty:
        sys.exit("ERROR: District of Kent boundary not found in input.")
    missing = [label for label, _ in EXCLUDE if label not in exclusions]
    if missing:
        print("WARNING: no OSM feature matched:", ", ".join(missing))

    lat0 = district.centroid.y
    d_m, kc = to_metres(district, lat0)
    cut = unary_union([to_metres(p, lat0)[0] for ps in exclusions.values() for p in ps]) if exclusions else None
    area = d_m.difference(cut) if cut is not None else d_m
    area = area.buffer(0).simplify(SIMPLIFY_M, preserve_topology=True)
    polys = list(area.geoms) if isinstance(area, MultiPolygon) else [area]
    polys = [p for p in polys if p.area > 20_000]     # drop slivers under 2 ha
    print(f"District {d_m.area/1e6:.1f} km², playable {sum(p.area for p in polys)/1e6:.1f} km², {len(polys)} polygon(s)")
    for label, ps in exclusions.items():
        overlap = unary_union([to_metres(p, lat0)[0] for p in ps]).intersection(d_m).area / 1e6
        print(f"  removed {overlap:6.2f} km²  {label}")

    playable = MultiPolygon(polys)
    out_roads, weights, total = [], [], {}
    for el in roads:
        line = LineString([(g["lon"], g["lat"]) for g in el["geometry"]])
        clipped = to_metres(line, lat0)[0].intersection(playable)
        parts = getattr(clipped, "geoms", [clipped])
        for part in parts:
            if part.is_empty or part.geom_type != "LineString" or part.length < 30:
                continue
            part = part.simplify(SIMPLIFY_M / 2)
            hw = el["tags"]["highway"]
            out_roads.append(latlng(from_metres(part, kc).coords))
            weights.append(ROAD_WEIGHT.get(hw, 1))
            total[hw] = total.get(hw, 0) + part.length
    print(f"Roads: {len(out_roads)} lines, {sum(total.values())/1000:.0f} km")
    for hw, m in sorted(total.items(), key=lambda kv: -kv[1]):
        print(f"  {hw:15s} {m/1000:7.1f} km")

    outline = []
    for p in polys:
        g = from_metres(p, kc)
        outline.append([latlng(g.exterior.coords)] + [latlng(r.coords) for r in g.interiors])
    json.dump({"outline": outline, "roads": out_roads, "weights": weights}, open(dst, "w"), separators=(",", ":"))
    print(f"Wrote {dst}")


if __name__ == "__main__":
    main(*sys.argv[1:3])

# /// script
# requires-python = ">=3.11"
# dependencies = ["shapely"]
# ///
"""The ground of the map as polygons, from OpenStreetMap: what the BGT gives the Dutch map, built from OSM tags.

OpenStreetMap has no wall-to-wall surface map like the BGT, so this lays it down in layers: a plain lawn-or-paving
base under everything (scripts/build_ground.py decides which from the aerial photo), the land-use areas, water
(the Ottawa River, cut to the map from its multipolygon), and on top the roads and paths, buffered from their centre
lines to their width. Roads come from the highway lines rather than from areas, as few streets in Ottawa have one.

Output: data/build/surfaces.json
  polygons   [{"id", "style" (a key of build_ground.STYLES), "road" (drawn as a crisp polygon), "bridge",
               "group" ("road", "cycle", "foot" for the traffic), "prio" (painting order), "rings" in UTM meters}]
  buildings  [{"id", "tags", "rings" in UTM meters}]
  trees      [[x, y], ...] single trees mapped in OSM (natural=tree)
"""

import json

from shapely.geometry import LineString, Point, Polygon, box
from shapely.ops import polygonize, unary_union

from config import BUILD, RAW, XMAX, XMIN, YMAX, YMIN
from geo import wgs_to_utm

PAD = 15
AREA = box(XMIN - PAD, YMIN - PAD, XMAX + PAD, YMAX + PAD)

# Paint order: lower first. Within a level, larger polygons first, so a park inside a residential area wins.
P_BASE, P_LAND, P_WATER, P_PATH, P_ROAD, P_BUILDING, P_BRIDGE = -1, 0, 0.2, 0.4, 0.6, 1, 2


def xy(pts):
    return [wgs_to_utm(p["lat"], p["lon"]) for p in pts]


def land_style(t):
    """A ground style for an area's tags, or None when the area doesn't paint the ground."""
    g = lambda k: t.get(k, "")
    if g("natural") == "water" or g("waterway") in ("riverbank", "dock") or g("landuse") in ("reservoir", "basin") \
            or g("leisure") == "swimming_pool" or g("water"):
        return "water"
    if g("place") in ("island", "islet"):
        return "grass"
    if g("natural") == "wetland":
        return "marsh"
    if g("natural") == "wood" or g("landuse") == "forest":
        return "forest"
    if g("natural") == "scrub":
        return "shrub"
    if g("natural") == "heath":
        return "heath"
    if g("natural") in ("beach", "sand") or g("golf") == "bunker" or g("leisure") == "playground" and g("surface") == "sand":
        return "sand"
    if g("natural") in ("bare_rock", "rock", "scree", "shingle"):
        return "gravel"
    if g("leisure") == "track" or g("sport") == "running" and g("leisure") == "track":
        return "clinkers"
    if g("amenity") in ("parking", "bicycle_parking", "fuel") or g("parking") in ("surface", "lane") or g("amenity") == "bus_station":
        return "asphalt"
    if g("area:highway") or g("highway") in ("pedestrian", "footway", "service", "residential", "platform") and g("area") == "yes":
        return "tiles" if g("highway") == "pedestrian" or g("area:highway") == "pedestrian" else "asphalt"
    if g("man_made") in ("pier", "breakwater", "groyne", "quay"):
        return "bridge" if g("man_made") == "pier" else "concrete"
    if g("man_made") in ("wastewater_plant", "water_works"):
        return "yard"
    if g("landuse") in ("farmland", "allotments"):
        return "field"
    if g("landuse") in ("orchard", "vineyard", "plant_nursery"):
        return "orchard"
    if g("landuse") == "meadow" or g("natural") == "grassland":
        return "meadow"
    if g("landuse") in ("grass", "village_green", "recreation_ground", "cemetery", "greenfield") or g("amenity") == "grave_yard" \
            or g("leisure") in ("park", "garden", "common", "dog_park", "golf_course", "pitch", "playground", "nature_reserve") \
            or g("golf") in ("fairway", "green", "tee", "rough"):
        return "grass"
    if g("landuse") in ("railway",):
        return "gravel"
    if g("landuse") in ("construction", "brownfield", "landfill", "quarry"):
        return "dirt"
    if g("landuse") in ("residential", "commercial", "retail", "industrial", "institutional", "religious", "education") \
            or g("amenity") in ("school", "university", "college", "hospital", "place_of_worship", "community_centre", "library"):
        return "yard"
    return None


# Road widths in meters when OSM gives neither width nor lanes.
ROAD_WIDTH = {
    "motorway": 11, "trunk": 11, "primary": 13, "secondary": 11, "tertiary": 9, "unclassified": 7, "residential": 7.5,
    "living_street": 5.5, "service": 4.5, "motorway_link": 6, "trunk_link": 6, "primary_link": 6, "secondary_link": 6,
    "tertiary_link": 6, "busway": 7.5, "cycleway": 2.6, "footway": 1.8, "path": 1.6, "pedestrian": 5, "track": 3.2,
    "steps": 2.2, "bridleway": 2.5, "construction": 6,
}
SERVICE_WIDTH = {"parking_aisle": 6, "driveway": 3.2, "alley": 4, "drive-through": 3.5}
SURFACE = {
    "asphalt": "asphalt", "paved": "asphalt", "chipseal": "asphalt", "concrete": "concrete", "concrete:plates": "concrete",
    "paving_stones": "concrete_pavers", "sett": "clinkers", "brick": "clinkers", "unhewn_cobblestone": "clinkers",
    "cobblestone": "clinkers", "gravel": "gravel", "fine_gravel": "gravel", "compacted": "gravel", "pebblestone": "gravel",
    "dirt": "dirt", "ground": "dirt", "earth": "dirt", "mud": "dirt", "unpaved": "dirt", "grass": "dirt", "woodchips": "dirt",
    "sand": "sand", "wood": "bridge", "metal": "bridge", "grass_paver": "grass_pavers",
}
MOTOR = {"motorway", "trunk", "primary", "secondary", "tertiary", "unclassified", "residential", "living_street", "service",
         "busway", "construction"}


def number(s):
    try:
        return float(str(s).split(";")[0].replace("m", "").strip())
    except ValueError:
        return None


def road(t):
    """(style, width, group, prio) for a highway way, or None."""
    hw = t.get("highway", "")
    base = hw.removesuffix("_link") if hw.endswith("_link") else hw
    if hw not in ROAD_WIDTH or t.get("area") == "yes" or t.get("tunnel") in ("yes", "building_passage", "culvert"):
        return None
    if number(t.get("layer", 0) or 0) is not None and (number(t.get("layer", 0) or 0) or 0) < 0:
        return None
    width = number(t.get("width")) if t.get("width") else None
    if not width and t.get("lanes") and base in MOTOR:
        lanes = number(t["lanes"]) or 2
        width = lanes * 3.4 + (1.5 if base in ("primary", "secondary") else 0)
    if not width:
        width = SERVICE_WIDTH.get(t.get("service"), ROAD_WIDTH[hw]) if hw == "service" else ROAD_WIDTH[hw]
    width = min(max(width, 1.0), 30.0)
    surface = t.get("surface")
    if base in MOTOR:
        style = SURFACE.get(surface, "motorway" if base in ("motorway", "trunk") else "asphalt")
        group, prio = "road", P_ROAD
    elif hw == "cycleway" or t.get("bicycle") == "designated" and hw in ("path", "footway"):
        style = SURFACE.get(surface, "asphalt")
        group, prio = "cycle", P_PATH
    elif hw == "track":
        style = SURFACE.get(surface, "gravel")
        group, prio = "cycle", P_PATH
    else:
        # Sidewalks are concrete; a path through a park without a surface is a trodden one.
        default = "concrete" if t.get("footway") in ("sidewalk", "crossing") or hw in ("footway", "steps", "pedestrian") else "dirt"
        style = SURFACE.get(surface, default)
        group, prio = "foot", P_PATH
    if t.get("bridge") not in (None, "no"):
        return "bridge", width, group, P_BRIDGE
    return style, width, group, prio


WATERWAY_WIDTH = {"river": 25, "canal": 10, "stream": 2.5, "ditch": 1.4, "drain": 1.6, "brook": 2}


def rings_of(geom):
    """The polygons of a shape as lists of rings (exterior first), rounded to centimetres."""
    ring = lambda r: [[round(x, 2), round(y, 2)] for x, y in r.coords]
    return [[ring(p.exterior), *map(ring, p.interiors)] for p in getattr(geom, "geoms", [geom])
            if p.geom_type == "Polygon" and not p.is_empty and p.area > 0.5]


def relation_polygon(rel):
    """A multipolygon relation's area, its outer and inner ways joined however they're split up."""
    outer, inner = [], []
    for m in rel.get("members", []):
        if m.get("type") != "way" or not m.get("geometry") or len(m["geometry"]) < 2:
            continue
        line = LineString(xy(m["geometry"]))
        (inner if m.get("role") == "inner" else outer).append(line)
    if not outer:
        return None
    polys = list(polygonize(unary_union(outer)))
    if not polys:
        return None
    shape = unary_union(polys)
    holes = list(polygonize(unary_union(inner))) if inner else []
    if holes:
        shape = shape.difference(unary_union(holes))
    return shape


def way_polygon(w):
    pts = xy(w["geometry"])
    if len(pts) < 4 or pts[0] != pts[-1]:
        return None
    return Polygon(pts).buffer(0)


def main():
    els = json.loads((RAW / "osm_ground.json").read_text())["elements"]
    polygons, buildings, trees = [], [], []
    polygons.append({"id": "base", "style": "yard", "road": False, "prio": P_BASE, "rings": rings_of(AREA)[0]})
    n_rel = 0
    for el in els:
        t = el.get("tags", {})
        kind = el["type"]
        if kind == "node":
            if t.get("natural") == "tree":
                x, y = wgs_to_utm(el["lat"], el["lon"])
                if AREA.contains(Point(x, y)):
                    trees.append([round(x, 2), round(y, 2)])
            continue
        if not t:
            continue
        shape = None
        is_building = ("building" in t and t["building"] != "no") and t.get("location") != "underground"
        style = land_style(t)
        if kind == "way" and el.get("geometry"):
            if t.get("waterway") in WATERWAY_WIDTH and not style and t.get("tunnel") not in ("yes", "culvert"):
                line = LineString(xy(el["geometry"]))
                width = number(t.get("width")) or WATERWAY_WIDTH[t["waterway"]]
                shape = line.buffer(width / 2, cap_style="round").intersection(AREA)
                polygons += [{"id": f"w{el['id']}", "style": "ditch" if t["waterway"] in ("ditch", "drain") else "water",
                              "road": False, "prio": P_WATER, "rings": r} for r in rings_of(shape)]
                continue
            if t.get("railway") in ("rail", "light_rail", "subway", "tram") and t.get("tunnel") != "yes":
                line = LineString(xy(el["geometry"]))
                shape = line.buffer(2.0 if t["railway"] == "tram" else 3.0, cap_style="flat").intersection(AREA)
                bridge = t.get("bridge") not in (None, "no")
                polygons += [{"id": f"w{el['id']}", "style": "bridge" if bridge else "gravel", "road": True, "bridge": bridge,
                              "prio": P_BRIDGE if bridge else P_PATH, "rings": r} for r in rings_of(shape)]
                continue
            if t.get("barrier") in ("wall", "retaining_wall") or t.get("man_made") in ("pier", "breakwater", "groyne") \
                    and el["geometry"][0] != el["geometry"][-1]:
                line = LineString(xy(el["geometry"]))
                width = number(t.get("width")) or (3.0 if t.get("man_made") else 0.5)
                shape = line.buffer(width / 2, cap_style="flat").intersection(AREA)
                style = "bridge" if t.get("man_made") == "pier" else "concrete"
                polygons += [{"id": f"w{el['id']}", "style": style, "road": style == "bridge", "bridge": style == "bridge",
                              "prio": P_BRIDGE, "rings": r} for r in rings_of(shape)]
                continue
            shape = way_polygon(el)
        elif kind == "relation" and t.get("type") in ("multipolygon", "boundary"):
            if not (is_building or style):
                continue
            shape = relation_polygon(el)
            n_rel += 1
        if shape is None or shape.is_empty or not shape.intersects(AREA):
            continue
        shape = shape.intersection(AREA)
        if is_building:
            buildings += [{"id": f"{kind[0]}{el['id']}", "tags": t, "rings": r} for r in rings_of(shape)]
            polygons += [{"id": f"{kind[0]}{el['id']}", "style": "building", "road": False, "prio": P_BUILDING, "rings": r}
                         for r in rings_of(shape)]
        elif style:
            prio = P_WATER if style == "water" else P_BRIDGE if style == "bridge" else (
                P_PATH if style in ("asphalt", "tiles") else P_LAND)
            road_like = style in ("asphalt", "tiles", "bridge")
            polygons += [{"id": f"{kind[0]}{el['id']}", "style": style, "road": road_like, "bridge": style == "bridge",
                          "group": "road" if t.get("amenity") == "parking" else None, "prio": prio, "rings": r}
                         for r in rings_of(shape)]

    n_roads = 0
    for w in json.loads((RAW / "osm_roads.json").read_text())["elements"]:
        if w["type"] != "way" or not w.get("geometry"):
            continue
        spec = road(w.get("tags", {}))
        if not spec:
            continue
        style, width, group, prio = spec
        line = LineString(xy(w["geometry"]))
        if not line.intersects(AREA):
            continue
        shape = line.buffer(width / 2, cap_style="flat", join_style="round").intersection(AREA)
        for r in rings_of(shape):
            polygons.append({"id": f"w{w['id']}", "style": style, "road": True, "bridge": style == "bridge", "group": group,
                             "prio": prio, "rings": r})
            n_roads += 1

    BUILD.mkdir(parents=True, exist_ok=True)
    (BUILD / "surfaces.json").write_text(json.dumps({"polygons": polygons, "buildings": buildings, "trees": trees},
                                                    separators=(",", ":")))
    from collections import Counter

    styles = Counter(p["style"] for p in polygons)
    print(f"{len(polygons)} polygons ({n_roads} road and path pieces, {n_rel} multipolygons), {len(buildings)} buildings, "
          f"{len(trees)} mapped trees; {dict(styles.most_common(12))}")


if __name__ == "__main__":
    main()

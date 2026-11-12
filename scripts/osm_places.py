"""Find named places in the OpenStreetMap download, so stories can point at "Britannia Yacht Club" instead of at
coordinates typed by hand.

find(pattern) gives the best match among the named features in data/raw/osm_ground.json and osm_landmarks.json:
(x, y) in UTM meters, the id of the building it is or stands in (as in scripts/build_surfaces.py), and its tags.
"""

import json
import re

from shapely import STRtree
from shapely.geometry import LineString, Point, Polygon

from config import BUILD, RAW
from geo import wgs_to_utm

_INDEX = None


def _shape(el):
    if el["type"] == "node":
        return Point(wgs_to_utm(el["lat"], el["lon"]))
    if el["type"] == "way" and el.get("geometry"):
        pts = [wgs_to_utm(p["lat"], p["lon"]) for p in el["geometry"]]
        if len(pts) >= 4 and pts[0] == pts[-1]:
            return Polygon(pts).buffer(0)
        return LineString(pts) if len(pts) > 1 else Point(pts[0])
    if el["type"] == "relation":
        from build_surfaces import relation_polygon

        return relation_polygon(el)
    return None


def _index():
    global _INDEX
    if _INDEX is None:
        named, seen = [], set()
        for f in ("osm_ground.json", "osm_landmarks.json"):
            path = RAW / f
            if not path.exists():
                continue
            for el in json.loads(path.read_text())["elements"]:
                t = el.get("tags", {})
                key = (el["type"], el["id"])
                if not t.get("name") or key in seen:
                    continue
                seen.add(key)
                shape = _shape(el)
                if shape is not None and not shape.is_empty:
                    named.append((t["name"], el, shape))
        blds = json.loads((BUILD / "surfaces.json").read_text())["buildings"]
        polys = [Polygon(b["rings"][0], b["rings"][1:]).buffer(0) for b in blds]
        _INDEX = (named, blds, polys, STRtree(polys))
    return _INDEX


def building_at(x, y, near=0.0):
    """The id of the building footprint at (or within `near` meters of) a point, or None."""
    _, blds, polys, tree = _index()
    p = Point(x, y)
    hits = [i for i in tree.query(p.buffer(near or 0.01)) if polys[i].distance(p) <= near]
    if not hits:
        return None
    return blds[min(hits, key=lambda i: polys[i].distance(p) * 1e6 + polys[i].area)]["id"]


def find(pattern, kind=None):
    """Best named match for a regular expression (case-insensitive); `kind` limits it to tags such as "building",
    "amenity=cafe". Prefers buildings and areas over points, then the smaller of equally good areas."""
    named, *_ = _index()
    rx = re.compile(pattern, re.I)
    hits = []
    for name, el, shape in named:
        if not rx.search(name):
            continue
        t = el.get("tags", {})
        if kind:
            k, _, v = kind.partition("=")
            if k not in t or (v and t[k] != v):
                continue
        exact = rx.fullmatch(name) is not None
        hits.append((not exact, el["type"] == "node", shape.area if shape.area else 1e9, name, el, shape))
    if not hits:
        raise KeyError(f"no OpenStreetMap feature named like {pattern!r}" + (f" with {kind}" if kind else ""))
    *_, name, el, shape = min(hits, key=lambda h: h[:3])
    c = shape.representative_point() if shape.geom_type in ("Polygon", "MultiPolygon") else shape.centroid
    t = el.get("tags", {})
    bid = f"{el['type'][0]}{el['id']}" if "building" in t else building_at(c.x, c.y)
    return {"x": c.x, "y": c.y, "building": bid, "tags": t, "name": name}

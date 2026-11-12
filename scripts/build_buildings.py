# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy", "mapbox-earcut", "pillow", "scipy", "shapely", "tifffile", "imagecodecs"]
# ///
"""Turn OpenStreetMap footprints and the HRDEM lidar into one triangle soup for the renderer (scripts/roof_fit.py fits
the roofs, in place of the 3D BAG's LoD2.2 solids that the Dutch map used).

Output (data/build/):
  buildings.bin   float32 per vertex: x, y, z, nx, ny, nz, u, v, kind, building index, atlas u, atlas v (-1: none)
  roof_atlas.png  aerial photo crops for the roofs
  buildings.json  per building: id, attributes, wall and roof colors
  footprints.json per building: footprint ring in local meters, for the ground and tree steps

Local coordinates: x east, y up, z south, origin at the area center, y=0 at the building's ground level.
"""

import hashlib
import json
import re
from collections import Counter, defaultdict

import mapbox_earcut as earcut
import numpy as np
from PIL import Image, ImageDraw
from shapely.geometry import Polygon

import dem
import roof_atlas
import roof_fit
from aerial import load_aerial, world_to_px
from config import BUILD, CX, CY, write_area

COLS = 12

# Roof vertex blocks and footprints per building index, for texturing roofs from the aerial photo afterwards.
ROOF_BLOCKS = defaultdict(list)
FOOTPRINT = {}
ATLAS_KINDS = {"house", "barn", "shed", "church", "school", "tank", "silo", "canopy"}

# OpenStreetMap building=* values, grouped into the kinds render/scene.js draws.
KINDS = {
    "church": "church", "chapel": "church", "cathedral": "church", "religious": "church", "temple": "church",
    "school": "school", "kindergarten": "school", "college": "school", "university": "school",
    "garage": "shed", "garages": "shed", "shed": "shed", "hut": "shed", "cabin": "shed", "boathouse": "shed",
    "kiosk": "shed", "toilets": "shed", "service": "shed", "transformer_tower": "shed",
    "carport": "canopy", "roof": "canopy", "grandstand": "canopy",
    "greenhouse": "greenhouse", "storage_tank": "tank", "silo": "silo",
    "warehouse": "barn", "industrial": "barn", "barn": "barn", "farm_auxiliary": "barn", "hangar": "barn",
}
COMMERCIAL = {"commercial", "retail", "supermarket", "industrial", "warehouse", "office", "yes", "civic", "public"}
# Heights for buildings the 2020 lidar doesn't show, when OpenStreetMap has no height or storeys either.
DEFAULT_HEIGHT = {"house": 7.0, "shed": 2.8, "canopy": 3.0, "barn": 6.0, "school": 8.0, "church": 10.0, "greenhouse": 4.0,
                  "tank": 5.0, "silo": 12.0}


def building_kind(tags):
    if tags.get("man_made") in ("storage_tank", "silo"):
        return "tank" if tags["man_made"] == "storage_tank" else "silo"
    return KINDS.get(tags.get("building", "yes"), "house")


def number(s):
    m = re.match(r"\s*([0-9.]+)", str(s or ""))
    return float(m.group(1)) if m else None


def tagged_height(tags, kind):
    h = number(tags.get("height"))
    if h:
        return h
    levels = number(tags.get("building:levels"))
    if levels:
        return levels * 3.0 + 0.5
    return DEFAULT_HEIGHT.get(kind, 7.0)


def year_of(tags):
    m = re.search(r"\b(1[6-9]\d\d|20\d\d)\b", tags.get("start_date", "") or tags.get("building:year", ""))
    return int(m.group(1)) if m else None


def ring_area(xy):
    return 0.5 * (np.dot(xy[:, 0], np.roll(xy[:, 1], 1)) - np.dot(xy[:, 1], np.roll(xy[:, 0], 1)))


def extrude(rings_xy, z0, z1):
    """Prism from a footprint (rings in RD x/y, first exterior). Yields (kind, rings in 3D)."""
    out = []
    for i, r in enumerate(rings_xy):
        r = np.asarray(r, dtype=np.float64)[:, :2]
        if np.allclose(r[0], r[-1]):
            r = r[:-1]
        # Exterior counter-clockwise, holes clockwise, so wall normals point outward.
        if (ring_area(r) > 0) != (i == 0):
            r = r[::-1]
        for a, b in zip(r, np.roll(r, -1, axis=0)):
            if np.linalg.norm(b - a) < 0.05:
                continue
            out.append((1, [np.array([[a[0], a[1], z0], [b[0], b[1], z0], [b[0], b[1], z1], [a[0], a[1], z1]])]))
    top = []
    for i, r in enumerate(rings_xy):
        r = np.asarray(r, dtype=np.float64)[:, :2]
        if np.allclose(r[0], r[-1]):
            r = r[:-1]
        if (ring_area(r) > 0) != (i == 0):
            r = r[::-1]
        top.append(np.column_stack([r, np.full(len(r), z1)]))
    out.append((2, top))
    return out


def long_axis(xy):
    """Direction (radians, RD x/y) of the longest footprint edge."""
    xy = np.asarray(xy)[:, :2]
    d = np.roll(xy, -1, axis=0) - xy
    i = int(np.argmax(np.hypot(d[:, 0], d[:, 1])))
    return float(np.arctan2(d[i, 1], d[i, 0]))


def newell_normal(pts):
    n = np.zeros(3)
    for a, b in zip(pts, np.roll(pts, -1, axis=0)):
        n += [(a[1] - b[1]) * (a[2] + b[2]), (a[2] - b[2]) * (a[0] + b[0]), (a[0] - b[0]) * (a[1] + b[1])]
    norm = np.linalg.norm(n)
    return n / norm if norm > 0 else None


def triangulate(rings):
    """rings: list of (N,3) arrays in RD meters, first is the exterior. Returns (M,3,3) triangles."""
    n = newell_normal(rings[0])
    if n is None:
        return np.zeros((0, 3, 3)), None
    drop = int(np.argmax(np.abs(n)))
    keep = [i for i in range(3) if i != drop]
    pts = np.concatenate(rings)
    ends = np.cumsum([len(r) for r in rings]).astype(np.uint32)
    idx = earcut.triangulate_float64(pts[:, keep], ends)
    tris = pts[idx].reshape(-1, 3, 3)
    # earcut does not keep the winding; flip triangles that face away from the surface normal.
    tn = np.cross(tris[:, 1] - tris[:, 0], tris[:, 2] - tris[:, 0])
    flip = (tn @ n) < 0
    tris[flip] = tris[flip][:, [0, 2, 1]]
    return tris, n


def stable_rand(key, salt=""):
    return int(hashlib.md5((key + salt).encode()).hexdigest()[:8], 16) / 0xFFFFFFFF


def emit(verts, surfaces, bi, ground, center_u=False):
    """surfaces: iterable of (kind, rings in RD meters with absolute z). Appends vertices; returns roof triangles.

    center_u puts u=0 in the middle of each wall, so towers can draw a clock or louvres centered on a face."""
    roof_tris = []
    for kind, rings in surfaces:
        if kind == 0:
            continue  # the floor is never visible
        tris, n = triangulate(rings)
        if n is None or len(tris) == 0:
            continue
        if kind == 2:
            roof_tris.append(tris)
        # Wall pattern coordinates: u runs along the wall, v is height above ground.
        if kind == 1:
            h = np.array([n[1], -n[0]])
            h = h / (np.linalg.norm(h) or 1)
            u = tris[..., :2] @ h
            if center_u:
                u = u - 0.5 * (u.min() + u.max())
            uv = np.stack([u, tris[..., 2] - ground], axis=-1)
        else:
            uv = tris[..., :2] - [CX, CY]
        local = np.stack([tris[..., 0] - CX, tris[..., 2] - ground, CY - tris[..., 1]], axis=-1)
        normal = [n[0], n[2], -n[1]]
        block = np.concatenate(
            [local, np.broadcast_to(normal, local.shape), uv, np.full(local.shape[:2] + (1,), kind), np.full(local.shape[:2] + (1,), bi),
             np.full(local.shape[:2] + (2,), -1.0)],
            axis=-1,
        ).reshape(-1, COLS)
        verts.append(block)
        if kind == 2:
            ROOF_BLOCKS[bi].append((block, tris))
    return roof_tris


def ccw(ring):
    ring = np.asarray(ring, dtype=np.float64)
    return ring if ring_area(ring) > 0 else ring[::-1]


def rect_ring(cx, cy, w, d, ang):
    c, s = np.cos(ang), np.sin(ang)
    pts = [(-w / 2, -d / 2), (w / 2, -d / 2), (w / 2, d / 2), (-w / 2, d / 2)]
    return ccw([(cx + x * c - y * s, cy + x * s + y * c) for x, y in pts])


def ngon_ring(cx, cy, r, n, ang):
    return ccw([(cx + r * np.cos(ang + 2 * np.pi * (i + 0.5) / n), cy + r * np.sin(ang + 2 * np.pi * (i + 0.5) / n)) for i in range(n)])


def pyramid(ring, z0, apex):
    """Roof triangles from a footprint ring up to a point above its center."""
    ring = ccw(ring)
    cx, cy = ring.mean(0)
    return [(2, [np.array([[a[0], a[1], z0], [b[0], b[1], z0], [cx, cy, apex]])]) for a, b in zip(ring, np.roll(ring, -1, axis=0))]


def frustum(bottom, top, z0, z1):
    """Walls between two rings with the same vertex count, for tapered masts."""
    bottom, top = ccw(bottom), ccw(top)
    out = []
    for i in range(len(bottom)):
        j = (i + 1) % len(bottom)
        out.append((1, [np.array([[*bottom[i], z0], [*bottom[j], z0], [*top[j], z1], [*top[i], z1]])]))
    out.append((2, [np.column_stack([top, np.full(len(top), z1)])]))
    return out


def part(meta, pid, kind, axis, height, wall, roof, param=0.0):
    meta.append({"id": f"{pid}-{kind}", "kind": kind, "axis": round(float(axis), 4), "param": param, "height": height,
                 "wall_fixed": list(wall), "roof_fixed": list(roof), "rand": 0.5, "roof_rgb": None})
    return len(meta) - 1


def add_landmark_parts(verts, meta, pid, lm, ground, axis, roof_tris):
    """Towers, roof turrets and crosses that the lidar roof fit smooths away."""
    wall, roof = lm.get("wall", (150, 80, 62)), lm.get("roof", (70, 72, 80))
    if "tower" in lm:
        t = lm["tower"]
        ring = rect_ring(t["x"], t["y"], t["w"], t["w"], axis)
        bi = part(meta, pid, "tower", axis, t["top"], wall, roof, param=t["body"])
        emit(verts, extrude([ring], ground, ground + t["body"]), bi, ground, center_u=True)
        emit(verts, pyramid(ring, ground + t["body"], ground + t["top"]), bi, ground)
        if t.get("cross"):
            bi = part(meta, pid, "cross", axis, t["top"] + 2, (196, 170, 96), (196, 170, 96))
            top = ground + t["top"]
            emit(verts, extrude([rect_ring(t["x"], t["y"], 0.3, 0.3, axis)], top - 0.4, top + 2.2), bi, ground)
            emit(verts, extrude([rect_ring(t["x"], t["y"], 1.4, 0.3, axis)], top + 1.2, top + 1.5), bi, ground)
    if "turret" in lm:
        t = lm["turret"]
        ring = ngon_ring(t["x"], t["y"], t["r"], 8, axis)
        bi = part(meta, pid, "turret", axis, t["top"], (240, 238, 230), (62, 64, 72), param=t["body"])
        emit(verts, extrude([ring], ground + t["base"], ground + t["body"]), bi, ground, center_u=True)
        emit(verts, pyramid(ring, ground + t["body"], ground + t["top"]), bi, ground)
    if lm.get("bell") and roof_tris:
        # A small white bell turret on the highest point of the roof, as on the school.
        pts = np.concatenate(roof_tris).reshape(-1, 3)
        x, y, zmax = pts[np.argmax(pts[:, 2])]
        ring = rect_ring(x, y, 1.5, 1.5, axis)
        bi = part(meta, pid, "turret", axis, zmax - ground + 3.4, (240, 238, 230), (70, 74, 82), param=zmax - ground + 2.0)
        emit(verts, extrude([ring], zmax - 0.4, zmax + 2.0), bi, ground, center_u=True)
        emit(verts, pyramid(ring, zmax + 2.0, zmax + 3.4), bi, ground)


def add_masts(verts, meta, masts):
    for m in masts:
        bot = rect_ring(m["x"], m["y"], m["base"], m["base"], 0.3)
        top = rect_ring(m["x"], m["y"], m["tip"], m["tip"], 0.3)
        bi = part(meta, "mast", "mast", 0.3, m["height"], (184, 188, 192), (150, 154, 158), param=m["height"])
        emit(verts, frustum(bot, top, 0.0, m["height"]), bi, 0.0, center_u=True)


def main():
    write_area()
    aerial = load_aerial()
    landmarks = json.loads((BUILD / "landmarks.json").read_text()) if (BUILD / "landmarks.json").exists() else {}
    named = landmarks.get("buildings", {})
    verts = []
    meta = []
    footprints = []
    dsm, dtm = dem.load("dsm"), dem.load("dtm")
    surf = json.loads((BUILD / "surfaces.json").read_text())
    models = Counter()
    for b in surf["buildings"]:
        tags = b["tags"]
        if "building:part" in tags and "building" not in tags:
            continue  # parts would stack on their building
        poly = Polygon(b["rings"][0], b["rings"][1:]).buffer(0)
        if poly.geom_type != "Polygon" or poly.area < 6:
            continue
        kind = building_kind(tags)
        roof = roof_fit.fit(poly, dsm, dtm, fallback_height=tagged_height(tags, kind))
        if roof is None:
            continue
        if kind == "canopy":
            roof.update(model="flat", planes=[(0.0, 0.0, roof["ground"] + min(roof["height"], 4.0))], height=min(roof["height"], 4.0))
        models[roof["model"] if roof["lidar"] else "not in lidar"] += 1
        ground = roof["ground"]
        fp = np.array(poly.exterior.coords)[:-1]
        footprints.append([[round(x - CX, 2), round(CY - y, 2)] for x, y in fp])
        bi = len(meta)
        FOOTPRINT[bi] = fp
        roof_tris = emit(verts, roof_fit.surfaces(poly, roof), bi, ground)
        height = roof["height"]
        if kind == "house" and poly.area > 400 and height < 10 and tags.get("building") in COMMERCIAL:
            kind = "barn"
        pid = b["id"]
        lm = named.get(pid)
        if lm:
            kind = lm["style"]
        meta.append(
            {
                "id": pid,
                "kind": kind,
                "axis": round(long_axis(fp), 4),
                "glass": False,
                "year": year_of(tags),
                "floors": tags.get("building:levels"),
                "roof": roof["model"],
                "area": round(poly.area, 1),
                "height": round(height, 2),
                "roof_rgb": sample_roof(aerial, roof_tris),
                "rand": round(stable_rand(pid), 4),
                "osm": tags.get("name"),
            }
        )
        if lm:
            meta[-1].update({"name": lm["name"], "wall_fixed": lm.get("wall"), "roof_fixed": lm.get("roof"), "frame_fixed": lm.get("frame")})
            add_landmark_parts(verts, meta, pid, lm, ground, long_axis(fp), roof_tris)
    print(f"roofs: {dict(models)}")
    add_masts(verts, meta, landmarks.get("masts", []))
    texture_roofs(aerial, meta)
    arr = np.concatenate(verts).astype(np.float32) if verts else np.zeros((0, COLS), np.float32)
    arr.tofile(BUILD / "buildings.bin")
    (BUILD / "buildings.json").write_text(json.dumps(meta))
    (BUILD / "footprints.json").write_text(json.dumps(footprints))
    print(f"{len(meta)} buildings, {len(arr) // 3} triangles, {Counter(m['kind'] for m in meta)}")


def texture_roofs(aerial, meta):
    roofs = [
        (bi, FOOTPRINT[bi], [t for _, t in ROOF_BLOCKS[bi]])
        for bi in ROOF_BLOCKS
        if bi in FOOTPRINT and meta[bi]["kind"] in ATLAS_KINDS
    ]
    fixed = {bi: meta[bi]["roof_fixed"] for bi, _, _ in roofs if meta[bi].get("roof_fixed")}
    rects, size = roof_atlas.build(aerial, roofs, fixed)
    for bi, rect in rects.items():
        for block, _ in ROOF_BLOCKS[bi]:
            u, v = roof_atlas.atlas_uv(np.column_stack([block[:, 0] + CX, CY - block[:, 2]]), rect, size)
            block[:, 10], block[:, 11] = u, v
        meta[bi]["atlas"] = True
    shifts = [np.hypot(*r[0]) for r in rects.values()]
    print(f"roof atlas {size[0]}x{size[1]} for {len(rects)} buildings, mean photo shift {np.mean(shifts):.2f} m")


def sample_roof(aerial, roof_tris):
    """Median aerial color over the roof, shrunk a little to stay off the ground around it."""
    if not roof_tris:
        return None
    tris = np.concatenate(roof_tris)[..., :2]
    px = world_to_px(tris.reshape(-1, 2)).reshape(-1, 3, 2)
    x0, y0 = np.floor(px.min(axis=(0, 1))).astype(int) - 1
    x1, y1 = np.ceil(px.max(axis=(0, 1))).astype(int) + 2
    h, w = aerial.shape[:2]
    if x1 <= 0 or y1 <= 0 or x0 >= w or y0 >= h:
        return None
    x0, y0 = max(x0, 0), max(y0, 0)
    x1, y1 = min(x1, w), min(y1, h)
    mask = Image.new("L", (x1 - x0, y1 - y0), 0)
    d = ImageDraw.Draw(mask)
    for t in px:
        d.polygon([(float(x - x0), float(y - y0)) for x, y in t], fill=255)
    m = np.array(mask) > 0
    # Erode by ~0.75 m so lean and misalignment in the photo pull in less of the surroundings.
    for _ in range(3):
        m[1:, :] &= m[:-1, :]
        m[:-1, :] &= m[1:, :]
        m[:, 1:] &= m[:, :-1]
        m[:, :-1] &= m[:, 1:]
    crop = aerial[y0:y1, x0:x1][m]
    if len(crop) < 8:
        crop = aerial[y0:y1, x0:x1][np.array(mask) > 0]
    if len(crop) == 0:
        return None
    return [int(v) for v in np.median(crop, axis=0)]


if __name__ == "__main__":
    main()

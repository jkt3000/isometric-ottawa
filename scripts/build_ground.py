# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy", "scipy", "pillow", "shapely"]
# ///
"""Paint the ground: the OpenStreetMap surfaces (scripts/build_surfaces.py) in a pixel-art palette, with baked sun shadows.

Output, in 2000x2000 px chunks of 500 m (0.25 m/px), row 0 north:
  data/build/ground/{ix}_{iy}.png   the ground, with the roads filled in by the surfaces around them
  data/build/roads/{ix}_{iy}.png    the roads' own colours (shadows included), running on past their edges
  data/build/roads.json             the road polygons, which render/scene.js draws with hard edges
"""

import hashlib
import json
import math

import numpy as np
from PIL import Image, ImageDraw
from scipy import ndimage as ndi

from aerial import load_aerial
from config import BUILD, CHUNK, CX, CY, GROUND_RES, NX as CHUNKS_X, NY as CHUNKS_Y, RAW, XMAX, XMIN, YMAX, YMIN
from sun import SUN_AZIMUTH, SUN_ELEVATION

NX, NY = int((XMAX - XMIN) / GROUND_RES), int((YMAX - YMIN) / GROUND_RES)

# Style categories. Order matters only for readability.
STYLES = {
    "gap": (150, 150, 120),
    "grass": (116, 160, 70),
    "meadow": (124, 166, 74),
    "berm": (120, 158, 74),
    "shrub": (84, 122, 56),
    "forest": (68, 98, 50),
    "field": (150, 118, 82),
    "orchard": (118, 152, 72),
    "heath": (146, 118, 96),
    "marsh": (104, 132, 92),
    "water": (70, 128, 172),
    "ditch": (98, 128, 70),
    "bank": (108, 144, 68),
    "yard": (160, 155, 140),
    "sand": (222, 204, 160),
    "asphalt": (98, 100, 106),
    "motorway": (84, 86, 92),
    "concrete_pavers": (160, 156, 148),
    "tiles": (180, 177, 170),
    "clinkers": (164, 104, 84),
    "concrete": (178, 175, 164),
    "gravel": (176, 164, 138),
    "grass_pavers": (138, 160, 98),
    "dirt": (178, 156, 114),
    "building": (168, 162, 150),
    "bridge": (150, 140, 126),
}
CAT = {k: i for i, k in enumerate(STYLES)}
WATERY = {CAT["water"]}
SOFT = {CAT[k] for k in ("grass", "meadow", "berm", "shrub", "forest", "field", "orchard", "heath", "marsh", "ditch", "bank", "yard", "dirt", "sand", "gap")}


def style_for(coll, p):
    """The style scripts/build_surfaces.py gave a polygon from its OpenStreetMap tags."""
    return p["style"]


# Surfaces that are drawn as crisp polygons over the ground (see render/scene.js, makeRoads): the whole road
# network, bridges, and paved squares and yards. Grass verges and plain yards stay part of the soft ground.
ROAD_STYLES = {"asphalt", "motorway", "concrete_pavers", "tiles", "clinkers", "concrete", "gravel", "grass_pavers", "dirt", "sand", "bridge"}
PAVED_STYLES = {"asphalt", "concrete_pavers", "tiles", "clinkers", "concrete", "gravel"}


def is_road(coll, style, p=None):
    return bool(p and p.get("road")) and style in ROAD_STYLES


def load_polygons():
    """The OpenStreetMap surfaces (scripts/build_surfaces.py), in painting order: by layer, then large ones first."""
    polys = []
    for p in json.loads((BUILD / "surfaces.json").read_text())["polygons"]:
        rings = p["rings"]
        ext = np.array(rings[0])[:, :2]
        area = 0.5 * abs(np.dot(ext[:, 0], np.roll(ext[:, 1], 1)) - np.dot(ext[:, 1], np.roll(ext[:, 0], 1)))
        props = {"style": p["style"], "road": p.get("road", False), "bridge": p.get("bridge", False), "lokaal_id": p["id"]}
        polys.append((p["prio"], -area, "osm", props, rings))
    polys.sort(key=lambda t: (t[0], t[1]))
    return polys


def to_px(ring):
    a = np.asarray(ring)[:, :2]
    return list(zip(((a[:, 0] - XMIN) / GROUND_RES).tolist(), ((YMAX - a[:, 1]) / GROUND_RES).tolist()))


def value_noise(r0, h, cell, seed, octaves=1):
    """Smooth noise in [0,1] with features of about `cell` pixels over raster rows r0..r0+h, the same in every band:
    a coarse random grid over the whole raster, interpolated at the band's pixels."""
    rng = np.random.default_rng(seed)
    out = np.zeros((h, NX), np.float32)
    amp, total = 1.0, 0.0
    for o in range(octaves):
        c = max(1, int(cell / (2**o)))
        g = rng.random((NY // c + 2, NX // c + 2)).astype(np.float32)
        y0 = r0 / c
        rows = ndi.map_coordinates(g, np.meshgrid(y0 + np.arange(h, dtype=np.float32) / c, np.arange(g.shape[1], dtype=np.float32), indexing="ij"), order=1)
        out += amp * ndi.zoom(rows, (1, c), order=1, grid_mode=True, mode="grid-constant")[:, :NX]
        total += amp
        amp *= 0.5
    return out / total


def main():
    aerial = load_aerial()
    polys = load_polygons()
    print(f"{len(polys)} polygons")

    # Polygon ids at 1 m for the per-polygon photo colours; the full 25 cm raster is drawn band by band below.
    ids = draw_ids(polys, 0, NY, step=4)

    # Per polygon: median aerial color, ignoring tree canopy that hides the surface.
    chm = np.load(BUILD / "chm.npy").astype(np.float32)
    lab = ids.copy()
    lab[chm[::2, ::2][: lab.shape[0], : lab.shape[1]] > 2.0] = 0
    small = aerial[::4, ::4].astype(np.float32)
    idx = np.arange(1, len(polys) + 1)
    med = np.stack([ndi.median(small[..., c], lab, idx) for c in range(3)], -1)
    med = np.nan_to_num(med, nan=128.0)
    del ids, lab, small, chm

    cats = np.zeros(len(polys) + 1, np.int32)
    roads = np.zeros(len(polys) + 1, bool)
    base = np.zeros((len(polys) + 1, 3), np.float32)
    stripes_dir = np.zeros(len(polys) + 1, np.float32)
    for i, (_, _, coll, props, rings) in enumerate(polys, start=1):
        # Seeded per polygon, so a field keeps its crop when the map area changes.
        rng = np.random.default_rng(int(hashlib.md5(str(props.get("lokaal_id", i)).encode()).hexdigest()[:8], 16))
        s = style_for(coll, props)
        cats[i] = CAT[s]
        roads[i] = is_road(coll, s, props)
        c = np.array(STYLES[s], np.float32)
        m = med[i - 1]
        if s == "field":
            # Pick a crop look per field; the photo decides whether it is green or bare.
            green = m[1] > m[0] + 6
            choice = rng.random()
            if green:
                c = np.array((128, 166, 72) if choice < 0.6 else (150, 176, 84), np.float32)
            elif choice < 0.55:
                c = np.array((152, 116, 80), np.float32) * (0.9 + 0.2 * rng.random())
            elif choice < 0.8:
                c = np.array((206, 184, 108), np.float32)
            else:
                c = np.array((136, 160, 76), np.float32)
        elif s in ("meadow", "grass", "berm"):
            c = c * (0.94 + 0.12 * rng.random())
        elif s == "yard":
            c = 0.55 * m + 0.45 * c
        elif s == "heath":
            c = 0.5 * c + 0.5 * m
        base[i] = c
        ext = np.asarray(rings[0])[:, :2]
        ext = ext - ext.mean(0)
        w, v = np.linalg.eigh(ext.T @ ext)
        stripes_dir[i] = math.atan2(v[1, -1], v[0, -1])
    base[0] = STYLES["gap"]

    # Paint in bands of one chunk row (500 m) with a margin, so filters and fills see across the band edges, and write
    # the chunks of each band as it is done: the whole 25 cm raster would not fit in memory.
    px = int(CHUNK / GROUND_RES)
    shadows = compute_shadows()
    for d in (BUILD / "ground", BUILD / "roads"):
        if d.exists():
            for f in d.glob("*.png"):
                f.unlink()
        d.mkdir(exist_ok=True)
    preview = np.zeros((NY // 8, NX // 8, 3), np.uint8)
    road_mask = np.zeros((NY // 4, NX // 4), bool)
    for iy in range(CHUNKS_Y - 1, -1, -1):
        row = (CHUNKS_Y - 1 - iy) * px
        r0, r1 = max(0, row - MARGIN), min(NY, row + px + MARGIN)
        ids = draw_ids(polys, r0, r1)
        img, road = paint(ids, aerial[r0:r1], r0, cats, roads, base, stripes_dir, shadows)
        img = np.clip(img, 0, 255).astype(np.uint8)
        # Two layers. The roads are drawn as polygons with hard edges and sample their own layer, in which each road
        # colour runs on outward, so a pixel on the edge never picks up the verge. The ground layer has the roads
        # filled in with the colours around them, so its soft texture doesn't smear grey past those hard edges.
        ri = ndi.distance_transform_edt(~road, return_distances=False, return_indices=True)
        road_img = img[ri[0], ri[1]]
        del ri
        gi = ndi.distance_transform_edt(road, return_distances=False, return_indices=True)
        ground_img = img[gi[0], gi[1]]
        del gi
        top = row - r0
        preview[row // 8 : (row + px) // 8] = img[top : top + px : 8, ::8]
        road_mask[row // 4 : (row + px) // 4] = road[top : top + px : 4, ::4]
        for ix in range(CHUNKS_X):
            cut = (slice(top, top + px), slice(ix * px, ix * px + px))
            Image.fromarray(ground_img[cut]).save(BUILD / "ground" / f"{ix}_{iy}.png", optimize=False, compress_level=1)
            Image.fromarray(road_img[cut]).save(BUILD / "roads" / f"{ix}_{iy}.png", optimize=False, compress_level=1)
        del img, road, road_img, ground_img, ids
        print(f"  band {CHUNKS_Y - iy}/{CHUNKS_Y}")
    Image.fromarray(preview).save(BUILD / "ground_preview.png")
    save_road_polygons(polys, roads, cats)
    print("ground done")


MARGIN = 96  # pixels of overlap around each band


def draw_ids(polys, r0, r1, step=1):
    """Polygon index per pixel (0: none) for raster rows r0..r1, every `step`-th pixel."""
    w, h = (NX + step - 1) // step, (r1 - r0 + step - 1) // step
    ids = Image.new("I", (w, h), 0)
    d = ImageDraw.Draw(ids)
    top = (YMAX - r0 * GROUND_RES)
    bottom = (YMAX - r1 * GROUND_RES)
    for i, (_, _, _, _, rings) in enumerate(polys, start=1):
        ext = np.asarray(rings[0])
        if ext[:, 1].min() > top or ext[:, 1].max() < bottom:
            continue
        shift = lambda ring: [(x / step, (y - r0) / step) for x, y in to_px(ring)]
        d.polygon(shift(rings[0]), fill=i)
        for hole in rings[1:]:
            d.polygon(shift(hole), fill=0)
    return np.array(ids, dtype=np.int32)


def paint(ids, aerial, r0, cats, roads, base, stripes_dir, shadows):
    """The ground colours of one band of the raster, starting at row r0, and where its roads are."""
    cat = cats[ids]
    img = base[ids]  # (N, N, 3) float32
    road = roads[ids]
    # The outer metre of a road stays clean: the photo's road edge never lines up exactly with the road polygon,
    # and its detail there turns a straight road edge into a blotchy one.
    road_rim = road & ~ndi.binary_erosion(road, iterations=4)

    yy, xx = np.ogrid[r0 : r0 + ids.shape[0], 0:NX]
    yy, xx = yy.astype(np.float32), xx.astype(np.float32)
    # Field furrows along the long axis of each parcel.
    ang = stripes_dir[ids]
    field = cat == CAT["field"]
    along = xx * np.sin(ang) + yy * np.cos(ang)
    furrow = (np.sin(along * (2 * np.pi / 7.0)) > 0.3).astype(np.float32)
    img[field] *= (0.96 + 0.05 * furrow[field])[:, None]
    del along, furrow, ang

    # Orchards and tree nurseries: rows of small dark shrubs.
    orch = cat == CAT["orchard"]
    rows = ((xx.astype(np.int32) // 6) % 3 == 0) & ((yy.astype(np.int32) // 6) % 2 == 0)
    img[orch & rows] *= 0.72
    del rows

    n1 = value_noise(r0, ids.shape[0], 20, 1, octaves=2)
    n2 = value_noise(r0, ids.shape[0], 40, 2, octaves=2)
    soft = np.isin(cat, list(SOFT))
    img[soft] *= (0.96 + 0.08 * n1[soft])[:, None]
    grassy = np.isin(cat, [CAT[k] for k in ("grass", "meadow", "berm", "bank", "orchard")])
    img[grassy] *= (0.95 + 0.1 * n2[grassy])[:, None]
    shrub = np.isin(cat, [CAT["shrub"], CAT["forest"], CAT["marsh"]])
    img[shrub & (n1 > 0.62)] *= 0.9
    img[shrub & (n1 < 0.3)] *= 1.05
    heath = cat == CAT["heath"]
    purple = np.array((132, 104, 118), np.float32)
    straw = np.array((168, 150, 102), np.float32)
    img[heath & (n2 > 0.6)] = img[heath & (n2 > 0.6)] * 0.5 + purple * 0.5
    img[heath & (n1 < 0.35)] = img[heath & (n1 < 0.35)] * 0.6 + straw * 0.4
    del soft, grassy, shrub, heath

    # Yards: lawn where the photo is green, light paving elsewhere.
    yard = cat == CAT["yard"]
    sm = ndi.gaussian_filter(aerial.astype(np.float32), (6, 6, 0))
    greenish = ndi.median_filter((sm[..., 1] > sm[..., 0] + 2) & (sm[..., 1] >= sm[..., 2]), size=7)
    lawn = np.array((112, 156, 68), np.float32)
    paving = np.array((176, 170, 156), np.float32)
    img[yard & greenish] = lawn * (0.96 + 0.08 * n1[yard & greenish])[:, None]
    img[yard & ~greenish] = paving * (0.97 + 0.05 * n1[yard & ~greenish])[:, None]
    del sm, greenish, yard

    # Real objects from the aerial photo: parked cars, road markings, garden paths, sheds' surroundings.
    # Only distinct things come through (a strong local contrast), not the photo's grain.
    paved = np.isin(cat, [CAT[k] for k in ("asphalt", "motorway", "concrete_pavers", "tiles", "clinkers", "concrete",
                                           "gravel", "grass_pavers", "bridge")]) & ~road_rim
    yard = cat == CAT["yard"]
    lum = aerial.astype(np.float32).mean(-1)
    strong = np.abs(lum - ndi.gaussian_filter(lum, 5)) > 14
    strong = ndi.binary_opening(strong, iterations=1)  # objects, not single noisy pixels
    del lum
    for k in range(3):
        ch = aerial[..., k].astype(np.float32)
        hp = np.where(strong, np.clip(ch - ndi.gaussian_filter(ch, 5), -60, 60), 0)
        img[..., k][paved] += 0.7 * hp[paved]
        img[..., k][yard] += 0.3 * hp[yard]
        del ch, hp
    del paved, yard, strong

    # Keep a little of the real ground texture (tracks, paths, field patterns) on soft surfaces.
    lum = aerial.astype(np.float32).mean(-1)
    detail = lum - ndi.uniform_filter(lum, 9)
    detail = np.clip(detail, -18, 18)
    keep = np.isin(cat, [CAT[k] for k in ("field", "heath", "meadow", "marsh", "dirt", "sand", "gap")]) & ~road_rim
    img[keep] += (0.2 * detail[keep])[:, None]
    del lum, detail, keep

    # Pavement patterns.
    # Not on the roads: their layer is sampled by sharp-edged polygons, where a 1 m grid turns into moiré.
    pav = np.isin(cat, [CAT["tiles"], CAT["concrete_pavers"], CAT["clinkers"]]) & ~road
    grid = ((xx.astype(np.int32) % 4 == 0) | (yy.astype(np.int32) % 4 == 0))
    img[pav & grid] *= 0.97
    asph = np.isin(cat, [CAT["asphalt"], CAT["motorway"]])
    img[asph] *= (0.985 + 0.03 * n1[asph])[:, None]
    del pav, grid, asph

    # Water: ripples and a darker far bank, which is the side of the canal you look into.
    water = np.isin(cat, list(WATERY))
    ripple = (np.sin((xx * 0.7 + yy * 0.7) * 0.9 + n2 * 20) > 0.93) & (n1 > 0.55)
    img[water & ripple] = img[water & ripple] * 0.5 + np.array((140, 186, 214), np.float32) * 0.5
    land = ~water
    # The camera looks north-east, so the visible bank is the land to the north and east of the water.
    far = np.zeros_like(water)
    for k in range(1, 4):
        far |= np.roll(land, -k, axis=1) | np.roll(land, k, axis=0)
    bank = water & far
    img[bank] = np.array((92, 84, 62), np.float32)
    edge = water & ~bank & ndi.binary_dilation(land, iterations=1)
    img[edge] *= 0.8
    del ripple, far, bank, edge, xx, yy

    paint_osm(img, cat, r0)

    # Darken edges between unlike surfaces so plots read as crisp shapes. Roads get their edge from their polygon.
    hard = ~np.isin(cat, list(SOFT)) & ~road
    border = (cat != np.roll(cat, 1, 0)) | (cat != np.roll(cat, 1, 1))
    img[border & hard] *= 0.86
    del hard, border, road_rim

    # Baked shadows from buildings and trees, plus a contact shadow along every wall.
    shadow, contact = shadows(r0, ids.shape[0])
    img *= np.where(shadow[..., None], np.array((0.62, 0.66, 0.8), np.float32), 1.0)
    img[contact] *= 0.8
    del shadow, contact

    return img, road
def save_chunks(img, out):
    if out.exists():
        for f in out.glob("*.png"):
            f.unlink()
    out.mkdir(exist_ok=True)
    px = int(CHUNK / GROUND_RES)
    for ix in range(CHUNKS_X):
        for iy in range(CHUNKS_Y):
            row = (CHUNKS_Y - 1 - iy) * px
            Image.fromarray(img[row : row + px, ix * px : ix * px + px]).save(out / f"{ix}_{iy}.png", optimize=False, compress_level=1)


def save_road_polygons(polys, roads, cats):
    """The road polygons and their kerbs in scene coordinates (x east, z south, from the center), for render/scene.js.

    The kerbs are the outlines of the road surfaces merged per kind of surface: so a road gets an edge where it
    meets the verge or a pavement of another kind, but not where OpenStreetMap happens to cut it into segments.
    """
    from shapely import STRtree
    from shapely.geometry import Polygon, box
    from shapely.ops import unary_union

    local = lambda ring: [(round(x - CX, 2), round(CY - y, 2)) for x, y, *_ in ring]
    rounded = lambda ring: [[round(x, 2), round(z, 2)] for x, z in ring.coords]
    extent = box(XMIN - CX, CY - YMAX, XMAX - CX, CY - YMIN)
    names = list(STYLES)
    shapes = [Polygon(local(rings[0]), [local(r) for r in rings[1:]]).buffer(0) for _, _, _, _, rings in polys]
    tree = STRtree(shapes)
    out, kinds = [], {}
    for i, (_, _, coll, _, rings) in enumerate(polys, start=1):
        if not roads[i]:
            continue
        shape = shapes[i - 1]
        if not shape.intersects(extent):
            continue
        # The surfaces overlap (a lawn over a footpath, a road on a bridge over another). The ground
        # raster paints them in list order, later ones on top, so the road keeps only what the raster shows:
        # minus every later polygon that isn't a road. Buildings stay out of it; they cover the road anyway.
        later = [shapes[j] for j in tree.query(shape) if j > i - 1 and not roads[j + 1] and names[cats[j + 1]] != "building"]
        if later:
            shape = shape.difference(unary_union(later))
        # Drop slivers thinner than 10 cm that the cut can leave along shared borders.
        shape = shape.buffer(-0.05, join_style="mitre").buffer(0.05, join_style="mitre")
        for part in getattr(shape, "geoms", [shape]):
            if part.geom_type != "Polygon" or part.is_empty:
                continue
            out.append({"rings": [rounded(part.exterior), *map(rounded, part.interiors)], "bridge": bool(polys[i - 1][3].get("bridge"))})
            kinds.setdefault(names[cats[i]], []).append(part)

    edges = []
    for shapes in kinds.values():
        # Closing by a few centimetres fuses neighbours whose shared border doesn't match exactly. Not clipped to
        # the map: the renderer clips per chunk, and a clip here would draw a kerb along the edge of the map.
        merged = unary_union(shapes).buffer(0.05, join_style="mitre").buffer(-0.05, join_style="mitre")
        for part in getattr(merged, "geoms", [merged]):
            if part.geom_type != "Polygon":
                continue
            for ring in [part.exterior, *part.interiors]:
                edges.append([[round(x, 2), round(z, 2)] for x, z in ring.coords])
    (BUILD / "roads.json").write_text(json.dumps({"polygons": out, "edges": edges}, separators=(",", ":")))
    print(f"{len(out)} road polygons, {len(edges)} kerb lines")


def paint_osm(img, cat, r0=0):
    """Sports pitches, riding arenas and graveyards from OpenStreetMap, drawn onto the band of the ground from row r0."""
    path = RAW / "osm_landmarks.json"
    if not path.exists():
        return
    from geo import wgs_to_rd

    for el in json.load(open(path))["elements"]:
        t = el.get("tags", {})
        g = el.get("geometry")
        if el["type"] != "way" or not g:
            continue
        rd = np.array([wgs_to_rd(p["lat"], p["lon"]) for p in g])
        px = [((x - XMIN) / GROUND_RES, (YMAX - y) / GROUND_RES - r0) for x, y in rd]
        x0, y0 = np.floor(np.min(px, 0)).astype(int) - 4
        x1, y1 = np.ceil(np.max(px, 0)).astype(int) + 4
        bh = img.shape[0]
        if x1 < 0 or y1 < 0 or x0 >= NX or y0 >= bh:
            continue
        x0, y0, x1, y1 = max(x0, 0), max(y0, 0), min(x1, NX), min(y1, bh)
        w, h = x1 - x0, y1 - y0
        area = Image.new("L", (w, h), 0)
        ImageDraw.Draw(area).polygon([(a - x0, b - y0) for a, b in px], fill=1)
        area = np.array(area) > 0
        lines = Image.new("L", (w, h), 0)
        d = ImageDraw.Draw(lines)
        sub = img[y0:y1, x0:x1]
        # The pitch's own axes, from its outline.
        pts = np.array(px) - (x0, y0)
        if np.allclose(pts[0], pts[-1]):
            pts = pts[:-1]  # OSM repeats the first node to close the ring; it would pull the center toward it
        c = pts.mean(0)
        _, vecs = np.linalg.eigh((pts - c).T @ (pts - c))
        ua, va = vecs[:, -1], vecs[:, 0]
        hu = np.abs((pts - c) @ ua).max()
        hv = np.abs((pts - c) @ va).max()
        P = lambda a, b: tuple(c + a * hu * ua + b * hv * va)
        sport = t.get("sport")
        if t.get("landuse") == "cemetery":
            sub[area] = np.array((108, 150, 70), np.float32)
            gy, gx = np.mgrid[0:h, 0:w]
            along = (gx - c[0]) * ua[0] + (gy - c[1]) * ua[1]
            across = (gx - c[0]) * va[0] + (gy - c[1]) * va[1]
            stones = (np.mod(along, 7) < 2) & (np.mod(across, 12) < 3) & area
            sub[stones] = np.array((176, 176, 170), np.float32)
            path_m = (np.abs(along) < 3) | (np.abs(across) < 3)
            sub[area & path_m] = np.array((196, 186, 160), np.float32)
        elif sport == "tennis":
            sub[area] = np.array((190, 104, 72), np.float32)
            d.polygon([P(-0.8, -0.7), P(0.8, -0.7), P(0.8, 0.7), P(-0.8, 0.7)], outline=1, width=2)
            d.line([P(0, -0.8), P(0, 0.8)], fill=1, width=2)
            d.line([P(-0.45, -0.55), P(0.45, -0.55)], fill=1, width=1)
            d.line([P(-0.45, 0.55), P(0.45, 0.55)], fill=1, width=1)
        elif sport == "equestrian":
            sub[area] = np.array((214, 196, 154), np.float32)
            d.polygon([tuple(p) for p in pts], outline=1, width=2)
        elif t.get("leisure") == "pitch":
            # Football: mowing stripes and the full set of lines, scaled to the pitch.
            gy, gx = np.mgrid[0:h, 0:w]
            along = (gx - c[0]) * ua[0] + (gy - c[1]) * ua[1]
            stripe = (np.mod(along, 40) < 20) & area
            sub[area] = np.array((96, 156, 64), np.float32)
            sub[stripe] = np.array((110, 170, 72), np.float32)
            m = 0.94
            d.polygon([P(-m, -m), P(m, -m), P(m, m), P(-m, m)], outline=1, width=2)
            d.line([P(0, -m), P(0, m)], fill=1, width=2)
            r = 9.15 / GROUND_RES
            d.ellipse([c[0] - r, c[1] - r, c[0] + r, c[1] + r], outline=1, width=2)
            for s_ in (-1, 1):
                d.polygon([P(s_ * m, -0.6), P(s_ * (m - 0.3), -0.6), P(s_ * (m - 0.3), 0.6), P(s_ * m, 0.6)], outline=1, width=2)
                d.polygon([P(s_ * m, -0.28), P(s_ * (m - 0.1), -0.28), P(s_ * (m - 0.1), 0.28), P(s_ * m, 0.28)], outline=1, width=2)
        else:
            continue
        sub[np.array(lines) > 0] = np.array((240, 240, 236), np.float32)


def compute_shadows(res=0.5):
    """Hard shadows at 0.5 m from building roofs and tree crowns, upsampled to the ground raster."""
    nx, ny = int((XMAX - XMIN) / res), int((YMAX - YMIN) / res)
    top = np.zeros((ny, nx), np.float32)
    bot = np.full((ny, nx), np.inf, np.float32)

    v = np.fromfile(BUILD / "buildings.bin", dtype=np.float32).reshape(-1, 12)
    tris = v.reshape(-1, 3, 12)
    tris = tris[tris[:, 0, 8] == 2]  # roofs
    for t in tris:
        col = (t[:, 0] + CX - XMIN) / res
        row = (YMAX - (CY - t[:, 2])) / res
        c0, c1 = int(max(col.min(), 0)), int(min(col.max() + 1, nx))
        r0, r1 = int(max(row.min(), 0)), int(min(row.max() + 1, ny))
        if c1 <= c0 or r1 <= r0:
            continue
        gy, gx = np.mgrid[r0:r1, c0:c1].astype(np.float32) + 0.5
        (x0, x1, x2), (y0, y1, y2) = col, row
        den = (y1 - y2) * (x0 - x2) + (x2 - x1) * (y0 - y2)
        if abs(den) < 1e-9:
            continue
        a = ((y1 - y2) * (gx - x2) + (x2 - x1) * (gy - y2)) / den
        b = ((y2 - y0) * (gx - x2) + (x0 - x2) * (gy - y2)) / den
        c = 1 - a - b
        inside = (a >= -0.01) & (b >= -0.01) & (c >= -0.01)
        h = a * t[0, 1] + b * t[1, 1] + c * t[2, 1]
        sub = top[r0:r1, c0:c1]
        np.maximum(sub, np.where(inside, h, 0), out=sub)
        sb = bot[r0:r1, c0:c1]
        np.minimum(sb, np.where(inside, 0, np.inf), out=sb)

    trees = np.fromfile(BUILD / "trees.bin", dtype=np.float32).reshape(-1, 10)
    for x, z, h, r, base, *_ in trees:
        col, row = (x + CX - XMIN) / res, (YMAX - (CY - z)) / res
        rr = r / res
        c0, c1 = int(max(col - rr, 0)), int(min(col + rr + 1, nx))
        r0, r1 = int(max(row - rr, 0)), int(min(row + rr + 1, ny))
        if c1 <= c0 or r1 <= r0:
            continue
        gy, gx = np.mgrid[r0:r1, c0:c1].astype(np.float32) + 0.5
        dd = ((gx - col) ** 2 + (gy - row) ** 2) / (rr * rr)
        inside = dd < 1
        mid, half = (h + base) / 2, (h - base) / 2
        s = np.sqrt(np.clip(1 - dd, 0, 1)) * half
        sub = top[r0:r1, c0:c1]
        np.maximum(sub, np.where(inside, mid + s, 0), out=sub)
        sb = bot[r0:r1, c0:c1]
        np.minimum(sb, np.where(inside, mid - s, np.inf), out=sb)

    az, el = math.radians(SUN_AZIMUTH), math.radians(SUN_ELEVATION)
    dx, dy = math.sin(az), -math.cos(az)  # toward the sun, in columns and rows
    shadow = np.zeros((ny, nx), bool)
    maxh = float(top.max())
    steps = int(maxh / math.tan(el) / res) + 1
    for k in range(1, steps + 1):
        ox, oy = int(round(dx * k)), int(round(dy * k))
        z = k * res * math.tan(el)
        if z > maxh:
            break
        src_t = shift(top, ox, oy, 0)
        src_b = shift(bot, ox, oy, np.inf)
        shadow |= (src_t >= z) & (src_b <= z)
    occupied = top > 0.3
    shadow &= ~occupied
    contact = ndi.binary_dilation(occupied & (bot < 0.5) & (top > 1.5), iterations=1) & ~occupied

    def band(r0, h):
        """Shadow and contact masks for ground raster rows r0..r0+h (the masks are at 0.5 m, the raster at 0.25 m)."""
        rows = (np.arange(r0, r0 + h) // 2).clip(0, ny - 1)
        cols = (np.arange(NX) // 2).clip(0, nx - 1)
        return shadow[rows][:, cols], contact[rows][:, cols]

    return band


def shift(a, ox, oy, fill):
    """out[r, c] = a[r + oy, c + ox]"""
    out = np.full_like(a, fill)
    h, w = a.shape
    rs, re = max(0, -oy), min(h, h - oy)
    cs, ce = max(0, -ox), min(w, w - ox)
    out[rs:re, cs:ce] = a[rs + oy : re + oy, cs + ox : ce + ox]
    return out


if __name__ == "__main__":
    main()

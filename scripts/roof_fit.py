"""Building shapes from a footprint and the lidar: wall height and a flat, gable or hip roof fitted to the DSM.

The Dutch map has the 3D BAG, which reconstructs every roof from lidar. Canada has no national dataset like it, so
this does a small version of the same: it samples the HRDEM surface model inside each OpenStreetMap footprint and
tries three roofs over the footprint's oriented bounding box, keeping the one that fits the lidar best:

  flat   one height
  gable  two slopes rising from the long sides to a ridge along the middle
  hip    four slopes, from every side of the box

A roof is the lowest of a few planes, so it works for any footprint shape, not just rectangles: the footprint is cut
into the parts where each plane is the lowest, and every wall runs up to the roof above it, so gable ends rise to
the ridge. Houses newer than the lidar (2020 in Ottawa) show up as bare ground; they get a height from their
OpenStreetMap tags or a default.
"""

import numpy as np
from shapely import contains_xy, prepare
from shapely.geometry import Polygon
from shapely.geometry.polygon import orient

from config import XMIN, YMAX

PITCH_MIN, PITCH_MAX = 0.2, 1.5  # roof slope as rise over run: about 11 to 56 degrees


def _samples(poly, a, shrink=0.6):
    """Pixel centres of a 1 m raster inside a polygon, and their values."""
    x0, y0, x1, y1 = poly.bounds
    c0, c1 = int(np.floor(x0 - XMIN)), int(np.ceil(x1 - XMIN))
    r0, r1 = int(np.floor(YMAX - y1)), int(np.ceil(YMAX - y0))
    c0, r0 = max(c0, 0), max(r0, 0)
    c1, r1 = min(c1, a.shape[1]), min(r1, a.shape[0])
    if c1 <= c0 or r1 <= r0:
        return np.zeros((0, 2)), np.zeros(0)
    cc, rr = np.meshgrid(np.arange(c0, c1), np.arange(r0, r1))
    xs, ys = XMIN + cc.ravel() + 0.5, YMAX - rr.ravel() - 0.5
    for s in (shrink, 0.0):
        p = poly.buffer(-s) if s else poly
        if p.is_empty:
            continue
        prepare(p)
        inside = contains_xy(p, xs, ys)
        if inside.sum() >= 6 or s == 0.0:
            break
    pts = np.column_stack([xs[inside], ys[inside]])
    return pts, a[rr.ravel()[inside], cc.ravel()[inside]]


def frame(poly):
    """Centre, long-axis direction, across direction and half sizes of the footprint's oriented bounding box."""
    box = np.array(poly.minimum_rotated_rectangle.exterior.coords)[:4]
    e1, e2 = box[1] - box[0], box[2] - box[1]
    if np.hypot(*e2) > np.hypot(*e1):
        e1, e2 = e2, e1
    d = e1 / (np.hypot(*e1) or 1)
    n = np.array([-d[1], d[0]])
    c = box.mean(0)
    return c, d, n, np.hypot(*e1) / 2, np.hypot(*e2) / 2


def _robust_fit(f, h):
    """h ~ e + s * f, least squares with the worst fifth dropped twice (tree crowns over a roof, chimneys)."""
    keep = np.ones(len(h), bool)
    e, s = float(np.median(h)), 0.0
    for _ in range(3):
        if keep.sum() < 4 or np.ptp(f[keep]) < 0.5:
            break
        A = np.column_stack([np.ones(keep.sum()), f[keep]])
        (e, s), *_ = np.linalg.lstsq(A, h[keep], rcond=None)
        r = np.abs(h - (e + s * f))
        keep = r <= np.quantile(r, 0.8) + 0.05
    return float(e), float(s)


def fit(poly, dsm, dtm, fallback_height=None):
    """Fit a roof. Returns a dict with the ground level, the roof model and its planes (z = a x + b y + c)."""
    pts, top = _samples(poly, dsm)
    gpts, ground_vals = _samples(poly.buffer(1.0), dtm, shrink=0.0)
    if len(ground_vals) == 0:
        return None
    ground = float(np.median(ground_vals))
    c, d, n, hl, hw = frame(poly)
    out = {"ground": ground, "frame": (c, d, n, hl, hw)}
    h = top - ground if len(top) else np.zeros(0)
    if len(h) < 3 or np.quantile(h, 0.9) < 1.8:
        # Not in the lidar: built since, or too small to see. A plain box at its tagged or a default height.
        height = fallback_height or 6.0
        out.update(model="flat", eave=height, height=height, lidar=False, planes=[(0.0, 0.0, ground + height)])
        return out
    h = np.clip(h, 0, None)
    u, v = (pts - c) @ d, (pts - c) @ n
    flat_top = float(np.quantile(h, 0.6))
    score = {"flat": float(np.median(np.abs(h - np.median(h))))}
    params = {}
    if hw <= 16 and hw >= 2.0:
        for model, f in (("gable", hw - np.abs(v)), ("hip", np.minimum(hw - np.abs(v), hl - np.abs(u)))):
            e, s = _robust_fit(f, h)
            if not (PITCH_MIN <= s <= PITCH_MAX) or e < 1.8 or s * hw < 0.9:
                continue
            score[model] = float(np.median(np.abs(h - (e + s * f))))
            params[model] = (e, s)
    model = "flat"
    if "gable" in score and score["gable"] < 0.6 * score["flat"]:
        model = "gable"
    if "hip" in score and score["hip"] < 0.6 * score["flat"] and (model == "flat" or score["hip"] < 0.88 * score["gable"]):
        model = "hip"
    if model == "flat":
        height = max(flat_top, 2.2)
        out.update(model="flat", eave=height, height=height, lidar=True, planes=[(0.0, 0.0, ground + height)])
        return out
    e, s = params[model]
    # One slope per side of the box: z = ground + e + s * (distance in from that side).
    planes = []
    sides = [(n, hw), (-n, hw)] + ([(d, hl), (-d, hl)] if model == "hip" else [])
    for k, half in sides:
        # distance in from the side at +k*half: half - (p - c).k
        a, b = -s * k[0], -s * k[1]
        cc = ground + e + s * (half + float(c @ k))
        planes.append((float(a), float(b), float(cc)))
    ridge = e + s * (hw if model == "gable" else min(hw, hl))
    out.update(model=model, eave=e, pitch=s, height=ridge, lidar=True, planes=planes)
    return out


def _z(plane, x, y):
    a, b, c = plane
    return a * x + b * y + c


def _clip_halfplane(pts, A, B, C):
    """Sutherland-Hodgman: the part of a convex polygon where A x + B y + C <= 0."""
    out = []
    for i in range(len(pts)):
        p, q = pts[i], pts[(i + 1) % len(pts)]
        fp, fq = A * p[0] + B * p[1] + C, A * q[0] + B * q[1] + C
        if fp <= 0:
            out.append(p)
        if (fp < 0) != (fq < 0) and fp != fq:
            t = fp / (fp - fq)
            out.append((p[0] + t * (q[0] - p[0]), p[1] + t * (q[1] - p[1])))
    return out


def surfaces(poly, roof):
    """Walls and roof faces for build_buildings.emit: (kind, rings with absolute z), kind 1 wall, 2 roof."""
    poly = orient(poly, 1.0)  # exterior counter-clockwise, holes clockwise: walls face outward
    ground, planes = roof["ground"], roof["planes"]
    out = []
    # Roof: the part of the footprint under each plane where that plane is the lowest.
    x0, y0, x1, y1 = poly.bounds
    rect = [(x0 - 1, y0 - 1), (x1 + 1, y0 - 1), (x1 + 1, y1 + 1), (x0 - 1, y1 + 1)]
    for i, pi in enumerate(planes):
        region = rect
        for j, pj in enumerate(planes):
            if i != j and region:
                region = _clip_halfplane(region, pi[0] - pj[0], pi[1] - pj[1], pi[2] - pj[2])
        if len(region) < 3:
            continue
        part = poly.intersection(Polygon(region))
        for p in getattr(part, "geoms", [part]):
            if p.geom_type != "Polygon" or p.area < 0.05:
                continue
            p = orient(p, 1.0)
            rings = [np.array([(x, y, _z(pi, x, y)) for x, y in r.coords[:-1]]) for r in (p.exterior, *p.interiors)]
            if all(len(r) >= 3 for r in rings):
                out.append((2, rings))
    # Walls: up to the roof along every edge, split where the lowest plane changes.
    for ring in (poly.exterior, *poly.interiors):
        pts = np.array(ring.coords)
        for a, b in zip(pts[:-1], pts[1:]):
            if np.hypot(*(b - a)) < 0.05:
                continue
            ts = {0.0, 1.0}
            for i in range(len(planes)):
                for j in range(i + 1, len(planes)):
                    za, zb = _z(planes[i], *a) - _z(planes[j], *a), _z(planes[i], *b) - _z(planes[j], *b)
                    if (za < 0) != (zb < 0) and za != zb:
                        ts.add(za / (za - zb))
            ts = sorted(ts)
            for t0, t1 in zip(ts[:-1], ts[1:]):
                if t1 - t0 < 1e-3:
                    continue
                p0, p1 = a + t0 * (b - a), a + t1 * (b - a)
                mid = (p0 + p1) / 2
                pl = min(planes, key=lambda q: _z(q, *mid))
                z0, z1 = _z(pl, *p0), _z(pl, *p1)
                out.append((1, [np.array([[*p0, ground], [*p1, ground], [*p1, z1], [*p0, z0]])]))
    return out

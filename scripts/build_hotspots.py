"""Hover areas for the viewer: the outline on screen of every named building, and a small disc for named points.
Each carries what the viewer's info card shows when it is clicked: kind, story, address, links, a photo, and the
title of a tour stop about the same place (scripts/build_history.py).

Polygons are in the render's screen meters (u right, v up from the image center line), one per view in config.VIEWS;
the viewer turns them into image pixels with map.json. Output: data/build/hotspots.json
"""

import hashlib
import json
import math
import subprocess
import urllib.parse

import numpy as np

from build_history import commons_thumb, download
from config import BUILD, CX, CY, VIEWS
from geo import screen as project

PHOTOS = BUILD / "photos"


def screen(x, y, z, look):
    """Local x east, y up, z south to screen meters, as in render/scene.js."""
    return np.stack(project(x, z, y, look), -1)


def hull(pts):
    pts = sorted(set(map(tuple, np.round(pts, 2))))
    if len(pts) < 3:
        return pts
    cross = lambda o, a, b: (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])
    lower, upper = [], []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return lower[:-1] + upper[:-1]


def main():
    lm = json.loads((BUILD / "landmarks.json").read_text())
    meta = json.loads((BUILD / "buildings.json").read_text())
    v = np.fromfile(BUILD / "buildings.bin", dtype=np.float32).reshape(-1, 12)
    parts = {}
    for i, m in enumerate(meta):
        parts.setdefault(str(m.get("id")).split("-")[0], []).append(i)
    out = []
    for h in lm["hotspots"]:
        if "pid" in h or "pids" in h:
            sel = np.isin(v[:, 9], [i for pid in h.get("pids", [h.get("pid")]) for i in parts.get(pid, [])])
            if not sel.any():
                continue
            pts = [screen(v[sel, 0], v[sel, 1], v[sel, 2], look) for look in VIEWS]
        else:
            a = np.linspace(0, 2 * np.pi, 16, endpoint=False)
            x, z = h["x"] - CX + 5 * np.cos(a), CY - h["y"] + 5 * np.sin(a)
            pts = [np.concatenate([screen(x, 0 * x, z, look), screen(x, 0 * x + 3, z, look)]) for look in VIEWS]
        spot = {k: h[k] for k in ("name", "note", "kind", "text", "address", "web", "more", "stop") if h.get(k)}
        spot["x"], spot["y"] = round(h["x"], 1), round(h["y"], 1)
        spot["poly"] = [[[round(float(p[0]), 2), round(float(p[1]), 2)] for p in hull(view)] for view in pts]
        if "photo" in h:
            spot["photo"] = photo(*h["photo"])
        out.append(spot)
    cats = [{"key": c["key"], "label": c["label"], "spots": walk([n for n, s in enumerate(out) if s["name"] in c["names"]], out)}
            for c in lm["categories"]]
    (BUILD / "hotspots.json").write_text(json.dumps({"views": list(VIEWS), "categories": cats, "spots": out}, ensure_ascii=False))
    print(f"{len(out)} hotspots, {sum('text' in s or 'stop' in s for s in out)} with a story, {len(cats)} categories")


def walk(idx, spots, start=(CX, CY)):
    """Order a category as a walk: the place nearest the middle of the map first, then each time the nearest next one."""
    pos = lambda i: (spots[i]["x"], spots[i]["y"])
    dist = lambda a, b: math.hypot(a[0] - b[0], a[1] - b[1])
    path, todo, here = [], idx[:], start
    while todo:
        nxt = min(todo, key=lambda i: dist(here, pos(i)))
        path.append(nxt)
        todo.remove(nxt)
        here = pos(nxt)
    return path


def photo(name, credit):
    """A Wikimedia Commons photo, downloaded once into data/build/photos/ (site.sh publishes that folder)."""
    dst = PHOTOS / f"{hashlib.md5(name.encode()).hexdigest()[:12]}.jpg"
    if not dst.exists():
        PHOTOS.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(download(commons_thumb(name)))
        subprocess.run(["magick", str(dst), "-resize", "640x640>", "-quality", "82", str(dst)], check=True)
    return {"src": f"photos/{dst.name}", "credit": credit, "url": f"https://commons.wikimedia.org/wiki/File:{urllib.parse.quote(name)}"}


if __name__ == "__main__":
    main()

# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy", "scipy", "scikit-image", "tifffile", "imagecodecs", "pillow"]
# ///
"""Find individual trees in the canopy height model: the HRDEM lidar surface minus the bare earth (scripts/dem.py).

Output (data/build/):
  trees.bin   float32 per tree: x, z, height, crown radius, crown base, kind, r, g, b, seed
  chm.npy     canopy height model at 0.5 m (row 0 north), used for hedges and shadows
"""

import json

import numpy as np
from PIL import Image, ImageDraw
from scipy import ndimage as ndi
from skimage.segmentation import watershed

import dem
from aerial import load_aerial
from config import BUILD, CX, CY, GROUND_RES, XMIN, YMAX

RES = 0.5


def fill_nearest(a):
    bad = np.isnan(a)
    idx = ndi.distance_transform_edt(bad, return_distances=False, return_indices=True)
    return a[tuple(idx)]


def footprint_mask(shape, grow_m):
    img = Image.new("L", (shape[1], shape[0]), 0)
    d = ImageDraw.Draw(img)
    for ring in json.load(open(BUILD / "footprints.json")):
        d.polygon([((x + CX - XMIN) / RES, (YMAX - (CY - z)) / RES) for x, z in ring], fill=255)
    m = np.array(img) > 0
    return ndi.binary_dilation(m, iterations=max(1, int(grow_m / RES)))


def main():
    dsm = dem.load("dsm", RES)
    dtm = dem.load("dtm", RES)
    chm = np.nan_to_num(dsm - dtm, nan=0.0)
    chm[chm < 0] = 0
    chm[footprint_mask(chm.shape, 1.0)] = 0
    np.save(BUILD / "chm.npy", chm.astype(np.float16))

    smooth = ndi.gaussian_filter(chm, 1.2)
    canopy = smooth > 2.5
    peaks = (smooth == ndi.maximum_filter(smooth, size=5)) & (smooth > 3.5)
    ys, xs = np.nonzero(peaks)
    hs = smooth[ys, xs]

    # Greedy suppression: taller trees claim a radius that grows with their height.
    order = np.argsort(-hs)
    taken = np.zeros_like(peaks)
    keep = []
    for i in order:
        y, x, h = ys[i], xs[i], hs[i]
        if taken[y, x]:
            continue
        keep.append(i)
        r = int(np.clip(0.12 * h + 1.0, 1.5, 4.5) / RES)
        taken[max(0, y - r) : y + r + 1, max(0, x - r) : x + r + 1] = True
    ys, xs, hs = ys[keep], xs[keep], hs[keep]

    markers = np.zeros(chm.shape, dtype=np.int32)
    markers[ys, xs] = np.arange(1, len(ys) + 1)
    labels = watershed(-smooth, markers, mask=canopy)
    areas = ndi.sum_labels(np.ones_like(labels), labels, index=np.arange(1, len(ys) + 1)) * RES * RES
    radius = np.clip(np.sqrt(areas / np.pi), 1.0, 7.5)

    # Crown color from the aerial photo: DRAPE is flown in spring with the leaves off, so evergreens are the green ones.
    aerial = load_aerial()
    scale = int(RES / GROUND_RES)
    # Block means at the canopy model's resolution, summed view by view: the whole photo as floats would not fit.
    hh, ww = aerial.shape[0] // scale, aerial.shape[1] // scale
    small = np.zeros((hh, ww, 3), np.float32)
    for dy in range(scale):
        for dx in range(scale):
            small += aerial[dy : hh * scale : scale, dx : ww * scale : scale]
    small /= scale * scale
    del aerial
    cols = np.stack([ndi.median(small[..., c], labels, index=np.arange(1, len(ys) + 1)) for c in range(3)], -1)
    cols = np.nan_to_num(cols, nan=90.0)
    r, g, b = cols[:, 0], cols[:, 1], cols[:, 2]
    evergreen = (g > r + 4) & (g > b + 4) & (g < 110)

    rng = np.random.default_rng(7)
    wx = XMIN + (xs + 0.5) * RES
    wy = YMAX - (ys + 0.5) * RES
    kind = evergreen.astype(np.float32)
    base = np.clip(hs * rng.uniform(0.25, 0.4, len(hs)), 1.2, 6)
    trees = np.stack(
        [wx - CX, CY - wy, hs, radius, base, kind, r / 255, g / 255, b / 255, rng.random(len(hs))], -1
    ).astype(np.float32)

    # Single trees mapped in OpenStreetMap that the canopy model missed (young street trees, planted since 2020).
    pts = np.array(json.loads((BUILD / "surfaces.json").read_text())["trees"]).reshape(-1, 2)
    have = np.zeros(chm.shape, bool)
    have[ys, xs] = True
    have = ndi.binary_dilation(have, iterations=int(3 / RES))
    extra = []
    for x, y in pts:
        col, row = int((x - XMIN) / RES), int((YMAX - y) / RES)
        if not (0 <= col < chm.shape[1] and 0 <= row < chm.shape[0]) or have[row, col]:
            continue
        h = max(float(chm[row, col]), 4.0 + rng.random() * 3)
        extra.append([x - CX, CY - y, h, 1.2 + h * 0.15, 1.5, 0, 0.35, 0.42, 0.25, rng.random()])
    if extra:
        trees = np.concatenate([trees, np.array(extra, dtype=np.float32)])
    # Masts show up in the canopy model as very tall, thin "trees".
    lm = BUILD / "landmarks.json"
    for m in json.loads(lm.read_text()).get("masts", []) if lm.exists() else []:
        trees = trees[np.hypot(trees[:, 0] - (m["x"] - CX), trees[:, 1] - (CY - m["y"])) > 6]
    trees.tofile(BUILD / "trees.bin")
    print(f"{len(trees)} trees ({len(extra)} from OpenStreetMap), {int(kind.sum())} evergreen, mean radius {radius.mean():.1f} m")


if __name__ == "__main__":
    main()

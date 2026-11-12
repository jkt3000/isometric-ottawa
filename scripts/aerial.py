"""Aerial photo mosaic at GROUND_RES, with row 0 at the north edge."""

import numpy as np
from PIL import Image

from config import BUILD, CHUNK, GROUND_RES, NX, NY, RAW, XMIN, YMAX


def load_aerial():
    cache = BUILD / "aerial.npy"
    if cache.exists():
        return np.load(cache, mmap_mode="r")  # 750 MB for the whole map: read from disk as needed
    px = int(CHUNK / GROUND_RES)
    out = np.zeros((NY * px, NX * px, 3), dtype=np.uint8)
    for ix in range(NX):
        for iy in range(NY):
            img = np.array(Image.open(RAW / "aerial" / f"{ix}_{iy}.jpg").convert("RGB"))
            row = (NY - 1 - iy) * px
            out[row : row + px, ix * px : ix * px + px] = img
    BUILD.mkdir(parents=True, exist_ok=True)
    np.save(cache, out)
    del out
    return np.load(cache, mmap_mode="r")


def world_to_px(xy):
    """RD meters (N,2) to fractional pixel coordinates (col, row) in the mosaic."""
    xy = np.asarray(xy, dtype=np.float64)
    return np.stack([(xy[:, 0] - XMIN) / GROUND_RES, (YMAX - xy[:, 1]) / GROUND_RES], axis=-1)

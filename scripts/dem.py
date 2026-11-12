"""The HRDEM surface (DSM) and bare-earth (DTM) models for the map area, row 0 at the north edge.

scripts/fetch.py stores them at 1 m on the map's UTM grid (data/raw/dem/{dsm,dtm}_1m.tif); this hands them out at
1 m or upsampled to 0.5 m, the resolution the Dutch AHN came in and the tree step works at.
"""

import numpy as np
import tifffile
from scipy import ndimage as ndi

from config import RAW, XMAX, XMIN, YMAX, YMIN

_CACHE = {}


def load(kind, res=1.0):
    key = (kind, res)
    if key in _CACHE:
        return _CACHE[key]
    a = tifffile.imread(RAW / "dem" / f"{kind}_1m.tif").astype(np.float32)
    a = a[: int(YMAX - YMIN), : int(XMAX - XMIN)]
    a[(a < -100) | (a > 9000)] = np.nan
    if np.isnan(a).any():
        idx = ndi.distance_transform_edt(np.isnan(a), return_distances=False, return_indices=True)
        a = a[tuple(idx)]
    if res != 1.0:
        a = ndi.zoom(a, 1.0 / res, order=1, grid_mode=True, mode="nearest")
    _CACHE[key] = a
    return a


def sample(a, xs, ys, res=1.0):
    """Bilinear values of a raster from load() at UTM points."""
    col = (np.asarray(xs) - XMIN) / res - 0.5
    row = (YMAX - np.asarray(ys)) / res - 0.5
    return ndi.map_coordinates(a, [row, col], order=1, mode="nearest")

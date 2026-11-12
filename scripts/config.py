"""Shared settings: the area we render, in NAD83(CSRS) / UTM zone 18N (EPSG:2959) meters.

Ottawa lies in UTM zone 18 (78 to 72 degrees west). The City of Ottawa itself publishes in MTM zone 9, but UTM is what
the national elevation model (HRDEM) and most Canadian open data use, and it keeps distortion under 0.04 % here.
"""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"
BUILD = ROOT / "data" / "build"

EPSG = 2959  # NAD83(CSRS) / UTM zone 18N

# Britannia Heights and Queensway Terrace North in Ottawa's west end, from the Queensway north to the Ottawa River at
# Britannia Bay and Mud Lake, and from Bayshore east to Woodroffe Avenue and Fairlawn Plaza: 4.5 x 3.5 km.
XMIN, YMIN, XMAX, YMAX = 436100, 5021550, 440600, 5025050
CX, CY = (XMIN + XMAX) / 2, (YMIN + YMAX) / 2

# Downloads and ground textures come in square chunks of this many meters.
CHUNK = 500
NX, NY = int((XMAX - XMIN) / CHUNK), int((YMAX - YMIN) / CHUNK)

# Ground raster resolution in meters per pixel.
GROUND_RES = 0.25

# The compass directions the map is rendered from (render/run.mjs --look); the first is the one the viewer opens on.
VIEWS = (45, 135, 225, 315)


def write_area():
    """The renderer and the viewer read the area from here."""
    BUILD.mkdir(parents=True, exist_ok=True)
    (BUILD / "area.json").write_text(
        json.dumps({"xmin": XMIN, "ymin": YMIN, "xmax": XMAX, "ymax": YMAX, "cx": CX, "cy": CY, "chunk": CHUNK, "nx": NX, "ny": NY})
    )

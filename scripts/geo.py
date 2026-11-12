"""UTM zone 18N (EPSG:2959) <-> latitude/longitude, and map projections.

Plain Python (Krueger's series on the GRS80 ellipsoid, accurate to well under a millimetre), so the small build
scripts run without pyproj. OpenStreetMap's WGS84 and NAD83(CSRS) differ by about a metre in Ottawa, which this
ignores, like the RD polynomial in the original Helenaveen map did.
"""

import math

from config import CX, CY, XMAX, XMIN, YMAX, YMIN

HX, HZ = (XMAX - XMIN) / 2, (YMAX - YMIN) / 2

_A, _F = 6378137.0, 1 / 298.257222101  # GRS80
_K0, _LON0, _FE = 0.9996, math.radians(-75.0), 500000.0  # zone 18: central meridian 75 W
_N = _F / (2 - _F)
_AA = _A / (1 + _N) * (1 + _N**2 / 4 + _N**4 / 64)
_ALPHA = (_N / 2 - 2 * _N**2 / 3 + 5 * _N**3 / 16, 13 * _N**2 / 48 - 3 * _N**3 / 5, 61 * _N**3 / 240)
_BETA = (_N / 2 - 2 * _N**2 / 3 + 37 * _N**3 / 96, _N**2 / 48 + _N**3 / 15, 17 * _N**3 / 480)
_DELTA = (2 * _N - 2 * _N**2 / 3 - 2 * _N**3, 7 * _N**2 / 3 - 8 * _N**3 / 5, 56 * _N**3 / 15)


def wgs_to_utm(lat, lon):
    phi, lam = math.radians(lat), math.radians(lon) - _LON0
    e = 2 * math.sqrt(_N) / (1 + _N)
    t = math.sinh(math.atanh(math.sin(phi)) - e * math.atanh(e * math.sin(phi)))
    xi, eta = math.atan2(t, math.cos(lam)), math.atanh(math.sin(lam) / math.sqrt(1 + t * t))
    x = eta + sum(a * math.cos(2 * j * xi) * math.sinh(2 * j * eta) for j, a in enumerate(_ALPHA, 1))
    y = xi + sum(a * math.sin(2 * j * xi) * math.cosh(2 * j * eta) for j, a in enumerate(_ALPHA, 1))
    return _FE + _K0 * _AA * x, _K0 * _AA * y


def utm_to_wgs(x, y):
    xi, eta = y / (_K0 * _AA), (x - _FE) / (_K0 * _AA)
    xi_ = xi - sum(b * math.sin(2 * j * xi) * math.cosh(2 * j * eta) for j, b in enumerate(_BETA, 1))
    eta_ = eta - sum(b * math.cos(2 * j * xi) * math.sinh(2 * j * eta) for j, b in enumerate(_BETA, 1))
    chi = math.asin(math.sin(xi_) / math.cosh(eta_))
    phi = chi + sum(d * math.sin(2 * j * chi) for j, d in enumerate(_DELTA, 1))
    return math.degrees(phi), math.degrees(_LON0 + math.atan2(math.sinh(eta_), math.cos(xi_)))


# The scripts came from a Dutch map in RD coordinates; these names keep them working unchanged.
wgs_to_rd, rd_to_wgs = wgs_to_utm, utm_to_wgs


def screen(x, z, h=0.0, look=45):
    """Local meters (x east, z south, h up) to the render's screen meters (u right, v up) for a camera looking `look`
    degrees from north, 30 degrees down (render/scene.js). Works on numbers and numpy arrays alike."""
    a = math.radians(look)
    return x * math.cos(a) + z * math.sin(a), (x * math.sin(a) - z * math.cos(a)) * 0.5 + h * math.cos(math.radians(30))


def rd_to_image(x, y, scale=4, height_m=0.0):
    """Pixel position in the full isometric render of a map point at a height above ground (see render/scene.js)."""
    width = round(2 * (HX + HZ) / math.sqrt(2) * scale)
    lx, lz = x - CX, CY - y
    u = (lx + lz) / math.sqrt(2)
    v = (lx - lz) / (2 * math.sqrt(2)) + height_m * math.cos(math.radians(30))
    top = (HX + HZ) / math.sqrt(2) * 0.5 + 32
    return u * scale + width / 2, (top - v) * scale

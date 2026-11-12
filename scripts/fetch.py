# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy", "rasterio", "pyproj", "pillow", "pmtiles"]
# ///
"""Download the raw open data for the area into data/raw/.

  uv run scripts/fetch.py            everything that is not there yet
  uv run scripts/fetch.py dem osm    only some parts: dem, aerial, osm

Sources (see DATA.md for the licences):
  dem      HRDEM, NRCan's lidar elevation model: surface (DSM) and bare earth (DTM) at 1 m, through the CanElevation
           STAC catalogue and cloud-optimized GeoTIFFs on AWS, reprojected onto the map's UTM grid.
  aerial   DRAPE 2024, Ontario's 16 cm spring orthophoto of Eastern Ontario, from the PMTiles copy that Data for Canada
           keeps on Source Cooperative, reprojected to 25 cm UTM chunks.
  osm      OpenStreetMap through Overpass: buildings, land use, water, roads and paths, addresses, points of interest
           and walking routes.

Everything is written as it goes and skipped when present, so a run that breaks off picks up where it stopped.
"""

import gzip
import json
import math
import os
import sys
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import CHUNK, EPSG, GROUND_RES, NX, NY, RAW, XMAX, XMIN, YMAX, YMIN  # noqa: E402

UA = {"User-Agent": "isometric-ottawa/0.1 (open-data map; github.com/jankeesvw/isometric-helenaveen fork)"}

STAC = "https://datacube.services.geo.ca/stac/api/search"
DEM_COLLECTIONS = ("hrdem-mosaic-1m", "hrdem-lidar")
DRAPE = [
    "https://data.source.coop/dataforcanada/d4c-datapkg-orthoimagery/processed/"
    "ca-on_province_of_ontario-2024A000235_drape_eastern_ontario_orthoimagery_d4c-datapkg-orthoimagery_2024_16cm_v0.1.0-beta.2.pmtiles",
    "https://data.source.coop/dataforcanada/d4c-datapkg-orthoimagery/processed/"
    "ca-on_province_of_ontario-2024A000235_drape_eastern_ontario_orthoimagery_2024_16cm_v0.1.0-beta.pmtiles",
]
AERIAL_ZOOM = 19  # 0.21 m per pixel at 45 degrees north, a little finer than the 0.25 m ground raster

os.environ.setdefault("GDAL_DISABLE_READDIR_ON_OPEN", "EMPTY_DIR")
os.environ.setdefault("CPL_VSIL_CURL_ALLOWED_EXTENSIONS", ".tif,.TIF,.tiff")
os.environ.setdefault("GDAL_HTTP_MULTIRANGE", "YES")
os.environ.setdefault("GDAL_HTTP_MERGE_CONSECUTIVE_RANGES", "YES")


def log(*a):
    print(*a, file=sys.stderr, flush=True)


def get(url, data=None, headers=None, tries=5, timeout=180):
    for attempt in range(tries):
        try:
            req = urllib.request.Request(url, data=data, headers={**UA, **(headers or {})})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except Exception as e:  # noqa: BLE001
            if attempt == tries - 1:
                raise
            log(f"  retry {attempt + 1} {url[:90]}: {e}")
            time.sleep(2 * (attempt + 1))


def bbox_wgs(pad=0.0):
    """(south, west, north, east) around the map area."""
    from pyproj import Transformer

    t = Transformer.from_crs(EPSG, 4326, always_xy=True)
    xs, ys = [XMIN - pad, XMAX + pad], [YMIN - pad, YMAX + pad]
    lons, lats = zip(*(t.transform(x, y) for x in xs for y in ys))
    return min(lats), min(lons), max(lats), max(lons)


# --- Elevation: HRDEM DSM and DTM -----------------------------------------------------------------------------------


def stac_items(collection):
    s, w, n, e = bbox_wgs(50)
    body = json.dumps({"collections": [collection], "bbox": [w, s, e, n], "limit": 100}).encode()
    page = json.loads(get(STAC, data=body, headers={"Content-Type": "application/json"}))
    return page.get("features", [])


def asset_href(item, want):
    assets = item.get("assets", {})
    if want in assets:
        return assets[want]["href"]
    for k, a in assets.items():
        if want in k.lower() and a.get("href", "").lower().endswith((".tif", ".tiff")) and "hillshade" not in k.lower():
            return a["href"]
    return None


def fetch_dem():
    import numpy as np
    import rasterio
    from rasterio.transform import from_origin
    from rasterio.warp import Resampling, reproject, transform_bounds
    from rasterio.windows import from_bounds

    d = RAW / "dem"
    d.mkdir(parents=True, exist_ok=True)
    if all((d / f"{k}_1m.tif").exists() for k in ("dsm", "dtm")):
        return
    w, h = int(XMAX - XMIN), int(YMAX - YMIN)
    dst_t = from_origin(XMIN, YMAX, 1.0, 1.0)
    dst_crs = f"EPSG:{EPSG}"
    items = []
    for coll in DEM_COLLECTIONS:
        items = stac_items(coll)
        log(f"dem: {len(items)} {coll} items over the area")
        if items:
            break
    if not items:
        raise SystemExit("dem: the HRDEM STAC catalogue has nothing over this area")
    # Newest acquisitions first, so they win where projects overlap.
    items.sort(key=lambda it: it.get("properties", {}).get("datetime") or it.get("properties", {}).get("end_datetime") or "", reverse=True)
    meta = []
    for kind in ("dsm", "dtm"):
        out = np.full((h, w), np.nan, np.float32)
        for it in items:
            href = asset_href(it, kind)
            if not href:
                log(f"  {it['id']}: no {kind} asset among {list(it.get('assets', {}))}")
                continue
            with rasterio.open(href) as src:
                l, b, r, t = transform_bounds(dst_crs, src.crs, XMIN - 10, YMIN - 10, XMAX + 10, YMAX + 10, densify_pts=21)
                win = from_bounds(l, b, r, t, src.transform).round_offsets().round_lengths()
                arr = src.read(1, window=win, boundless=True, fill_value=src.nodata if src.nodata is not None else -32767,
                               out_dtype="float32")
                nod = src.nodata if src.nodata is not None else -32767
                arr[(arr == nod) | (arr < -500) | (arr > 9000)] = np.nan
                tmp = np.full((h, w), np.nan, np.float32)
                reproject(arr, tmp, src_transform=src.window_transform(win), src_crs=src.crs, src_nodata=np.nan,
                          dst_transform=dst_t, dst_crs=dst_crs, dst_nodata=np.nan, resampling=Resampling.bilinear)
                fill = np.isnan(out) & ~np.isnan(tmp)
                out[fill] = tmp[fill]
                meta.append({"kind": kind, "item": it["id"], "href": href, "crs": str(src.crs), "filled": int(fill.sum())})
                log(f"  {kind} from {it['id']}: {fill.sum() / out.size:.0%} of the area")
            if not np.isnan(out).any():
                break
        missing = float(np.isnan(out).mean())
        if missing > 0.5:
            raise SystemExit(f"dem: {kind} covers only {1 - missing:.0%} of the area")
        with rasterio.open(d / f"{kind}_1m.tif", "w", driver="GTiff", width=w, height=h, count=1, dtype="float32",
                           crs=dst_crs, transform=dst_t, nodata=np.nan, compress="deflate", predictor=3, tiled=True) as dst:
            dst.write(out, 1)
        log(f"dem: wrote {kind}_1m.tif ({missing:.1%} empty)")
    (d / "sources.json").write_text(json.dumps(meta, indent=1))


# --- Aerial photo: DRAPE 2024 from PMTiles ---------------------------------------------------------------------------


class PMTiles:
    """A PMTiles v3 archive over HTTP range requests, with its directories cached."""

    def __init__(self, url):
        from pmtiles.tile import deserialize_directory, deserialize_header

        self.url, self._dd = url, deserialize_directory
        self.header = deserialize_header(self.range(0, 127))
        h = self.header
        self.root = self._dd(self.range(h["root_offset"], h["root_length"]))
        self.leaves = {}

    def range(self, offset, length):
        return get(self.url, headers={"Range": f"bytes={offset}-{offset + length - 1}"})

    def leaf(self, offset, length):
        key = (offset, length)
        if key not in self.leaves:
            self.leaves[key] = self._dd(self.range(self.header["leaf_directory_offset"] + offset, length))
        return self.leaves[key]

    def tile(self, z, x, y):
        from pmtiles.tile import Compression, find_tile, zxy_to_tileid

        tid = zxy_to_tileid(z, x, y)
        directory = self.root
        for _ in range(4):
            e = find_tile(directory, tid)
            if e is None:
                return None
            if e.run_length == 0:
                directory = self.leaf(e.offset, e.length)
                continue
            data = self.range(self.header["tile_data_offset"] + e.offset, e.length)
            if self.header.get("tile_compression") == Compression.GZIP:
                data = gzip.decompress(data)
            return data
        return None


def open_drape():
    for url in DRAPE:
        try:
            p = PMTiles(url)
            log(f"aerial: {url.rsplit('/', 1)[-1]}, zoom {p.header['min_zoom']}-{p.header['max_zoom']}")
            return p
        except Exception as e:  # noqa: BLE001
            log(f"aerial: {url.rsplit('/', 1)[-1]}: {e}")
    raise SystemExit("aerial: the DRAPE 2024 PMTiles are not reachable")


R = 6378137.0


def merc(lon, lat):
    return R * math.radians(lon), R * math.log(math.tan(math.pi / 4 + math.radians(lat) / 2))


def fetch_aerial():
    import io

    import numpy as np
    from PIL import Image
    from pyproj import Transformer
    from rasterio.transform import from_origin
    from rasterio.warp import Resampling, reproject

    d, cache = RAW / "aerial", RAW / "aerial_tiles"
    d.mkdir(parents=True, exist_ok=True)
    cache.mkdir(parents=True, exist_ok=True)
    todo = [(i, j) for i in range(NX) for j in range(NY) if not (d / f"{i}_{j}.jpg").exists()]
    if not todo:
        return
    pm = open_drape()
    z = min(AERIAL_ZOOM, int(pm.header["max_zoom"]))
    ts = 2 * math.pi * R / 2**z
    to_wgs = Transformer.from_crs(EPSG, 4326, always_xy=True)

    def tiles_for(x0, y0, x1, y1, pad=20):
        corners = [merc(*to_wgs.transform(x, y)) for x in (x0 - pad, x1 + pad) for y in (y0 - pad, y1 + pad)]
        mx0, mx1 = min(c[0] for c in corners), max(c[0] for c in corners)
        my0, my1 = min(c[1] for c in corners), max(c[1] for c in corners)
        tx0, tx1 = int((mx0 + math.pi * R) // ts), int((mx1 + math.pi * R) // ts)
        ty0, ty1 = int((math.pi * R - my1) // ts), int((math.pi * R - my0) // ts)
        return tx0, ty0, tx1, ty1

    def fetch_tile(xy):
        x, y = xy
        f = cache / f"{z}_{x}_{y}"
        if f.exists():
            return
        data = pm.tile(z, x, y)
        f.write_bytes(data or b"")

    tx0, ty0, tx1, ty1 = tiles_for(XMIN, YMIN, XMAX, YMAX)
    need = [(x, y) for x in range(tx0, tx1 + 1) for y in range(ty0, ty1 + 1)]
    log(f"aerial: {len(need)} tiles at zoom {z}")
    done = 0
    with ThreadPoolExecutor(12) as pool:
        for _ in pool.map(fetch_tile, need):
            done += 1
            if done % 500 == 0:
                log(f"  {done}/{len(need)} tiles")

    px = int(CHUNK / GROUND_RES)
    empty = 0
    for i, j in todo:
        x0, y0 = XMIN + i * CHUNK, YMIN + j * CHUNK
        a0, b0, a1, b1 = tiles_for(x0, y0, x0 + CHUNK, y0 + CHUNK)
        tw = 256
        mosaic = None
        for tx in range(a0, a1 + 1):
            for ty in range(b0, b1 + 1):
                raw = (cache / f"{z}_{tx}_{ty}").read_bytes() if (cache / f"{z}_{tx}_{ty}").exists() else b""
                if not raw:
                    empty += 1
                    continue
                img = np.array(Image.open(io.BytesIO(raw)).convert("RGB"))
                if mosaic is None:
                    tw = img.shape[0]
                    mosaic = np.zeros(((b1 - b0 + 1) * tw, (a1 - a0 + 1) * tw, 3), np.uint8)
                mosaic[(ty - b0) * tw : (ty - b0 + 1) * tw, (tx - a0) * tw : (tx - a0 + 1) * tw] = img[:tw, :tw]
        out = np.zeros((3, px, px), np.uint8)
        if mosaic is not None:
            src_t = from_origin(a0 * ts - math.pi * R, math.pi * R - b0 * ts, ts / tw, ts / tw)
            reproject(np.moveaxis(mosaic, -1, 0), out, src_transform=src_t, src_crs="EPSG:3857",
                      dst_transform=from_origin(x0, y0 + CHUNK, GROUND_RES, GROUND_RES), dst_crs=f"EPSG:{EPSG}",
                      resampling=Resampling.bilinear)
        Image.fromarray(np.moveaxis(out, 0, -1)).save(d / f"{i}_{j}.jpg", quality=92)
    log(f"aerial: {len(todo)} chunks written" + (f", {empty} tile reads came back empty (no imagery there)" if empty else ""))


# --- OpenStreetMap ---------------------------------------------------------------------------------------------------


def overpass(out, q, what):
    """Run an Overpass query into out (Overpass is often busy, so try a few servers)."""
    if out.exists():
        return
    for host in ("overpass-api.de/api/interpreter", "overpass.private.coffee/api/interpreter",
                 "maps.mail.ru/osm/tools/overpass/api/interpreter"):
        try:
            data = get(f"https://{host}", data=urllib.parse.urlencode({"data": q}).encode(), tries=2, timeout=400)
            json.loads(data)  # an error page is not JSON
            out.with_suffix(".tmp").write_bytes(data)
            out.with_suffix(".tmp").rename(out)
            log(f"osm {what}: {len(data) / 1e6:.1f} MB")
            return
        except Exception as e:  # noqa: BLE001
            log(f"overpass {host}: {e}")
    log(f"OpenStreetMap unavailable; {what} are skipped")


def fetch_osm():
    s, w, n, e = bbox_wgs(20)
    bb = f"{s:.6f},{w:.6f},{n:.6f},{e:.6f}"
    jobs = {
        # Roads, cycle paths, footpaths and tracks with node ids, for the ground and the traffic.
        "osm_roads.json": ("roads and paths", f"""[out:json][timeout:240];
( way["highway"]({bb}); );
out geom;"""),
        "osm_landmarks.json": ("pitches and graveyards", f"""[out:json][timeout:120];
( nwr["military"]({bb}); way["leisure"="pitch"]({bb}); nwr["landuse"="cemetery"]({bb}); nwr["amenity"="grave_yard"]({bb});
  nwr["amenity"="place_of_worship"]({bb}); nwr["tourism"]({bb}); nwr["historic"]({bb}); );
out geom tags;"""),
        "osm_routes.json": ("walking routes", f"""[out:json][timeout:180];
( relation["route"~"^(hiking|foot|walking)$"]({bb}); node["rwn_ref"]({bb}); );
out geom;"""),
        "osm_addresses.json": ("addresses", f"""[out:json][timeout:120];
( nwr["addr:housenumber"]({bb}); );
out center tags;"""),
    }
    for name, (what, q) in jobs.items():
        overpass(RAW / name, q, what)
    # The ground, in a few smaller queries that Overpass copes with better than one big one, merged into one file.
    # Large water relations (the Ottawa River) come whole and are cut to the map by scripts/build_surfaces.py.
    parts = {
        "buildings": f'way["building"]({bb}); relation["building"]({bb}); way["building:part"]({bb});',
        "land use": f'way["landuse"]({bb}); relation["landuse"]({bb}); way["leisure"]({bb}); relation["leisure"]({bb}); '
                    f'way["golf"]({bb}); nwr["place"~"^(island|islet)$"]({bb});',
        "nature and trees": f'way["natural"]({bb}); node["natural"="tree"]({bb}); way["water"]({bb}); way["waterway"]({bb});',
        "water relations": f'relation["natural"]({bb}); relation["water"]({bb}); relation["waterway"]({bb});',
        "amenities": f'way["amenity"]({bb}); relation["amenity"]({bb}); way["man_made"]({bb}); way["railway"]({bb}); '
                     f'way["area:highway"]({bb}); way["highway"]["area"="yes"]({bb}); way["barrier"~"^(wall|retaining_wall)$"]({bb}); '
                     f'nwr["tourism"]({bb}); nwr["historic"]({bb}); node["name"]["amenity"]({bb}); node["name"]["shop"]({bb});',
    }
    merged = RAW / "osm_ground.json"
    if not merged.exists():
        els, seen = [], set()
        for i, (what, body) in enumerate(parts.items()):
            part = RAW / f"osm_ground_{i}.json"
            overpass(part, f"[out:json][timeout:300];\n( {body} );\nout geom tags;", what)
            if not part.exists():
                log(f"osm: {what} missing; run fetch.py again to retry")
                return
            for el in json.loads(part.read_text())["elements"]:
                if (el["type"], el["id"]) not in seen:
                    seen.add((el["type"], el["id"]))
                    els.append(el)
        merged.write_text(json.dumps({"elements": els}))
        log(f"osm ground: {len(els)} features")


def check_area():
    """Downloads are for one area. When scripts/config.py has moved it, the old files go (the aerial tile cache stays:
    it is the same for any area)."""
    here = {"bbox": [XMIN, YMIN, XMAX, YMAX], "epsg": EPSG}
    mark = RAW / "area.json"
    old = json.loads(mark.read_text()) if mark.exists() else None
    if old != here:
        stale = [f for f in RAW.glob("osm_*.json")] + list((RAW / "dem").glob("*")) + list((RAW / "aerial").glob("*.jpg"))
        if stale:
            log(f"area changed from {old and old['bbox']} to {here['bbox']}: removing {len(stale)} old downloads")
        for f in stale:
            f.unlink()
    mark.write_text(json.dumps(here))


def main():
    RAW.mkdir(parents=True, exist_ok=True)
    check_area()
    parts = sys.argv[1:] or ["osm", "dem", "aerial"]
    s, w, n, e = bbox_wgs()
    log(f"area: {XMAX - XMIN:.0f} x {YMAX - YMIN:.0f} m, {s:.5f},{w:.5f} to {n:.5f},{e:.5f}")
    for p in parts:
        {"dem": fetch_dem, "aerial": fetch_aerial, "osm": fetch_osm}[p]()
    log(f"done: {sum(f.stat().st_size for f in RAW.rglob('*') if f.is_file()) / 1e6:.0f} MB in {RAW}")


if __name__ == "__main__":
    main()

# Isometric Ottawa West

A zoomable isometric pixel-art map of Ottawa's west end: 4.5 × 3.5 km from Bayshore to Woodroffe Avenue and from
the Queensway north to Britannia Bay, with Britannia, Mud Lake, Britannia Heights and Queensway Terrace North. It is
a fork of [Isometric Helenaveen](https://github.com/jankeesvw/isometric-helenaveen) by
[@jankeesvw](https://x.com/jankeesvw), with the Dutch open data swapped for Canadian open data. Rendered procedurally,
so it is exact, seamless and rebuilds from scratch.

## Pipeline

Run `./build.sh` for everything. The steps:

1. `scripts/fetch.py` downloads the raw data into `data/raw/`, on the map's grid in NAD83(CSRS) / UTM zone 18N
   (EPSG:2959; `scripts/config.py` holds the area, `scripts/geo.py` converts to and from latitude and longitude):
   - **elevation**: NRCan's HRDEM lidar mosaic, surface (DSM) and bare earth (DTM) at 1 m, found through the
     CanElevation STAC catalogue and read as cloud-optimized GeoTIFFs from AWS;
   - **aerial photo**: Ontario's DRAPE 2024 orthophoto (16 cm, spring 2024), read tile by tile from Data for
     Canada's PMTiles copy on Source Cooperative and reprojected to 25 cm UTM chunks;
   - **OpenStreetMap** through Overpass: buildings, land use, water (the Ottawa River multipolygon whole), roads and
     paths, addresses, points of interest and trails.

   It remembers the area it downloaded and starts over when `config.py` moves it. Run it where those servers are
   reachable; it needs only `uv` (`uv run scripts/fetch.py`).
2. `scripts/build_surfaces.py` turns OpenStreetMap into what the Dutch BGT gave for free: a wall-to-wall ground of
   polygons with a style each (lawn or paving under everything, then land use, water, and the roads and paths
   buffered from their centre lines to their width), plus the building footprints and single mapped trees.
3. `scripts/landmarks.py` finds the places with a story by their OpenStreetMap name (`scripts/osm_places.py`):
   Britannia Beach and pier, the Yacht Club, Mud Lake, the purification plant, the Olde Forge, the churches, Frank
   Ryan Park, Elmhurst Woods, IKEA and two Bridgeheads, each with a short story in English and French.
4. `scripts/build_buildings.py` builds every OpenStreetMap footprint from the lidar: `scripts/roof_fit.py` samples
   the DSM inside it and fits a flat, gable or hip roof over its oriented bounding box (a roof is the lowest of a few
   planes, so it works for any footprint shape, and walls rise to the roof above them, gable ends included). Houses
   newer than the lidar get their height from OpenStreetMap or a default. The aerial photo is laid on the roofs as in
   the original (`scripts/roof_atlas.py`).
5. `scripts/build_trees.py` finds trees in the canopy height model (DSM minus DTM, `scripts/dem.py`) and adds the
   single trees mapped in OpenStreetMap.
6. `scripts/build_ground.py` paints the ground in 500 m bands, so it fits in a few GB of memory, with baked shadows.
7. `render/scene.js` and `render/run.mjs` render it in headless Chromium, as in the original, with a palette of
   Ottawa houses (postwar brick and siding). Without a GPU, `RENDER_GL=swiftshader` renders on the CPU, and `CHROME`
   points at a Chrome to use.
8. `site.sh` cuts the Deep Zoom tiles and `viewer/index.html` shows them. The viewer speaks English and French
   (`?lang=fr`, or the browser's language); the French of the stories is in `data/build/fr.json`, written by
   `scripts/build_history.py`, which also builds the tour **Britannia back then** (1818 to the floods of 2017).
   Swimmers bob off Britannia Beach, street lamps line every street in the evening, and geese fly over.
9. `deploy.sh` publishes `site/` to GitHub Pages.

The spots the viewer opens on, the middle the birds fly over and the swimmers' place are `SPOTS` in
`viewer/index.html`.

## Data

HRDEM (Natural Resources Canada, Open Government Licence – Canada), DRAPE 2024 (Geospatial Ontario, Open Government
Licence – Ontario), OpenStreetMap (© OpenStreetMap contributors, ODbL). See [DATA.md](DATA.md) for the details and
for what was considered and left out.

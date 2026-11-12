# Data sources and licences

Every source is open data whose licence allows a derived map like this one to be published, with attribution. The
viewer credits them in its corner (`viewer/index.html`, `#readout .credits`).

| What | Source | Licence | Attribution |
| --- | --- | --- | --- |
| Surface (DSM) and bare-earth (DTM) elevation, 1 m, from airborne lidar | [High Resolution Digital Elevation Model (HRDEM) mosaic](https://open.canada.ca/data/en/dataset/957782bf-847c-4644-a757-e383c0057995), CanElevation Series, Natural Resources Canada, through its [STAC catalogue](https://datacube.services.geo.ca/stac/api/) and [cloud-optimized GeoTIFFs on AWS](https://registry.opendata.aws/canelevation-dem/) | [Open Government Licence – Canada](https://open.canada.ca/en/open-government-licence-canada) | Contains information licensed under the Open Government Licence – Canada. |
| Aerial photo, 16 cm, spring 2024 | [Digital Raster Acquisition Project Eastern Ontario (DRAPE) 2024](https://data.ontario.ca/en/dataset/open-ontario-imagery), Geospatial Ontario; read from the PMTiles copy that [Data for Canada](https://github.com/dataforcanada/d4c-datapkg-orthoimagery) publishes on Source Cooperative | [Open Government Licence – Ontario](https://www.ontario.ca/page/open-government-licence-ontario) | Contains information licensed under the Open Government Licence – Ontario. |
| Buildings, roads, paths, land use, water, addresses, points of interest, trails | [OpenStreetMap](https://www.openstreetmap.org), through the Overpass API. Ottawa's building footprints and addresses in OpenStreetMap were largely imported from City of Ottawa open data (2016–17). | [Open Database License (ODbL)](https://www.openstreetmap.org/copyright) | © OpenStreetMap contributors |
| Music: "My Street" | [congusbongus on OpenGameArt](https://opengameart.org/content/my-street) | CC0 | — |

The stories in `scripts/landmarks.py` and `scripts/build_history.py` are written for this map; each tour stop names
the source of its facts (Wikipedia, Heritage Ottawa, the Historical Society of Ottawa, the OC Transpo virtual museum,
Kitchissippi Times, the NCC, Britannia Yacht Club, Bridgehead, IKEA).

## What was considered and not used

- **City of Ottawa 3D Buildings LOD1** (open data, heights from the city's 2020 lidar) gives flat boxes only and comes
  as per-neighbourhood DXF/FileGDB downloads; fitting roofs to the HRDEM lidar inside the OpenStreetMap footprints
  (`scripts/roof_fit.py`) gives the same heights and adds gable and hip roofs. The LOD2.5 model with roof shapes is
  sold, not open.
- **City of Ottawa orthophotos** (6 cm, 2024) are sold, not open; DRAPE 2024 is open.
- **Statistics Canada's Open Database of Buildings** has the same Ottawa footprints as OpenStreetMap, without names
  or building types.

## The code

This is a fork of [Isometric Helenaveen](https://github.com/jankeesvw/isometric-helenaveen) by Jankees van Woezik,
which invites exactly this ("Make your own village"). That repository has no licence file; ask its author before
reusing its code for anything beyond a personal map like this one.

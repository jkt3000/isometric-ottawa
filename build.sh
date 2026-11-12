#!/usr/bin/env bash
# Build the isometric map of Ottawa's west end from open data, end to end.
#   ./build.sh            fetch, preprocess, render at 4 px/m, cut Deep Zoom tiles into site/
#   SCALE=3 ./build.sh    render at another resolution (update SCALE/WIDTH/HEIGHT in viewer/index.html to match)
set -euo pipefail
builtin cd "$(dirname "$0")"
SCALE="${SCALE:-4}"

uv run --quiet scripts/fetch.py
(builtin cd scripts && uv run --quiet build_surfaces.py)
(builtin cd scripts && uv run --quiet landmarks.py)
(builtin cd scripts && uv run --quiet build_buildings.py)
(builtin cd scripts && uv run --quiet build_trees.py)
(builtin cd scripts && uv run --quiet build_ground.py)
(builtin cd scripts && python3 build_places.py)
(builtin cd scripts && uv run --quiet --with numpy python build_hotspots.py)
(builtin cd scripts && python3 build_routes.py)
(builtin cd scripts && uv run --quiet build_traffic.py)
(builtin cd scripts && uv run --quiet --with shapely python build_history.py)

[ -d node_modules ] || bun install
# Every view in scripts/config.py, each with the mask of buildings and trees that the viewer draws again over the
# cars and walkers behind them.
VIEWS=$(builtin cd scripts && python3 -c "from config import VIEWS; print(*VIEWS)")
for look in $VIEWS; do
  node render/run.mjs all --scale "$SCALE" --tile 2048 --kuwahara 2 --look "$look"
  node render/run.mjs all --scale "$SCALE" --tile 2048 --pass objects --look "$look"
done

layout="out/tiles-$SCALE/layout.json"
read -r W H C < <(python3 -c "import json; d=json.load(open('$layout')); print(d['width'], d['height'], d['cols'])")
join() {  # tile folder, output image, bands to keep
  vips arrayjoin "$(/bin/ls "$1"/*.png | tr '\n' ' ')" out/joined.v --across "$C"
  vips extract_area out/joined.v out/cropped.v 0 0 "$W" "$H"
  vips extract_band out/cropped.v "$2[compression=1]" 0 --n "$3"
  rm -f out/joined.v out/cropped.v
}
for look in $VIEWS; do
  suffix=$([ "$look" = 45 ] && echo "" || echo "-v$look")
  join "out/tiles-$SCALE$suffix" "out/full-$SCALE$suffix.png" 3
  join "out/tiles-$SCALE-objects$suffix" "out/objects-$SCALE$suffix.png" 1
done

./site.sh

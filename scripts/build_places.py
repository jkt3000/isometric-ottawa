"""The viewer's search list: named places first, then every OpenStreetMap address inside the map."""

import json

from config import BUILD, RAW, XMAX, XMIN, YMAX, YMIN
from geo import wgs_to_utm


def addresses():
    """(x, y, "1234 Street Name") for every address in data/raw/osm_addresses.json."""
    path = RAW / "osm_addresses.json"
    if not path.exists():
        return
    seen = set()
    for el in json.loads(path.read_text())["elements"]:
        t = el.get("tags", {})
        num, street = t.get("addr:housenumber"), t.get("addr:street")
        p = el if "lat" in el else el.get("center")
        if not (num and street and p):
            continue
        label = f"{num} {street}" + (f", unit {t['addr:unit']}" if t.get("addr:unit") else "")
        if label in seen:
            continue
        seen.add(label)
        x, y = wgs_to_utm(p["lat"], p["lon"])
        yield x, y, label


def main():
    lm = json.loads((BUILD / "landmarks.json").read_text())
    places = [{"name": p["name"], "note": p.get("note"), "x": round(p["x"], 1), "y": round(p["y"], 1), "h": p["h"]} for p in lm["places"]]
    for x, y, label in addresses():
        if XMIN <= x <= XMAX and YMIN <= y <= YMAX:
            places.append({"name": label, "x": round(x, 1), "y": round(y, 1), "h": 4})
    (BUILD / "places.json").write_text(json.dumps(places, ensure_ascii=False))
    print(f"{len(places)} places")


if __name__ == "__main__":
    main()

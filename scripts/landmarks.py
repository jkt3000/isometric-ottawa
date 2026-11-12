# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy", "shapely"]
# ///
"""Named places of Ottawa's west end: their stories for the viewer, and style overrides for their buildings.

Each place is found in OpenStreetMap by its name (scripts/osm_places.py), so it sits on the right building without
coordinates typed by hand; `latlon` pins the few that have no name there. Every text has its French beside it, which
goes into fr.json for the viewer.

Sources: Wikipedia (Britannia, Britannia Village, Britannia Yacht Club, Queensway Terrace North, Bridgehead Coffee),
Heritage Ottawa, the Historical Society of Ottawa, the NCC, the Ottawa Field-Naturalists' Club, Bridgehead and IKEA.

Output: data/build/landmarks.json, and the French of every text in data/build/fr_landmarks.json
"""

import json

from config import BUILD
from geo import wgs_to_utm
from osm_places import building_at, find

HSO = "https://www.historicalsocietyottawa.ca/publications/ottawa-stories/significant-technological-changes-in-the-city/britannia-on-the-bay-75"

# A place: name, kind and story (each with its French), how to find it, and for buildings a style for the render.
PLACES = [
    {
        "name": "Britannia Beach", "fr_name": "Plage Britannia",
        "find": r"Britannia Beach", "kind": "Beach · since 1900", "fr_kind": "Plage · depuis 1900",
        "text": "The Ottawa Electric Railway opened a park here in 1900 to sell streetcar rides: for five cents, a "
                "half-hour trip out Richmond Road from downtown. On fine summer Sundays as many as 25,000 people came "
                "to swim, picnic and walk the pier. The anchor of the steamer G.B. Greene, which took excursions up "
                "the river until the 1940s, still lies at the beach.",
        "fr_text": "L'Ottawa Electric Railway ouvre un parc ici en 1900 pour vendre des trajets en tramway : pour cinq "
                   "cents, une demi-heure depuis le centre-ville par le chemin Richmond. Les beaux dimanches d'été, "
                   "jusqu'à 25 000 personnes venaient s'y baigner, pique-niquer et marcher sur la jetée. L'ancre du "
                   "vapeur G.B. Greene, qui faisait des excursions sur la rivière jusqu'aux années 1940, est encore "
                   "sur la plage.",
        "more": HSO,
    },
    {
        "name": "Britannia Pier", "fr_name": "Jetée Britannia",
        "find": r"Britannia (Pier|Point)|Pier", "kind": "Pier · 1900", "fr_kind": "Jetée · 1900",
        "text": "Built in fifteen weeks in the spring of 1900 on the boulders of a canal that the Metropolitan "
                "Electric Company had dug for a power plant that never ran. Ten arc lights lit it at night; during "
                "the First World War men signed up for the 207th Battalion here.",
        "fr_text": "Construite en quinze semaines au printemps 1900 sur les blocs d'un canal creusé par la Metropolitan "
                   "Electric Company pour une centrale qui n'a jamais fonctionné. Dix lampes à arc l'éclairaient le "
                   "soir; pendant la Première Guerre mondiale, on s'y enrôlait dans le 207e Bataillon.",
        "more": "https://en.wikipedia.org/wiki/Britannia_Village,_Ottawa",
    },
    {
        "name": "Britannia Yacht Club", "fr_name": "Club nautique Britannia",
        "find": r"Britannia Yacht Club", "kind": "Club · 1887", "fr_kind": "Club · 1887",
        "text": "Founded in 1887 by Britannia's summer cottagers as the Britannia Aquatic Club, meeting in an old "
                "sawmill on the water. It is one of the oldest yacht clubs in Canada, at the head of the Deschênes "
                "Rapids.",
        "fr_text": "Fondé en 1887 par les estivants de Britannia sous le nom de Britannia Aquatic Club, qui se "
                   "réunissait dans une ancienne scierie au bord de l'eau. C'est l'un des plus vieux clubs nautiques "
                   "du Canada, en amont des rapides Deschênes.",
        "web": "https://byc.ca",
    },
    {
        "name": "Mud Lake", "fr_name": "Lac Mud",
        "find": r"^Mud Lake$", "kind": "Britannia Conservation Area", "fr_kind": "Aire de conservation Britannia",
        "text": "A bay of the Ottawa River until the early 1960s, when building the water purification plant cut it "
                "off. Now it is the best birding spot in the city: the NCC counts 269 species of birds here.",
        "fr_text": "Une baie de la rivière des Outaouais jusqu'au début des années 1960, quand la construction de "
                   "l'usine de purification l'a isolée. C'est aujourd'hui le meilleur endroit de la ville pour "
                   "observer les oiseaux : la CCN y compte 269 espèces.",
        "web": "https://ncc-ccn.gc.ca/places/mud-lake",
    },
    {
        "name": "Britannia Water Purification Plant", "fr_name": "Usine de purification de l'eau Britannia",
        "find": r"Britannia.*(Water|Purification|Filtration)", "kind": "Waterworks · 1961", "fr_kind": "Usine · 1961",
        "text": "Built from 1958 to 1961, one of the two plants that make Ottawa's drinking water from the river. Its "
                "point is where birders come to watch the gulls over the Deschênes Rapids.",
        "fr_text": "Construite de 1958 à 1961, c'est l'une des deux usines qui produisent l'eau potable d'Ottawa à "
                   "partir de la rivière. Sa pointe est l'endroit où les ornithologues observent les goélands au-dessus "
                   "des rapides Deschênes.",
    },
    {
        "name": "Old St. Stephen's Church", "fr_name": "Ancienne église St. Stephen",
        "find": r"St\.? Stephen", "kind": "Church · 1892", "fr_kind": "Église · 1892", "style": "church",
        "text": "One of the two churches built for the fashionable summer resort of Britannia, and the only one "
                "left. The city put a heritage plaque on it in 2010.",
        "fr_text": "L'une des deux églises construites pour la villégiature chic de Britannia, et la seule qui reste. "
                   "La Ville y a posé une plaque patrimoniale en 2010.",
    },
    {
        "name": "The Olde Forge", "fr_name": "La Vieille Forge",
        "find": r"Old(e)? Forge", "kind": "Log building · 1830s", "fr_kind": "Bâtiment en rondins · années 1830",
        "address": "2730 Carling Avenue",
        "text": "George Winthrop built this log blacksmith shop in the early 1830s where the Richmond and March roads "
                "met. It became a tearoom in 1931, a gas station in 1941 and a tourist office in 1958, and since the "
                "1970s it has been the neighbourhood's community resource centre.",
        "fr_text": "George Winthrop construit cette forge en rondins au début des années 1830, au croisement des "
                   "chemins Richmond et March. Elle devient salon de thé en 1931, station-service en 1941, bureau "
                   "touristique en 1958, et depuis les années 1970 c'est le centre de ressources communautaires du "
                   "quartier.",
        "web": "https://www.oldeforge.ca/", "more": "https://heritageottawa.org/en/node/2799",
    },
    {
        "name": "Britannia United Church", "fr_name": "Église unie Britannia",
        "find": r"Britannia (Heights )?United", "kind": "Church", "fr_kind": "Église", "style": "church",
        "text": "One of Ottawa's oldest congregations: its members met in homes from 1869. They moved from Britannia "
                "village up to this church on Pinecrest Road in 1961.",
        "fr_text": "L'une des plus anciennes congrégations d'Ottawa : ses membres se réunissaient dans des maisons dès "
                   "1869. Ils ont quitté le village de Britannia pour cette église du chemin Pinecrest en 1961.",
    },
    {
        "name": "Frank Ryan Park", "fr_name": "Parc Frank-Ryan",
        "find": r"Frank Ryan Park", "kind": "Park", "fr_kind": "Parc", "address": "950 Alpine Avenue",
        "text": "The neighbourhood's back yard: two ball diamonds, tennis and basketball courts, a wading pool in "
                "summer and a skating rink in winter, with the woods along its edge. A new field house replaces the "
                "old one.",
        "fr_text": "La cour arrière du quartier : deux losanges de balle, des terrains de tennis et de basketball, une "
                   "pataugeoire l'été et une patinoire l'hiver, avec le boisé en bordure. Un nouveau pavillon "
                   "remplace l'ancien.",
    },
    {
        "name": "Elmhurst Woods", "fr_name": "Boisé Elmhurst",
        "find": r"Elmhurst", "kind": "Woodlot", "fr_kind": "Boisé",
        "text": "A woodlot in the middle of Queensway Terrace North. Many of its trees were ash, and when the emerald "
                "ash borer arrived around 2015 the city took the dead ones down and planted hundreds of saplings to "
                "grow a more varied forest.",
        "fr_text": "Un boisé au milieu de Queensway Terrace North. Beaucoup de ses arbres étaient des frênes, et quand "
                   "l'agrile du frêne est arrivé vers 2015, la Ville a abattu les arbres morts et planté des "
                   "centaines de jeunes arbres pour une forêt plus variée.",
    },
    {
        "name": "Queensway Terrace North", "fr_name": "Queensway Terrace North",
        "latlon": (45.35806, -75.78667), "kind": "Neighbourhood · 1958", "fr_kind": "Quartier · 1958",
        "text": "In 1956 Robert Campeau's firm bought 114 acres of the Arkell farm on Pinecrest Road and built "
                "family houses on more than 300 lots. The Queensway, cut through in the 1960s, split the new "
                "neighbourhood into North and South.",
        "fr_text": "En 1956, l'entreprise de Robert Campeau achète 114 acres de la ferme Arkell, sur le chemin "
                   "Pinecrest, et construit des maisons familiales sur plus de 300 lots. L'autoroute 417, percée dans "
                   "les années 1960, coupe le nouveau quartier en deux : Nord et Sud.",
        "more": "https://en.wikipedia.org/wiki/Queensway_Terrace_North",
    },
    {
        "name": "IKEA Ottawa", "fr_name": "IKEA Ottawa",
        "find": r"IKEA", "kind": "Store", "fr_kind": "Magasin", "address": "2685 Iris Street", "style": "barn",
        "wall": (0, 81, 186), "roof": (150, 150, 146),
        "text": "The largest IKEA in Canada. It used to be out in Bells Corners; in the early 2010s it was rebuilt, "
                "much bigger, on the east side of the Pinecrest Shopping Centre, which IKEA owns.",
        "fr_text": "Le plus grand IKEA au Canada. Il était autrefois à Bells Corners; au début des années 2010, il a "
                   "été reconstruit, beaucoup plus grand, du côté est du centre commercial Pinecrest, qui appartient à "
                   "IKEA.",
        "web": "https://www.ikea.com/ca/en/stores/ottawa/",
    },
    {
        "name": "Bridgehead Iris & Greenbank", "fr_name": "Bridgehead Iris et Greenbank",
        "find": r"Bridgehead", "near": (45.3510, -75.7905), "kind": "Coffeehouse · 2015", "fr_kind": "Café · 2015",
        "address": "2745 Iris Street",
        "text": "Opened on 4 August 2015 as Bridgehead's sixteenth coffeehouse, in a new office building at Iris and "
                "Greenbank. Bridgehead started in 1981, when two United Church ministers and two activists began "
                "selling fairly traded coffee from Nicaraguan farmers in church basements.",
        "fr_text": "Ouvert le 4 août 2015, c'est le seizième café Bridgehead, dans un nouvel immeuble de bureaux à "
                   "l'angle d'Iris et de Greenbank. Bridgehead est né en 1981, quand deux pasteurs de l'Église unie et "
                   "deux militants ont commencé à vendre le café équitable de paysans du Nicaragua dans des "
                   "sous-sols d'église.",
        "web": "https://bridgehead.ca",
    },
    {
        "name": "Bridgehead Fairlawn", "fr_name": "Bridgehead Fairlawn",
        "find": r"Bridgehead", "near": (45.3735, -75.7650), "kind": "Coffeehouse", "fr_kind": "Café",
        "address": "2140 Carling Avenue",
        "text": "The Bridgehead in Fairlawn Plaza, on Carling Avenue at Woodroffe.",
        "fr_text": "Le Bridgehead de la place Fairlawn, sur l'avenue Carling à l'angle de Woodroffe.",
        "web": "https://bridgehead.ca",
    },
]

CATEGORIES = [
    ("britannia", "Britannia by the Bay", "Britannia au bord de la baie",
     ["Britannia Beach", "Britannia Pier", "Britannia Yacht Club", "Mud Lake", "Britannia Water Purification Plant",
      "Old St. Stephen's Church"]),
    ("neighbourhood", "The neighbourhood", "Le quartier",
     ["Queensway Terrace North", "Frank Ryan Park", "Elmhurst Woods", "Britannia United Church", "The Olde Forge"]),
    ("coffee", "Coffee and shops", "Cafés et magasins", ["Bridgehead Iris & Greenbank", "Bridgehead Fairlawn", "IKEA Ottawa"]),
]

# What the viewer shows when a place is clicked; the rest of an entry is for the render.
INFO = ("note", "kind", "text", "address", "web", "more", "photo", "stop")
STYLE = ("style", "wall", "roof", "frame", "tower", "turret", "bell", "name")


def locate(p):
    if "latlon" in p:
        x, y = wgs_to_utm(*p["latlon"])
        return {"x": x, "y": y, "building": building_at(x, y), "name": p["name"]}
    if "near" in p:
        # Several places share a name (two Bridgeheads): take the match closest to a rough position.
        from osm_places import _index

        nx, ny = wgs_to_utm(*p["near"])
        import re

        rx = re.compile(p["find"], re.I)
        best = None
        for name, el, shape in _index()[0]:
            if rx.search(name):
                c = shape.representative_point() if shape.area else shape.centroid
                d = (c.x - nx) ** 2 + (c.y - ny) ** 2
                if best is None or d < best[0]:
                    best = (d, c, el)
        if best is None or best[0] > 400**2:
            raise KeyError(f"{p['name']}: nothing named like {p['find']!r} near {p['near']}")
        _, c, el = best
        bid = f"{el['type'][0]}{el['id']}" if "building" in el.get("tags", {}) else building_at(c.x, c.y, near=3)
        return {"x": c.x, "y": c.y, "building": bid, "name": el["tags"]["name"]}
    return find(p["find"], p.get("osm_kind"))


def main():
    out = {"buildings": {}, "places": [], "masts": [], "hotspots": [], "categories": []}
    fr = {}
    for p in PLACES:
        try:
            hit = locate(p)
        except KeyError as e:
            print(f"  skipped: {e}")
            continue
        x, y, pid = hit["x"], hit["y"], hit["building"] if p.get("style") or p.get("on_building", True) else None
        for k in ("name", "kind", "text", "note"):
            if p.get(k) and p.get(f"fr_{k}"):
                fr[p[k]] = p[f"fr_{k}"]
        out["places"].append({"name": p["name"], "note": p.get("note"), "x": x, "y": y, "h": 4.0 if pid else 2.0})
        spot = {"name": p["name"], "x": x, "y": y, **{k: p[k] for k in INFO if k in p}}
        if pid:
            spot["pid"] = pid
            if p.get("style"):
                out["buildings"][pid] = {k: p[k] for k in STYLE if k in p}
        out["hotspots"].append(spot)
        print(f"{p['name']}: {hit['name']} {'building ' + pid if pid else '(point)'}")
    names = {h["name"] for h in out["hotspots"]}
    for key, label, fr_label, members in CATEGORIES:
        members = [n for n in members if n in names]
        fr[label] = fr_label
        out["categories"].append({"key": key, "label": label, "names": members})
    BUILD.mkdir(parents=True, exist_ok=True)
    (BUILD / "landmarks.json").write_text(json.dumps(out, indent=1, ensure_ascii=False))
    (BUILD / "fr_landmarks.json").write_text(json.dumps(fr, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()

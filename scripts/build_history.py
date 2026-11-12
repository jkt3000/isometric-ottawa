"""Britannia back then: a guided tour along the history of Ottawa's west end, as an overlay for the viewer.

Texts are written for this map from Wikipedia (Britannia, Britannia Village, Britannia Yacht Club, Queensway Terrace
North), Heritage Ottawa, the Historical Society of Ottawa, the OC Transpo virtual museum, Kitchissippi Times and the
NCC; every stop names its source. Stops are found in OpenStreetMap by name, like the places in scripts/landmarks.py.
No photos yet: a stop can list Wikimedia Commons files under "photos", which are downloaded once with their credits.

Output: data/build/history.json, data/build/history/*.jpg, and data/build/fr.json, the French for the viewer of
every text here and in scripts/landmarks.py
"""

import hashlib
import json
import re
import subprocess
import time
import urllib.parse
import urllib.request

from config import BUILD
from landmarks import locate

WIKI = "https://en.wikipedia.org/wiki/"
HSO = ("Historical Society of Ottawa", "https://www.historicalsocietyottawa.ca/publications/ottawa-stories/significant-technological-changes-in-the-city/britannia-on-the-bay-75")

STOPS = [
    {
        "year": "1818", "title": "Britannia begins", "fr_title": "Les débuts de Britannia",
        "find": r"^Britannia Beach$", "zoom": 0.3,
        "text": "Captain John LeBreton, a naval officer back from the War of 1812, received land on Lac Deschênes "
                "and laid out a village he called Britannia, eight years before Bytown, the town that became Ottawa.",
        "fr_text": "Le capitaine John LeBreton, officier de marine revenu de la guerre de 1812, reçoit des terres au "
                   "bord du lac Deschênes et y trace un village qu'il nomme Britannia, huit ans avant Bytown, la ville "
                   "qui deviendra Ottawa.",
        "source": ("Wikipedia: Britannia Village", WIKI + "Britannia_Village,_Ottawa"),
    },
    {
        "year": "1830s", "fr_year": "années 1830", "title": "The forge at the crossroads", "fr_title": "La forge au carrefour",
        "osm": "w233903852", "h": 3,
        "text": "George Winthrop built a log blacksmith shop where the Richmond and March roads met, some 8 km from "
                "Bytown. It still stands on its own spot, one of the very few log buildings from the first "
                "settlement that do.",
        "fr_text": "George Winthrop construit une forge en rondins au croisement des chemins Richmond et March, à "
                   "quelque 8 km de Bytown. Elle est toujours à sa place d'origine, l'un des très rares bâtiments en "
                   "rondins des premiers colons dans ce cas.",
        "source": ("Heritage Ottawa", "https://heritageottawa.org/en/node/2799"),
    },
    {
        "year": "1887", "title": "The cottagers' boat club", "fr_title": "Le club nautique des estivants",
        "find": r"BYC Clubhouse", "h": 6,
        "text": "Britannia had become a summer cottage colony. The cottagers founded the Britannia Aquatic Club, which "
                "met in an old sawmill on the water and raced war canoes before it took up sailing: today's "
                "Britannia Yacht Club.",
        "fr_text": "Britannia est devenue une colonie de chalets d'été. Les estivants fondent le Britannia Aquatic "
                   "Club, qui se réunit dans une ancienne scierie au bord de l'eau et court en canots de guerre avant "
                   "de passer à la voile : c'est l'actuel club nautique Britannia.",
        "source": ("Britannia Yacht Club", "https://byc.ca/about/club-house/"),
    },
    {
        "year": "1892", "title": "St. Stephen's", "fr_title": "St. Stephen",
        "addr": "181 Britannia Road", "h": 8,
        "text": "One of the two churches of the fashionable resort, and the only one still standing.",
        "fr_text": "L'une des deux églises de la villégiature chic, et la seule encore debout.",
        "source": ("Wikipedia: Britannia", WIKI + "Britannia,_Ottawa"),
    },
    {
        "year": "1900", "title": "Britannia-on-the-Bay", "fr_title": "Britannia-on-the-Bay",
        "find": r"^Britannia Beach$", "zoom": 0.8,
        "text": "On 24 May 1900 the Ottawa Electric Railway opened its new park at the end of the streetcar line. "
                "Riders crossed the railway on a high footbridge to the beach, pavilions and a long pier, built in "
                "fifteen weeks on the rubble of a failed power canal and lit at night by ten arc lights.",
        "fr_text": "Le 24 mai 1900, l'Ottawa Electric Railway ouvre son nouveau parc au bout de la ligne de tramway. "
                   "Les passagers traversent la voie ferrée sur une haute passerelle vers la plage, les pavillons et "
                   "une longue jetée, bâtie en quinze semaines sur les déblais d'un canal de centrale raté et éclairée "
                   "le soir par dix lampes à arc.",
        "source": HSO,
    },
    {
        "year": "1905", "title": "25,000 on a Sunday", "fr_title": "25 000 personnes un dimanche",
        "osm": "w626243179", "zoom": 1.2,
        "text": "On fine Sundays in July and August the streetcars carried up to 25,000 people out to Britannia. The "
                "steamer G.B. Greene, the 'Queen of the River', took 250 passengers at a time up the river; her "
                "anchor lies at the beach.",
        "fr_text": "Les beaux dimanches de juillet et d'août, les tramways amenaient jusqu'à 25 000 personnes à "
                   "Britannia. Le vapeur G.B. Greene, la « reine de la rivière », emmenait 250 passagers à la fois "
                   "sur la rivière; son ancre repose sur la plage.",
        "source": ("OC Transpo virtual museum", "https://www.octranspo.com/en/virtual-museum/colourizing-the-past"),
    },
    {
        "year": "1958", "title": "Queensway Terrace", "fr_title": "Queensway Terrace",
        "latlon": (45.35806, -75.78667), "zoom": 0.5,
        "text": "Robert Campeau's firm bought 114 acres of the Arkell farm on Pinecrest Road and built family houses "
                "on more than 300 lots, one of Ottawa's first large postwar neighbourhoods.",
        "fr_text": "L'entreprise de Robert Campeau achète 114 acres de la ferme Arkell, sur le chemin Pinecrest, et "
                   "construit des maisons familiales sur plus de 300 lots : l'un des premiers grands quartiers "
                   "d'après-guerre d'Ottawa.",
        "source": ("Wikipedia: Queensway Terrace North", WIKI + "Queensway_Terrace_North"),
    },
    {
        "year": "1959", "title": "The last streetcar", "fr_title": "Le dernier tramway",
        "osm": "w68587547", "zoom": 0.8,
        "text": "On 1 May 1959 the last car ran out to Britannia, on what had become Ottawa's last streetcar line. "
                "Buses took over.",
        "fr_text": "Le 1er mai 1959, le dernier tramway roule jusqu'à Britannia, sur ce qui était devenu la dernière "
                   "ligne de tramway d'Ottawa. Les autobus prennent le relais.",
        "source": ("Kitchissippi Times", "https://kitchissippi.com/?p=188086"),
    },
    {
        "year": "1961", "title": "The plant and Mud Lake", "fr_title": "L'usine et le lac Mud",
        "find": r"^Mud Lake$", "zoom": 0.6,
        "text": "The city built the Britannia Water Purification Plant on the point from 1958 to 1961. Its "
                "causeway cut off a shallow bay of the river, which became Mud Lake, now the best birding spot in "
                "Ottawa.",
        "fr_text": "La Ville construit l'usine de purification de l'eau Britannia sur la pointe, de 1958 à 1961. Sa "
                   "digue isole une baie peu profonde de la rivière, qui devient le lac Mud, aujourd'hui le meilleur "
                   "endroit d'Ottawa pour observer les oiseaux.",
        "source": ("Ottawa Citizen", "https://www.pressreader.com/canada/ottawa-citizen/20210114/281565178404492"),
    },
    {
        "year": "1960s", "fr_year": "années 1960", "title": "The Queensway cuts through", "fr_title": "L'autoroute 417 traverse",
        "latlon": (45.3505, -75.7870), "zoom": 0.5,
        "text": "Highway 417 was built along the old railway line and split Queensway Terrace in two, North and South.",
        "fr_text": "L'autoroute 417 est construite le long de l'ancienne voie ferrée et coupe Queensway Terrace en "
                   "deux : Nord et Sud.",
        "source": ("Wikipedia: Queensway Terrace South", WIKI + "Queensway_Terrace_South"),
    },
    {
        "year": "2017", "title": "The floods", "fr_title": "Les inondations",
        "find": r"^Mud Lake$", "zoom": 0.4,
        "text": "The spring floods of 2017 and again 2019 put the shore of Britannia under water and badly damaged "
                "the trails around Mud Lake; the NCC has since rebuilt them and replaced the footbridge.",
        "fr_text": "Les crues printanières de 2017, puis de 2019, inondent la rive de Britannia et abîment gravement "
                   "les sentiers autour du lac Mud; la CCN les a depuis refaits et a remplacé la passerelle.",
        "source": ("NCC", "https://ncc-ccn.gc.ca/projects/mud-lake-trail-rehabilitation-pedestrian-bridge-replacement"),
    },
]


def commons_thumb(name, width=960):  # Wikimedia only serves a fixed set of thumbnail widths
    h = hashlib.md5(name.encode()).hexdigest()
    q = urllib.parse.quote(name)
    return f"https://upload.wikimedia.org/wikipedia/commons/thumb/{h[0]}/{h[:2]}/{q}/{width}px-{q}"


def download(url, tries=6):
    """Wikimedia rate-limits bursts of thumbnail requests; wait politely and retry."""
    req = urllib.request.Request(url, headers={"User-Agent": "isometric-ottawa/0.1 (personal map)"})
    for attempt in range(tries):
        try:
            data = urllib.request.urlopen(req, timeout=60).read()
            time.sleep(3)
            return data
        except urllib.error.HTTPError as e:
            if e.code != 429 or attempt == tries - 1:
                raise
            time.sleep(15 * (attempt + 1))


def route_order(stops):
    """Visit the stops as a route over the map instead of by date: the first and last stay put, the rest is a
    nearest-neighbour path tidied with 2-opt, so dots that lie together come one after another. Stops on the same
    spot (the old and the new church) keep their chronological order."""
    first, last, middle = stops[0], stops[-1], stops[1:-1]
    dist = lambda a, b: ((a["x"] - b["x"]) ** 2 + (a["y"] - b["y"]) ** 2) ** 0.5
    path, todo = [first], middle[:]
    while todo:
        nxt = min(todo, key=lambda s: (dist(path[-1], s), stops.index(s)))
        path.append(nxt)
        todo.remove(nxt)
    path.append(last)
    improved = True
    while improved:
        improved = False
        for i in range(1, len(path) - 2):
            for j in range(i + 1, len(path) - 1):
                before = dist(path[i - 1], path[i]) + dist(path[j], path[j + 1])
                after = dist(path[i - 1], path[j]) + dist(path[i], path[j + 1])
                if after < before - 0.01:
                    path[i : j + 1] = path[i : j + 1][::-1]
                    improved = True
    # Places within 40 m of each other (church, parsonage, Sunday school) in chronological order.
    i = 0
    while i < len(path):
        j = i
        while j + 1 < len(path) - 1 and i > 0 and dist(path[j + 1], path[i]) < 40:
            j += 1
        year = lambda st: int(m.group()) if (m := re.search(r"\d{4}", st["year"])) else 9999
        path[i : j + 1] = sorted(path[i : j + 1], key=lambda st: (year(st), stops.index(st)))
        i = j + 1
    return path


def main():
    out_dir = BUILD / "history"
    out_dir.mkdir(parents=True, exist_ok=True)
    fr = json.loads((BUILD / "fr_landmarks.json").read_text()) if (BUILD / "fr_landmarks.json").exists() else {}
    stops = []
    for i, s in enumerate(STOPS, start=1):
        try:
            hit = locate({"name": s["title"], **{k: s[k] for k in ("find", "latlon", "near", "osm", "addr") if k in s}})
        except KeyError as e:
            print(f"  skipped: {e}")
            continue
        for k in ("year", "title", "text"):
            if s.get(f"fr_{k}"):
                fr[s[k]] = s[f"fr_{k}"]
        stop = {"n": i, "year": s["year"], "title": s["title"], "text": s["text"], "x": round(hit["x"], 1),
                "y": round(hit["y"], 1), "h": s.get("h", 0), "zoom": s.get("zoom", 1.2),
                "source": {"label": s["source"][0], "url": s["source"][1]}, "more": s.get("more")}
        stop["photos"] = []
        for k, (name, credit) in enumerate(s.get("photos", [])):
            dst = out_dir / f"{i:02d}_{k}.jpg"
            if not dst.exists():
                dst.write_bytes(download(commons_thumb(name)))
                subprocess.run(["magick", str(dst), "-resize", "640x640>", "-quality", "82", str(dst)], check=True)
            stop["photos"].append({"src": f"history/{dst.name}", "credit": credit,
                                   "url": f"https://commons.wikimedia.org/wiki/File:{urllib.parse.quote(name)}"})
        stops.append(stop)
    # In order of time, which is how the stories build on each other (route_order() would walk them by place instead).
    for n, stop in enumerate(stops, start=1):
        stop["n"] = n
    # A version in every photo URL, so browsers never show a cached photo that has since been replaced.
    version = hashlib.md5(b"".join(f.read_bytes() for f in sorted(out_dir.glob("*.jpg")))).hexdigest()[:8]
    for stop in stops:
        for ph in stop["photos"]:
            ph["src"] += f"?v={version}"
    (BUILD / "history.json").write_text(json.dumps({"stops": stops}, ensure_ascii=False, indent=1))
    (BUILD / "fr.json").write_text(json.dumps(fr, ensure_ascii=False, indent=1))
    print(f"{len(stops)} stops, {sum(len(s['photos']) for s in stops)} photos; {len(fr)} French texts")


if __name__ == "__main__":
    main()

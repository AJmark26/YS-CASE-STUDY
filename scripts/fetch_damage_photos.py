"""Download the licensed photos of real building damage used to test the learned damage detector.

    python scripts/fetch_damage_photos.py [folder]      (default data/damage_photos)

They come from Wikimedia Commons. manifest.json records each photo's page, licence and author,
and the damage it shows, judged by eye (null: outdoor or not usable). The photos stay out of the
repository; scripts/damage_learned_eval.py and scripts/damage_learned_synthetic.py read them.
"""
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
UA = {"User-Agent": "ys-case-study-eval/0.1 (damage detector test)"}
PHOTOS = [   # Commons file title, damage shown
    ("Structural ceiling defect.jpg", None),
    ("Ceiling water damage.jpg", "water_stain"),
    ("Ceiling sheetrock damaged by water so paint was peeling.jpg", "water_stain"),
    ("Water Damage.JPG", "water_stain"),
    ("Mold on Ceiling on House on Dublin Street New Orleans after Katrina.jpg", "water_stain+dark_spot"),
    ("Mold on wall1.jpg", "dark_spot"),
    ("Mold on wall2.jpg", "dark_spot"),
    ("Mold on wall3.jpg", "dark_spot"),
    ("Fungus staining and mold growth on the walls.jpg", None),
    ("Indoor Mold.jpg", "dark_spot"),
    ("Crack in the wall (44892).jpg", None),
    ("Seattle - 619 Western - crack in wall.jpg", "crack"),
    ("Atalaya interior wall cracks measurement.jpg", "crack"),
    ("Damp interior walls of a barrack hut - geograph.org.uk - 4039648.jpg", "water_stain"),
]


def get(url):
    for k in range(6):
        try:
            return urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60).read()
        except urllib.error.HTTPError as e:
            if e.code != 429:
                raise
            time.sleep(10 * (k + 1))                  # Commons rate limit
    raise RuntimeError("rate limited: " + url)


def main(folder=None):
    folder = Path(folder or ROOT / "data" / "damage_photos")
    folder.mkdir(parents=True, exist_ok=True)
    strip = lambda s: re.sub("<[^>]+>", "", s or "").strip()
    man = []
    for i, (title, damage) in enumerate(PHOTOS):
        q = urllib.parse.urlencode({"action": "query", "titles": "File:" + title, "prop": "imageinfo",
                                    "iiprop": "url|extmetadata", "iiurlwidth": 1280, "format": "json"})
        page = next(iter(json.loads(get("https://commons.wikimedia.org/w/api.php?" + q))["query"]["pages"].values()))
        if "imageinfo" not in page:
            print("missing on Commons:", title)
            continue
        ii, fn = page["imageinfo"][0], f"{i:02d}.jpg"
        md = ii.get("extmetadata", {})
        if not (folder / fn).exists():
            time.sleep(4)
            (folder / fn).write_bytes(get(ii["thumburl"]))
        man.append({"file": fn, "title": title, "page": ii["descriptionurl"],
                    "licence": strip(md.get("LicenseShortName", {}).get("value")),
                    "author": strip(md.get("Artist", {}).get("value"))[:120], "damage": damage})
        print(fn, man[-1]["licence"], "|", title)
        time.sleep(4)
    (folder / "manifest.json").write_text(json.dumps(man, indent=1))


if __name__ == "__main__":
    main(*sys.argv[1:])

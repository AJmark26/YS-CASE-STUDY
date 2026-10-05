"""Run the learned damage detector on photos of real damage and draw what it finds.

The sample captures show almost no damage (one hairline crack, on the bathroom wall of the
bedroom capture), so the detector is checked on licensed photos from Wikimedia Commons, fetched
by scripts/fetch_damage_photos.py and listed with author and licence in the folder's
manifest.json, together with the damage each photo shows, judged by eye. A photo counts as
found when a detection of the right class covers the damage.

    python scripts/damage_learned_eval.py [photo folder] [out folder]     (default data/damage_photos)
"""
import json
import sys
import time
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from ysplan import damage_learned as dl  # noqa: E402

COLOURS = {"water_stain": (0, 140, 255), "dark_spot": (180, 0, 180), "crack": (0, 0, 255), "hole": (255, 0, 0)}


def main(folder=None, out=None):
    folder = Path(folder or ROOT / "data" / "damage_photos")
    out = Path(out or folder / "detections")
    out.mkdir(parents=True, exist_ok=True)
    man = json.loads((folder / "manifest.json").read_text())
    rows = []
    for m in man:
        if not m.get("damage"):
            continue                                  # outdoor or not a damage photo
        bgr = cv2.imread(str(folder / m["file"]))
        t0 = time.time()
        dets = dl.detect_image(np.ascontiguousarray(bgr[..., ::-1]))
        dt = time.time() - t0
        vis = bgr.copy()
        for cls, mask, score, (x0, y0, x1, y1) in dets:
            c = COLOURS[cls]
            vis[mask] = (0.5 * vis[mask] + 0.5 * np.array(c)).astype(np.uint8)
            cv2.rectangle(vis, (int(x0), int(y0)), (int(x1), int(y1)), c, 2)
            cv2.putText(vis, f"{cls} {score:.2f}", (int(x0) + 3, int(y0) + 18), cv2.FONT_HERSHEY_SIMPLEX, 0.6, c, 2)
        cv2.imwrite(str(out / m["file"]), vis)
        rows.append({"file": m["file"], "damage": m["damage"], "seconds": round(dt, 1),
                     "detections": [{"class": c, "score": round(s, 2), "box": [round(v) for v in b],
                                     "mask_frac": round(float(k.mean()), 3)} for c, k, s, b in dets]})
        print(m["file"], m["damage"], f"{dt:.1f} s", [(d["class"], d["score"]) for d in rows[-1]["detections"]], flush=True)
    (out / "detections.json").write_text(json.dumps(rows, indent=1))


if __name__ == "__main__":
    main(*sys.argv[1:])

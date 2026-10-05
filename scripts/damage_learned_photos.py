"""Learned damage detector on the photo tier: the same real damage as damage_learned_synthetic.py,
laid onto the walls of a photo set made from a sample capture's frames (photo-sets/doorways_*),
then the photo tier runs on the clean and on the damaged set.

    python scripts/damage_learned_photos.py <photo set> <capture_dir> <out dir> [--recon DIR]

Each photo is its source frame (the file name is the frame number), so the damage is drawn with
the frame's LiDAR pose and depth and the photo is written back upright with its EXIF.
--recon: cached per-room reconstructions (photo_recon/ of an earlier run on the same set); the
damage changes no geometry, so both runs reuse them and skip MapAnything.
A laid-on item counts as found when the damaged run has a region of its class in its room
(photo folders are named after the LiDAR room ids). The photo plan's frame is not the LiDAR
plan's, so positions are not compared.
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
import damage_learned_synthetic as dls  # noqa: E402
from ysplan import damage, io_stray, mono, pipeline_lidar  # noqa: E402


def run_tier(photo_set, out, recon):
    out.mkdir(parents=True, exist_ok=True)
    if recon and not (out / "photo_recon").exists():
        shutil.copytree(recon, out / "photo_recon")
    subprocess.run([sys.executable, "-m", "ysplan", str(photo_set), "-o", str(out), "--tier", "photo"],
                   cwd=ROOT, check=True)
    return json.loads((out / "plan.json").read_text())


def main(photo_set, capture, out, recon=None):
    photo_set, capture, out = Path(photo_set), Path(capture), Path(out)
    plan, _, _ = pipeline_lidar.run(capture, log=lambda *a: None)
    poses = pipeline_lidar.run.poses
    cap = io_stray.load(capture)
    _, surfs = damage.run(cap, poses, plan, capture / "rgb.mp4", log=lambda *a: None)
    items = []
    for (cls, photo, box, width, paint), (_, s, a, h) in zip(dls.PATCHES, dls.wall_spots(plan, surfs, len(dls.PATCHES))):
        ratio, mask = dls.load_patch(photo, box, width, paint)
        ph, pw = mask.shape
        hh = width * ph / pw
        corners = np.array([[a - width / 2, h + hh / 2], [a + width / 2, h + hh / 2],
                            [a + width / 2, h - hh / 2], [a - width / 2, h - hh / 2]])
        items.append({"class": cls, "room": s.room, "surface": s.sid, "ratio": ratio,
                      "area_m2": round(float(mask.sum()) * (width / pw) ** 2, 4),
                      "corners": dls.ds.to_world(plan, s, corners)})
    # the damaged copy of the set
    dmg_set = out / "set_with_damage"
    if dmg_set.exists():
        shutil.rmtree(dmg_set)
    shutil.copytree(photo_set, dmg_set)
    photos = sorted(dmg_set.glob("*/*.jpg"))
    frame_of = {p: int(p.stem.rsplit("_", 1)[-1]) for p in photos}
    row = {int(f): i for i, f in enumerate(cap.frames)}
    raw = dict(mono.iter_frames(capture / "rgb.mp4", sorted(set(frame_of.values()))))
    shown = {}
    for p in photos:
        f = frame_of[p]
        i = row[f]
        bgr = raw[f]
        d, _ = cap.depth(i)
        D = cv2.resize(d, (bgr.shape[1], bgr.shape[0]), interpolation=cv2.INTER_NEAREST)
        im, n = dls.lay_on(bgr.astype(np.float32), poses[i], cap.K_rgb[i], D, items)
        orig = Image.open(p)
        ref = np.asarray(orig.convert("RGB"))[..., ::-1].astype(np.float32)
        # the photo is the frame turned upright: find the turn by comparing with the clean frame
        k = min(range(4), key=lambda k: np.inf if np.rot90(bgr, k).shape != ref.shape
                else float(np.abs(np.rot90(bgr, k).astype(np.float32) - ref).mean()))
        Image.fromarray(np.ascontiguousarray(np.rot90(im, k)[..., ::-1])).save(p, exif=orig.getexif(), quality=95)
        shown[f"{p.parent.name}/{p.name}"] = n
    clean = run_tier(photo_set, out / "clean", recon)
    with_dmg = run_tier(dmg_set, out / "with_damage", recon)
    rep_items = []
    for j, it in enumerate(items):
        hits = [r for r in with_dmg.get("damage", {}).get("regions", []) if r.get("room") == it["room"]]
        rep_items.append({"class": it["class"], "room": it["room"], "lidar_surface": it["surface"],
                          "area_m2": it["area_m2"], "photos_showing": [k for k, n in shown.items() if n[j] > 500],
                          "found": any(r["class"] == it["class"] for r in hits),
                          "regions_in_room": [(r["surface"], r["class"], r["area_m2"]["value"]) for r in hits]})
    reg = lambda pl: [(r["surface"], r["class"], r["area_m2"]["value"], r["score"])
                      for r in pl.get("damage", {}).get("regions", [])]
    rep = {"photo_set": photo_set.name, "capture": capture.name, "photos": len(photos),
           "clean_regions": reg(clean), "with_damage_regions": reg(with_dmg),
           "frame_detections": {"clean": clean.get("damage", {}).get("frame_detections"),
                                "with_damage": with_dmg.get("damage", {}).get("frame_detections")},
           "items": rep_items}
    print(json.dumps(rep, indent=1))
    (out / "photo_damage_test.json").write_text(json.dumps(rep, indent=1))


if __name__ == "__main__":
    argv = sys.argv[1:]
    recon = argv[argv.index("--recon") + 1] if "--recon" in argv else None
    pos = [a for j, a in enumerate(argv) if not a.startswith("--") and (j == 0 or argv[j - 1] != "--recon")]
    main(*pos, recon=recon)

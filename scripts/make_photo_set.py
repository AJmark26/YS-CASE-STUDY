"""Build a photo-tier benchmark set (per-room folders of stills, no poses, no depth) from a
LiDAR capture's video, using the LiDAR plan only to decide which room each still belongs to.

For each room: frames whose camera is inside the room footprint, then up to N stills chosen by
farthest-point sampling on viewing direction (yaw) so they cover the walls like a person turning
in the room would. Doorway stills (camera within 0.6 m of an opening) are added to the room the
camera looks into, which is what the capture protocol asks a person to do.

usage: python scripts/make_photo_set.py <capture_dir> <lidar plan.json> <out_dir> [--per-room 6]
"""
import argparse
import json
import sys
from pathlib import Path

import cv2
import numpy as np
from shapely.geometry import Point, Polygon

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from ysplan import io_stray, mono  # noqa: E402


def upright(bgr, T_wc, K):
    """The video is stored in sensor orientation; a phone photo is stored upright (EXIF). Rotate by
    the multiple of 90 deg that puts world-up at the top of the image, as the Camera app would."""
    up_cam = T_wc[:3, :3].T @ np.array([0.0, 1.0, 0.0])          # world up in camera coords
    d = up_cam[:2]                                                # its image direction (x right, y down)
    options = [(None, d), (cv2.ROTATE_90_CLOCKWISE, np.array([-d[1], d[0]])),
               (cv2.ROTATE_180, -d), (cv2.ROTATE_90_COUNTERCLOCKWISE, np.array([d[1], -d[0]]))]
    rot, _ = max(options, key=lambda o: -o[1][1])                 # 'up' should point to -y
    img = bgr if rot is None else cv2.rotate(bgr, rot)
    diag = np.hypot(io_stray.RGB_W, io_stray.RGB_H)
    f35 = K[0, 0] * 43.27 / diag                                   # 35 mm-equivalent focal length
    return img, f35


def save_jpeg(path, bgr, f35):
    from PIL import Image
    im = Image.fromarray(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB))
    ex = Image.Exif()
    ex[0x010F] = "Apple"                     # Make
    ex[0x0110] = "iPhone (benchmark frame)"  # Model
    ex.get_ifd(0x8769)[0xA405] = int(round(f35))   # FocalLengthIn35mmFilm
    im.save(path, quality=92, exif=ex)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("capture", type=Path)
    ap.add_argument("plan", type=Path)
    ap.add_argument("out", type=Path)
    ap.add_argument("--per-room", type=int, default=6)
    ap.add_argument("--seed", type=int, default=0)
    a = ap.parse_args()
    cap = io_stray.load(a.capture)
    plan = json.loads(a.plan.read_text())
    yaw = plan["alignment"]["yaw_rad"]
    c, s = np.cos(yaw), np.sin(yaw)
    t = cap.T_wc[:, :3, 3]
    xy = np.stack([t[:, 0] * c + t[:, 2] * s, -t[:, 0] * s + t[:, 2] * c], 1)
    fwd = cap.T_wc[:, :3, 2]                       # camera z axis in world
    fyaw = np.arctan2(-fwd[:, 0] * s + fwd[:, 2] * c, fwd[:, 0] * c + fwd[:, 2] * s)
    tilt = np.degrees(np.arcsin(np.clip(-cap.T_wc[:, 1, 2], -1, 1)))  # + = looking up
    level = np.abs(tilt) < 35
    rng = np.random.default_rng(a.seed)
    picks = {}
    for room in plan["rooms"]:
        poly = Polygon(room["polygon"])
        inside = np.array([poly.buffer(-0.2).contains(Point(p)) for p in xy]) & level
        idx = np.flatnonzero(inside)
        if len(idx) < 2:
            continue
        chosen = [int(rng.choice(idx))]
        while len(chosen) < min(a.per_room, len(idx)):
            d = np.min(np.abs(np.angle(np.exp(1j * (fyaw[idx][:, None] - fyaw[chosen][None, :])))), axis=1)
            chosen.append(int(idx[np.argmax(d)]))
        picks[room["id"]] = sorted(chosen)
    # doorway stills: camera near an opening, assigned to the room it looks into
    for o in plan["openings"]:
        if len(o.get("rooms", [])) < 2:
            continue
        mid = (o["from"] + o["to"]) / 2
        cxy = np.array([mid, o["line"]]) if o["axis"] == "u" else np.array([o["line"], mid])
        near = np.flatnonzero((np.linalg.norm(xy - cxy, axis=1) < 0.6) & level)
        for rid in o["rooms"]:
            poly = Polygon(next(r["polygon"] for r in plan["rooms"] if r["id"] == rid))
            look = [i for i in near if poly.contains(Point(xy[i] + 1.5 * np.array([np.cos(fyaw[i]), np.sin(fyaw[i])])))]
            if look and rid in picks and len(picks[rid]) < 8:
                picks[rid] = sorted(set(picks[rid]) | {int(look[len(look) // 2])})
    allf = sorted({f for v in picks.values() for f in v})
    frames = mono.read_frames(a.capture / "rgb.mp4", allf)
    manifest = {}
    for rid, fs in picks.items():
        d = a.out / rid
        d.mkdir(parents=True, exist_ok=True)
        for f in fs:
            img, f35 = upright(frames[f], cap.T_wc[f], cap.K_rgb[f])
            save_jpeg(d / f"{f:06d}.jpg", img, f35)
        manifest[rid] = fs
    (a.out / "_source_frames.json").write_text(json.dumps(manifest, indent=1))
    print({k: len(v) for k, v in manifest.items()})


if __name__ == "__main__":
    main()

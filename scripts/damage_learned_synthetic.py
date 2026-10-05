"""Learned damage detector on a sample capture: false alarms on the clean walls, then real damage
laid onto the walls. A water stain, a patch of mould and a crack are cut from licensed photos
(scripts/fetch_damage_photos.py) and placed on plain, light, well-seen wall spots (wall_spots).
Each is drawn into every frame through the wall plane, the frame's LiDAR pose and intrinsics,
and hidden where the LiDAR depth shows something in front. The damage darkens the sample wall by
the ratio it darkens the paint in its photo, so it takes on the wall's colour and lighting.

    python scripts/damage_learned_synthetic.py <capture_dir> [out.json] [--tier lidar|video] [--frames-out DIR]

--tier video runs the detector on the video tier: MapAnything keyframe depth (cached in
out/<capture>_video/video_depths.npz), its poses and plan, and its wider wall band.
--frames-out saves the frames that show the most of the laid-on damage.
The flat painted blobs of scripts/damage_synthetic.py are not used: the learned models look for
damage that looks real and ignore them.
"""
import json
import sys
import time
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
import damage_synthetic as ds  # noqa: E402
from ysplan import damage, damage_learned as dl, io_stray, measure, pipeline_lidar  # noqa: E402

PHOTOS = ROOT / "data" / "damage_photos"
PATCHES = [   # class, photo, crop (x0, y0, x1, y1) in the photo, width on the wall (m), clean paint from
    ("water_stain", "02.jpg", (230, 170, 730, 670), 0.5, "patch"),   # CC0, Tomwsulcer
    ("dark_spot", "06.jpg", (400, 500, 900, 1000), 0.5, "patch"),    # CC BY-SA 4.0, see the manifest
    ("crack", "12.jpg", (40, 90, 740, 200), 0.7, "local"),           # CC BY-SA 3.0, Doug Coldwell
]
DARKER = 0.9          # a photo pixel is damage when at most this bright relative to its clean paint
CLOSE_M = 0.05        # damage area: the damaged pixels closed over 5 cm (the detector closes over 5 cells)


def load_patch(photo, box, width, paint="patch"):
    """Damage as a per-channel brightness ratio to the photo's clean paint (1 = unchanged), and the
    damaged area's mask. paint="patch": the clean paint is the crop's brightest fifth; "local": the
    paint around each pixel (a 3 cm closing), which keeps a thin crack and drops the lighting."""
    x0, y0, x1, y1 = box
    p = cv2.imread(str(PHOTOS / photo)).astype(np.float32)[y0:y1, x0:x1]
    px = (x1 - x0) / width                                       # photo pixels per metre on the wall
    if paint == "local":
        k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (int(0.03 * px) | 1,) * 2)
        ref = cv2.GaussianBlur(cv2.morphologyEx(p, cv2.MORPH_CLOSE, k), (0, 0), 2)
    else:
        ref = np.percentile(p.reshape(-1, 3), 80, axis=0)[None, None]
    ratio = np.clip(p / np.maximum(ref, 1), 0.05, 1.0)
    # fade out towards the crop's inscribed ellipse, so no straight crop edge shows on the wall
    h, w = ratio.shape[:2]
    yy, xx = np.mgrid[:h, :w]
    rho = np.hypot((xx + 0.5) / w * 2 - 1, (yy + 0.5) / h * 2 - 1)
    fade = np.clip((1 - rho) / 0.5, 0, 1)
    dark = ((ratio.mean(2) < DARKER) & (fade > 0.5)).astype(np.uint8)
    soft = cv2.GaussianBlur(cv2.dilate(dark, np.ones((3, 3), np.uint8)).astype(np.float32), (0, 0), 1.5)
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (int(CLOSE_M * px) | 1,) * 2)
    return 1 + (ratio - 1) * (soft * fade)[..., None], cv2.morphologyEx(dark, cv2.MORPH_CLOSE, k).astype(bool)


def wall_spots(plan, surfs, n):
    """One 50 cm spot per wall, on plain painted wall that the capture saw well: light paint (not
    a wardrobe front or tiles), in the best-seen half of the wall's cells, then the plainest
    (smallest brightness spread, as damage_synthetic). Best-seen walls first."""
    spots = []
    for s in surfs:
        if s.kind != "wall" or s.length < 1.0:
            continue
        tex, ok = s.texture()
        L = cv2.cvtColor(tex, cv2.COLOR_RGB2LAB)[..., 0].astype(np.float32)
        for a0, a1, _ in s.excluded:
            ok[:, int(a0 / 0.01):int(a1 / 0.01) + 1] = False
        k, f = 51, ok.astype(np.float32)
        box = lambda a: cv2.boxFilter(a, -1, (k, k), normalize=False, borderType=cv2.BORDER_CONSTANT)
        cnt = box(f)
        m1, m2 = box(L * f) / np.maximum(cnt, 1), box(L * L * f) / np.maximum(cnt, 1)
        sd = np.sqrt(np.maximum(m2 - m1 * m1, 0))
        seen = box(s.count.astype(np.float32)) / k / k          # depth points per cell around the spot
        good = (cnt >= 0.97 * k * k) & (m1 >= 170) & (sd < 8)    # light, plain paint
        good[:80] = good[190:] = False                           # centre 0.8 to 1.9 m above the floor
        if good.any():
            good &= seen >= np.median(seen[good])
            r, c = np.unravel_index(np.argmin(np.where(good, sd, 1e9)), sd.shape)
            spots.append((-float(seen[r, c]), s, c * 0.01, r * 0.01))
    return sorted(spots, key=lambda x: x[0])[:n]


def lay_on(bgr, T, K, D, items):
    """Draw each item's ratio image onto its wall rectangle in one frame. Also returns, per item,
    how many pixels it darkened."""
    H, W = bgr.shape[:2]
    gain = np.ones((H, W, 3), np.float32)
    shown = [0] * len(items)
    for j, it in enumerate(items):
        Pc = (it["corners"] - T[:3, 3]) @ T[:3, :3]               # camera coordinates
        if (Pc[:, 2] < 0.2).any():
            continue
        uv = (Pc @ K.T)[:, :2] / Pc[:, 2:]
        if uv[:, 0].max() < 0 or uv[:, 0].min() > W or uv[:, 1].max() < 0 or uv[:, 1].min() > H:
            continue
        ph, pw = it["ratio"].shape[:2]
        Hm = cv2.getPerspectiveTransform(np.float32([[0, 0], [pw, 0], [pw, ph], [0, ph]]), uv.astype(np.float32))
        g = cv2.warpPerspective(it["ratio"], Hm, (W, H), flags=cv2.INTER_LINEAR, borderValue=(1, 1, 1))
        g = cv2.GaussianBlur(g, (0, 0), 1.0)                      # the video is a little soft
        # depth of the wall plane at every pixel, against the LiDAR depth: hidden behind furniture
        nrm = np.cross(Pc[1] - Pc[0], Pc[3] - Pc[0])
        u, v = np.meshgrid(np.arange(W, dtype=np.float32), np.arange(H, dtype=np.float32))
        den = nrm[0] * (u - K[0, 2]) / K[0, 0] + nrm[1] * (v - K[1, 2]) / K[1, 1] + nrm[2]
        zp = (nrm @ Pc[0]) / np.where(np.abs(den) < 1e-9, 1e-9, den)
        vis = (D > 0) & (np.abs(D - zp) < 0.08 + 0.03 * zp)
        gain = np.where(vis[..., None], gain * g, gain)
        shown[j] = int((vis & (g.mean(2) < 0.97)).sum())
    return np.clip(bgr * gain, 0, 255).astype(np.uint8), shown


def main(capture, out_json=None, tier="lidar", frames_out=None):
    capture = Path(capture)
    t0 = time.time()
    lidar_plan, _, _ = pipeline_lidar.run(capture, log=lambda *a: None)
    lidar_poses = pipeline_lidar.run.poses
    lcap = io_stray.load(capture)
    if tier == "video":
        from ysplan import pipeline_video
        cap = pipeline_video.build(capture, cache=ROOT / "out" / f"{capture.name}_video" / "video_depths.npz",
                                   log=lambda *a: None)
        plan, _, _ = pipeline_lidar.run(capture, cap=cap, tier="video", tier_scale=measure.TIER_SCALE["video"],
                                        log=lambda *a: None)
        poses = pipeline_lidar.run.poses
        rows = sorted(cap.depths)[::2]
        band = dl.VIDEO_WALL_BAND
    else:
        plan, poses, cap = lidar_plan, lidar_poses, lcap
        rows = dl.pick_frames(cap)
        band = None
    print(f"plans in {time.time() - t0:.0f} s; {len(rows)} frames", flush=True)
    frames = dl.video_frames(cap, rows, capture / "rgb.mp4")
    t1 = time.time()
    clean, surfs = dl.run(cap, poses, plan, frames, wall_band=band)
    t_clean = time.time() - t1

    # real damage on the plainest wall spots of the LiDAR plan
    _, surfs0 = damage.run(lcap, lidar_poses, lidar_plan, capture / "rgb.mp4", log=lambda *a: None)
    items = []
    for (cls, photo, box, width, paint), (_, s, a, h) in zip(PATCHES, wall_spots(lidar_plan, surfs0, len(PATCHES))):
        ratio, mask = load_patch(photo, box, width, paint)
        ph, pw = mask.shape
        hh = width * ph / pw
        corners = np.array([[a - width / 2, h + hh / 2], [a + width / 2, h + hh / 2],
                            [a + width / 2, h - hh / 2], [a - width / 2, h - hh / 2]])
        items.append({"class": cls, "photo": photo, "surface": s.sid, "along_m": round(a, 2), "height_m": round(h, 2),
                      "area_m2": round(float(mask.sum()) * (width / pw) ** 2, 4), "ratio": ratio,
                      "corners": ds.to_world(lidar_plan, s, corners)})
    lrow = {int(f): i for i, f in enumerate(lcap.frames)}
    painted, shown = [], []
    for f, bgr in frames:
        i = lrow[int(f)]
        d, _ = lcap.depth(i)
        D = cv2.resize(d, (bgr.shape[1], bgr.shape[0]), interpolation=cv2.INTER_NEAREST)
        im, n = lay_on(bgr.astype(np.float32), lidar_poses[i], lcap.K_rgb[i], D, items)
        painted.append((f, im))
        shown.append(n)
    if frames_out:
        Path(frames_out).mkdir(parents=True, exist_ok=True)
        for j in np.argsort([sum(n) for n in shown])[::-1][:4]:
            cv2.imwrite(str(Path(frames_out) / f"frame_{painted[j][0]}.jpg"), painted[j][1], [cv2.IMWRITE_JPEG_QUALITY, 85])
    t2 = time.time()
    dmg, _ = dl.run(cap, poses, plan, painted, wall_band=band)
    t_dmg = time.time() - t2

    # an item is found when a wall region of the test run lies within 0.6 m of it in plan (the
    # video plan's wall ids differ from the LiDAR plan's, so match by position)
    yaw = plan["alignment"]["yaw_rad"]
    c, s_ = np.cos(yaw), np.sin(yaw)
    out_items = []
    for j, it in enumerate(items):
        Wc = it["corners"].mean(0)
        xy = np.array([Wc[0] * c + Wc[2] * s_, -Wc[0] * s_ + Wc[2] * c])
        hits = []
        for r in dmg["regions"]:
            sf = next(x for x in surfs if x.sid == r["surface"])
            if sf.kind != "wall":
                continue
            a0, a1 = r["along_range_m"]
            p0, p1 = sf.origin + a0 * sf.axis, sf.origin + a1 * sf.axis
            t = np.clip(np.dot(xy - p0, p1 - p0) / max(np.dot(p1 - p0, p1 - p0), 1e-9), 0, 1)
            if np.linalg.norm(p0 + t * (p1 - p0) - xy) < 0.6:
                hits.append(r)
        got = sum(r["area_m2"]["value"] for r in hits)
        out_items.append({"class": it["class"], "photo": it["photo"], "lidar_surface": it["surface"],
                          "along_m": it["along_m"], "height_m": it["height_m"], "area_m2": it["area_m2"],
                          "frames_showing": int(sum(1 for n in shown if n[j] > 500)), "found": bool(hits),
                          "classes_found": sorted({r["class"] for r in hits}), "area_found_m2": round(got, 4),
                          "area_err_pct": round((got - it["area_m2"]) / it["area_m2"] * 100, 1)})
    rep = {"capture": capture.name, "tier": tier, "frames": len(frames), "detector": clean["detector"],
           "seconds_per_run": round((t_clean + t_dmg) / 2, 1),
           "clean": {"frame_detections": clean["frame_detections"], "regions": len(clean["regions"]),
                     "region_area_m2": round(sum(r["area_m2"]["value"] for r in clean["regions"]), 4),
                     "list": [(r["surface"], r["class"], r["area_m2"]["value"], r["score"]) for r in clean["regions"]]},
           "with_damage": {"frame_detections": dmg["frame_detections"], "regions": len(dmg["regions"]),
                           "list": [(r["surface"], r["class"], r["area_m2"]["value"], r["score"]) for r in dmg["regions"]]},
           "items": out_items}
    print(json.dumps(rep, indent=1))
    if out_json:
        Path(out_json).parent.mkdir(parents=True, exist_ok=True)
        Path(out_json).write_text(json.dumps(rep, indent=1))


if __name__ == "__main__":
    argv = sys.argv[1:]
    opt = lambda k, d=None: argv[argv.index(k) + 1] if k in argv else d
    pos = [a for j, a in enumerate(argv) if not a.startswith("--") and (j == 0 or argv[j - 1] not in ("--tier", "--frames-out"))]
    main(*pos, tier=opt("--tier", "lidar"), frames_out=opt("--frames-out"))

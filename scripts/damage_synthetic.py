"""Synthetic damage test: paint stains of known size onto real walls in the RGB frames, then
check that the damage stage finds them on the right surface with the right area.

The sample captures contain no damage, so this tests everything after the detector's colour
decision: unwrapping onto surfaces, region extraction, metric area and its interval. Each stain
is an irregular blob defined in wall coordinates (metres along the wall, metres above the floor),
projected into every keyframe with the frame's pose and intrinsics, hidden where the depth map
shows something in front of the wall, and blended in with a soft edge.

    python scripts/damage_synthetic.py <capture_dir> [out.json]
"""
import json
import sys
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from ysplan import damage, io_stray, mono, pipeline_lidar  # noqa: E402

TINTS = {"water_stain": (0.55, 0.70, 0.80),     # BGR factors: darker and yellow-brown
         "dark_spot": (0.40, 0.42, 0.42)}       # darker, neutral (mould-like)


def blob(center, R, n=48, seed=0):
    """Irregular closed outline in wall coordinates (along, height)."""
    th = np.linspace(0, 2 * np.pi, n, endpoint=False)
    rng = np.random.default_rng(seed)
    p1, p2 = rng.uniform(0, 2 * np.pi, 2)
    r = R * (1 + 0.22 * np.sin(3 * th + p1) + 0.12 * np.cos(5 * th + p2))
    return np.stack([center[0] + r * np.cos(th), center[1] + r * np.sin(th)], 1)


def area(poly):
    x, y = poly[:, 0], poly[:, 1]
    return 0.5 * abs(np.dot(x, np.roll(y, 1)) - np.dot(y, np.roll(x, 1)))


def to_world(plan, s, outline):
    """Wall coordinates -> ARKit world points."""
    yaw, fy = plan["alignment"]["yaw_rad"], plan["alignment"]["floor_y_world"]
    xy = s.origin[None] + outline[:, :1] * s.axis[None]
    c, si = np.cos(yaw), np.sin(yaw)
    X = xy[:, 0] * c - xy[:, 1] * si
    Z = xy[:, 0] * si + xy[:, 1] * c
    return np.stack([X, outline[:, 1] + fy, Z], 1)


def main(capture, out_json=None):
    capture = Path(capture)
    plan, _, _ = pipeline_lidar.run(capture, log=lambda *a: None)
    poses = pipeline_lidar.run.poses
    cap = io_stray.load(capture)
    # 1. clean run: what the detector reports on undamaged walls (specificity), and textures
    clean, surfs0 = damage.run(cap, poses, plan, capture / "rgb.mp4", log=lambda *a: None)
    # 2. place each stain on the plainest patch of a well-seen wall (a stain painted over a
    #    picture frame or a curtain fold tests the scene, not the pipeline)
    spots = []
    for s in surfs0:
        if s.kind != "wall" or s.length < 1.0:
            continue
        tex, ok = s.texture()
        L = cv2.cvtColor(tex, cv2.COLOR_RGB2LAB)[..., 0].astype(np.float32)
        for a0, a1, _ in s.excluded:
            ok[:, int(a0 / 0.01):int(a1 / 0.01) + 1] = False
        k = 51                                               # 50 cm window
        f = ok.astype(np.float32)
        box = lambda a: cv2.boxFilter(a, -1, (k, k), normalize=False, borderType=cv2.BORDER_CONSTANT)
        n = box(f)
        m1 = box(L * f) / np.maximum(n, 1)
        m2 = box(L * L * f) / np.maximum(n, 1)
        sd = np.sqrt(np.maximum(m2 - m1 * m1, 0))
        good = (n >= 0.97 * k * k)
        good[:int(0.8 / 0.01)] = False                       # stains between 0.8 and 1.9 m
        good[int(1.9 / 0.01):] = False
        if good.any():
            r, c = np.unravel_index(np.argmin(np.where(good, sd, 1e9)), sd.shape)
            spots.append((float(sd[r, c]), s, (c * 0.01, r * 0.01)))
    spots.sort(key=lambda x: x[0])
    if len(spots) < 2:
        raise SystemExit("need two plain wall patches")
    truth = []
    for (cls, R), (_, s, ctr), seed in zip((("water_stain", 0.18), ("dark_spot", 0.11)), spots[:2], (1, 2)):
        outline = blob(ctr, R, seed=seed)
        truth.append({"class": cls, "surface": s.sid, "area_m2": round(area(outline), 4),
                      "along_range_m": [round(outline[:, 0].min(), 3), round(outline[:, 0].max(), 3)],
                      "height_range_m": [round(outline[:, 1].min(), 3), round(outline[:, 1].max(), 3)],
                      "_world": to_world(plan, s, outline)})

    painted_px = []

    def tint(i, bgr):
        T, K = poses[i], cap.K_rgb[i]
        d, _ = cap.depth(i)
        D = cv2.resize(d, (bgr.shape[1], bgr.shape[0]), interpolation=cv2.INTER_NEAREST)
        out = bgr.astype(np.float32)
        for t in truth:
            Pc = (t["_world"] - T[:3, 3]) @ T[:3, :3]          # world -> camera (OpenCV axes)
            if (Pc[:, 2] < 0.2).any():
                continue
            uv = (Pc @ K.T)[:, :2] / Pc[:, 2:]
            if (uv[:, 0].max() < 0 or uv[:, 1].max() < 0 or uv[:, 0].min() > bgr.shape[1]
                    or uv[:, 1].min() > bgr.shape[0]):
                continue
            m = np.zeros(bgr.shape[:2], np.uint8)
            cv2.fillPoly(m, [np.round(uv).astype(np.int32)], 1)
            vis = (np.abs(D - Pc[:, 2].mean()) < 0.12) & (D > 0)  # hidden behind nearer objects
            painted_px.append(int((m.astype(bool) & vis).sum()))
            soft = cv2.GaussianBlur((m.astype(bool) & vis).astype(np.float32), (0, 0), 2.0)[..., None]
            f = np.array(TINTS[t["class"]], np.float32)[None, None]
            out = out * (1 - soft) + out * f * soft
        return np.clip(out, 0, 255).astype(np.uint8)

    real_iter = mono.iter_frames

    def painted(video, wanted, size=None):
        row_of = {int(f): i for i, f in enumerate(cap.frames)}
        for f, im in real_iter(video, wanted, size):
            yield f, tint(row_of[f], im)

    mono.iter_frames = painted
    try:
        dmg, surfs = damage.run(cap, poses, plan, capture / "rgb.mp4")
    finally:
        mono.iter_frames = real_iter
    import os
    if os.environ.get("SYN_PICKLE"):              # debugging: keep the textured surfaces
        import pickle
        pickle.dump({"surfs": surfs, "truth": truth, "plan": plan}, open(os.environ["SYN_PICKLE"], "wb"))
    if out_json:                                  # textures of the painted walls, for inspection
        for t in truth:
            s = next(x for x in surfs if x.sid == t["surface"])
            tex, ok = s.texture()
            img = cv2.cvtColor(tex, cv2.COLOR_RGB2BGR)
            img[~ok] = 255
            cv2.imwrite(str(Path(out_json).with_name(f"synthetic_{s.sid}.jpg")), np.ascontiguousarray(img[::-1]))
    print("painted pixels per keyframe (mean):", round(float(np.mean(painted_px)), 1) if painted_px else 0)
    rows = []
    for t in truth:
        hits = [r for r in dmg["regions"] if r["surface"] == t["surface"]]
        got = sum(r["area_m2"]["value"] for r in hits)
        sig = float(np.sqrt(sum(r["area_m2"]["sigma"] ** 2 for r in hits))) if hits else None
        rows.append({k: v for k, v in t.items() if not k.startswith("_")} | {
            "found": bool(hits), "classes_found": sorted({r["class"] for r in hits}),
            "area_found_m2": round(got, 4), "area_err_pct": round((got - t["area_m2"]) / t["area_m2"] * 100, 1),
            "inside_ci95": bool(hits and abs(got - t["area_m2"]) <= 1.96 * sig),
            "regions": [r["id"] for r in hits]})
    false_pos = [r for r in dmg["regions"] if r["surface"] not in {t["surface"] for t in truth}]
    rep = {"capture": capture.name, "clean_run_regions": len(clean["regions"]),
           "clean_run_area_m2": round(sum(r["area_m2"]["value"] for r in clean["regions"]), 4),
           "stains": rows, "false_positive_regions": len(false_pos),
           "false_positive_area_m2": round(sum(r["area_m2"]["value"] for r in false_pos), 4),
           "flags": dmg["flags"], "scope": dmg["scope"]}
    print(json.dumps({k: v for k, v in rep.items() if k not in ("scope", "flags")}, indent=1))
    if out_json:
        Path(out_json).parent.mkdir(parents=True, exist_ok=True)
        Path(out_json).write_text(json.dumps(rep, indent=1))


if __name__ == "__main__":
    main(*sys.argv[1:])

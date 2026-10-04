"""Photo-tier benchmark set that follows the capture protocol: per room, 2 to 8 sharp stills
whose neighbours overlap, plus a still through each doorway from both sides. A doorway still goes
in the folder of the room it looks into and is named door-from-<room>_*.jpg after the room the
photographer stands in; the photo tier uses these pairs to stitch rooms together.

make_photo_set.py picks stills that look in as many different directions as possible. Those
barely overlap (on the single_room capture only 2 of 30 photo pairs share any surface), so no
multi-view method can register them, and a person following the protocol would not shoot that
way. Here the stills grow as a chain: start from the sharpest in-room frame, then repeatedly add the
sharp frame that looks in the most new direction while still sharing 30-75% of its view with a
still already taken, which is how the protocol asks a person to shoot.
Overlap between stills is measured with the LiDAR depth and reported, so a set can be checked.

usage: python scripts/make_photo_set_protocol.py <capture_dir> <lidar plan.json> <out_dir> [--per-room 8]
"""
import argparse
import json
import sys
from pathlib import Path

import cv2
import numpy as np
from shapely.geometry import Point, Polygon

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from ysplan import io_stray, mono  # noqa: E402
from make_photo_set import save_jpeg, upright  # noqa: E402


def plan_xy(cap, yaw):
    c, s = np.cos(yaw), np.sin(yaw)
    t = cap.T_wc[:, :3, 3]
    return np.stack([t[:, 0] * c + t[:, 2] * s, -t[:, 0] * s + t[:, 2] * c], 1)


def sharpness(video, n, step=2, size=(480, 360)):
    """Variance of the Laplacian on every `step`-th frame (motion blur lowers it)."""
    fr = mono.read_frames(video, range(0, n, step), size)
    sh = np.zeros(n)
    for i, im in fr.items():
        sh[i] = cv2.Laplacian(cv2.cvtColor(im, cv2.COLOR_BGR2GRAY), cv2.CV_64F).var()
    return sh


def overlap(cap, i, j, stride=4):
    """Fraction of frame i's confident LiDAR points that land inside frame j."""
    d, c = cap.depth(i)
    d, c = d[::stride, ::stride], c[::stride, ::stride]
    K = cap.K_depth(i, cap.depth(i)[0].shape).copy()
    K[:2] /= stride
    v, u = np.nonzero((c >= 2) & (d > 0.2))
    if len(u) == 0:
        return 0.0
    z = d[v, u]
    P = np.stack([(u - K[0, 2]) * z / K[0, 0], (v - K[1, 2]) * z / K[1, 1], z], 1)
    P = P @ cap.T_wc[i][:3, :3].T + cap.T_wc[i][:3, 3]
    Tj = np.linalg.inv(cap.T_wc[j])
    Q = P @ Tj[:3, :3].T + Tj[:3, 3]
    Kj = cap.K_rgb[j]
    ok = Q[:, 2] > 0.1
    uj = Q[ok, 0] / Q[ok, 2] * Kj[0, 0] + Kj[0, 2]
    vj = Q[ok, 1] / Q[ok, 2] * Kj[1, 1] + Kj[1, 2]
    return float(np.sum((uj >= 0) & (uj < io_stray.RGB_W) & (vj >= 0) & (vj < io_stray.RGB_H)) / len(u))


def passage(plan, polys, A, B):
    """Doorway between rooms A and B: (centre xy, unit normal of its wall, half width, source).
    A door or opening the LiDAR plan detected between them, else the strip where the two outlines
    meet (an open passage)."""
    for o in plan.get("openings", []):
        if sorted(o.get("rooms", [])) == sorted([A, B]):
            mid = (o["from"] + o["to"]) / 2
            centre = np.array([mid, o["line"]]) if o["axis"] == "u" else np.array([o["line"], mid])
            axis = np.array([0.0, 1.0]) if o["axis"] == "u" else np.array([1.0, 0.0])
            return centre, axis, (o["to"] - o["from"]) / 2, o["id"]
    shared = polys[A].buffer(0.15).intersection(polys[B].buffer(0.15))
    if shared.is_empty:
        return None
    x0, y0, x1, y1 = shared.bounds
    axis = np.array([0.0, 1.0]) if x1 - x0 >= y1 - y0 else np.array([1.0, 0.0])
    return np.array(shared.centroid.coords[0]), axis, max(x1 - x0, y1 - y0) / 2, "shared wall"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("capture", type=Path)
    ap.add_argument("plan", type=Path)
    ap.add_argument("out", type=Path)
    ap.add_argument("--per-room", type=int, default=8)
    ap.add_argument("--min-overlap", type=float, default=0.3, help="each still shares this much with another")
    a = ap.parse_args()
    cap = io_stray.load(a.capture)
    plan = json.loads(a.plan.read_text())
    xy = plan_xy(cap, plan["alignment"]["yaw_rad"])
    tilt = np.degrees(np.arcsin(np.clip(-cap.T_wc[:, 1, 2], -1, 1)))
    level = np.abs(tilt) < 35
    sh = sharpness(a.capture / "rgb.mp4", len(cap))
    yaw = plan["alignment"]["yaw_rad"]
    fwd = cap.T_wc[:, :3, 2]
    fyaw = np.arctan2(-fwd[:, 0] * np.sin(yaw) + fwd[:, 2] * np.cos(yaw), fwd[:, 0] * np.cos(yaw) + fwd[:, 2] * np.sin(yaw))
    picks, report = {}, {}
    for room in plan["rooms"]:
        poly = Polygon(room["polygon"])
        inside = np.array([poly.buffer(-0.2).contains(Point(p)) for p in xy]) & level
        idx = np.flatnonzero(inside & (sh > 0))
        if len(idx) < 2:
            continue
        cand = idx[::3]
        cand = cand[sh[cand] >= np.median(sh[cand])]
        chosen = [int(cand[np.argmax(sh[cand])])]
        ov = {}
        while len(chosen) < a.per_room:                          # grow an overlapping chain
            best, best_score = None, -1
            for k in cand:
                if k in chosen:
                    continue
                o = max(ov.setdefault((k, j), max(overlap(cap, k, j), overlap(cap, j, k))) for j in chosen)
                if not a.min_overlap <= o <= 0.75:
                    continue
                d = np.min(np.abs(np.angle(np.exp(1j * (fyaw[k] - fyaw[chosen])))))
                if d > best_score:
                    best, best_score = int(k), d
            if best is None:
                break
            chosen.append(best)
        picks[room["id"]] = sorted(set(chosen))
    doors, doorways = {}, []                                     # rid -> {frame: room the camera stands in}
    # doorway stills, as the protocol asks: from each side of the doorway between two adjacent
    # rooms, standing up to 2 m back and looking straight through it. The stitching reads the
    # direction into the room from each still, so a still more than 45 deg oblique is not used.
    c, s = np.cos(yaw), np.sin(yaw)
    fdir = np.stack([fwd[:, 0] * c + fwd[:, 2] * s, -fwd[:, 0] * s + fwd[:, 2] * c], 1)
    fdir /= np.maximum(np.linalg.norm(fdir, axis=1, keepdims=True), 1e-6)
    polys = {r["id"]: Polygon(r["polygon"]) for r in plan["rooms"]}
    for adj in plan.get("adjacency", []):
        A0, B0 = adj["rooms"]
        if A0 not in picks or B0 not in picks:
            continue
        door = passage(plan, polys, A0, B0)
        if door is None:
            continue
        centre, axis, half, src = door
        for A, B in ((A0, B0), (B0, A0)):
            n = axis if polys[B].distance(Point(centre + 0.4 * axis)) < polys[B].distance(Point(centre - 0.4 * axis)) else -axis
            e = np.array([-n[1], n[0]])
            rel = xy - centre
            back, lat = -(rel @ n), np.abs(rel @ e)
            ang = np.degrees(np.arccos(np.clip(fdir @ n, -1, 1)))
            ok = level & (sh > 0) & (back > 0.2) & (back < 2.0) & (lat <= max(0.4, half)) & (ang < 45)
            ok &= np.array([polys[A].contains(Point(p)) for p in xy])
            cand = np.flatnonzero(ok)
            if len(cand) == 0:
                doorways.append({"from": A, "into": B, "via": src, "frame": None})
                continue
            cand = cand[ang[cand] <= ang[cand].min() + 10]          # the straightest views, then the sharpest
            f = int(cand[np.argmax(sh[cand])])
            picks[B] = sorted(set(picks[B]) | {f})
            doors.setdefault(B, {})[f] = A
            doorways.append({"from": A, "into": B, "via": src, "frame": f, "oblique_deg": round(float(ang[f]), 1),
                             "metres_back": round(float(back[f]), 2)})
    for rid, fs in picks.items():
        ov = [round(max(overlap(cap, i, j), overlap(cap, j, i)), 2) for i, j in zip(fs[:-1], fs[1:])]
        report[rid] = {"frames": fs, "consecutive_overlap": ov}
    allf = sorted({f for v in picks.values() for f in v})
    frames = mono.read_frames(a.capture / "rgb.mp4", allf)
    for rid, fs in picks.items():
        d = a.out / rid
        d.mkdir(parents=True, exist_ok=True)
        for f in fs:
            img, f35 = upright(frames[f], cap.T_wc[f], cap.K_rgb[f])
            # protocol: a doorway still is named after the room the photographer stands in
            name = f"door-from-{doors[rid][f]}_{f:06d}.jpg" if f in doors.get(rid, {}) else f"{f:06d}.jpg"
            save_jpeg(d / name, img, f35)
    (a.out / "_source_frames.json").write_text(json.dumps({k: v["frames"] for k, v in report.items()}, indent=1))
    (a.out / "_overlap.json").write_text(json.dumps(report, indent=1))
    (a.out / "_doorways.json").write_text(json.dumps(doorways, indent=1))
    for k, v in report.items():
        print(k, len(v["frames"]), "overlap", v["consecutive_overlap"])


if __name__ == "__main__":
    main()

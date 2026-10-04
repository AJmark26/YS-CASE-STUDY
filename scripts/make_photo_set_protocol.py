"""Photo-tier benchmark set that follows the capture protocol: per room, 2 to 8 sharp stills
whose neighbours overlap, plus a still through each doorway from both sides.

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
    for o in plan["openings"]:                                   # doorway stills, as before
        if len(o.get("rooms", [])) < 2:
            continue
        mid = (o["from"] + o["to"]) / 2
        cxy = np.array([mid, o["line"]]) if o["axis"] == "u" else np.array([o["line"], mid])
        fwd = cap.T_wc[:, :3, 2]
        c, s = np.cos(plan["alignment"]["yaw_rad"]), np.sin(plan["alignment"]["yaw_rad"])
        fdir = np.stack([fwd[:, 0] * c + fwd[:, 2] * s, -fwd[:, 0] * s + fwd[:, 2] * c], 1)
        near = np.flatnonzero((np.linalg.norm(xy - cxy, axis=1) < 0.6) & level & (sh > 0))
        for rid in o["rooms"]:
            poly = Polygon(next(r["polygon"] for r in plan["rooms"] if r["id"] == rid))
            look = [i for i in near if poly.contains(Point(xy[i] + 1.5 * fdir[i] / max(np.linalg.norm(fdir[i]), 1e-6)))]
            if look and rid in picks and len(picks[rid]) < a.per_room + 2:
                picks[rid] = sorted(set(picks[rid]) | {int(max(look, key=lambda i: sh[i]))})
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
            save_jpeg(d / f"{f:06d}.jpg", img, f35)
    (a.out / "_source_frames.json").write_text(json.dumps({k: v["frames"] for k, v in report.items()}, indent=1))
    (a.out / "_overlap.json").write_text(json.dumps(report, indent=1))
    for k, v in report.items():
        print(k, len(v["frames"]), "overlap", v["consecutive_overlap"])


if __name__ == "__main__":
    main()

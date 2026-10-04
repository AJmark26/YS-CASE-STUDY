"""Score a photo-tier plan.json against the LiDAR plan of the same capture.

Rooms are matched by folder name (photo sets are cut from the LiDAR plan, so folders carry its
room ids; --swap R3:R4 handles renumbering). Two questions are kept apart:

1. Room geometry. Each photo room's wall points are registered (90 deg rotations plus
   translation, 2D) onto the LiDAR wall points around the reference room. Both fused clouds are
   then measured with the pipeline's own measure.walls() on the LiDAR outline, as the benchmark
   does for the video tier. Also: floor area, bounding-box sides, and whether the 95% area
   interval covered the LiDAR value. The photo-tier gate is +-8% on wall lengths and footprint.
2. Stitching. Every photo is a frame of the LiDAR capture, so its true camera position is known
   (poses_world.npy of the LiDAR run). The stitched plan records where it put each camera. A
   rigid 2D fit of all stitched cameras onto their true positions gives the plan-wide frame; a
   room's placement error is how far its cameras then land from the truth, and its rotation
   error is the angle between its own best fit and the plan-wide one. Unstitched rooms are laid
   out beside the plan and are left out of that summary.
   The per-room camera fit also starts the wall registration of part 1 (then ICP on the walls),
   which is far faster and safer than a blind search on a partly seen room.

usage: python scripts/eval_photo.py <photo out dir> <lidar out dir> [--swap R3:R4]
"""
import argparse
import json
import sys
from pathlib import Path

import numpy as np
from shapely import contains_xy
from shapely.geometry import Polygon

sys.path.insert(0, str(Path(__file__).resolve().parent))
import benchmark as bm  # noqa: E402
import register_plans as rp  # noqa: E402
from ysplan import measure  # noqa: E402


def fit2d(A, B):
    """Rigid 2D transform (3x3) taking points A onto B (least squares, no reflection)."""
    ma, mb = A.mean(0), B.mean(0)
    U, _, Vt = np.linalg.svd((A - ma).T @ (B - mb))
    R = (U @ Vt).T
    if np.linalg.det(R) < 0:
        Vt[-1] *= -1
        R = (U @ Vt).T
    T = np.eye(3)
    T[:2, :2], T[:2, 2] = R, mb - R @ ma
    return T


def _angle(T):
    return float(np.degrees(np.arctan2(T[1, 0], T[0, 0])))


def true_cameras(plan, lidar_dir, lidar_plan):
    """{room: (photo xy in the photo plan, true xy in the LiDAR plan)} from the frame numbers in
    the photo names and the LiDAR run's camera poses."""
    pw = Path(lidar_dir) / "poses_world.npy"
    cams = plan.get("photo", {}).get("cameras")
    if not pw.exists() or not cams:
        return {}
    poses = np.load(pw)
    yaw = lidar_plan["alignment"]["yaw_rad"]
    c, s = np.cos(yaw), np.sin(yaw)
    out = {}
    for room, lst in cams.items():
        fr = [int(Path(x["photo"]).stem.rsplit("_", 1)[-1]) for x in lst]
        t = poses[fr, :3, 3]
        out[room] = (np.array([x["xy"] for x in lst]), np.stack([t[:, 0] * c + t[:, 2] * s, -t[:, 0] * s + t[:, 2] * c], 1))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("photo")
    ap.add_argument("lidar")
    ap.add_argument("--swap", action="append", default=[])
    ap.add_argument("--min-obs", type=float, default=0.2)
    a = ap.parse_args()
    ph, P_xy, UP = bm.load(a.photo)
    li, L_xy, UL = bm.load(a.lidar)
    ren = {}
    for s in a.swap:
        x, y = s.split(":")
        ren[x], ren[y] = y, x
    ref = {r["id"]: r for r in li["rooms"]}
    cams = true_cameras(ph, a.lidar, li)
    stitched = [r["id"] for r in ph["rooms"] if r.get("stitched") and r["id"] in cams]
    Tg = None
    if stitched:
        Tg = fit2d(np.concatenate([cams[n][0] for n in stitched]), np.concatenate([cams[n][1] for n in stitched]))
    rows = []
    for r in ph["rooms"]:
        lid = ren.get(r["id"], r["id"])
        if lid not in ref:
            continue
        g = ref[lid]
        A, Ag = r["floor_area_m2"], g["floor_area_m2"]["value"]
        bb, bbg = sorted(r["bbox_m"]), sorted(g["bbox_m"])
        ppoly, gpoly = Polygon(r["polygon"]).buffer(0), Polygon(g["polygon"]).buffer(0)
        sub = P_xy[contains_xy(ppoly.buffer(0.4), P_xy[:, 0], P_xy[:, 1])]
        Lsub = L_xy[contains_xy(gpoly.buffer(1.0), L_xy[:, 0], L_xy[:, 1])]
        walls, fit, place, T = [], None, None, None
        if r["id"] in cams and len(cams[r["id"]][0]) >= 2:
            pa, tr = cams[r["id"]]
            T = fit2d(pa, tr)
            place = {"cameras": len(pa), "own_fit_rms_m": round(float(np.sqrt(np.mean(np.sum((rp.apply(T, pa) - tr) ** 2, 1)))), 3)}
            if Tg is not None and r["id"] in stitched:
                place["offset_m"] = round(float(np.mean(np.linalg.norm(rp.apply(Tg, pa) - tr, axis=1))), 3)
                place["rotation_err_deg"] = round((_angle(T) - _angle(Tg) + 180) % 360 - 180, 1)
        if len(sub) > 100 and len(Lsub) > 100:
            if T is not None:
                T, fit = rp.icp2d(Lsub, sub, T, iters=30, max_d=0.25, rotate=True)
            else:
                T, fit = rp.register(Lsub, sub, step=0.5)
            Ur = bm._moveU(UP[contains_xy(ppoly.buffer(0.6), UP[:, 0], UP[:, 2])], T)
            near_l = contains_xy(gpoly.buffer(0.6), UL[:, 0], UL[:, 2])
            near_p = contains_xy(gpoly.buffer(0.6), Ur[:, 0], Ur[:, 2])
            wl = measure.walls(UL[near_l], gpoly)
            wp = measure.walls(Ur[near_p], gpoly, tier_scale=ph.get("interval_scale", 1.0))
            for k, (x, y) in enumerate(zip(wl, wp)):
                Lx, Ly = x["length_m"]["value"], y["length_m"]["value"]
                if Lx < 0.5 or x["observed_fraction"] < 0.3 or y["observed_fraction"] < a.min_obs:
                    continue
                if not (x.get("ends_observed", True) and y.get("ends_observed", True)):
                    continue
                walls.append({"wall": f"W{k + 1}", "length_ref": Lx, "length_test": Ly,
                              "diff_cm": round((Ly - Lx) * 100, 1), "diff_pct": round((Ly - Lx) / Lx * 100, 1),
                              "ci95_covers": bool(y["length_m"]["ci95"][0] <= Lx <= y["length_m"]["ci95"][1])})
        rows.append({"room": r["id"], "lidar_room": lid, "stitched": r.get("stitched", False),
                     "registration_fit": round(fit, 3) if fit else None, "placement": place,
                     "walls": walls,
                     "area": A["value"], "area_ref": Ag, "area_err_pct": round(100 * (A["value"] / Ag - 1), 1),
                     "area_ci_covers": bool(A["ci95"][0] <= Ag <= A["ci95"][1]),
                     "bbox": bb, "bbox_ref": bbg,
                     "bbox_err_pct": [round(100 * (x / y - 1), 1) for x, y in zip(bb, bbg)]})
    werr = np.array([w["diff_pct"] for r in rows for w in r["walls"]])
    errs = np.array([e for r in rows for e in r["bbox_err_pct"]])
    aerr = np.array([r["area_err_pct"] for r in rows])
    st = [r for r in rows if r["placement"] and "offset_m" in r["placement"]]
    summary = {"rooms": len(rows), "rooms_reference": len(li["rooms"]),
               "walls_within_8pct": f"{int(np.sum(np.abs(werr) <= 8))}/{len(werr)}",
               "median_abs_wall_err_pct": round(float(np.median(np.abs(werr))), 1) if len(werr) else None,
               "wall_ci_coverage": f"{sum(w['ci95_covers'] for r in rows for w in r['walls'])}/{len(werr)}",
               "bbox_within_8pct": f"{int(np.sum(np.abs(errs) <= 8))}/{len(errs)}",
               "area_within_8pct": f"{int(np.sum(np.abs(aerr) <= 8))}/{len(aerr)}",
               "median_abs_bbox_err_pct": round(float(np.median(np.abs(errs))), 1) if len(errs) else None,
               "area_ci_coverage": f"{sum(r['area_ci_covers'] for r in rows)}/{len(rows)}",
               "stitching": {"rooms_stitched": len(st), "rooms_with_true_cameras": len(cams),
                             "rotation_within_5deg": f"{sum(abs(r['placement']['rotation_err_deg']) <= 5 for r in st)}/{len(st)}",
                             "median_offset_m": round(float(np.median([r['placement']['offset_m'] for r in st])), 3) if st else None,
                             "max_offset_m": round(float(max(r['placement']['offset_m'] for r in st)), 3) if st else None}}
    print(json.dumps({"summary": summary, "rooms": rows}, indent=1))


if __name__ == "__main__":
    main()

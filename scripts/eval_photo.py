"""Score a photo-tier plan.json against the LiDAR plan of the same capture.

Rooms are matched by folder name (photo sets are cut from the LiDAR plan, so folders carry its
room ids; --swap R3:R4 handles renumbering). Each room is compared on floor area and bounding box
sides (sorted, since the photo frame's yaw is arbitrary until stitching). The gate for the photo
tier is +-8% on wall lengths and footprint; the report also says whether the 95% interval
covered the LiDAR value (calibration).

Wall lengths: each photo room's wall points are registered (rigid, 2D) onto the LiDAR wall points
around the reference room, then both are measured on the LiDAR polygon, as the benchmark does
for the video tier. This scores the geometry that was observed, whatever the outline.

usage: python scripts/eval_photo.py <photo out dir> <lidar out dir> [--swap R3:R4]
"""
import argparse
import json
import sys
from pathlib import Path

import numpy as np
from shapely.geometry import Polygon

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
import register_plans as rp  # noqa: E402
from ysplan import measure  # noqa: E402


def load(out):
    out = Path(out)
    return json.loads((out / "plan.json").read_text()), np.load(out / "wall_points_plan.npz")["xy"]


def compare_walls(poly, xy_a, xy_b, min_obs=0.3, min_len=0.5):
    """Both runs' 2D wall points measured on one polygon (the comparison benchmark.py made
    before it moved to 3D clouds)."""
    U = lambda xy: np.stack([xy[:, 0], np.full(len(xy), 1.0), xy[:, 1]], 1)  # inside the wall band
    wa, wb = measure.walls(U(xy_a), poly), measure.walls(U(xy_b), poly)
    rows = []
    for a, b in zip(wa, wb):
        La, Lb = a["length_m"]["value"], b["length_m"]["value"]
        if La < min_len or a["observed_fraction"] < min_obs or b["observed_fraction"] < min_obs:
            continue
        if not (a["ends_observed"] and b["ends_observed"]):   # length set by an unseen wall
            continue
        rows.append({"length_ref": La, "length_test": Lb, "diff_cm": round((Lb - La) * 100, 2),
                     "diff_pct": round((Lb - La) / La * 100, 2),
                     "sigma_ref_cm": round(a["length_m"]["sigma"] * 100, 2),
                     "sigma_test_cm": round(b["length_m"]["sigma"] * 100, 2)})
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("photo")
    ap.add_argument("lidar")
    ap.add_argument("--swap", action="append", default=[])
    a = ap.parse_args()
    ph, P_xy = load(a.photo)
    li, L_xy = load(a.lidar)
    ren = {}
    for s in a.swap:
        x, y = s.split(":")
        ren[x], ren[y] = y, x
    ref = {r["id"]: r for r in li["rooms"]}
    rows = []
    for r in ph["rooms"]:
        lid = ren.get(r["id"], r["id"])
        if lid not in ref:
            continue
        g = ref[lid]
        A, Ag = r["floor_area_m2"], g["floor_area_m2"]["value"]
        bb, bbg = sorted(r["bbox_m"]), sorted(g["bbox_m"])
        walls = []
        pb = np.array(r["polygon"])
        lo, hi = pb.min(0) - 0.4, pb.max(0) + 0.4
        sub = P_xy[np.all((P_xy > lo) & (P_xy < hi), axis=1)]
        gpoly = Polygon(g["polygon"])
        gb = np.array(g["polygon"])
        Lsub = L_xy[np.all((L_xy > gb.min(0) - 1.0) & (L_xy < gb.max(0) + 1.0), axis=1)]
        fit = None
        if len(sub) > 100 and len(Lsub) > 100:
            T, fit = rp.register(Lsub, sub, step=0.25)
            walls = compare_walls(gpoly, Lsub, rp.apply(T, sub), min_obs=0.2)
        rows.append({"room": r["id"], "lidar_room": lid, "registration_fit": round(fit, 3) if fit else None,
                     "walls": [{k: w[k] for k in ("length_ref", "length_test", "diff_pct")} for w in walls],
                     "area": A["value"], "area_ref": Ag, "area_err_pct": round(100 * (A["value"] / Ag - 1), 1),
                     "area_ci_covers": bool(A["ci95"][0] <= Ag <= A["ci95"][1]),
                     "bbox": bb, "bbox_ref": bbg,
                     "bbox_err_pct": [round(100 * (x / y - 1), 1) for x, y in zip(bb, bbg)]})
    werr = np.array([w["diff_pct"] for r in rows for w in r["walls"]])
    errs = np.array([e for r in rows for e in r["bbox_err_pct"]])
    aerr = np.array([r["area_err_pct"] for r in rows])
    summary = {"rooms": len(rows), "rooms_reference": len(li["rooms"]),
               "walls_within_8pct": f"{int(np.sum(np.abs(werr) <= 8))}/{len(werr)}",
               "median_abs_wall_err_pct": round(float(np.median(np.abs(werr))), 1) if len(werr) else None,
               "bbox_within_8pct": f"{int(np.sum(np.abs(errs) <= 8))}/{len(errs)}",
               "area_within_8pct": f"{int(np.sum(np.abs(aerr) <= 8))}/{len(aerr)}",
               "median_abs_bbox_err_pct": round(float(np.median(np.abs(errs))), 1) if len(errs) else None,
               "area_ci_coverage": f"{sum(r['area_ci_covers'] for r in rows)}/{len(rows)}"}
    print(json.dumps({"summary": summary, "rooms": rows}, indent=1))


if __name__ == "__main__":
    main()

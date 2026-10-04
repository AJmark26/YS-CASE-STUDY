"""Benchmark: repeatability, cross-tier accuracy and drift ablation, from pipeline outputs.

There is no tape/laser ground truth for the provided sample captures, so:
  - repeatability compares the two LiDAR captures over the rooms they share,
  - video/photo accuracy uses the LiDAR tier of the same capture as the reference,
  - every comparison measures wall faces on a common reference polygon, so it tests the
    measurement, not whether two runs happened to split rooms the same way (reported separately).
Writes bench/benchmark.json and bench/benchmark.md.
"""
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


def pseudo_U(xy):
    """2D wall points -> (u, h, v) with h inside the wall band so measure.face_stats accepts them."""
    return np.stack([xy[:, 0], np.full(len(xy), 1.0), xy[:, 1]], 1)


def walls_on(poly, xy):
    return measure.walls(pseudo_U(xy), poly)


def compare(poly, xy_a, xy_b, min_obs=0.3, min_len=0.5):
    wa, wb = walls_on(poly, xy_a), walls_on(poly, xy_b)
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


def load(out):
    out = Path(out)
    return json.loads((out / "plan.json").read_text()), np.load(out / "wall_points_plan.npz")["xy"]


def repeatability(dir_a, dir_b, min_cover=0.6):
    """Register capture B onto A in plan space and compare walls of the rooms of A that B covers."""
    A_plan, A_xy = load(dir_a)
    B_plan, B_xy = load(dir_b)
    T, fit = rp.register(A_xy, B_xy)
    B_in_A = rp.apply(T, B_xy)
    hull_b = Polygon()
    for r in B_plan["rooms"]:
        hull_b = hull_b.union(Polygon(rp.apply(T, np.array(r["polygon"]))).buffer(0))
    rows, rooms = [], []
    for r in A_plan["rooms"]:
        poly = Polygon(r["polygon"])
        if poly.intersection(hull_b).area / poly.area < min_cover:
            continue
        rooms.append(r["id"])
        for x in compare(poly, A_xy, B_in_A):
            x["room"] = r["id"]
            x["pair"] = f"{Path(dir_a).name}~{Path(dir_b).name}"
            x["pass"] = abs(x["diff_cm"]) <= 1.0 or abs(x["diff_pct"]) <= 0.5
            rows.append(x)
    return {"registration_fit": round(fit, 3), "rooms_compared": rooms, "walls": rows,
            "pass_rate": round(float(np.mean([x["pass"] for x in rows])), 3) if rows else None}


def main(out_root="out", bench_dir="bench"):
    out_root, bench = Path(out_root), Path(bench_dir)
    bench.mkdir(exist_ok=True)
    res = {}
    # repeatability: every pair of LiDAR captures, over the rooms of A that B also covers
    caps = [c for c in ["1a8384c3f6", "c7d28f72c6", "c00a170fe1"] if (out_root / f"{c}_lidar" / "plan.json").exists()]
    rep_all = {}
    for i, a in enumerate(caps):
        for b in caps[i + 1:]:
            rep_all[f"{a}~{b}"] = repeatability(out_root / f"{a}_lidar", out_root / f"{b}_lidar")
    walls = [w for r in rep_all.values() for w in r["walls"]]
    res["repeatability_lidar"] = {
        "pairs": {k: {kk: vv for kk, vv in v.items() if kk != "walls"} for k, v in rep_all.items()},
        "walls": walls, "n_walls": len(walls),
        "pass_rate": round(float(np.mean([x["pass"] for x in walls])), 3) if walls else None,
        "median_abs_diff_cm": round(float(np.median([abs(x["diff_cm"]) for x in walls])), 2) if walls else None}
    # cross-tier: video vs LiDAR on the same capture
    for cid in ["c00a170fe1", "1a8384c3f6"]:
        for tier in ["video", "photo"]:
            d = out_root / f"{cid}_{tier}"
            if not (d / "plan.json").exists():
                continue
            L_plan, L_xy = load(out_root / f"{cid}_lidar")
            V_plan, V_xy = load(d)
            if not V_plan["rooms"]:
                res[f"{tier}_vs_lidar_{cid}"] = {"rooms_found": 0, "rooms_reference": len(L_plan["rooms"]),
                                                 "pass_rate": 0.0, "note": "no rooms recovered"}
                continue
            Tv, fv = rp.register(L_xy, V_xy)
            V_in_L = rp.apply(Tv, V_xy)
            rows = []
            for r in L_plan["rooms"]:
                for x in compare(Polygon(r["polygon"]), L_xy, V_in_L, min_obs=0.2):
                    x["room"] = r["id"]
                    rows.append(x)
            gate = 3.0 if tier == "video" else 8.0
            res[f"{tier}_vs_lidar_{cid}"] = {
                "registration_fit": round(fv, 3), "walls": rows, "gate_pct": gate,
                "pass_rate": round(float(np.mean([abs(x["diff_pct"]) <= gate for x in rows])), 3) if rows else None,
                "median_abs_err_pct": round(float(np.median([abs(x["diff_pct"]) for x in rows])), 2) if rows else None,
                "rooms_found": len(V_plan["rooms"]), "rooms_reference": len(L_plan["rooms"])}
    # drift ablation
    for tag in ["1a8384c3f6_lidar", "1a8384c3f6_lidar_nodrift"]:
        d = out_root / tag
        if (d / "plan.json").exists():
            p = json.loads((d / "plan.json").read_text())
            res.setdefault("drift_ablation", {})[tag] = {
                "footprint_m2": p["footprint_m2"]["value"], "rooms": len(p["rooms"]),
                "drift": {k: v for k, v in p["drift"].items() if k != "loops"},
                "timing_s": p["timing_s"]}
    (bench / "benchmark.json").write_text(json.dumps(res, indent=1))
    print(json.dumps({k: {kk: vv for kk, vv in v.items() if kk != "walls"} if isinstance(v, dict) else v
                      for k, v in res.items()}, indent=1))


if __name__ == "__main__":
    main(*sys.argv[1:])

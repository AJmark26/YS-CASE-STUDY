"""Benchmark: repeatability, cross-tier accuracy and drift ablation, from pipeline outputs.

There is no tape/laser ground truth for the provided sample captures, so:
  - repeatability compares every pair of LiDAR captures over the rooms they share, for wall
    lengths and for opening widths (with missed and phantom openings counted),
  - video/photo accuracy uses the LiDAR tier of the same capture as the reference.
Run B is placed in run A's plan frame (90 deg rotations plus translation, refined per room).
Both runs' fused clouds are then measured with the pipeline's own measure.walls() on run A's
room outline. This tests the measurement, not whether two runs split the space into rooms the
same way; that is reported separately as room-split agreement (IoU of the best-matching room).
Walls whose length is bounded by an unseen wall are skipped. Each run's walls get the same
interval terms the pipeline gave them (its drift term and tier scale), so the calibration
check tests the intervals plan.json reports.
Writes bench/benchmark.json.
"""
import json
import sys
from pathlib import Path

import numpy as np
from shapely import contains_xy
from shapely.geometry import Point, Polygon
from shapely.ops import unary_union

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
import register_plans as rp  # noqa: E402
from ysplan import measure  # noqa: E402


def load(out):
    out = Path(out)
    plan = json.loads((out / "plan.json").read_text())
    xy = np.load(out / "wall_points_plan.npz")["xy"]
    U = np.load(out / "points_plan_frame.npz")["U"].astype(np.float64)
    return plan, xy, U


def _sigma_drift(plan):
    """The drift term the pipeline put in this run's wall intervals (pipeline_lidar.run)."""
    d = plan.get("drift") or {}
    return d.get("loop_misalignment_cm_after", 0.0) / 100.0 if d.get("enabled") else 0.0


def _moveU(U, T):
    xy = rp.apply(T, U[:, [0, 2]])
    return np.stack([xy[:, 0], U[:, 1], xy[:, 1]], 1)


def compare_runs(dir_a, dir_b, min_cover=0.6, min_len=0.5, min_obs=0.3, same_capture=False):
    """Measure run A's rooms in both runs' clouds and compare wall lengths."""
    A, A_xy, UA = load(dir_a)
    B, B_xy, UB = load(dir_b)
    T, fit = rp.register(A_xy, B_xy)
    B_polys = [Polygon(rp.apply(T, np.array(r["polygon"]))).buffer(0) for r in B["rooms"]]
    hull_b = Polygon()
    for q in B_polys:
        hull_b = hull_b.union(q)
    B_xy_A = rp.apply(T, B_xy)
    rows, rooms = [], []
    for ra in A["rooms"]:
        pa = Polygon(ra["polygon"]).buffer(0)
        if pa.intersection(hull_b).area / pa.area < min_cover:
            continue
        iou = max((q.intersection(pa).area / q.union(pa).area for q in B_polys), default=0.0)
        # residual drift differs between captures: refine the translation on this room's walls
        a_pts = A_xy[contains_xy(pa.buffer(0.3), A_xy[:, 0], A_xy[:, 1])]
        b_pts = B_xy_A[contains_xy(pa.buffer(0.5), B_xy_A[:, 0], B_xy_A[:, 1])]
        Tl = np.eye(3)
        if len(a_pts) > 100 and len(b_pts) > 100:
            Tl, _ = rp.icp2d(a_pts, b_pts, np.eye(3), iters=30, max_d=0.10, rotate=False)
        near_a = contains_xy(pa.buffer(0.6), UA[:, 0], UA[:, 2])
        UBr = _moveU(UB, Tl @ T)
        near_b = contains_xy(pa.buffer(0.6), UBr[:, 0], UBr[:, 2])
        wa = measure.walls(UA[near_a], pa, sigma_drift=_sigma_drift(A), tier_scale=A.get("interval_scale", 1.0))
        wb = measure.walls(UBr[near_b], pa, sigma_drift=_sigma_drift(B), tier_scale=B.get("interval_scale", 1.0))
        rooms.append({"room": ra["id"], "split_iou": round(iou, 3),
                      "shift_cm": [round(Tl[0, 2] * 100, 1), round(Tl[1, 2] * 100, 1)]})
        for k, (a, b) in enumerate(zip(wa, wb)):
            La, Lb = a["length_m"]["value"], b["length_m"]["value"]
            if La < min_len or a["observed_fraction"] < min_obs or b["observed_fraction"] < min_obs:
                continue
            if not (a.get("ends_observed", True) and b.get("ends_observed", True)):
                continue
            rows.append({"room": ra["id"], "wall": f"W{k + 1}", "length_ref": La, "length_test": Lb,
                         "diff_cm": round((Lb - La) * 100, 2), "diff_pct": round((Lb - La) / La * 100, 2),
                         "sigma_ref_cm": round(a["length_m"]["sigma"] * 100, 2),
                         "sigma_test_cm": round(b["length_m"]["sigma"] * 100, 2)})
    return {"registration_fit": round(fit, 3), "rooms": rooms, "walls": rows}


def _opening_ends(o):
    a, b = (o["from"], o["line"]), (o["to"], o["line"])
    if o["axis"] == "v":
        a, b = (o["line"], o["from"]), (o["line"], o["to"])
    return np.array([a, b], float)


def compare_openings(dir_a, dir_b, match_m=0.35, gate_cm=2.0):
    """Openings found in both runs, over the part of the home both runs cover.

    Run B is placed in run A's frame as in compare_runs. An opening of either run whose centre
    lies inside the other run's rooms (grown by 30 cm, so doors on the boundary count) should
    have been found by both; it is matched to the other run's nearest opening on a parallel wall
    with its centre within `match_m`. Unmatched ones count as misses, as the gate counts missed
    and phantom openings."""
    A, A_xy, _ = load(dir_a)
    B, B_xy, _ = load(dir_b)
    T, _ = rp.register(A_xy, B_xy)
    cov_a = unary_union([Polygon(r["polygon"]).buffer(0.3) for r in A["rooms"]])
    cov_b = unary_union([Polygon(rp.apply(T, np.array(r["polygon"]))).buffer(0.3) for r in B["rooms"]])
    oa = [(o, _opening_ends(o)) for o in A["openings"]]
    ob = [(o, rp.apply(T, _opening_ends(o))) for o in B["openings"]]
    oa = [(o, e) for o, e in oa if cov_b.contains(Point(e.mean(0)))]
    ob = [(o, e) for o, e in ob if cov_a.contains(Point(e.mean(0)))]
    used, rows, missed = set(), [], []
    for o, e in oa:
        d = e[1] - e[0]
        best, bd = None, match_m
        for j, (q, f) in enumerate(ob):
            g = f[1] - f[0]
            if j in used or abs(abs(np.dot(d, g)) / (np.linalg.norm(d) * np.linalg.norm(g) + 1e-9) - 1) > 0.05:
                continue
            dist = np.linalg.norm(e.mean(0) - f.mean(0))
            if dist < bd:
                best, bd = j, dist
        if best is None:
            missed.append({"run": "A", "id": o["id"], "type": o["type"], "width_m": o["width_m"]["value"]})
            continue
        used.add(best)
        q = ob[best][0]
        wa, wb = o["width_m"]["value"], q["width_m"]["value"]
        rows.append({"id_ref": o["id"], "id_test": q["id"], "type_ref": o["type"], "type_test": q["type"],
                     "width_ref": wa, "width_test": wb, "diff_cm": round((wb - wa) * 100, 2),
                     "inside_ci95": bool(abs(wb - wa) <= 1.96 * np.hypot(o["width_m"]["sigma"], q["width_m"]["sigma"])),
                     "centre_offset_cm": round(bd * 100, 1)})
    missed += [{"run": "B", "id": q["id"], "type": q["type"], "width_m": q["width_m"]["value"]}
               for j, (q, _) in enumerate(ob) if j not in used]
    n = len(rows) + len(missed)
    ok = sum(abs(r["diff_cm"]) <= gate_cm for r in rows)
    return {"matched": rows, "unmatched": missed, "n_openings": n,
            "pass_rate": round(ok / n, 3) if n else None,
            "median_abs_diff_cm": round(float(np.median([abs(r["diff_cm"]) for r in rows])), 2) if rows else None,
            "within_ci95": round(float(np.mean([r["inside_ci95"] for r in rows])), 3) if rows else None}


def summary(rows, ok):
    return {"n_walls": len(rows),
            "pass_rate": round(float(np.mean([ok(x) for x in rows])), 3) if rows else None,
            "median_abs_diff_cm": round(float(np.median([abs(x["diff_cm"]) for x in rows])), 2) if rows else None,
            "median_abs_diff_pct": round(float(np.median([abs(x["diff_pct"]) for x in rows])), 2) if rows else None,
            # calibration: share of differences inside the combined 95% interval of the two runs
            "within_ci95": round(float(np.mean([abs(x["diff_cm"]) <= 1.96 * np.hypot(x["sigma_ref_cm"], x["sigma_test_cm"])
                                                for x in rows])), 3) if rows else None}


def main(out_root="out", bench_dir="bench"):
    out_root, bench = Path(out_root), Path(bench_dir)
    bench.mkdir(exist_ok=True)
    res = {}
    # repeatability: every pair of LiDAR captures, over the rooms both runs found
    caps = [c for c in ["1a8384c3f6", "c7d28f72c6", "c00a170fe1"] if (out_root / f"{c}_lidar" / "plan.json").exists()]
    rep_ok = lambda x: abs(x["diff_cm"]) <= 1.0 or abs(x["diff_pct"]) <= 0.5
    pairs, walls = {}, []
    for i, a in enumerate(caps):
        for b in caps[i + 1:]:
            r = compare_runs(out_root / f"{a}_lidar", out_root / f"{b}_lidar")
            for x in r["walls"]:
                x["pair"] = f"{a}~{b}"
                x["pass"] = rep_ok(x)
            pairs[f"{a}~{b}"] = dict({k: v for k, v in r.items() if k != "walls"}, **summary(r["walls"], rep_ok))
            walls += r["walls"]
    res["repeatability_lidar"] = dict(summary(walls, rep_ok), gate="|diff| <= 1 cm or 0.5%", pairs=pairs, walls=walls)
    # openings: same opening in two captures within 2 cm; a miss in either capture counts as a failure
    op = {}
    for i, a in enumerate(caps):
        for b in caps[i + 1:]:
            op[f"{a}~{b}"] = compare_openings(out_root / f"{a}_lidar", out_root / f"{b}_lidar")
    n = sum(v["n_openings"] for v in op.values())
    diffs = [abs(r["diff_cm"]) for v in op.values() for r in v["matched"]]
    res["openings_repeatability_lidar"] = {
        "gate": "|width diff| <= 2 cm on >= 85% of openings; missed or phantom openings count as misses",
        "n_openings": n, "matched": len(diffs),
        "pass_rate": round(sum(d <= 2.0 for d in diffs) / n, 3) if n else None,
        "matched_within_2cm": round(float(np.mean(np.array(diffs) <= 2.0)), 3) if diffs else None,
        "median_abs_diff_cm": round(float(np.median(diffs)), 2) if diffs else None,
        "within_ci95": round(float(np.mean([r["inside_ci95"] for v in op.values() for r in v["matched"]])), 3) if diffs else None,
        "pairs": op}
    # cross-tier: video/photo vs LiDAR on the same capture
    for cid in caps:
        for tier, gate in (("video", 3.0), ("photo", 8.0)):
            d = out_root / f"{cid}_{tier}"
            if not (d / "plan.json").exists():
                continue
            V = json.loads((d / "plan.json").read_text())
            L = json.loads((out_root / f"{cid}_lidar" / "plan.json").read_text())
            key = f"{tier}_vs_lidar_{cid}"
            if not (d / "points_plan_frame.npz").exists():
                res[key] = {"note": "stale output without points_plan_frame.npz; rerun the tier"}
                continue
            if not V["rooms"]:
                res[key] = {"rooms_found": 0, "rooms_reference": len(L["rooms"]), "pass_rate": 0.0,
                            "note": "no rooms recovered"}
                continue
            r = compare_runs(out_root / f"{cid}_lidar", d)
            ok = lambda x, g=gate: abs(x["diff_pct"]) <= g
            res[key] = dict({k: v for k, v in r.items()}, **summary(r["walls"], ok), gate_pct=gate,
                            rooms_found=len(V["rooms"]), rooms_reference=len(L["rooms"]))
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

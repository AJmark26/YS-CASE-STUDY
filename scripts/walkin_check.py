"""Walk-in rehearsal: run the pipeline on captures it has never seen and compare its plan,
end to end, with the plan from the full sample capture the test was cut from.

Unlike the repeatability benchmark (which measures both clouds on one room outline), this
compares each run's own output: its own rooms, its own wall lines, its own openings. That is
what a live test on a new room checks.

    python scripts/walkin_check.py <test_out>:<reference_out> [...] [--json bench/walkin.json]

Each test room is matched to the reference room it overlaps most (after placing the test plan
in the reference frame with 90-degree turns plus translation). Each wall is matched to the
parallel reference wall in that room whose midpoint is nearest, within 30 cm.
"""
import argparse
import json
import sys
from pathlib import Path

import numpy as np
from shapely.geometry import Polygon

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import register_plans as rp  # noqa: E402


def _walls(room, T=np.eye(3)):
    out = []
    for i, w in enumerate(room["walls"]):
        a, b = rp.apply(T, np.array([w["from"], w["to"]], float))
        d = b - a
        out.append({"id": f"W{i + 1}", "a": a, "b": b, "mid": (a + b) / 2, "dir": d / (np.linalg.norm(d) + 1e-9),
                    "len": w["length_m"]["value"], "sigma": w["length_m"]["sigma"],
                    "obs": w.get("observed_fraction", 1.0), "ends": w.get("ends_observed", True)})
    return out


def compare(test_dir, ref_dir):
    test = json.loads((Path(test_dir) / "plan.json").read_text())
    ref = json.loads((Path(ref_dir) / "plan.json").read_text())
    T, fit = rp.register(np.load(Path(ref_dir) / "wall_points_plan.npz")["xy"],
                         np.load(Path(test_dir) / "wall_points_plan.npz")["xy"])
    ref_polys = {r["id"]: Polygon(r["polygon"]).buffer(0) for r in ref["rooms"]}
    rooms, walls = [], []
    for r in test["rooms"]:
        p = Polygon(rp.apply(T, np.array(r["polygon"]))).buffer(0)
        iou = {k: q.intersection(p).area / q.union(p).area for k, q in ref_polys.items()}
        k = max(iou, key=iou.get) if iou else None
        rr = next(x for x in ref["rooms"] if x["id"] == k)
        row = {"room": r["id"], "ref_room": k, "iou": round(iou[k], 3),
               "area_m2": r["floor_area_m2"]["value"], "ref_area_m2": rr["floor_area_m2"]["value"],
               "ceiling_m": r["ceiling_height_m"].get("value"), "ref_ceiling_m": rr["ceiling_height_m"].get("value")}
        rooms.append(row)
        if iou[k] < 0.5:
            continue
        rw = _walls(rr)
        for w in _walls(r, T):
            if w["len"] < 0.5 or w["obs"] < 0.3 or not w["ends"]:
                continue
            cand = [x for x in rw if abs(abs(np.dot(x["dir"], w["dir"])) - 1) < 0.02 and x["ends"] and x["obs"] >= 0.3]
            if not cand:
                continue
            m = min(cand, key=lambda x: np.linalg.norm(x["mid"] - w["mid"]))
            if np.linalg.norm(m["mid"] - w["mid"]) > 0.3:
                continue
            diff = (w["len"] - m["len"]) * 100
            walls.append({"room": r["id"], "wall": w["id"], "ref_wall": f"{k}-{m['id']}", "length_m": w["len"],
                          "ref_length_m": m["len"], "diff_cm": round(diff, 2),
                          "inside_ci95": bool(abs(diff) <= 196 * np.hypot(w["sigma"], m["sigma"]))})
    d = np.abs([w["diff_cm"] for w in walls])
    return {"test": str(test_dir), "reference": str(ref_dir), "registration_fit": round(fit, 3),
            "runtime_s": test.get("timing_s", {}).get("wall_clock_s", test.get("timing_s", {}).get("total_s")),
            "duration_s": test["capture"].get("duration_s"), "notes": test["capture"].get("notes", []),
            "rooms": rooms, "walls": walls,
            "summary": {"rooms_found": len(test["rooms"]), "rooms_matched": sum(x["iou"] >= 0.5 for x in rooms),
                        "walls_compared": len(walls),
                        "median_abs_diff_cm": round(float(np.median(d)), 2) if len(d) else None,
                        "within_2cm": round(float(np.mean(d <= 2)), 3) if len(d) else None,
                        "inside_ci95": round(float(np.mean([w["inside_ci95"] for w in walls])), 3) if walls else None}}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pairs", nargs="+", help="test_out:reference_out")
    ap.add_argument("--json", type=Path, default=None)
    a = ap.parse_args()
    res = [compare(*p.split(":")) for p in a.pairs]
    for r in res:
        print(Path(r["test"]).name, json.dumps(r["summary"]), "rooms:",
              [(x["room"], x["ref_room"], x["iou"], x["area_m2"], x["ref_area_m2"]) for x in r["rooms"]])
    if a.json:
        a.json.parent.mkdir(parents=True, exist_ok=True)
        a.json.write_text(json.dumps(res, indent=1))


if __name__ == "__main__":
    main()

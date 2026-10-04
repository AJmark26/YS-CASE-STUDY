"""Ceiling-height repeatability from one capture: measure every room's ceiling twice, from two
disjoint sets of frames, and report the spread against the gate (spread <= 1 cm).

Only one sample capture looks at the ceiling, and it looks at each ceiling during one visit, so
a split into first and second half leaves most rooms with a ceiling in one half only. The frames
are therefore split into alternating 2-second blocks (A B A B ...): both sets see every room's
floor and ceiling, from different viewpoints, with the same drift-corrected poses. That bounds
the measurement noise (which ceiling and floor points are picked up, and how they are fitted),
not drift between separate captures.

    python scripts/ceiling_repeat.py <capture_dir> [out.json] [--run OUT_DIR]

--run OUT_DIR  reuse plan.json and poses_world.npy from a previous `python -m ysplan` run
               instead of running the pipeline again
"""
import argparse
import json
import sys
from pathlib import Path

import numpy as np
from shapely.geometry import Polygon

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from ysplan import fuse, grid, io_stray, measure, pipeline_lidar  # noqa: E402

BLOCK_S = 2.0
GATE_CM = 1.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("capture", type=Path)
    ap.add_argument("out_json", type=Path, nargs="?")
    ap.add_argument("--run", type=Path, default=None)
    a = ap.parse_args()
    cap = io_stray.load(a.capture)
    if a.run and (a.run / "poses_world.npy").exists():
        plan = json.loads((a.run / "plan.json").read_text())
        poses = np.load(a.run / "poses_world.npy").astype(np.float64)
    else:
        plan, _, _ = pipeline_lidar.run(a.capture, log=lambda *x: None)
        poses = pipeline_lidar.run.poses
    yaw, floor_y = plan["alignment"]["yaw_rad"], plan["alignment"]["floor_y_world"]
    t = cap.timestamps - cap.timestamps[0]
    block = (t // BLOCK_S).astype(int) % 2
    ids = np.arange(len(cap))
    meas = {}
    for name, b in (("A", 0), ("B", 1)):
        P, _ = fuse.fuse(cap, poses=poses, frame_ids=ids[(block == b)][::2])
        U, _, _ = grid.align(P, floor_y=floor_y, yaw=yaw)
        meas[name] = {r["id"]: measure.ceiling(U, Polygon(r["polygon"])) for r in plan["rooms"]}
    rows = []
    for r in plan["rooms"]:
        x, y = meas["A"][r["id"]], meas["B"][r["id"]]
        row = {"room": r["id"], "A": x.get("value"), "B": y.get("value"),
               "sigma_A_cm": round(x["sigma"] * 100, 2) if x.get("sigma") else None,
               "sigma_B_cm": round(y["sigma"] * 100, 2) if y.get("sigma") else None}
        if x.get("value") and y.get("value"):
            row["spread_cm"] = round(abs(x["value"] - y["value"]) * 100, 2)
            row["pass"] = row["spread_cm"] <= GATE_CM
            row["inside_ci95"] = bool(row["spread_cm"] <= 196 * np.hypot(x["sigma"], y["sigma"]))
        rows.append(row)
    both = [x for x in rows if "spread_cm" in x]
    rep = {"capture": a.capture.name, "split": f"alternating {BLOCK_S:.0f} s blocks", "gate": f"spread <= {GATE_CM} cm",
           "rooms": rows, "rooms_in_both": len(both),
           "pass_rate": round(float(np.mean([x["pass"] for x in both])), 3) if both else None,
           "median_spread_cm": round(float(np.median([x["spread_cm"] for x in both])), 2) if both else None,
           "max_spread_cm": max((x["spread_cm"] for x in both), default=None),
           "inside_ci95": round(float(np.mean([x["inside_ci95"] for x in both])), 3) if both else None}
    print(json.dumps(rep, indent=1))
    if a.out_json:
        a.out_json.parent.mkdir(parents=True, exist_ok=True)
        a.out_json.write_text(json.dumps(rep, indent=1))


if __name__ == "__main__":
    main()

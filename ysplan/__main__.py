"""One command per capture:  python -m ysplan <capture_dir> [-o out_dir] [--no-drift]"""
import argparse
import json
from pathlib import Path


def detect_tier(p: Path):
    if (p / "odometry.csv").exists():
        return "lidar" if (p / "depth").is_dir() else "video"
    if p.is_file() and p.suffix.lower() in (".mp4", ".mov"):
        return "video"
    if p.is_dir() and any(p.glob("*.mp4")) or any(p.glob("*.MOV")):
        return "video"
    return "photo"


def main(argv=None):
    ap = argparse.ArgumentParser(prog="ysplan", description=__doc__)
    ap.add_argument("capture", type=Path)
    ap.add_argument("-o", "--out", type=Path, default=None)
    ap.add_argument("--tier", choices=["lidar", "video", "photo"], default=None)
    ap.add_argument("--no-drift", action="store_true", help="use raw ARKit poses (ablation)")
    a = ap.parse_args(argv)
    tier = a.tier or detect_tier(a.capture)
    out = a.out or Path("out") / f"{a.capture.stem}_{tier}{'_nodrift' if a.no_drift else ''}"
    out.mkdir(parents=True, exist_ok=True)
    if tier == "lidar":
        from . import pipeline_lidar
        result, cloud, U = pipeline_lidar.run(a.capture, drift=not a.no_drift)
    elif tier == "video":
        from . import pipeline_lidar, pipeline_video
        vc = pipeline_video.build(a.capture)
        result, cloud, U = pipeline_lidar.run(a.capture, drift=not a.no_drift, cap=vc, tier="video")
        result["video"] = vc.stats
    else:
        raise SystemExit(f"tier {tier} not implemented yet")
    (out / "plan.json").write_text(json.dumps(result, indent=1))
    import numpy as np
    np.savez_compressed(out / "wall_points_plan.npz", xy=cloud.astype(np.float32))
    # fused cloud in the plan frame (x, height above floor, y), every 3rd voxel, for diagnostics
    np.savez_compressed(out / "points_plan_frame.npz", U=U[::3].astype(np.float32))
    from . import render
    render.plan(result, out / "plan.png", title=f"{a.capture.name} ({tier} tier)", cloud=cloud)
    print(f"wrote {out / 'plan.json'} and {out / 'plan.png'}")


if __name__ == "__main__":
    main()

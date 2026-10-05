"""One command per capture:  python -m ysplan <capture_dir> [-o out_dir] [--no-drift]"""
import argparse
import json
from pathlib import Path


def detect_tier(p: Path):
    if p.suffix.lower() == ".zip" or (p.is_dir() and not (p / "odometry.csv").exists()
                                      and any(p.rglob("odometry.csv"))):
        from . import io_stray
        p = io_stray.resolve(p)
    if (p / "odometry.csv").exists():
        return "lidar" if (p / "depth").is_dir() else "video"
    if p.is_file() and p.suffix.lower() in (".mp4", ".mov"):
        return "video"
    if p.is_dir() and (any(p.glob("*.mp4")) or any(p.glob("*.MOV")) or any(p.glob("*.mov"))):
        return "video"
    return "photo"


def _save_textures(d, surfs, regions):
    """Unwrapped surface textures (1 cm per pixel), damaged regions outlined in red."""
    import cv2
    import numpy as np
    d.mkdir(parents=True, exist_ok=True)
    hit = {r["surface"] for r in regions}
    for s in surfs:
        tex, ok = s.texture()
        if ok.sum() < 2000:
            continue
        img = cv2.cvtColor(tex, cv2.COLOR_RGB2BGR)
        img[~ok] = (255, 255, 255)
        if s.kind == "wall":
            img = np.ascontiguousarray(img[::-1])               # height up
        if s.sid in hit:
            for r in regions:
                if r["surface"] == s.sid:
                    x0, y0, x1, y1 = (np.array(r["bbox_m"]) / 0.01).astype(int)
                    if s.kind == "wall":
                        y0, y1 = img.shape[0] - y1, img.shape[0] - y0
                    cv2.rectangle(img, (x0, y0), (x1, y1), (0, 0, 255), 2)
        cv2.imwrite(str(d / f"{s.sid}.jpg"), img, [cv2.IMWRITE_JPEG_QUALITY, 85])


def main(argv=None):
    ap = argparse.ArgumentParser(prog="ysplan", description=__doc__)
    ap.add_argument("capture", type=Path)
    ap.add_argument("-o", "--out", type=Path, default=None)
    ap.add_argument("--tier", choices=["lidar", "video", "photo"], default=None)
    ap.add_argument("--no-drift", action="store_true", help="use raw ARKit poses (ablation)")
    ap.add_argument("--no-damage", action="store_true", help="skip damage detection")
    ap.add_argument("--damage-detector", choices=["classical", "learned"], default=None,
                    help="classical: colour statistics of the LiDAR surface textures (LiDAR default); "
                         "learned: Grounding DINO + CLIP + SAM 2 on the frames (video and photo default; "
                         "needs requirements-learned.txt)")
    ap.add_argument("--wet", nargs="*", default=None, help="room ids that are wet rooms (default: small rooms)")
    ap.add_argument("--no-cache", action="store_true", help="recompute learned depth even if cached")
    ap.add_argument("--video-depth", choices=["mapanything", "mono"], default="mapanything",
                    help="video tier depth source (mono = the earlier monocular baseline, for ablation)")
    a = ap.parse_args(argv)
    import time
    t_start = time.time()
    tier = a.tier or detect_tier(a.capture)
    tag = "_mono" if tier == "video" and a.video_depth == "mono" else ""
    out = a.out or Path("out") / f"{a.capture.stem}_{tier}{tag}{'_nodrift' if a.no_drift else ''}"
    out.mkdir(parents=True, exist_ok=True)
    if tier in ("lidar", "video"):
        from . import io_stray
        plain_clip = tier == "video" and (a.capture.is_file() and a.capture.suffix.lower() != ".zip" or
                                          a.capture.is_dir() and not any(a.capture.rglob("odometry.csv")))
        if not plain_clip:                                   # a plain video file has no Stray folder
            a.capture = io_stray.resolve(a.capture)          # accepts the .zip or a parent folder
    if tier == "lidar":
        from . import pipeline_lidar
        result, cloud, U = pipeline_lidar.run(a.capture, drift=not a.no_drift)
        if not a.no_damage:
            from . import damage, io_stray
            cap = io_stray.load(a.capture)
            if a.damage_detector == "learned":
                from . import damage_learned
                frames = damage_learned.video_frames(cap, damage_learned.pick_frames(cap), a.capture / "rgb.mp4")
                result["damage"], surfs = damage_learned.run(cap, pipeline_lidar.run.poses, result, frames, wet=a.wet)
            else:
                result["damage"], surfs = damage.run(cap, pipeline_lidar.run.poses, result, a.capture / "rgb.mp4", wet=a.wet)
            _save_textures(out / "textures", surfs, result["damage"]["regions"])
        import numpy as np
        np.save(out / "poses_world.npy", pipeline_lidar.run.poses.astype(np.float32))   # drift-corrected camera->world
    elif tier == "video":
        from . import measure, pipeline_lidar, pipeline_video
        if a.capture.is_dir() and (a.capture / "odometry.csv").exists():
            vc = pipeline_video.build(a.capture, depth=a.video_depth,
                                      cache=None if a.no_cache else out / "video_depths.npz")
            result, cloud, U = pipeline_lidar.run(a.capture, drift=not a.no_drift, cap=vc, tier="video",
                                                  tier_scale=measure.TIER_SCALE["video"])
            if not a.no_damage and a.damage_detector != "classical":     # the classical detector needs LiDAR depth
                from . import damage_learned
                try:
                    frames = damage_learned.video_frames(vc, sorted(vc.depths)[::2], a.capture / "rgb.mp4")
                    result["damage"], surfs = damage_learned.run(vc, pipeline_lidar.run.poses, result, frames,
                                                                 wall_band=damage_learned.VIDEO_WALL_BAND, wet=a.wet)
                    _save_textures(out / "textures", surfs, result["damage"]["regions"])
                except (ImportError, OSError) as e:                     # models not installed or downloadable
                    print(f"[damage] learned detector unavailable, no damage regions: {e}")
        else:                                   # plain clip from any camera app: no poses
            clip = a.capture if a.capture.is_file() else sorted(
                [*a.capture.glob("*.mp4"), *a.capture.glob("*.MOV"), *a.capture.glob("*.mov")])[0]
            vc, stats = pipeline_video.build_posefree(clip)
            vc.stats = stats
            result, cloud, U = pipeline_lidar.run(clip, drift=False, cap=vc, tier="video", tier_scale=measure.TIER_SCALE["video"])
        result["video"] = vc.stats
    else:
        from . import pipeline_photo
        result, cloud, U = pipeline_photo.run(a.capture, cache=None if a.no_cache else out / "photo_recon")
        if not a.no_damage and a.damage_detector != "classical" and pipeline_photo.run.capture is not None:
            from . import damage_learned
            pc = pipeline_photo.run.capture
            plan = dict(result, alignment=pipeline_photo.run.alignment)   # the photos' world is the plan frame
            try:
                frames = damage_learned.photo_frames(pc, pipeline_photo.run.paths)
                dmg, surfs = damage_learned.run(pc, pc.T_wc, plan, frames, wall_band=damage_learned.VIDEO_WALL_BAND,
                                                wet=a.wet)
                result["damage"] = dmg
                _save_textures(out / "textures", surfs, dmg["regions"])
            except (ImportError, OSError) as e:                         # models not installed or downloadable
                print(f"[damage] learned detector unavailable, no damage regions: {e}")
    result.setdefault("timing_s", {})["wall_clock_s"] = round(time.time() - t_start, 1)   # load to plan, damage included
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

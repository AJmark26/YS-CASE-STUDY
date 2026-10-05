"""LiDAR tier: Stray Scanner capture -> stitched, dimensioned plan."""
import time

import numpy as np

from . import drift as drift_mod
from . import fuse, grid, io_stray, layout, measure, openings, rooms


# Predicted depth puts points where no ceiling was seen: the video run of the floor-only capture
# reported a 2.07 m ceiling in a room whose LiDAR never saw above 1.86 m. No sample capture has
# both a ceiling sweep and a video-tier run, so these tiers report no heights until one does.
UNCHECKED_CEILING = "ceiling heights from predicted depth are not checked against LiDAR yet, so none is reported"


def run(capture_dir, drift=True, step=4, log=print, cap=None, tier="lidar", high_rays=True, tier_scale=1.0,
        segment=None):
    """Geometry pipeline shared by all tiers. `cap` defaults to the LiDAR capture; the video tier
    passes a VideoCapture whose depth maps come from predicted depth. `tier_scale` multiplies
    every wall and ceiling sigma (1 for LiDAR; set per tier from its benchmark against LiDAR).
    `segment`, if given, replaces the watershed room split: segment(G, free, walls) -> label
    grid (the photo tier knows which photos belong to which room)."""
    t0 = time.time()
    timing = {}
    cap = io_stray.load(capture_dir) if cap is None else cap
    duration = float(cap.timestamps[-1] - cap.timestamps[0])
    log(f"[{tier}] {len(cap)} frames, {duration:.0f} s")
    for note in getattr(cap, "notes", []):
        log(f"[{tier}] note: {note}")
    if drift:
        poses, valid, drift_rep = drift_mod.correct(cap, log=log)
    else:
        poses, valid = cap.T_wc, np.ones(len(cap), bool)
        drift_rep = {"enabled": False}
    drift_rep["enabled"] = bool(drift)
    timing["drift_s"] = time.time() - t0
    frames = np.where(valid)[0]
    if hasattr(cap, "depths"):                      # video tier: only keyframes carry depth
        frames = np.array([f for f in frames if f in cap.depths])
        step = 1
    P, _ = fuse.fuse(cap, poses=poses, frame_ids=frames[::step], min_hits=getattr(cap, "min_hits", 2 if tier != "lidar" else 3))
    cam_y = poses[frames, 1, 3] if getattr(cap, "floor_from_cameras", True) else None
    U, floor_y, yaw = grid.align(P, cam_y=cam_y)
    floor_seen = grid.align.floor_observed
    if not floor_seen:
        log(f"[{tier}] floor not seen: heights are relative to a guessed floor and are not reported")
    G = grid.build(U, floor_y, yaw, wall_span=0.4)
    timing["fuse_s"] = time.time() - t0 - timing["drift_s"]
    carve_ids = frames[::5] if tier == "lidar" else frames
    c_low = rooms.carve(G, cap, poses, carve_ids)
    c_mid = rooms.carve(G, cap, poses, carve_ids, end_h=(0.9, 1.9))
    # rays ending on the ceiling also prove the floor below them is open (captures that look up a
    # lot see little floor); they stay inside the room unless they pass through a high window
    c_high = rooms.carve(G, cap, poses, carve_ids, end_h=(2.0, 3.6)) if high_rays else 0
    # the walked path uses every tracked frame, not only the keyframes that carry depth (video)
    t = poses[np.where(valid)[0], :3, 3]
    c, s = np.cos(yaw), np.sin(yaw)
    traj = np.stack([t[:, 0] * c + t[:, 2] * s, -t[:, 0] * s + t[:, 2] * c], 1)
    free, walls = rooms.free_space(G, G.to_cell(traj), c_low + c_high, wall_count=8 if tier == "lidar" else "p70")
    labels = segment(G, free, walls) if segment else rooms.segment(free & ~rooms.close_doors(walls))
    polys, _, _ = layout.room_polygons(U, G, labels)
    # A region the camera never entered was only seen through a doorway: report it, but keep it
    # out of the plan (its far walls are unobserved, so its dimensions would be guesses).
    from shapely import contains_xy
    unvisited = []
    for k in list(polys):
        inside = contains_xy(polys[k], traj[:, 0], traj[:, 1]).sum()
        if inside < getattr(cap, "min_inside", 30):     # trajectory samples; sparse captures set fewer
            unvisited.append({"area_m2": round(polys[k].area, 2),
                              "polygon": [[round(x, 3), round(y, 3)] for x, y in list(polys[k].exterior.coords)[:-1]]})
            del polys[k]
    polys = {i: p for i, p in enumerate(sorted(polys.values(), key=lambda g: -g.area), 1)}
    log(f"[{tier}] {len(polys)} rooms ({len(unvisited)} regions seen only through doorways)")
    sigma_drift = drift_rep.get("loop_misalignment_cm_after", 0.0) / 100.0 if drift else 0.0
    out_rooms = []
    for k, poly in polys.items():
        rid = f"R{k}"
        yaw_r = measure.room_yaw(U, poly)          # residual drift can leave a room slightly turned
        wl = measure.walls(U, poly, sigma_drift=sigma_drift, tier_scale=tier_scale, yaw=yaw_r)
        lp = poly.representative_point()
        out_rooms.append({
            "id": rid,
            "polygon": [[round(x, 4), round(y, 4)] for x, y in list(poly.exterior.coords)[:-1]],
            "label_point": [lp.x, lp.y],
            "walls": [dict(w, id=f"{rid}-W{i + 1}") for i, w in enumerate(wl)],
            "floor_area_m2": measure.area(poly, wl),
            "ceiling_height_m": measure.ceiling(U, poly, tier_scale=tier_scale, floor_observed=floor_seen)
            if getattr(cap, "report_ceilings", True) else measure.withheld(UNCHECKED_CEILING),
            "bbox_m": [round(poly.bounds[2] - poly.bounds[0], 3), round(poly.bounds[3] - poly.bounds[1], 3)],
            "wall_observed_fraction": round(float(np.mean([w["observed_fraction"] for w in wl])), 3),
            "squared_by_deg": round(float(np.degrees(yaw_r)), 2),
        })
    ops = openings.detect_lines(U, G, polys, c_low, c_mid, sigma_drift=sigma_drift)
    for i, o in enumerate(ops, 1):
        o["id"] = f"{o['type'][0].upper()}{i}"
        o["width_m"] = measure._val(o["width_m"], o.pop("sigma_m") * tier_scale)
    adjacency = _adjacency(polys, ops)
    timing["layout_s"] = time.time() - t0 - timing["drift_s"] - timing["fuse_s"]
    timing["total_s"] = time.time() - t0
    result = {
        "schema_version": "1.0",
        "tier": tier,
        "interval_scale": float(tier_scale),
        "units": "m",
        "frame": "gravity-aligned plan; x,y = Manhattan-aligned floor coordinates, origin arbitrary",
        "alignment": {"yaw_rad": float(yaw), "floor_y_world": float(floor_y), "floor_observed": bool(floor_seen),
                      "note": "plan x = X cos(yaw) + Z sin(yaw), plan y = -X sin(yaw) + Z cos(yaw) in capture world"},
        "capture": {"path": str(capture_dir), "frames": int(len(cap)), "frames_used": int(valid.sum()),
                    "duration_s": round(duration, 2), "notes": list(getattr(cap, "notes", []))},
        "rooms": out_rooms,
        "openings": ops,
        "adjacency": adjacency,
        "unvisited_regions": unvisited,
        "footprint_m2": measure._val(sum(r["floor_area_m2"]["value"] for r in out_rooms),
                                     float(np.sqrt(sum(r["floor_area_m2"]["sigma"] ** 2 for r in out_rooms)))),
        "drift": drift_rep,
        "timing_s": {k: round(v, 1) for k, v in timing.items()},
    }
    if tier == "video":                              # the photo tier converts after stitching (pipeline_photo.widen)
        measure.areas_as_lower_bounds(result)
    if not floor_seen:
        result["capture"]["notes"].append(
            "floor not seen: no heights are reported, and the room outlines rest on walls seen high up "
            "and on rays that ended on the ceiling, so a partition seen only near the ceiling may not "
            "separate two rooms (walk-in rehearsal t4)")
    m = (U[:, 1] > 0.3) & (U[:, 1] < 1.9)
    cloud = U[m][:, [0, 2]][::7]
    run.poses = poses                                # drift-corrected, for later stages (damage)
    return result, cloud, U


def _adjacency(polys, ops, touch=0.35):
    """Rooms are adjacent if their footprints come within a wall thickness of each other;
    'via' lists the openings that connect them."""
    adj = []
    ids = sorted(polys)
    for a_i, a in enumerate(ids):
        for b in ids[a_i + 1:]:
            if polys[a].distance(polys[b]) > touch:
                continue
            ra, rb = f"R{a}", f"R{b}"
            via = [o["id"] for o in ops if set(o["rooms"]) == {ra, rb}]
            shared = polys[a].buffer(touch / 2).intersection(polys[b].buffer(touch / 2))
            adj.append({"rooms": [ra, rb], "via": via, "shared_boundary_m": round(shared.length / 2, 2)})
    return adj

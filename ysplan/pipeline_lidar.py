"""LiDAR tier: Stray Scanner capture -> stitched, dimensioned plan."""
import time

import numpy as np

from . import drift as drift_mod
from . import fuse, grid, io_stray, layout, measure, openings, rooms


def run(capture_dir, drift=True, step=4, log=print, cap=None, tier="lidar", high_rays=True):
    """Geometry pipeline shared by all tiers. `cap` defaults to the LiDAR capture; the video tier
    passes a VideoCapture whose depth maps come from scaled monocular depth."""
    t0 = time.time()
    timing = {}
    cap = io_stray.load(capture_dir) if cap is None else cap
    duration = float(cap.timestamps[-1] - cap.timestamps[0])
    log(f"[{tier}] {len(cap)} frames, {duration:.0f} s")
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
    P, _ = fuse.fuse(cap, poses=poses, frame_ids=frames[::step], min_hits=2 if tier != "lidar" else 3)
    U, floor_y, yaw = grid.align(P)
    G = grid.build(U, floor_y, yaw, wall_span=0.4)
    timing["fuse_s"] = time.time() - t0 - timing["drift_s"]
    carve_ids = frames[::5] if tier == "lidar" else frames
    c_low = rooms.carve(G, cap, poses, carve_ids)
    c_mid = rooms.carve(G, cap, poses, carve_ids, end_h=(0.9, 1.9))
    # rays ending on the ceiling also prove the floor below them is open (captures that look up a
    # lot see little floor); they stay inside the room unless they pass through a high window
    c_high = rooms.carve(G, cap, poses, carve_ids, end_h=(2.0, 3.6)) if high_rays else 0
    t = poses[frames, :3, 3]
    c, s = np.cos(yaw), np.sin(yaw)
    traj = np.stack([t[:, 0] * c + t[:, 2] * s, -t[:, 0] * s + t[:, 2] * c], 1)
    free, walls = rooms.free_space(G, G.to_cell(traj), c_low + c_high, wall_count=8 if tier == "lidar" else "p70")
    labels = rooms.segment(free & ~rooms.close_doors(walls))
    polys, _, _ = layout.room_polygons(U, G, labels)
    # A region the camera never entered was only seen through a doorway: report it, but keep it
    # out of the plan (its far walls are unobserved, so its dimensions would be guesses).
    from shapely import contains_xy
    unvisited = []
    for k in list(polys):
        inside = contains_xy(polys[k], traj[:, 0], traj[:, 1]).sum()
        if inside < 30:
            unvisited.append({"area_m2": round(polys[k].area, 2),
                              "polygon": [[round(x, 3), round(y, 3)] for x, y in list(polys[k].exterior.coords)[:-1]]})
            del polys[k]
    polys = {i: p for i, p in enumerate(sorted(polys.values(), key=lambda g: -g.area), 1)}
    log(f"[{tier}] {len(polys)} rooms ({len(unvisited)} regions seen only through doorways)")
    sigma_drift = drift_rep.get("loop_misalignment_cm_after", 0.0) / 100.0 if drift else 0.0
    out_rooms, per_room_open = [], {}
    for k, poly in polys.items():
        rid = f"R{k}"
        wl = measure.walls(U, poly, sigma_drift=sigma_drift)
        lp = poly.representative_point()
        out_rooms.append({
            "id": rid,
            "polygon": [[round(x, 4), round(y, 4)] for x, y in list(poly.exterior.coords)[:-1]],
            "label_point": [lp.x, lp.y],
            "walls": [dict(w, id=f"{rid}-W{i + 1}") for i, w in enumerate(wl)],
            "floor_area_m2": measure.area(poly, wl),
            "ceiling_height_m": measure.ceiling(U, poly),
            "bbox_m": [round(poly.bounds[2] - poly.bounds[0], 3), round(poly.bounds[3] - poly.bounds[1], 3)],
            "wall_observed_fraction": round(float(np.mean([w["observed_fraction"] for w in wl])), 3),
        })
        per_room_open[rid] = openings.detect(U, G, poly, c_low, c_mid)
    ops = openings.merge(per_room_open)
    for i, o in enumerate(ops, 1):
        o["id"] = f"{o['type'][0].upper()}{i}"
        s_open = np.hypot(0.006, np.std(o["widths_seen"]) if len(o["widths_seen"]) > 1 else 0.0)
        o["width_m"] = measure._val(o["width_m"], s_open if o["jambs_found"] == 2 else 0.05)
        o.pop("widths_seen", None)
    adjacency = _adjacency(polys, ops)
    timing["layout_s"] = time.time() - t0 - timing["drift_s"] - timing["fuse_s"]
    timing["total_s"] = time.time() - t0
    result = {
        "schema_version": "1.0",
        "tier": tier,
        "units": "m",
        "frame": "gravity-aligned plan; x,y = Manhattan-aligned floor coordinates, origin arbitrary",
        "alignment": {"yaw_rad": float(yaw), "floor_y_world": float(floor_y),
                      "note": "plan x = X cos(yaw) + Z sin(yaw), plan y = -X sin(yaw) + Z cos(yaw) in capture world"},
        "capture": {"path": str(capture_dir), "frames": int(len(cap)), "frames_used": int(valid.sum()),
                    "duration_s": round(duration, 2)},
        "rooms": out_rooms,
        "openings": ops,
        "adjacency": adjacency,
        "unvisited_regions": unvisited,
        "footprint_m2": measure._val(sum(r["floor_area_m2"]["value"] for r in out_rooms),
                                     float(np.sqrt(sum(r["floor_area_m2"]["sigma"] ** 2 for r in out_rooms)))),
        "drift": drift_rep,
        "timing_s": {k: round(v, 1) for k, v in timing.items()},
    }
    m = (U[:, 1] > 0.3) & (U[:, 1] < 1.9)
    cloud = U[m][:, [0, 2]][::7]
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

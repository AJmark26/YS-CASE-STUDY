"""Photo tier: one folder of 2 to 8 stills per room, no depth, no poses -> stitched plan.

Per room:
1. MapAnything (recon.py) gives every photo a pose and a dense depth map in one metric frame,
   using EXIF focal lengths as intrinsics.
2. Gravity: the photos are upright (EXIF orientation), so each camera's x axis is horizontal and
   'up' is the direction orthogonal to all of them. A floor plane fitted among the lowest points
   then refines it.
3. The result is wrapped as a capture (PhotoCapture) and goes through the same geometry modules
   as the LiDAR tier: fusion, plan grids, ray carving, room polygon, wall faces, openings.
4. Intervals add a relative scale error to every length, because metric scale is the dominant
   error of learned depth (see SCALE_SIGMA).
Rooms are then stitched into one plan (stitch.py).
"""
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from shapely.geometry import Polygon

from . import fuse, grid, layout, measure, openings, recon, rooms, stitch

SCALE_SIGMA = 0.05      # relative 1-sigma scale error of the learned depth; calibrated on the benchmark
MAX_DEPTH = 6.0
DOOR_SKIP_M = 1.2       # doorway stills are shot from up to 1 m outside the room they show


@dataclass
class PhotoCapture:
    """Posed depth images with the interface of io_stray.StrayCapture (world y up, metres)."""
    root: Path
    T_wc: np.ndarray
    K: np.ndarray
    depths: dict = field(default_factory=dict)
    masks: dict = field(default_factory=dict)
    names: list = field(default_factory=list)
    min_inside: int = 1        # one camera position inside a room proves it was entered
    min_hits: int = 1          # most surfaces are seen by one or two photos only
    # floor = densest low slab, not the camera-height finder: a few stills see well under 2 m2 of
    # floor, so that finder would guess the floor and count furniture sides as walls, which cut
    # the apartment's rooms 21-58% short (benchmark_report.md, photo tier)
    floor_from_cameras: bool = False
    report_ceilings: bool = False   # see pipeline_lidar.UNCHECKED_CEILING

    @property
    def frames(self):
        return np.arange(len(self.T_wc))

    @property
    def timestamps(self):
        return np.arange(len(self.T_wc), dtype=float)

    def __len__(self):
        return len(self.T_wc)

    def depth(self, i):
        d = self.depths[i]
        m = self.masks.get(i, np.ones(d.shape, bool)) & (d > 0.2) & (d < MAX_DEPTH)
        return d, np.where(m, 2, 0).astype(np.uint8)

    def K_depth(self, i, shape):
        return self.K[i].copy()


def gravity_from_cameras(R_wc):
    """Upright photos have almost no roll, so camera x axes are horizontal and 'up' is the
    direction most orthogonal to all of them. Sign: the image's up (-y) points up on average.
    With one photo, or all photos facing one way, image up itself is used."""
    X = R_wc[:, :, 0]
    up_img = -R_wc[:, :, 1].mean(0)
    if len(X) >= 2:
        _, sv, vt = np.linalg.svd(X, full_matrices=False)
        up = vt[-1] if sv[1] > 0.2 * sv[0] else up_img
    else:
        up = up_img
    up = up / np.linalg.norm(up)
    return up if up @ up_img > 0 else -up


def floor_plane(P, up, tol=0.02, max_angle_deg=12, iters=400, rng=None, return_count=False):
    """RANSAC plane among the lowest 25% of points with its normal within max_angle of `up`.
    Returns (normal, offset) or (up, None) if no floor was found (plus the inlier count)."""
    rng = rng or np.random.default_rng(0)
    h = P @ up
    low = P[h < np.percentile(h, 25)]
    if len(low) < 200:
        return (up, None, 0) if return_count else (up, None)
    cosmax = np.cos(np.radians(max_angle_deg))
    best, best_n = None, 0
    for _ in range(iters):
        a, b, c = low[rng.choice(len(low), 3, replace=False)]
        n = np.cross(b - a, c - a)
        L = np.linalg.norm(n)
        if L < 1e-9:
            continue
        n = n / L * np.sign(n @ up)
        if n @ up < cosmax:
            continue
        cnt = int(np.sum(np.abs((low - a) @ n) < tol))
        if cnt > best_n:
            best, best_n = (n, a), cnt
    if best is None or best_n < 100:
        return (up, None, best_n) if return_count else (up, None)
    n, a = best
    inl = low[np.abs((low - a) @ n) < tol]
    _, _, vt = np.linalg.svd(inl - inl.mean(0), full_matrices=False)
    n = vt[-1] * np.sign(vt[-1] @ up)
    return (n, float(inl.mean(0) @ n), best_n) if return_count else (n, float(inl.mean(0) @ n))


def rotation_to_y(up):
    """Rotation R with R @ up = (0, 1, 0)."""
    y = np.array([0.0, 1.0, 0.0])
    v = np.cross(up, y)
    s, c = np.linalg.norm(v), float(up @ y)
    if s < 1e-9:
        return np.eye(3) if c > 0 else np.diag([1.0, -1.0, -1.0])
    vx = np.array([[0, -v[2], v[1]], [v[2], 0, -v[0]], [-v[1], v[0], 0]])
    return np.eye(3) + vx + vx @ vx * ((1 - c) / s ** 2)


def build_capture(folder, log=print):
    """Reconstruct one room folder and return a gravity-aligned PhotoCapture plus diagnostics."""
    paths = recon.list_images(folder)
    t0 = time.time()
    views = recon.reconstruct(paths, log=log)
    T = np.array([v["T_wc"] for v in views])
    pc = PhotoCapture(Path(folder), T, np.array([v["K"] for v in views]),
                      {i: v["depth"] for i, v in enumerate(views)},
                      {i: v["mask"] for i, v in enumerate(views)}, [p.name for p in paths])
    # metric scale: per photo, median ratio of MoGe-2 depth to MapAnything depth; median over photos
    import cv2
    ratios = []
    for p, v in zip(paths, views):
        dm, mm = recon.single_view_depth(p)
        h, w = v["depth"].shape
        dm = cv2.resize(np.where(mm, dm, 0).astype(np.float32), (w, h), interpolation=cv2.INTER_NEAREST)
        ok = v["mask"] & (dm > 0.2) & (v["depth"] > 0.2) & (dm < MAX_DEPTH)
        if ok.sum() > 500:
            ratios.append(float(np.median(dm[ok] / v["depth"][ok])))
    scale = float(np.median(ratios)) if ratios else 1.0
    T[:, :3, 3] *= scale
    pc.T_wc = T
    pc.depths = {i: d * scale for i, d in pc.depths.items()}
    up = gravity_from_cameras(T[:, :3, :3])
    P, _ = fuse.fuse(pc, step=1, max_depth=MAX_DEPTH, min_hits=1)
    up_floor, _ = floor_plane(P, up)
    tilt = float(np.degrees(np.arccos(np.clip(up @ up_floor, -1, 1))))
    R = rotation_to_y(up_floor)
    A = np.eye(4)
    A[:3, :3] = R
    pc.T_wc = A @ T
    diag = {"photos": [p.name for p in paths], "recon_s": round(time.time() - t0, 1),
            "scale_vs_multiview": round(scale, 4), "scale_per_photo": [round(r, 3) for r in ratios],
            "gravity_floor_vs_cameras_deg": round(tilt, 2), "floor_found": up_floor is not up}
    return pc, diag


def room_plan(pc, rid="R1", log=print):
    """Plan of the one room a folder covers, in its own gravity-aligned frame."""
    ids = np.arange(len(pc))
    P, _ = fuse.fuse(pc, frame_ids=ids, max_depth=MAX_DEPTH, min_hits=1)
    U, floor_y, yaw = grid.align(P)
    G = grid.build(U, floor_y, yaw, wall_span=0.4)
    c_low = rooms.carve(G, pc, pc.T_wc, ids, stride=4)
    c_mid = rooms.carve(G, pc, pc.T_wc, ids, stride=4, end_h=(0.9, 1.9))
    c_high = rooms.carve(G, pc, pc.T_wc, ids, stride=4, end_h=(2.0, 3.6))
    t = pc.T_wc[:, :3, 3]
    c, s = np.cos(yaw), np.sin(yaw)
    cams = np.stack([t[:, 0] * c + t[:, 2] * s, -t[:, 0] * s + t[:, 2] * c], 1)
    free, walls = rooms.free_space(G, G.to_cell(cams), c_low + c_high, min_rays=1, wall_count="p70")
    labels = rooms.segment(free & ~rooms.close_doors(walls))
    if labels.max() == 0:
        return None, U, G
    # the room is the segment holding most camera positions (doorway shots stand outside it)
    ij = G.to_cell(cams).clip(0, np.array(labels.shape) - 1)
    hit = labels[ij[:, 0], ij[:, 1]]
    hit = hit[hit > 0]
    keep = np.bincount(hit).argmax() if len(hit) else np.bincount(labels[labels > 0]).argmax()
    polys, _, _ = layout.room_polygons(U, G, np.where(labels == keep, 1, 0))
    if not polys:
        return None, U, G
    poly = polys[1]
    wl = measure.walls(U, poly)
    for w in wl:                                  # learned depth: add the relative scale error
        L, s0 = w["length_m"]["value"], w["length_m"]["sigma"]
        w["length_m"] = measure._val(L, float(np.hypot(s0, SCALE_SIGMA * L)))
    a = measure.area(poly, wl)
    a = measure._val(a["value"], float(np.hypot(a["sigma"], 2 * SCALE_SIGMA * a["value"])))
    ceil = measure.ceiling(U, poly, min_pts=100)
    if ceil.get("status") == "measured":
        v = ceil["value"]
        ceil.update(measure._val(v, float(np.hypot(ceil["sigma"], SCALE_SIGMA * v))))
    lp = poly.representative_point()
    room = {"id": rid, "polygon": [[round(x, 4), round(y, 4)] for x, y in list(poly.exterior.coords)[:-1]],
            "label_point": [lp.x, lp.y],
            "walls": [dict(w, id=f"{rid}-W{i + 1}") for i, w in enumerate(wl)],
            "floor_area_m2": a, "ceiling_height_m": ceil,
            "bbox_m": [round(poly.bounds[2] - poly.bounds[0], 3), round(poly.bounds[3] - poly.bounds[1], 3)],
            "wall_observed_fraction": round(float(np.mean([w["observed_fraction"] for w in wl])), 3),
            "alignment": {"yaw_rad": float(yaw), "floor_y_world": float(floor_y)},
            "cameras_plan": np.round(cams, 3).tolist()}
    room["openings"] = openings.detect(U, G, poly, c_low, c_mid)
    for o in room["openings"]:
        o["width_m"] = measure._val(o["width_m"], float(np.hypot(0.03, SCALE_SIGMA * o["width_m"])))
        o.pop("widths_seen", None)
    return room, U, G


def build_joint_capture(capture_dir, log=print):
    """All photos of all rooms in one MapAnything pass: one shared frame, so the rooms come out
    already stitched wherever doorway photos tie them together. Returns (PhotoCapture, folder
    name per photo, diagnostics)."""
    capture_dir = Path(capture_dir)
    folders = sorted(p for p in capture_dir.iterdir() if p.is_dir() and recon.list_images(p))
    paths, owner = [], []
    for f in folders:
        imgs = recon.list_images(f)
        paths += imgs
        owner += [f.name] * len(imgs)
    log(f"[photo] {len(paths)} photos in {len(folders)} room folders: one joint reconstruction")
    pc, diag = _capture_from(paths, capture_dir, log=log)
    return pc, owner, diag


def _capture_from(paths, root, log=print):
    import cv2
    t0 = time.time()
    views = recon.reconstruct(paths, log=log)
    T = np.array([v["T_wc"] for v in views])
    depths = {i: v["depth"] for i, v in enumerate(views)}
    # metric scale: per photo, median ratio of MoGe-2 depth to MapAnything depth; median over photos
    ratios = []
    for p, v in zip(paths, views):
        dm, mm = recon.single_view_depth(p)
        h, w = v["depth"].shape
        dm = cv2.resize(np.where(mm, dm, 0).astype(np.float32), (w, h), interpolation=cv2.INTER_NEAREST)
        ok = v["mask"] & (dm > 0.2) & (v["depth"] > 0.2) & (dm < MAX_DEPTH)
        if ok.sum() > 500:
            ratios.append(float(np.median(dm[ok] / v["depth"][ok])))
    scale = float(np.median(ratios)) if ratios else 1.0
    T[:, :3, 3] *= scale
    pc = PhotoCapture(Path(root), T, np.array([v["K"] for v in views]),
                      {i: d * scale for i, d in depths.items()},
                      {i: v["mask"] for i, v in enumerate(views)}, [p.name for p in paths])
    up = gravity_from_cameras(T[:, :3, :3])
    P, _ = fuse.fuse(pc, step=1, max_depth=MAX_DEPTH, min_hits=1)
    up_floor, _ = floor_plane(P, up)
    A = np.eye(4)
    A[:3, :3] = rotation_to_y(up_floor)
    pc.T_wc = A @ T
    diag = {"photos": [str(Path(p).relative_to(root)) for p in paths], "recon_s": round(time.time() - t0, 1),
            "scale_vs_multiview": round(scale, 4), "scale_per_photo": [round(r, 3) for r in ratios],
            "scale_spread_rel": round(float(np.std(ratios) / np.mean(ratios)), 4) if ratios else None,
            "gravity_floor_vs_cameras_deg": round(float(np.degrees(np.arccos(np.clip(up @ up_floor, -1, 1)))), 2)}
    return pc, diag


def widen(result, scale_sigma=SCALE_SIGMA):
    """Add the relative scale error of learned depth to every length, area and height."""
    for r in result["rooms"]:
        for w in r["walls"]:
            L, s0 = w["length_m"]["value"], w["length_m"]["sigma"]
            w["length_m"] = measure._val(L, float(np.hypot(s0, scale_sigma * L)))
        a = r["floor_area_m2"]
        r["floor_area_m2"] = measure._val(a["value"], float(np.hypot(a["sigma"], 2 * scale_sigma * a["value"])))
        c = r["ceiling_height_m"]
        if c.get("status") == "measured":
            c.update(measure._val(c["value"], float(np.hypot(c["sigma"], scale_sigma * c["value"]))))
    for o in result["openings"]:
        W = o["width_m"]
        o["width_m"] = measure._val(W["value"], float(np.hypot(np.hypot(W["sigma"], 0.03), scale_sigma * W["value"])))
    fp = sum(r["floor_area_m2"]["value"] for r in result["rooms"])
    result["footprint_m2"] = measure._val(fp, float(np.sqrt(sum(r["floor_area_m2"]["sigma"] ** 2 for r in result["rooms"]))))
    return result


def folder_segment(pc, owner, min_cells=200):
    """Room split for the photo tier: each room folder claims the floor its own photos' rays
    pass over. A doorway still (door-from-*) is taken from the neighbouring room, so its rays
    first cross that room's floor: it carves only beyond `DOOR_SKIP_M` from its camera.
    A cell goes to the folder with the most rays through it; rays are normalised per folder so a
    room shot with 8 photos does not swallow one shot with 3."""
    names = sorted(set(owner))
    seen_state = {}

    def seg(G, free, walls):
        import cv2
        from scipy import ndimage as ndi
        votes = []
        H, W = G.wall.shape
        ii, jj = np.ogrid[:H, :W]
        c, s = np.cos(G.yaw), np.sin(G.yaw)

        def carve(ids):
            # a handful of photos sees little floor, so every ray that ends below 1.9 m carves
            # (in the LiDAR tier only rays ending below sill height do, to keep windows closed)
            return rooms.carve(G, pc, pc.T_wc, ids, end_h=(-0.05, 1.9)) + rooms.carve(G, pc, pc.T_wc, ids, end_h=(2.0, 3.6))

        for nme in names:
            ids = [i for i, o in enumerate(owner) if o == nme]
            door = [i for i in ids if str(pc.names[i]).startswith("door-from-")]
            v = carve([i for i in ids if i not in door]).astype(float)
            for i in door:
                X, Z = pc.T_wc[i, 0, 3], pc.T_wc[i, 2, 3]
                ci, cj = G.to_cell([X * c + Z * s, -X * s + Z * c])
                near = (ii - ci) ** 2 + (jj - cj) ** 2 < (DOOR_SKIP_M / grid.RES) ** 2
                v += np.where(near, 0, carve([i]))
            votes.append(v / max(1, len(ids)))
        votes = np.stack(votes)
        wd = cv2.dilate(walls.astype(np.uint8), np.ones((3, 3), np.uint8)).astype(bool)
        seen = cv2.morphologyEx((votes.max(0) > 0).astype(np.uint8), cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))
        seen = ndi.binary_fill_holes(seen) & ~wd
        lab = np.where(seen, votes.argmax(0) + 1, 0)
        out = np.zeros_like(lab)
        k = 0
        for i in range(1, len(names) + 1):            # keep each folder's largest blob
            cc, n = ndi.label(lab == i)
            if n == 0:
                continue
            sizes = ndi.sum(np.ones_like(cc), cc, range(1, n + 1))
            if sizes.max() < min_cells:
                continue
            k += 1
            out[cc == (int(np.argmax(sizes)) + 1)] = k
        seen_state.update(G=G, labels=out)
        return out
    seg.state = seen_state
    return seg


def mask_rectangle(G, mask, lo=5, hi=95):
    """Fallback outline when too few wall lines were seen for the rectilinear polygon: the
    axis-aligned rectangle spanning the 5th-95th percentile of the room's carved floor."""
    from shapely.geometry import box
    from .grid import RES
    ij = np.argwhere(mask)
    if len(ij) < 50:
        return None
    (i0, j0), (i1, j1) = np.percentile(ij, lo, axis=0), np.percentile(ij, hi, axis=0)
    u0, v0 = G.origin + np.array([i0, j0]) * RES
    u1, v1 = G.origin + np.array([i1 + 1, j1 + 1]) * RES
    return box(u0, v0, u1, v1)


def fallback_room(U, seg, rid):
    """Room record from the carved-floor rectangle, measured like any other room."""
    from . import pipeline_lidar
    st = getattr(seg, "state", {})
    if "labels" not in st or st["labels"].max() == 0:
        return None
    lab = st["labels"]
    k = np.bincount(lab[lab > 0]).argmax()
    poly = mask_rectangle(st["G"], lab == k)
    if poly is None:
        return None
    wl = measure.walls(U, poly)
    lp = poly.representative_point()
    return {"id": rid, "polygon": [[round(x, 4), round(y, 4)] for x, y in list(poly.exterior.coords)[:-1]],
            "label_point": [lp.x, lp.y], "walls": [dict(w, id=f"{rid}-W{i + 1}") for i, w in enumerate(wl)],
            "floor_area_m2": measure.area(poly, wl), "ceiling_height_m": measure.withheld(pipeline_lidar.UNCHECKED_CEILING),
            "bbox_m": [round(poly.bounds[2] - poly.bounds[0], 3), round(poly.bounds[3] - poly.bounds[1], 3)],
            "wall_observed_fraction": round(float(np.mean([w["observed_fraction"] for w in wl])), 3),
            "outline": "carved_floor_rectangle"}


def save_capture(path, pc, owner, diag):
    import json
    n = len(pc)
    np.savez_compressed(path, T_wc=pc.T_wc, K=pc.K, owner=np.array(owner), names=np.array(pc.names),
                        diag=json.dumps(diag), **{f"d{i}": pc.depths[i].astype(np.float16) for i in range(n)},
                        **{f"m{i}": pc.masks[i] for i in range(n)})


def load_capture(path, root):
    import json
    z = np.load(path)
    n = len(z["T_wc"])
    pc = PhotoCapture(Path(root), z["T_wc"], z["K"], {i: z[f"d{i}"].astype(np.float32) for i in range(n)},
                      {i: z[f"m{i}"] for i in range(n)}, list(z["names"]))
    return pc, list(z["owner"]), json.loads(str(z["diag"]))


def plan_xy(v, yaw):
    """World (X, *, Z) vectors -> plan (x, y): x = X cos + Z sin, y = -X sin + Z cos."""
    c, s = np.cos(yaw), np.sin(yaw)
    v = np.atleast_2d(v)
    return np.stack([v[:, 0] * c + v[:, 2] * s, -v[:, 0] * s + v[:, 2] * c], 1)


def door_views(pc, yaw, floor_y):
    """Doorway stills (`door-from-<room>_*.jpg`) in this folder, in the room's plan frame:
    [(room stood in, camera xy, viewing direction xy, distance to the door wall or None)].
    The door wall is the nearest wall face across the viewing axis in the still's own depth:
    the wall around the doorway, seen from the room the photographer stands in."""
    cam, fwd = plan_xy(pc.T_wc[:, :3, 3], yaw), plan_xy(pc.T_wc[:, :3, 2], yaw)
    out = []
    for i, nm in enumerate(pc.names):
        if not str(nm).startswith("door-from-"):
            continue
        n = stitch.snap_dir(fwd[i])
        P = fuse.backproject(pc, i, max_depth=MAX_DEPTH, pose=pc.T_wc[i], stride=2)
        h = P[:, 1] - floor_y
        q = plan_xy(P[(h > 0.2) & (h < 2.2)], yaw) - cam[i]
        ahead, side = q @ n, np.abs(q @ np.array([-n[1], n[0]]))
        out.append((str(nm)[len("door-from-"):].rsplit("_", 1)[0], cam[i], fwd[i],
                    stitch.first_wall(ahead[side < 1.5])))
    return out


def run(capture_dir, log=print, cache=None, gap=1.0):
    """capture_dir holds one sub-folder of photos per room. Each room is reconstructed on its own
    (all photos of a whole home in one MapAnything pass need more than 15 GB of RAM on CPU), then
    the shared LiDAR-tier geometry runs on it (no drift step: there is no trajectory).
    Rooms are then stitched into one plan from doorway stills (stitch.py). Rooms without a
    two-sided doorway link are laid out beside the plan, `gap` metres apart, and listed in
    photo.unstitched_rooms.
    `cache`: folder of per-room .npz reconstructions from an earlier run (skips the networks)."""
    from . import pipeline_lidar
    capture_dir = Path(capture_dir)
    folders = sorted(p for p in capture_dir.iterdir() if p.is_dir() and recon.list_images(p))
    own, diags, views, cams = {}, {}, {}, {}
    t0 = time.time()
    for f in folders:
        paths = recon.list_images(f)
        npz = Path(cache) / f"{f.name}.npz" if cache is not None else None
        if npz is not None and npz.exists():
            pc, owner, diag = load_capture(npz, f)
        else:
            log(f"[photo] {f.name}: {len(paths)} photos")
            pc, diag = _capture_from(paths, f, log=log)
            owner = [f.name] * len(paths)
            if npz is not None:
                npz.parent.mkdir(parents=True, exist_ok=True)
                save_capture(npz, pc, owner, diag)
        diags[f.name] = diag
        seg = folder_segment(pc, owner)
        res, cloud, U = pipeline_lidar.run(f, drift=False, cap=pc, tier="photo", log=lambda *a: None, segment=seg)
        if not res["rooms"]:
            fb = fallback_room(U, seg, "R1")
            if fb is None:
                log(f"[photo] {f.name}: no room outline found")
                continue
            res["rooms"] = [fb]
        r = max(res["rooms"], key=lambda q: q["floor_area_m2"]["value"])
        r["openings"] = [o for o in res["openings"] if r_id_in(o, res["rooms"], r)]
        r["id"] = f.name
        for w in r["walls"]:
            w["id"] = f"{f.name}-W{w['id'].split('-W')[-1]}"
        r["yaw_rad_in_own_frame"] = res["alignment"]["yaw_rad"]
        own[f.name] = (r, cloud, U)
        views[f.name] = door_views(pc, res["alignment"]["yaw_rad"], res["alignment"]["floor_y_world"])
        cams[f.name] = ([str(n) for n in pc.names], plan_xy(pc.T_wc[:, :3, 3], res["alignment"]["yaw_rad"]))
        log(f"[photo] {f.name}: bbox {r['bbox_m']} m, area {r['floor_area_m2']['value']:.2f} m2, "
            f"scale x{diag['scale_vs_multiview']} (spread {diag['scale_spread_rel']}), "
            f"{len(views[f.name])} doorway photos")
    polys = {n: Polygon(r["polygon"]) for n, (r, _, _) in own.items()}
    doors = {n: [(((o["from"] + o["to"]) / 2, o["line"]) if o["axis"] == "u" else (o["line"], (o["from"] + o["to"]) / 2),
                  o["axis"]) for o in r["openings"] if o["type"] in ("door", "opening")] for n, (r, _, _) in own.items()}
    pose, links, unplaced, one_sided = stitch.place(polys, views, doors)
    # rooms with no two-sided link go to the right of the stitched plan, side by side
    x0 = max((stitch.apply_poly(polys[n], *pose[n]).bounds[2] for n in pose), default=-gap) + gap
    for n in sorted(unplaced, key=lambda n: -polys[n].area):
        b = polys[n].bounds
        pose[n] = (0, np.array([x0 - b[0], -b[1]]))
        x0 += b[2] - b[0] + gap
    linked = {n for l in links for n in l["rooms"]}
    out_rooms, clouds, Us, ops, cameras = [], [], [], [], {}
    for n in sorted(own):
        r, cloud, U = own[n]
        k, t = pose[n]
        r = _move_room(r, k, t)
        r["stitched"] = n in linked
        r["pose_in_plan"] = {"rotation_deg": 90 * k, "offset_m": [round(float(t[0]), 4), round(float(t[1]), 4)]}
        for o in r.pop("openings"):
            o = _move_opening(o, k, t)
            o["rooms"] = [n]
            ops.append(o)
        out_rooms.append(r)
        names, cxy = cams[n]
        cameras[n] = [{"photo": nm, "xy": [round(float(x), 3), round(float(y), 3)]}
                      for nm, (x, y) in zip(names, stitch.apply_xy(cxy, k, t))]
        clouds.append(stitch.apply_xy(cloud, k, t))
        Uxy = stitch.apply_xy(U[:, [0, 2]], k, t)
        Us.append(np.stack([Uxy[:, 0], U[:, 1], Uxy[:, 1]], 1))
    for i, o in enumerate(ops, 1):
        o["id"] = f"{o['type'][0].upper()}{i}"
    unstitched = sorted(n for n in own if n not in linked)
    if links:
        log(f"[photo] stitched {len(linked)} of {len(own)} rooms via doorways "
            + ", ".join("-".join(l["rooms"]) for l in links))
    if unstitched:
        log(f"[photo] not stitched (no doorway photo from both sides): {', '.join(unstitched)}")
    result = {"schema_version": "1.0", "tier": "photo", "units": "m",
              "frame": "gravity-aligned plan; rooms joined at doorways seen from both sides "
                       "(see photo.stitched); unlinked rooms are laid out to the right",
              "capture": {"path": str(capture_dir), "rooms": len(folders)},
              "rooms": out_rooms, "openings": ops,
              "adjacency": [{"rooms": l["rooms"], "via": []} for l in links],   # joined by doorway photos (photo.links)
              "photo": {"per_room": diags, "scale_sigma_rel": SCALE_SIGMA,
                        "stitched": bool(links) and not unstitched, "links": links,
                        "unstitched_rooms": unstitched, "one_sided_doorways": one_sided,
                        "cameras": cameras},
              "timing_s": {"total_s": round(time.time() - t0, 1)}}
    cloud = np.concatenate(clouds) if clouds else np.zeros((0, 2))
    U = np.concatenate(Us) if Us else np.zeros((0, 3))
    return widen(result), cloud, U


def r_id_in(o, rooms_, r):
    return o.get("rooms", [None])[0] == r["id"] or len(rooms_) == 1


def _move_room(r, k, t):
    """Rotate a room by 90 deg * k about the origin, then shift by t."""
    from .stitch import apply_xy
    mv = lambda p: [round(float(v), 4) for v in apply_xy(p, k, t)]
    r = dict(r)
    r["polygon"] = [mv(p) for p in r["polygon"]]
    r["label_point"] = mv(r["label_point"])
    r["walls"] = [dict(w, **{"from": mv(w["from"]), "to": mv(w["to"])}) for w in r["walls"]]
    if k % 2:
        r["bbox_m"] = r["bbox_m"][::-1]
    return r


def _move_opening(o, k, t):
    """Same move for an opening: axis "u" is a wall along plan x at y = line, "v" along y."""
    from .stitch import apply_xy
    o = dict(o)
    ends = [[o["from"], o["line"]], [o["to"], o["line"]]] if o["axis"] == "u" else \
           [[o["line"], o["from"]], [o["line"], o["to"]]]
    a, b = apply_xy(ends, k, t)
    if abs(b[0] - a[0]) >= abs(b[1] - a[1]):
        o["axis"], o["line"] = "u", float(a[1])
        o["from"], o["to"] = sorted([float(a[0]), float(b[0])])
    else:
        o["axis"], o["line"] = "v", float(a[0])
        o["from"], o["to"] = sorted([float(a[1]), float(b[1])])
    return o

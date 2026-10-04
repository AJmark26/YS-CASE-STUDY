"""Video tier: RGB walkthrough + ARKit poses/intrinsics (no depth).

Every `kf_step`-th frame becomes a keyframe with a metric depth map. Two depth sources:
- "mapanything" (default): keyframes go to MapAnything (recon.py) in windows of 8, together
  with their ARKit poses and intrinsics, so each window's depth is multi-view consistent and
  metric by construction. On single_room this is within 6% of LiDAR per keyframe.
- "mono": monocular depth (Depth Anything V2 small) scaled by points triangulated from the
  known poses (see mono.py). Fast, but its walls are too smeared to recover rooms; kept for the
  ablation.
The result quacks like a StrayCapture, so the LiDAR-tier geometry pipeline runs on it unchanged.
Keyframes whose depth fails a check are dropped and counted in the report.
"""
import time
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

from . import io_stray, mono

W, H = 960, 720       # working resolution for tracking
DW, DH = 256, 192     # depth map resolution (same as LiDAR so downstream code is shared)


@dataclass
class VideoCapture:
    root: object
    frames: np.ndarray
    timestamps: np.ndarray
    T_wc: np.ndarray
    K_rgb: np.ndarray
    depths: dict = field(default_factory=dict)
    stats: dict = field(default_factory=dict)

    def __len__(self):
        return len(self.frames)

    def depth(self, i):
        d = self.depths.get(i)
        if d is None:
            return np.zeros((DH, DW), np.float32), np.zeros((DH, DW), np.uint8)
        return d, np.where((d > 0.2) & (d < 4.5), 2, 0).astype(np.uint8)

    def K_depth(self, i, shape):
        h, w = shape
        K = self.K_rgb[i].copy()
        K[0] *= w / io_stray.RGB_W
        K[1] *= h / io_stray.RGB_H
        return K


def shifted_poses(T_wc, offset):
    """Camera->world poses resampled at fractional index k+offset (slerp + lerp)."""
    from scipy.spatial.transform import Rotation as R, Slerp
    n = len(T_wc)
    x = np.clip(np.arange(n) + offset, 0, n - 1)
    out = np.tile(np.eye(4), (n, 1, 1))
    out[:, :3, :3] = Slerp(np.arange(n), R.from_matrix(T_wc[:, :3, :3]))(x).as_matrix()
    i = np.floor(x).astype(int)
    j = np.minimum(i + 1, n - 1)
    f = (x - i)[:, None]
    out[:, :3, 3] = (1 - f) * T_wc[i, :3, 3] + f * T_wc[j, :3, 3]
    return out


def estimate_time_offset(cap, fr, kfs, chain_step, offsets=np.arange(-2, 3.01, 0.5), n_sample=20):
    """Video frames and ARKit poses are not perfectly synchronised (on the sample captures the
    video lags by ~1 frame). Pick the pose offset that maximises the number of tracks that
    triangulate consistently: a self-calibration that needs no ground truth."""
    sample = kfs[:: max(1, len(kfs) // n_sample)]
    tr = {}
    for k in sample:
        ids = [i for i in range(k, k + 31, chain_step) if i in fr]
        grays = [cv2.cvtColor(fr[i], cv2.COLOR_BGR2GRAY) for i in ids]
        tr[k] = [(ids[L - 1], *mono.track_chain(grays[:L])) for L in (4, 8, len(ids))]
    scores = []
    for o in offsets:
        T = shifted_poses(cap.T_wc, o)
        cnt = 0
        for k in sample:
            K = cap.K_rgb[k].copy()
            K[:2] *= W / io_stray.RGB_W
            for last, p0, p1 in tr[k]:
                cnt += len(mono.triangulate(p0, p1, K, T[k], T[last])[1])
        scores.append(cnt)
    return float(offsets[int(np.argmax(scores))]), dict(zip([float(o) for o in offsets], scores))


def mapanything_depths(cap, T, kfs, video, window=8, log=print):
    """Dense metric depth per keyframe from MapAnything, given ARKit poses and intrinsics.
    Keyframes are processed in consecutive windows; each prediction is splatted into a DWxDH
    depth map with the known intrinsics (z-buffer), so it lines up with the pose exactly."""
    import torch
    from mapanything.utils.image import preprocess_inputs
    from . import recon
    fr = mono.read_frames(video, kfs)
    kfs = [k for k in kfs if k in fr]
    depths, ratios = {}, []
    for w0 in range(0, len(kfs), window):
        ids = kfs[w0:w0 + window]
        if len(ids) < 2:                          # a lone last keyframe joins the previous window
            ids = kfs[max(0, w0 - window + 1):w0 + 1]
        views = [{"img": torch.from_numpy(cv2.cvtColor(fr[i], cv2.COLOR_BGR2RGB)),
                  "intrinsics": torch.from_numpy(cap.K_rgb[i].astype(np.float32)),
                  "camera_poses": torch.from_numpy(T[i].astype(np.float32)),
                  "is_metric_scale": torch.tensor([True])} for i in ids]
        t0 = time.time()
        with torch.no_grad():
            preds = recon.model().infer(preprocess_inputs(views), memory_efficient_inference=False,
                                        use_amp=False, apply_mask=True, mask_edges=True)
        for i, p in zip(ids, preds):
            X = p["pts3d_cam"][0].numpy().reshape(-1, 3)
            m = p["mask"][0, ..., 0].numpy().reshape(-1).astype(bool) & (X[:, 2] > 0.1)
            X = X[m]
            K = cap.K_rgb[i].copy()
            K[0] *= DW / io_stray.RGB_W
            K[1] *= DH / io_stray.RGB_H
            u = np.round(X[:, 0] / X[:, 2] * K[0, 0] + K[0, 2]).astype(int)
            v = np.round(X[:, 1] / X[:, 2] * K[1, 1] + K[1, 2]).astype(int)
            ok = (u >= 0) & (u < DW) & (v >= 0) & (v < DH)
            d = np.full(DH * DW, np.inf, np.float32)
            np.minimum.at(d, v[ok] * DW + u[ok], X[ok, 2].astype(np.float32))
            d[~np.isfinite(d)] = 0
            depths[i] = d.reshape(DH, DW)
            ratios.append(float(np.linalg.norm(p["camera_poses"][0, :3, 3].numpy() - T[i][:3, 3])))
        log(f"[video] MapAnything window {w0 // window + 1}/{-(-len(kfs) // window)}: "
            f"{len(ids)} keyframes in {time.time() - t0:.0f} s")
    return depths, {"pose_echo_err_cm_median": round(100 * float(np.median(ratios)), 2) if ratios else None}


def build(capture_dir, kf_step=None, chain_step=3, max_res=0.10, min_pts=20, log=print, depth="mapanything",
          cache=None):
    """Keyframes every kf_step frames: 40 (1.5 Hz at 60 fps) for MapAnything, 10 for mono.
    `cache`: .npz holding keyframe depth maps from an earlier run of the same capture and
    settings; when present the network is skipped (the brief allows cached model outputs as long
    as the live path also runs, which it does when the file is absent)."""
    kf_step = kf_step or (40 if depth == "mapanything" else 10)
    cap = io_stray.load(capture_dir)
    n = len(cap)
    kfs = list(range(0, n - 31, kf_step))
    want = set()
    for k in kfs:
        want |= set(range(k, k + 31, chain_step))
    log(f"[video] decoding {len(want)} frames, {len(kfs)} keyframes")
    fr = mono.read_frames(cap.root / "rgb.mp4", want, (W, H))
    offset, scores = estimate_time_offset(cap, fr, kfs, chain_step)
    log(f"[video] video-to-pose time offset {offset:+.1f} frames (track inliers {scores})")
    T = shifted_poses(cap.T_wc, offset)
    cap.T_wc = T
    vc = VideoCapture(cap.root, cap.frames, cap.timestamps, T, cap.K_rgb)
    if depth == "mapanything":
        t0 = time.time()
        if cache is not None and Path(cache).exists():
            z = np.load(cache)
            vc.depths = {int(k[1:]): z[k].astype(np.float32) for k in z.files if k.startswith("d")}
            extra = {"depth_cache": str(cache)}
            log(f"[video] loaded {len(vc.depths)} keyframe depth maps from {cache}")
        else:
            vc.depths, extra = mapanything_depths(cap, T, kfs, cap.root / "rgb.mp4", log=log)
            if cache is not None:
                np.savez_compressed(cache, **{f"d{k}": v.astype(np.float16) for k, v in vc.depths.items()})
        vc.stats = {"depth_source": "mapanything", "time_offset_frames": offset, "keyframes": len(kfs),
                    "keyframes_used": len(vc.depths), "kf_step": kf_step,
                    "depth_s": round(time.time() - t0, 1), **extra}
        log(f"[video] {len(vc.depths)}/{len(kfs)} keyframes with MapAnything depth")
        return vc
    ok_n, res_all, npts = 0, [], []
    disps, sparse, Ks = [], [], []
    for k in kfs:
        ids = [i for i in range(k, k + 31, chain_step) if i in fr]
        grays = [cv2.cvtColor(fr[i], cv2.COLOR_BGR2GRAY) for i in ids]
        K = cap.K_rgb[k].copy()
        K[:2] *= W / io_stray.RGB_W
        best = (np.zeros((0, 2)), np.zeros(0))
        for L in (4, 6, 8, len(ids)):
            p0, p1 = mono.track_chain(grays[:L])
            uv, dep = mono.triangulate(p0, p1, K, cap.T_wc[ids[0]], cap.T_wc[ids[L - 1]])
            if len(dep) > len(best[1]):
                best = (uv, dep)
        uv, dep = best
        disp = mono.disparity(fr[k], (DH, DW))
        disps.append(disp)
        sparse.append((uv * [DW / W, DH / H], dep))
        Kd = cap.K_rgb[k].copy()
        Kd[0] *= DW / io_stray.RGB_W
        Kd[1] *= DH / io_stray.RGB_H
        Ks.append(Kd)
        npts.append(len(dep))
    for i, k in enumerate(kfs):
        uv, dep = sparse[i]
        if len(dep) < min_pts:
            continue
        dense, res, _ = mono.fit_metric(disps[i], uv, dep, (1, 1))
        if dense is None or res > max_res:
            continue
        res_all.append(res)
        vc.depths[k] = dense
        ok_n += 1
    vc.stats = {"depth_source": "mono", "time_offset_frames": offset, "keyframes": len(kfs), "keyframes_used": ok_n,
                "median_fit_residual": float(np.median(res_all)) if res_all else None,
                "median_triangulated_points": float(np.median(npts)) if npts else 0}
    log(f"[video] {ok_n}/{len(kfs)} keyframes kept, median fit residual {vc.stats['median_fit_residual']}")
    return vc


def build_posefree(video, kf_hz=1.5, window=8, overlap=2, moge_per_window=2, log=print):
    """Video tier without ARKit poses (any phone's camera app): keyframes go through MapAnything
    in overlapping windows with no pose input. Each window is made metric on its own with MoGe-2
    (median depth ratio on `moge_per_window` of its keyframes), so scale errors do not compound
    along the walk, then chained rigidly onto the previous window through a shared keyframe.
    The floor plane sets gravity. Returns a pipeline_photo.PhotoCapture over the keyframes plus
    stats. No loop closure: drift along a long walk stays in the result.

    EXPERIMENTAL: on the single_room clip the chained windows disagree in yaw and the walls
    smear, so no room is recovered yet (a single shared keyframe is too weak a link). Results
    are flagged `experimental` in plan.json."""
    import gc
    import resource
    import torch
    from mapanything.utils.image import preprocess_inputs
    from . import pipeline_photo as pp
    from . import recon
    video = Path(video)
    vcap = cv2.VideoCapture(str(video))
    fps = vcap.get(cv2.CAP_PROP_FPS) or 30.0
    n = int(vcap.get(cv2.CAP_PROP_FRAME_COUNT))
    w0_, h0_ = vcap.get(cv2.CAP_PROP_FRAME_WIDTH), vcap.get(cv2.CAP_PROP_FRAME_HEIGHT)
    vcap.release()
    f = 960 / max(w0_, h0_)                        # MapAnything works at 518 px anyway
    size = (int(round(w0_ * f)), int(round(h0_ * f)))
    step = max(1, int(round(fps / kf_hz)))
    fr = mono.read_frames(video, range(0, n, step), size)
    kfs = sorted(fr)
    log(f"[video] no poses: {len(kfs)} keyframes at {kf_hz} Hz, windows of {window}")
    T_glob, D_glob, M_glob, K_glob, scales = {}, {}, {}, {}, []
    t0 = time.time()
    starts = list(range(0, max(1, len(kfs) - overlap), window - overlap))
    for wi, w0 in enumerate(starts):
        ids = kfs[w0:w0 + window]
        if len(ids) < 2:
            break
        views = [{"img": torch.from_numpy(cv2.cvtColor(fr[i], cv2.COLOR_BGR2RGB))} for i in ids]
        with torch.no_grad():
            preds = recon.model().infer(preprocess_inputs(views), memory_efficient_inference=True,
                                        use_amp=False, apply_mask=True, mask_edges=True)
        P = {i: p["camera_poses"][0].numpy().astype(np.float64) for i, p in zip(ids, preds)}
        D = {i: p["depth_z"][0, ..., 0].numpy().copy() for i, p in zip(ids, preds)}
        M = {i: p["mask"][0, ..., 0].numpy().astype(bool) for i, p in zip(ids, preds)}
        K = {i: p["intrinsics"][0].numpy().astype(np.float64) for i, p in zip(ids, preds)}
        del preds, views
        gc.collect()
        new = [i for i in ids if i not in T_glob] or ids
        ratios = []
        for i in new[:: max(1, len(new) // moge_per_window)][:moge_per_window]:
            dm, mm = recon.single_view_depth_array(cv2.cvtColor(fr[i], cv2.COLOR_BGR2RGB), K[i], D[i].shape)
            ok = M[i] & mm & (dm > 0.2) & (D[i] > 0.05)
            if ok.sum() > 500:
                ratios.append(float(np.median(dm[ok] / D[i][ok])))
        s = float(np.median(ratios)) if ratios else (scales[-1] if scales else 1.0)
        scales.append(s)
        shared = [i for i in ids if i in T_glob]
        A = T_glob[shared[0]] @ np.linalg.inv(_scaled(P[shared[0]], s)) if shared else np.eye(4)
        for i in ids:
            if i in T_glob:
                continue
            T_glob[i] = A @ _scaled(P[i], s)
            D_glob[i], M_glob[i], K_glob[i] = D[i] * s, M[i], K[i]
        log(f"[video] window {wi + 1}/{len(starts)}: metric scale {s:.3f}, "
            f"peak memory {resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1e6:.1f} GB")
    order = sorted(T_glob)
    T = np.array([T_glob[i] for i in order])
    pc = pp.PhotoCapture(video.parent, T, np.array([K_glob[i] for i in order]),
                         {j: D_glob[i] for j, i in enumerate(order)},
                         {j: M_glob[i] for j, i in enumerate(order)}, [f"frame_{i:06d}" for i in order],
                         min_inside=max(1, int(2 * kf_hz)))
    # gravity: the floor is a large plane 0.5-2.2 m below the cameras. The clip may be stored
    # sideways (no rotation metadata), so each image axis is tried as 'up'.
    from . import fuse
    Pts, _ = fuse.fuse(pc, step=1, max_depth=pp.MAX_DEPTH, min_hits=1)
    best = None
    for ax, sgn in ((1, -1), (1, 1), (0, -1), (0, 1)):
        cand = sgn * T[:, :3, ax].mean(0)
        cand /= np.linalg.norm(cand)
        nrm, off, cnt = pp.floor_plane(Pts, cand, max_angle_deg=25, return_count=True)
        if off is None:
            continue
        below = T[:, :3, 3] @ nrm - off
        if np.median(below) < 0.5 or np.median(below) > 2.2:
            continue
        if best is None or cnt > best[1]:
            best = (nrm, cnt)
    up_floor = best[0] if best else -T[0, :3, 1]
    G = np.eye(4)
    G[:3, :3] = pp.rotation_to_y(up_floor)
    pc.T_wc = G @ pc.T_wc
    log("[video] WARNING: pose-free video is experimental; check the plan before trusting it")
    stats = {"depth_source": "mapanything_posefree", "experimental": True, "keyframes": len(order), "kf_hz": kf_hz,
             "window_scales": [round(x, 3) for x in scales],
             "scale_spread_rel": round(float(np.std(scales) / np.mean(scales)), 4) if scales else None,
             "gravity_found": best is not None, "depth_s": round(time.time() - t0, 1)}
    return pc, stats


def _scaled(T, s):
    out = T.copy()
    out[:3, 3] *= s
    return out

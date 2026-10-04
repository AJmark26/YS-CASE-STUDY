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

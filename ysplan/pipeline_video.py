"""Video tier: RGB walkthrough + ARKit poses/intrinsics (no depth).

Every `kf_step`-th frame becomes a keyframe with a metric depth map made from monocular depth
(Depth Anything V2) scaled by points triangulated from the known poses (see mono.py). The
result quacks like a StrayCapture, so the LiDAR-tier geometry pipeline runs on it unchanged.
Keyframes whose scale fit is poor are dropped and counted in the report.
"""
from dataclasses import dataclass, field

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


def build(capture_dir, kf_step=10, chain_step=3, max_res=0.10, min_pts=20, log=print):
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
    vc.stats = {"time_offset_frames": offset, "keyframes": len(kfs), "keyframes_used": ok_n,
                "median_fit_residual": float(np.median(res_all)) if res_all else None,
                "median_triangulated_points": float(np.median(npts)) if npts else 0}
    log(f"[video] {ok_n}/{len(kfs)} keyframes kept, median fit residual {vc.stats['median_fit_residual']}")
    return vc

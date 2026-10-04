"""Depth fusion: posed LiDAR depth frames -> voxel-averaged world point cloud.

Each confident depth pixel is back-projected with its frame pose; points are binned into
a voxel grid and averaged. Voxels seen by fewer than `min_hits` pixels are dropped, which
removes flying pixels at depth edges and most glass/mirror ghosts (seen from one angle only).
"""
import numpy as np


def backproject(cap, i, max_depth=4.0, min_conf=2, pose=None, stride=1):
    d, c = cap.depth(i)
    d, c = d[::stride, ::stride], c[::stride, ::stride]
    K = cap.K_depth(i, cap.depth(i)[0].shape)
    K = K.copy(); K[:2] /= stride
    v, u = np.nonzero((c >= min_conf) & (d > 0.1) & (d < max_depth))
    z = d[v, u]
    pc = np.stack([(u - K[0, 2]) * z / K[0, 0], (v - K[1, 2]) * z / K[1, 1], z], 1)
    T = cap.T_wc[i] if pose is None else pose
    return pc @ T[:3, :3].T + T[:3, 3]


_OFF = 1 << 20   # voxel index offset; 21 bits per axis covers +-20 km at 2 cm


def _reduce(flat, P, n=None):
    """Sum points and counts per voxel key."""
    uk, inv = np.unique(flat, return_inverse=True)
    w = np.ones(len(flat)) if n is None else n
    sums = np.stack([np.bincount(inv, weights=P[:, a], minlength=len(uk)) for a in range(3)], 1)
    return uk, sums, np.bincount(inv, weights=w, minlength=len(uk))


def fuse(cap, step=3, voxel=0.02, max_depth=4.0, min_conf=2, min_hits=3, poses=None, frame_ids=None,
         batch=150):
    """Return (points (M,3), hits (M,)) voxel centroids of the fused capture.

    Frames are reduced to per-voxel sums in batches, so memory scales with the number of
    occupied voxels rather than the number of depth pixels (long captures have > 100 M pixels).
    """
    idx = list(range(0, len(cap), step) if frame_ids is None else frame_ids)
    acc = None
    for b in range(0, len(idx), batch):
        Ps = [backproject(cap, i, max_depth, min_conf, None if poses is None else poses[i])
              for i in idx[b:b + batch]]
        Ps = [x for x in Ps if len(x)]
        if not Ps:
            continue
        P = np.concatenate(Ps)
        k = np.floor(P / voxel).astype(np.int64) + _OFF
        flat = (k[:, 0] << 42) | (k[:, 1] << 21) | k[:, 2]
        part = _reduce(flat, P)
        if acc is not None:
            part = _reduce(np.concatenate([acc[0], part[0]]), np.concatenate([acc[1], part[1]]),
                           np.concatenate([acc[2], part[2]]))
        acc = part
    if acc is None:
        return np.zeros((0, 3)), np.zeros(0, int)
    _, sums, n = acc
    keep = n >= min_hits
    return sums[keep] / n[keep, None], n[keep].astype(int)

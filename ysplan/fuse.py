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


def fuse(cap, step=3, voxel=0.02, max_depth=4.0, min_conf=2, min_hits=3, poses=None, frame_ids=None):
    """Return (points (M,3), hits (M,)) voxel centroids of the fused capture."""
    idx = range(0, len(cap), step) if frame_ids is None else frame_ids
    keys, sums, cnt = [], [], []
    for i in idx:
        P = backproject(cap, i, max_depth, min_conf, None if poses is None else poses[i])
        k = np.floor(P / voxel).astype(np.int64)
        keys.append(k); sums.append(P)
    K = np.concatenate(keys); P = np.concatenate(sums)
    uk, inv, n = np.unique(K, axis=0, return_inverse=True, return_counts=True)
    inv = inv.ravel()
    acc = np.zeros((len(uk), 3))
    np.add.at(acc, inv, P)
    pts = acc / n[:, None]
    keep = n >= min_hits
    return pts[keep], n[keep]

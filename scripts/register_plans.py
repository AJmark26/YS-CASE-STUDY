"""Register two plans of the same space (different captures) in the plan frame.

Both plans are gravity-aligned and Manhattan-aligned, so they differ by a multiple of 90 deg,
a small residual yaw and a translation. We rasterise wall points, try the 4 rotations with
phase correlation for translation, then refine (yaw, t) with 2D point-to-point ICP.
Returns the 3x3 transform mapping plan B coordinates into plan A.
"""
import numpy as np
from scipy.spatial import cKDTree


def _raster(xy, origin, shape, res):
    ij = ((xy - origin) / res).astype(int)
    m = (ij[:, 0] >= 0) & (ij[:, 0] < shape[0]) & (ij[:, 1] >= 0) & (ij[:, 1] < shape[1])
    img = np.zeros(shape, np.float32)
    np.add.at(img, (ij[m, 0], ij[m, 1]), 1)
    return np.minimum(img, 5)


def _T(theta, t):
    c, s = np.cos(theta), np.sin(theta)
    return np.array([[c, -s, t[0]], [s, c, t[1]], [0, 0, 1]])


def apply(T, xy):
    return xy @ T[:2, :2].T + T[:2, 2]


def icp2d(A, B, T, iters=40, max_d=0.15):
    tree = cKDTree(A)
    for _ in range(iters):
        Bt = apply(T, B)
        d, idx = tree.query(Bt, distance_upper_bound=max_d)
        ok = np.isfinite(d)
        if ok.sum() < 50:
            break
        p, q = Bt[ok], A[idx[ok]]
        mp, mq = p.mean(0), q.mean(0)
        U, _, Vt = np.linalg.svd((p - mp).T @ (q - mq))
        R = (U @ Vt).T
        if np.linalg.det(R) < 0:
            Vt[-1] *= -1
            R = (U @ Vt).T
        dT = np.eye(3)
        dT[:2, :2] = R
        dT[:2, 2] = mq - R @ mp
        T = dT @ T
    d, _ = tree.query(apply(T, B), distance_upper_bound=0.05)
    return T, float(np.isfinite(d).mean())


def register(A, B, step=0.5, n_sub=1500, seed=0):
    """Exhaustive start grid (4 rotations x translations every `step` m) + ICP; best inlier fit."""
    rng = np.random.default_rng(seed)
    Bs = B[rng.choice(len(B), min(n_sub, len(B)), replace=False)]
    As = A[rng.choice(len(A), min(20000, len(A)), replace=False)]
    best = None
    for k in range(4):
        th = k * np.pi / 2
        Bk = apply(_T(th, (0, 0)), Bs)
        c = Bk.mean(0)
        xs = np.arange(As[:, 0].min(), As[:, 0].max(), step)
        ys = np.arange(As[:, 1].min(), As[:, 1].max(), step)
        for x in xs:
            for y in ys:
                T0 = _T(th, (x - c[0], y - c[1]))
                T, fit = icp2d(As, Bs, T0, iters=12)
                if best is None or fit > best[1]:
                    best = (T, fit)
    T, fit = icp2d(A, B, best[0], iters=40, max_d=0.08)
    return T, fit


def register_phase(A, B, res=0.05):
    import cv2
    best = None
    for k in range(4):
        th = k * np.pi / 2
        Bk = apply(_T(th, (0, 0)), B)
        lo = np.minimum(A.min(0), Bk.min(0)) - 1
        hi = np.maximum(A.max(0), Bk.max(0)) + 1
        shape = tuple(((hi - lo) / res).astype(int) + 1)
        ia, ib = _raster(A, lo, shape, res), _raster(Bk, lo, shape, res)
        (dy, dx), resp = cv2.phaseCorrelate(ib, ia)     # shift that maps B onto A (rows=x, cols=y)
        T0 = _T(th, (dy * res, dx * res))
        T, fit = icp2d(A, B, T0)
        if best is None or fit > best[1]:
            best = (T, fit)
    return best

"""Single-photo metric geometry for the photo tier (no poses, no depth).

1. Gravity: vertical vanishing point from line segments (LSD + RANSAC on interpretation planes).
   Walls, door frames and furniture edges give plenty of vertical lines indoors.
2. Metric depth: monocular disparity is affine in inverse depth. Pixels on the floor have a known
   metric depth once gravity and the camera height above the floor are known, so the floor
   anchors the affine fit. Camera height comes from the capture protocol (phone at chest height)
   with its spread carried into the intervals.
"""
import cv2
import numpy as np


def vertical_vp(gray, K, iters=400, tol_deg=1.0, min_len=40, rng=None):
    """Return the unit 'up' direction in camera coordinates (OpenCV: y down), or None."""
    rng = rng or np.random.default_rng(0)
    segs = cv2.createLineSegmentDetector().detect(gray)[0]
    if segs is None:
        return None, 0
    segs = segs.reshape(-1, 4)
    L = np.hypot(segs[:, 2] - segs[:, 0], segs[:, 3] - segs[:, 1])
    ang = np.degrees(np.arctan2(np.abs(segs[:, 2] - segs[:, 0]), np.abs(segs[:, 3] - segs[:, 1])))
    keep = (L > min_len) & (ang < 30)           # roughly vertical in an upright photo
    segs, L = segs[keep], L[keep]
    if len(segs) < 5:
        return None, 0
    Ki = np.linalg.inv(K)
    a = (Ki @ np.c_[segs[:, :2], np.ones(len(segs))].T).T
    b = (Ki @ np.c_[segs[:, 2:], np.ones(len(segs))].T).T
    n = np.cross(a, b)
    n /= np.linalg.norm(n, axis=1, keepdims=True)
    tol = np.sin(np.radians(tol_deg))
    best, best_in = None, None
    for _ in range(iters):
        i, j = rng.choice(len(n), 2, replace=False)
        d = np.cross(n[i], n[j])
        if np.linalg.norm(d) < 1e-6:
            continue
        d /= np.linalg.norm(d)
        inl = np.abs(n @ d) < tol
        score = L[inl].sum()
        if best is None or score > best:
            best, best_in = score, inl
    if best_in is None or best_in.sum() < 5:
        return None, 0
    _, _, vt = np.linalg.svd(n[best_in] * L[best_in, None])
    d = vt[-1]
    if d[1] > 0:                                   # 'up' points to negative image y
        d = -d
    return d, int(best_in.sum())


def floor_anchored_depth(disp, K, up, cam_h, floor_rows=0.35, iters=4):
    """Metric depth from relative disparity using the floor plane as anchor.

    Floor pixels are searched in the lower `floor_rows` of the image: for each, the floor model
    gives depth z_f = cam_h / (ray . down) * ray_z. Fit 1/z = a*disp + b robustly over pixels whose
    model ray hits the floor in front of the camera; furniture is rejected by re-weighting.
    Returns (depth (H,W) float32, n_floor_pixels, residual).
    """
    H, W = disp.shape
    v, u = np.mgrid[0:H, 0:W]
    rays = np.stack([(u - K[0, 2]) / K[0, 0], (v - K[1, 2]) / K[1, 1], np.ones_like(u, float)], -1)
    down = -up
    cosd = rays @ down
    sel = (v > H * (1 - floor_rows)) & (cosd > 0.15)
    if sel.sum() < 50:
        return None, 0, None
    t = cam_h / cosd[sel]
    zf = t                                          # ray has z component 1, so depth = t
    x, y = disp[sel], 1.0 / zf
    w = np.ones_like(y)
    for _ in range(iters):
        A = np.stack([x, np.ones_like(x)], 1) * w[:, None]
        a, b = np.linalg.lstsq(A, y * w, rcond=None)[0]
        r = np.abs(a * x + b - y) / y
        w = 1.0 / np.maximum(r / 0.05, 1.0)
    inv = a * disp + b
    depth = np.where(inv > 0.1, 1.0 / np.maximum(inv, 0.1), 0.0).astype(np.float32)
    return depth, int((r < 0.05).sum()), float(np.median(r))

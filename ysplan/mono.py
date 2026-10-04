"""Monocular depth (Depth Anything V2 small, ONNX, CPU) made metric with triangulated points.

Video tier: ARKit gives metric camera poses but no depth. For each keyframe we
1. track corners (LK optical flow) into a neighbouring frame with enough baseline,
2. triangulate them with the two known poses -> sparse metric depths in the keyframe,
3. fit inverse depth = a * disparity + b (robust, Huber-like iterative reweighting) between the
   network's relative disparity and the sparse metric depths,
4. return a dense metric depth map plus a per-frame fit residual used as its confidence.
"""
from pathlib import Path

import cv2
import numpy as np

WEIGHTS_URL = "https://github.com/fabio-sim/Depth-Anything-ONNX/releases/download/v2.0.0/depth_anything_v2_vits.onnx"
WEIGHTS = Path(__file__).resolve().parent.parent / "weights" / "depth_anything_v2_vits.onnx"
_SESSION = None


def session():
    global _SESSION
    if _SESSION is None:
        import onnxruntime as ort
        if not WEIGHTS.exists():
            raise SystemExit(f"missing {WEIGHTS}; run scripts/fetch_weights.sh")
        _SESSION = ort.InferenceSession(str(WEIGHTS), providers=["CPUExecutionProvider"])
    return _SESSION


def disparity(bgr, out_hw=(192, 256)):
    x = cv2.resize(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB), (518, 518)).astype(np.float32) / 255
    x = (x - [0.485, 0.456, 0.406]) / [0.229, 0.224, 0.225]
    x = x.transpose(2, 0, 1)[None].astype(np.float32)
    s = session()
    d = s.run(None, {s.get_inputs()[0].name: x})[0][0]
    return cv2.resize(d, (out_hw[1], out_hw[0]), interpolation=cv2.INTER_AREA)


def track_chain(grays, max_pts=800):
    """KLT-track corners of grays[0] through the whole list (small steps keep tracking reliable,
    the chain end gives a usable baseline). Forward-backward check at every step.
    Returns (p0 (N,2) in frame 0, p_end (N,2) in the last frame)."""
    p0 = cv2.goodFeaturesToTrack(grays[0], max_pts, 0.005, 10)
    if p0 is None:
        return np.zeros((0, 2)), np.zeros((0, 2))
    keep = np.arange(len(p0))
    cur = p0.copy()
    lk = dict(winSize=(25, 25), maxLevel=3)
    for g_prev, g_next in zip(grays[:-1], grays[1:]):
        nxt, st, _ = cv2.calcOpticalFlowPyrLK(g_prev, g_next, cur, None, **lk)
        back, stb, _ = cv2.calcOpticalFlowPyrLK(g_next, g_prev, nxt, None, **lk)
        ok = (st[:, 0] == 1) & (stb[:, 0] == 1) & (np.linalg.norm(back - cur, axis=2)[:, 0] < 0.7)
        cur, keep = nxt[ok], keep[ok]
        if len(keep) < 8:
            break
    return p0[keep, 0], cur[:, 0]


def triangulate(p0, p1, K, T0, T1, min_parallax_deg=1.0):
    """Triangulate pixel tracks p0 (frame 0) <-> p1 (frame 1) with camera->world poses T0, T1.
    Returns (uv in frame 0 (N,2), depth in camera 0 (N,))."""
    if len(p0) < 8:
        return np.zeros((0, 2)), np.zeros(0)
    W0, W1 = np.linalg.inv(T0), np.linalg.inv(T1)            # world->camera
    P0, P1 = K @ W0[:3], K @ W1[:3]
    X = cv2.triangulatePoints(P0, P1, p0.T, p1.T)
    X = (X[:3] / X[3]).T
    c0 = X @ W0[:3, :3].T + W0[:3, 3]
    c1 = X @ W1[:3, :3].T + W1[:3, 3]
    # parallax angle and reprojection checks
    r0 = X - T0[:3, 3]
    r1 = X - T1[:3, 3]
    cosang = np.sum(r0 * r1, 1) / (np.linalg.norm(r0, axis=1) * np.linalg.norm(r1, axis=1))
    par = np.degrees(np.arccos(np.clip(cosang, -1, 1)))
    proj = c1 @ K.T
    err = np.linalg.norm(proj[:, :2] / proj[:, 2:3] - p1, axis=1)
    good = (c0[:, 2] > 0.3) & (c0[:, 2] < 6) & (c1[:, 2] > 0.3) & (par > min_parallax_deg) & (err < 1.5)
    return p0[good], c0[good, 2]


def fit_metric(disp, uv, depth, scale_xy, iters=5):
    """Fit 1/depth = a*disp + b at the sparse points; return (dense depth, rel residual, n)."""
    if len(depth) < 15:
        return None, None, len(depth)
    u = np.clip((uv[:, 0] * scale_xy[0]).astype(int), 0, disp.shape[1] - 1)
    v = np.clip((uv[:, 1] * scale_xy[1]).astype(int), 0, disp.shape[0] - 1)
    x, y = disp[v, u], 1.0 / depth
    w = np.ones_like(y)
    for _ in range(iters):
        A = np.stack([x, np.ones_like(x)], 1) * w[:, None]
        a, b = np.linalg.lstsq(A, y * w, rcond=None)[0]
        r = np.abs(a * x + b - y) / y
        w = 1.0 / np.maximum(r / 0.05, 1.0)                   # Huber-style down-weighting
    inv = a * disp + b
    dense = np.where(inv > 1e-3, 1.0 / np.maximum(inv, 1e-3), 0.0).astype(np.float32)
    return dense, float(np.median(r)), int(len(y))


def read_frames(video, wanted, size=None):
    """Decode `wanted` frame indices sequentially (seeking in H.264 is not frame-accurate)."""
    wanted = sorted(set(int(w) for w in wanted))
    out, vc, i, k = {}, cv2.VideoCapture(str(video)), 0, 0
    while k < len(wanted):
        if i == wanted[k]:
            ok, im = vc.read()
            if not ok:
                break
            out[i] = cv2.resize(im, size, interpolation=cv2.INTER_AREA) if size else im
            k += 1
        elif not vc.grab():
            break
        i += 1
    return out



def iter_frames(video, wanted, size=None):
    """Yield (index, frame) for `wanted` indices in order, decoding sequentially, one at a time."""
    wanted = sorted(set(int(w) for w in wanted))
    vc, i, k = cv2.VideoCapture(str(video)), 0, 0
    while k < len(wanted):
        if i == wanted[k]:
            ok, im = vc.read()
            if not ok:
                break
            yield i, (cv2.resize(im, size, interpolation=cv2.INTER_AREA) if size else im)
            k += 1
        elif not vc.grab():
            break
        i += 1
    vc.release()

"""Per-room measurements with uncertainty: wall lengths, floor area, ceiling height.

Error model (LiDAR tier), per wall face position:
    sigma_face^2 = (point scatter / sqrt(n_eff))^2 + sigma_sensor^2 + sigma_drift^2
  - point scatter: std of the wall points supporting the face (surface roughness, noise)
  - n_eff: supporting points divided by 25 (neighbouring LiDAR points are not independent)
  - sigma_sensor: 0.5 cm residual LiDAR range bias (literature for iPhone LiDAR at < 3 m)
  - sigma_drift: residual loop misalignment after drift correction, split across both faces
A wall length is the distance between its two bounding faces, so sigma_len^2 = sa^2 + sb^2.
Unobserved faces (no supporting points) get sigma = 10 cm. The `tier_scale` factor widens
everything for thinner inputs and is calibrated on the benchmark (see calibrate.py).
"""
import numpy as np

Z95 = 1.96
SIGMA_SENSOR = 0.005
SIGMA_UNOBSERVED = 0.10


def face_stats(U, axis, line, lo, hi, tol=0.05, band=(0.2, 2.2)):
    """Support of an axis-aligned wall face at `line` between lo..hi along `axis`."""
    other = 2 if axis == 0 else 0
    m = ((np.abs(U[:, other] - line) < tol) & (U[:, axis] > lo) & (U[:, axis] < hi)
         & (U[:, 1] > band[0]) & (U[:, 1] < band[1]))
    pts = U[m]
    if len(pts) < 10:
        return {"pos": float(line), "sigma": SIGMA_UNOBSERVED, "n": int(len(pts)), "observed": 0.0}
    pos = float(np.median(pts[:, other]))
    scatter = float(np.std(pts[:, other]))
    n_eff = max(1.0, len(pts) / 25.0)
    cells = np.unique(((pts[:, axis] - lo) / 0.02).astype(int))
    return {"pos": pos, "sigma": float(np.hypot(scatter / np.sqrt(n_eff), SIGMA_SENSOR)),
            "n": int(len(pts)), "observed": float(min(1.0, len(cells) * 0.02 / max(hi - lo, 1e-6)))}


def walls(U, poly, sigma_drift=0.0, tier_scale=1.0):
    """Measure every edge of a rectilinear room polygon.

    The length of edge k is bounded by the faces of edges k-1 and k+1 (perpendicular walls).
    """
    xy = list(poly.exterior.coords)[:-1]
    n = len(xy)
    edges = []
    for k in range(n):
        (x0, y0), (x1, y1) = xy[k], xy[(k + 1) % n]
        horiz = abs(y1 - y0) < 1e-6
        axis, line = (0, y0) if horiz else (2, x0)
        lo, hi = sorted([x0, x1] if horiz else [y0, y1])
        edges.append({"axis": axis, "line": line, "lo": lo, "hi": hi,
                      "face": face_stats(U, axis, line, lo + 0.05, hi - 0.05)})
    out = []
    for k, e in enumerate(edges):
        a, b = edges[k - 1]["face"], edges[(k + 1) % n]["face"]
        sd = sigma_drift / np.sqrt(2)
        sa = np.sqrt(a["sigma"] ** 2 + sd ** 2) * tier_scale
        sb = np.sqrt(b["sigma"] ** 2 + sd ** 2) * tier_scale
        L = e["hi"] - e["lo"]
        s = float(np.hypot(sa, sb))
        x0, y0 = xy[k]
        x1, y1 = xy[(k + 1) % n]
        out.append({"from": [round(x0, 4), round(y0, 4)], "to": [round(x1, 4), round(y1, 4)],
                    "length_m": _val(L, s), "observed_fraction": round(e["face"]["observed"], 3),
                    "face_points": e["face"]["n"]})
    return out


def area(poly, wall_list):
    """Floor area with interval from perimeter x mean face sigma (first-order)."""
    sig = np.mean([w["length_m"]["sigma"] for w in wall_list]) / np.sqrt(2)
    s = float(poly.length * sig)
    return _val(poly.area, s)


def ceiling(U, poly, floor_sigma=0.005, tier_scale=1.0, min_pts=200):
    """Ceiling height above floor inside the room footprint, or an honest 'not observed'.

    Ceiling = densest 1 cm height bin among points above 2.0 m inside the room; its height is the
    median of points within +-2 cm of that bin. Without enough ceiling points we report the highest
    observed wall point as a lower bound and no value.
    """
    from shapely import contains_xy
    inside = contains_xy(poly.buffer(-0.15), U[:, 0], U[:, 2])
    h = U[inside, 1]
    top = h[h > 2.0]
    lower = float(np.percentile(h, 99.9)) if len(h) else None
    if len(top) >= min_pts:
        hist, e = np.histogram(top, bins=np.arange(2.0, top.max() + 0.02, 0.01))
        k = int(np.argmax(hist))
        if hist[k] >= min_pts / 4:
            sel = top[np.abs(top - (e[k] + 0.005)) < 0.02]
            val = float(np.median(sel))
            s = float(np.hypot(np.std(sel) / np.sqrt(max(1, len(sel) / 25)), floor_sigma, ) * tier_scale)
            s = float(np.hypot(s, SIGMA_SENSOR))
            return dict(_val(val, s), status="measured", ceiling_points=int(len(sel)))
    return {"value": None, "ci95": None, "sigma": None, "status": "not_observed",
            "lower_bound_m": round(lower, 3) if lower is not None else None}


def _val(v, s):
    return {"value": round(float(v), 4), "sigma": round(float(s), 4),
            "ci95": [round(float(v - Z95 * s), 4), round(float(v + Z95 * s), 4)]}

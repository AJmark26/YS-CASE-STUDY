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


def _sharpness(xy):
    """How tightly points pile up on the two axes (sum of squared 1 cm histogram counts)."""
    sc = 0.0
    for v in (xy[:, 0], xy[:, 1]):
        h = np.bincount(((v - v.min()) / 0.01).astype(int))
        sc += float(np.sum(h.astype(float) ** 2))
    return sc / len(xy)


def room_yaw(U, poly, band=(0.3, 1.9), search_deg=4.0, step_deg=0.05, min_gain=0.03, min_deg=0.2):
    """Small rotation (rad) that squares this room's own walls to the plan axes.

    Drift that loop closure could not remove can leave one room of a multi-room capture turned by
    a few tenths of a degree up to a few degrees against the rest of the plan. Measured on the
    global axes, its walls smear across several cm. Returns 0 unless squaring the room sharpens
    its walls by at least `min_gain` and the rotation is at least `min_deg`.
    """
    from shapely import contains_xy
    m = (U[:, 1] > band[0]) & (U[:, 1] < band[1])
    W = U[m][:, [0, 2]]
    W = W[contains_xy(poly.buffer(0.25), W[:, 0], W[:, 1])]
    if len(W) < 500:
        return 0.0
    W = W - W.mean(0)
    degs = np.arange(-search_deg, search_deg + 1e-9, step_deg)
    sc = []
    for d in degs:
        c, s = np.cos(np.radians(d)), np.sin(np.radians(d))
        sc.append(_sharpness(np.stack([W[:, 0] * c + W[:, 1] * s, -W[:, 0] * s + W[:, 1] * c], 1)))
    sc = np.array(sc)
    k = int(np.argmax(sc))
    best = degs[k]
    if 0 < k < len(sc) - 1:                       # parabolic refinement between grid steps
        den = sc[k - 1] - 2 * sc[k] + sc[k + 1]
        if den < 0:
            best += 0.5 * step_deg * (sc[k - 1] - sc[k + 1]) / den
    s0 = sc[np.argmin(np.abs(degs))]
    if abs(best) < min_deg or sc[k] < s0 * (1 + min_gain):
        return 0.0
    return float(np.radians(best))


def _rotate(xy, ang, c):
    """Rotate plan points by `ang` (rad) about centre c."""
    ca, sa = np.cos(ang), np.sin(ang)
    d = xy - c
    return np.stack([d[:, 0] * ca - d[:, 1] * sa, d[:, 0] * sa + d[:, 1] * ca], 1) + c


def walls(U, poly, sigma_drift=0.0, tier_scale=1.0, yaw=None):
    """Measure every edge of a rectilinear room polygon.

    Each edge's wall face is located from its supporting points. Each corner is moved to the
    intersection of its two measured faces, so the length of edge k is the distance between the
    measured faces of edges k-1 and k+1, not the grid-snapped polygon. Unobserved faces keep
    their polygon position.

    `yaw` squares the room first (see room_yaw; None = estimate it here, 0 = off). The room is
    measured in its own squared frame and corners are rotated back into the plan frame.
    """
    if yaw is None:
        yaw = room_yaw(U, poly)
    xy = np.array(poly.exterior.coords)[:-1]
    ctr = xy.mean(0)
    if yaw:
        near = poly.buffer(0.6).bounds
        m = (U[:, 0] > near[0]) & (U[:, 0] < near[2]) & (U[:, 2] > near[1]) & (U[:, 2] < near[3])
        U = U[m].copy()
        U[:, [0, 2]] = _rotate(U[:, [0, 2]], -yaw, ctr)
        xy = _rotate(xy, -yaw, ctr)
    xy = [tuple(q) for q in xy]
    n = len(xy)
    edges = []
    for k in range(n):
        (x0, y0), (x1, y1) = xy[k], xy[(k + 1) % n]
        horiz = abs(y1 - y0) < abs(x1 - x0)
        axis, line = (0, (y0 + y1) / 2) if horiz else (2, (x0 + x1) / 2)
        lo, hi = sorted([x0, x1] if horiz else [y0, y1])
        edges.append({"axis": axis, "line": line, "lo": lo, "hi": hi,
                      "face": face_stats(U, axis, line, lo + 0.05, hi - 0.05)})
    # corner k joins edge k-1 and edge k; one is horizontal (fixes y), the other vertical (fixes x)
    corners = []
    for k in range(n):
        a, b = edges[k - 1], edges[k]
        if a["axis"] == b["axis"]:
            corners.append(xy[k])
            continue
        h, v = (a, b) if a["axis"] == 0 else (b, a)
        corners.append((v["face"]["pos"], h["face"]["pos"]))
    corners_plan = _rotate(np.array(corners), yaw, ctr) if yaw else np.array(corners)
    out = []
    for k, e in enumerate(edges):
        a, b = edges[k - 1]["face"], edges[(k + 1) % n]["face"]
        sd = sigma_drift / np.sqrt(2)
        sa = np.sqrt(a["sigma"] ** 2 + sd ** 2) * tier_scale
        sb = np.sqrt(b["sigma"] ** 2 + sd ** 2) * tier_scale
        (x0, y0), (x1, y1) = corners[k], corners[(k + 1) % n]
        L = abs(x1 - x0) if e["axis"] == 0 else abs(y1 - y0)
        s = float(np.hypot(sa, sb))
        (x0, y0), (x1, y1) = corners_plan[k], corners_plan[(k + 1) % n]
        out.append({"from": [round(x0, 4), round(y0, 4)], "to": [round(x1, 4), round(y1, 4)],
                    "length_m": _val(L, s), "observed_fraction": round(e["face"]["observed"], 3),
                    "face_points": e["face"]["n"],
                    # the length is measured only if both perpendicular walls that bound it were seen
                    "ends_observed": bool(a["observed"] > 0 and b["observed"] > 0)})
    return out


def measured_polygon(wall_list):
    """Room outline through the measured wall faces (corners from walls())."""
    from shapely.geometry import Polygon
    return Polygon([w["from"] for w in wall_list]).buffer(0)


def area(poly, wall_list):
    """Floor area of the measured outline, interval from perimeter x mean face sigma (first-order)."""
    mp = measured_polygon(wall_list)
    if not mp.is_valid or mp.is_empty or abs(mp.area - poly.area) > 0.25 * poly.area:
        mp = poly
    sig = np.mean([w["length_m"]["sigma"] for w in wall_list]) / np.sqrt(2)
    s = float(mp.length * sig)
    return _val(mp.area, s)


def _level(h, h0, win=0.06, iters=5):
    """Robust height of a horizontal surface near h0: median of the points within 3 MAD,
    iterated. Returns (level, residual std, inlier mask)."""
    sel = np.abs(h - h0) < win
    lev = h0
    for _ in range(iters):
        if sel.sum() < 10:
            break
        lev = float(np.median(h[sel]))
        mad = 1.4826 * float(np.median(np.abs(h[sel] - lev)))
        sel = np.abs(h - lev) < 3 * max(mad, 0.005)
    return lev, float(np.std(h[sel] - lev)) if sel.any() else 0.0, sel


def ceiling(U, poly, floor_sigma=0.005, tier_scale=1.0, min_pts=200, floor_observed=True):
    """Floor-to-ceiling height of one room, or an honest 'not observed'.

    Both surfaces are measured inside this room's footprint (15 cm in from the walls): the
    ceiling level relative to this room's own floor level, so a room whose floor sits a
    centimetre or two above or below the rest of the home is measured from its own floor. The
    ceiling search starts from the 1 cm height bin covering the most area (counted in 5 cm
    cells, so a patch the camera lingered on does not outweigh the rest of the ceiling); each
    level is a robust median (3-MAD inliers, iterated).

    sigma^2 = ceiling level^2 + floor level^2 + sensor^2 + relief^2, where each level term is
    the residual scatter over sqrt(n/25), and `relief` is how much the ceiling height varies
    across the room (spread of 10 cm cell medians, minus what sensor noise puts into a cell
    median): lights, beams, a stepped section, or a slope. With it, a laser reading taken
    anywhere in the room should fall inside the interval.
    Without enough ceiling points we report the highest observed point as a lower bound. If the
    floor was not seen anywhere in the capture, nothing is reported.
    """
    from shapely import contains_xy
    if not floor_observed:                        # no floor anywhere: any height would be a guess
        return {"value": None, "ci95": None, "sigma": None, "status": "not_observed",
                "reason": "floor not seen", "lower_bound_m": None}
    inner = poly.buffer(-0.15)
    if inner.is_empty:
        inner = poly
    V = U[contains_xy(inner, U[:, 0], U[:, 2])]
    h = V[:, 1]
    top = V[h > 2.0]
    lower = float(np.percentile(h, 99.9)) if len(h) else None
    not_seen = {"value": None, "ci95": None, "sigma": None, "status": "not_observed",
                "lower_bound_m": round(lower, 3) if lower is not None else None}
    if len(top) < min_pts:
        return not_seen
    cells = np.unique(np.c_[np.floor(top[:, 0] / 0.05), np.floor(top[:, 2] / 0.05),
                            np.floor((top[:, 1] - 2.0) / 0.01)].astype(np.int64), axis=0)
    cover = np.bincount(cells[:, 2])
    k = int(np.argmax(cover))
    if cover[k] < 20:
        return not_seen
    c_lev, c_res, c_in = _level(top[:, 1], 2.0 + (k + 0.5) * 0.01)
    fsel = np.abs(h) < 0.06
    if fsel.sum() >= min_pts:
        f_lev, f_res, f_in = _level(h[fsel], 0.0)
        s_floor = f_res / np.sqrt(max(1.0, f_in.sum() / 25.0))
        f_n, floor_note = int(f_in.sum()), "room floor"
    else:                                         # floor not seen in this room: global floor
        f_lev, s_floor, f_n, floor_note = 0.0, floor_sigma, 0, "global floor (room floor not seen)"
    T = top[c_in]
    res = T[:, 1] - c_lev
    _, inv, cnt = np.unique(np.floor(T[:, [0, 2]] / 0.10).astype(np.int64), axis=0,
                            return_inverse=True, return_counts=True)
    inv = inv.ravel()
    ok = cnt >= 10
    relief = 0.0
    if ok.sum() >= 5:
        groups = np.split(res[np.argsort(inv, kind="stable")], np.cumsum(cnt)[:-1])
        med = np.array([np.median(g) for g, k_ in zip(groups, ok) if k_])
        noise = np.mean((1.2533 * c_res) ** 2 / cnt[ok])
        relief = float(np.sqrt(max(0.0, np.var(med) - noise)))
    s_ceil = c_res / np.sqrt(max(1.0, len(T) / 25.0))
    s = float(np.sqrt(s_ceil ** 2 + s_floor ** 2 + SIGMA_SENSOR ** 2 + relief ** 2)) * tier_scale
    return dict(_val(c_lev - f_lev, s), status="measured", ceiling_points=int(len(T)), floor_points=f_n,
                floor_reference=floor_note, floor_offset_cm=round(f_lev * 100, 2),
                ceiling_relief_cm=round(relief * 100, 2))


def _val(v, s):
    return {"value": round(float(v), 4), "sigma": round(float(s), 4),
            "ci95": [round(float(v - Z95 * s), 4), round(float(v + Z95 * s), 4)]}

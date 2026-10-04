"""Rectilinear room polygons by cell-complex labelling.

1. Wall lines: on each plan axis, peaks of the wall-point histogram (1 cm bins) whose points
   cover >= `min_support` metres along the other axis. Position = mean of the supporting points,
   so a wall face is located to ~1 cm even though the grid is 2 cm.
2. All lines are extended across the plan, cutting it into rectangles.
3. Each rectangle takes the room label that covers most of it, if free space covers at least
   `min_fill` of it. Rectangles beyond the outermost walls are mostly empty and drop out, which
   removes window/door leakage from the carved free space.
4. Each room polygon = union of its rectangles (shapely), so every edge lies on a measured wall face.
"""
import numpy as np
from shapely.geometry import box
from shapely.ops import unary_union

from .grid import RES


def wall_lines(U, axis, lo=0.3, hi=1.9, min_support=0.8, nms=0.08):
    """Return sorted wall-face coordinates along plan axis (0 -> u, 2 -> v)."""
    m = (U[:, 1] > lo) & (U[:, 1] < hi)
    w, o = U[m, axis], U[m, 2 if axis == 0 else 0]
    b = 0.01
    w0 = w.min()
    wi = ((w - w0) / b).astype(int)
    hist = np.bincount(wi).astype(float)
    sm = np.convolve(hist, np.ones(3) / 3, mode="same")
    order = np.argsort(sm)[::-1]
    taken = np.zeros(len(sm), bool)
    lines = []
    for k in order:
        if sm[k] < 20:
            break
        if taken[max(0, k - int(nms / b)): k + int(nms / b) + 1].any():
            continue
        sel = np.abs(wi - k) <= 2
        support = len(np.unique((o[sel] / RES).astype(int))) * RES
        if support < min_support:
            continue
        taken[k] = True
        lines.append((float(w[sel].mean()), support))
    return sorted(lines)


def _snap(v, lines, tol=0.10):
    if not lines:
        return v
    k = int(np.argmin([abs(v - l) for l in lines]))
    return lines[k] if abs(v - lines[k]) <= tol else v


def regularize(p, us, vs, notch=0.25, min_area=1.5):
    """Morphological open+close with mitred buffers (keeps right angles): removes leak strips and
    notches narrower than 2*notch, then snaps vertices back onto measured wall lines.
    Returns a list of polygons: opening can split a region into two genuine rooms."""
    q = p.buffer(-notch, **_KW).buffer(notch, **_KW)        # open: drop thin leaks
    if q.is_empty:
        q = p
    parts = list(q.geoms) if q.geom_type == "MultiPolygon" else [q]
    return [r for r in (_close_snap(g, us, vs, notch) for g in parts) if r.area >= min_area]


_KW = dict(join_style=2, mitre_limit=10)


def _rect_simplify(coords, min_step=0.2):
    """Remove jogs shorter than `min_step` from an axis-aligned ring.

    For a short edge between two parallel neighbours, the shorter neighbour is moved onto the
    longer neighbour's line (the longer one has more wall support), then collinear vertices
    are merged. Repeats until no short jog remains.
    """
    pts = [list(p) for p in coords[:-1]]

    def clean(pts):
        out = []
        n = len(pts)
        for i in range(n):
            a, b, c = pts[i - 1], pts[i], pts[(i + 1) % n]
            if abs(a[0] - b[0]) < 1e-6 and abs(b[0] - c[0]) < 1e-6:
                continue
            if abs(a[1] - b[1]) < 1e-6 and abs(b[1] - c[1]) < 1e-6:
                continue
            if abs(a[0] - b[0]) < 1e-6 and abs(a[1] - b[1]) < 1e-6:
                continue
            out.append(b)
        return out

    for _ in range(200):
        pts = clean(pts)
        n = len(pts)
        if n <= 4:
            break
        lens = [np.hypot(pts[(i + 1) % n][0] - pts[i][0], pts[(i + 1) % n][1] - pts[i][1]) for i in range(n)]
        i = int(np.argmin(lens))
        if lens[i] >= min_step:
            break
        j = (i + 1) % n
        prev_len, next_len = lens[i - 1], lens[j]
        ax = 0 if abs(pts[i][0] - pts[j][0]) > 1e-6 else 1   # axis along which the jog runs
        if prev_len >= next_len:      # move the next edge onto the previous edge's line
            val = pts[i][ax]
            pts[j][ax] = val
            pts[(j + 1) % n][ax] = val
        else:
            val = pts[j][ax]
            pts[i][ax] = val
            pts[i - 1][ax] = val
    return [tuple(p) for p in clean(pts)]


def _close_snap(q, us, vs, notch):
    from shapely.geometry import Polygon
    q = q.buffer(notch, **_KW).buffer(-notch, **_KW)        # close: fill notches
    if q.geom_type == "MultiPolygon":
        q = max(q.geoms, key=lambda g: g.area)
    pts = [(_snap(x, us), _snap(y, vs)) for x, y in q.exterior.coords]
    pts = _rect_simplify(Polygon(pts).buffer(0).simplify(0.002).exterior.coords)
    out = Polygon(pts).buffer(0)
    if out.geom_type == "MultiPolygon":
        out = max(out.geoms, key=lambda g: g.area)
    return out.simplify(0.005)


def room_polygons(U, G, labels, min_fill=0.5):
    us = [l for l, _ in wall_lines(U, 0)]
    vs = [l for l, _ in wall_lines(U, 2)]
    # plan bounds act as outer lines
    us = [G.origin[0]] + us + [G.origin[0] + labels.shape[0] * RES]
    vs = [G.origin[1]] + vs + [G.origin[1] + labels.shape[1] * RES]
    rects = {k: [] for k in range(1, labels.max() + 1)}
    for a, b in zip(us[:-1], us[1:]):
        i0, i1 = G.to_cell([a, 0])[0], G.to_cell([b, 0])[0]
        for c, d in zip(vs[:-1], vs[1:]):
            j0, j1 = G.to_cell([0, c])[1], G.to_cell([0, d])[1]
            patch = labels[max(i0, 0):max(i1, i0 + 1), max(j0, 0):max(j1, j0 + 1)]
            if patch.size == 0:
                continue
            filled = patch > 0
            if filled.mean() < min_fill:
                continue
            k = np.bincount(patch[filled]).argmax()
            rects[k].append(box(a, c, b, d))
    polys = []
    for k, rs in rects.items():
        if not rs:
            continue
        p = unary_union(rs)
        for g in (p.geoms if p.geom_type == "MultiPolygon" else [p]):
            polys += regularize(g, us[1:-1], vs[1:-1])
    polys.sort(key=lambda g: -g.area)
    return dict(enumerate(polys, 1)), us[1:-1], vs[1:-1]

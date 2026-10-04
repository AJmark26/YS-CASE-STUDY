"""Door and window detection on room boundary edges.

For every polygon edge we look just outside the wall line and ask two questions per 2 cm step:
  - did low rays (ending below sill height) pass through the wall line here?  -> door-like gap
  - did mid-height rays (ending 0.9-1.9 m) pass through, while the wall below is solid? -> window
Rays that crossed the wall line prove the gap is real, which separates openings from wall that
was simply never scanned. Widths are measured jamb to jamb from the wall points themselves.
"""
import numpy as np

from .grid import RES


def _runs(mask):
    """(start, end) index pairs of True runs."""
    d = np.diff(np.concatenate([[0], mask.astype(np.int8), [0]]))
    return list(zip(np.where(d == 1)[0], np.where(d == -1)[0]))


def _wall_coords(U, axis, line, band=(0.2, 1.9), tol=0.06):
    other = 2 if axis == 0 else 0
    m = (np.abs(U[:, other] - line) < tol) & (U[:, 1] > band[0]) & (U[:, 1] < band[1])
    return U[m, axis]


def _jamb_refine(w, gap_lo, gap_hi, reach=0.08):
    """Refine a gap to the wall points that bound it. A jamb counts only if wall points sit
    within `reach` of the gap end, i.e. the gap is cut into a real wall."""
    left = w[(w < gap_lo + 0.04) & (w > gap_lo - reach)]
    right = w[(w > gap_hi - 0.04) & (w < gap_hi + reach)]
    l = np.percentile(left, 98) if len(left) > 5 else gap_lo
    r = np.percentile(right, 2) if len(right) > 5 else gap_hi
    return float(l), float(r), int(len(left) > 5) + int(len(right) > 5)


def detect(U, G, poly, carved_low, carved_mid, min_door=0.55, max_door=1.3, max_opening=2.6,
           min_win=0.4, min_wall_cover=0.3):
    from shapely.geometry import Point
    out = []
    xy = list(poly.exterior.coords)
    for (x0, y0), (x1, y1) in zip(xy[:-1], xy[1:]):
        L = np.hypot(x1 - x0, y1 - y0)
        if L < 0.5:
            continue
        horiz = abs(y1 - y0) < 1e-6          # edge runs along u (axis 0) at constant v
        axis, line = (0, y0) if horiz else (2, x0)
        w_all = _wall_coords(U, axis, line)
        lo_e, hi_e = sorted([x0, x1] if horiz else [y0, y1])
        occ = np.unique(((w_all[(w_all > lo_e) & (w_all < hi_e)] - lo_e) / RES).astype(int))
        if len(occ) * RES / L < min_wall_cover:
            continue                         # not a wall edge (open boundary between rooms)
        t = np.arange(0.06, L - 0.06, RES)
        d = np.array([x1 - x0, y1 - y0]) / L
        nrm = np.array([d[1], -d[0]])
        mid = np.array([(x0 + x1) / 2, (y0 + y1) / 2])
        if poly.contains(Point(mid + nrm * 0.1)):
            nrm = -nrm                       # make the normal point outwards
        low = np.zeros(len(t), bool)
        midp = np.zeros(len(t), bool)
        for off in (0.06, 0.10, 0.14):
            pts = np.array([x0, y0]) + t[:, None] * d + off * nrm
            ij = G.to_cell(pts)
            ok = (ij[:, 0] >= 0) & (ij[:, 0] < G.wall.shape[0]) & (ij[:, 1] >= 0) & (ij[:, 1] < G.wall.shape[1])
            ij = ij.clip(0, np.array(G.wall.shape) - 1)
            low |= ok & (carved_low[ij[:, 0], ij[:, 1]] >= 2)
            midp |= ok & (carved_mid[ij[:, 0], ij[:, 1]] >= 2)
        # close 1-2 cell holes
        for arr in (low, midp):
            for k in range(1, len(arr) - 1):
                if not arr[k] and arr[k - 1] and arr[k + 1]:
                    arr[k] = True
        pos = lambda k: (x0 + t[k] * d[0]) if horiz else (y0 + t[k] * d[1])
        for a, b in _runs(low):
            w = (b - a) * RES
            if w < min_door * 0.8 or w > max_door * 1.2:
                continue
            g0, g1 = sorted([pos(a), pos(min(b, len(t) - 1))])
            l, r, n_jambs = _jamb_refine(w_all, g0, g1)
            width = r - l
            if n_jambs < 2 or not (min_door <= width <= max_opening):
                continue
            out.append({"type": "door" if width <= max_door else "opening", "axis": "u" if horiz else "v", "line": float(line),
                        "from": l, "to": r, "width_m": width, "jambs_found": int(n_jambs)})
        for a, b in _runs(midp & ~low):
            w = (b - a) * RES
            if w < min_win:
                continue
            g0, g1 = sorted([pos(a), pos(min(b, len(t) - 1))])
            l, r, n_jambs = _jamb_refine(_wall_coords(U, axis, line, band=(0.9, 1.9)), g0, g1)
            if n_jambs < 2:
                continue
            out.append({"type": "window", "axis": "u" if horiz else "v", "line": float(line),
                        "from": l, "to": r, "width_m": r - l, "jambs_found": int(n_jambs)})
    return out


def merge(per_room, line_tol=0.35, min_overlap=0.5):
    """Merge detections of the same opening seen from the two rooms it connects."""
    flat = [dict(o, rooms=[k]) for k, os in per_room.items() for o in os]
    merged = []
    for o in flat:
        for m in merged:
            if m["axis"] != o["axis"] or abs(m["line"] - o["line"]) > line_tol:
                continue
            ov = min(m["to"], o["to"]) - max(m["from"], o["from"])
            if ov > min_overlap * min(m["width_m"], o["width_m"]):
                m["rooms"] = sorted(set(m["rooms"] + o["rooms"]))
                m["widths_seen"].append(o["width_m"])
                break
        else:
            merged.append(dict(o, widths_seen=[o["width_m"]]))
    for m in merged:
        m["width_m"] = float(np.mean(m["widths_seen"]))
    return merged

"""Door, opening and window detection along the plan's wall lines.

Openings are searched for along every wall line of the plan (room outline edges and tall
partitions inside rooms), not per room, so a door is found whatever way the rooms were split.
A gap is where solid wall (points at most heights) is missing; rays that crossed the wall line
prove the gap is real, which separates openings from wall that was simply never scanned.
Widths are measured jamb to jamb from the wall points themselves, per height slice.
"""
import numpy as np


def _runs(mask):
    """(start, end) index pairs of True runs."""
    d = np.diff(np.concatenate([[0], mask.astype(np.int8), [0]]))
    return list(zip(np.where(d == 1)[0], np.where(d == -1)[0]))


def _segments(U, polys, cax, tol_line=0.03, ext=1.0, reach=0.0, min_tall=1.0):
    """Stretches of wall line to search along axis `cax` (0: lines of constant u, 2: of constant v),
    as (line, start, end, stretches): a gap counts only if it overlaps one of `stretches`, but
    the line is read from `start` to `end`, `reach` further at both ends.

    Every room outline edge lies on a wall line; its stretch is searched, extended by `ext` at
    both ends so a door next to a corner is not cut off. A wall line that is not an outline edge
    but runs through a room (a partition between two rooms that this capture merged into one)
    is searched too if it is a tall wall: at least `min_tall` metres of it hold wall points at
    most heights. Where a capture's room outline stops at a door, the door runs past the end
    of its stretch; reading the line `reach` further finds its far jamb (fix-loop round 7).
    Collinear stretches whose reaches overlap are read as one line, so no gap is found twice."""
    from shapely.geometry import Point
    from .layout import wall_lines
    segs = []
    for k, poly in polys.items():
        xy = list(poly.exterior.coords)
        for (x0, y0), (x1, y1) in zip(xy[:-1], xy[1:]):
            horiz = abs(y1 - y0) < abs(x1 - x0)
            if (cax == 2) != horiz:
                continue
            pos = (y0 + y1) / 2 if horiz else (x0 + x1) / 2
            lo, hi = sorted([x0, x1] if horiz else [y0, y1])
            segs.append([pos, lo - ext, hi + ext, [(lo - ext, hi + ext)]])
    aax = 2 if cax == 0 else 0
    for pos, _ in wall_lines(U, cax):
        if any(abs(pos - s_[0]) < tol_line for s_ in segs):
            continue
        near = (np.abs(U[:, cax] - pos) < 0.06) & (U[:, 1] > 0.3) & (U[:, 1] < 1.9)
        if near.sum() < 200:
            continue
        a = U[near, aax]
        col = ((a - a.min()) / 0.02).astype(int)
        occ = np.zeros((col.max() + 1, 16), bool)
        occ[col, ((U[near, 1] - 0.3) / 0.1).astype(int).clip(0, 15)] = True
        if (occ.sum(1) >= 11).sum() * 0.02 < min_tall:
            continue
        mid = [0.0, 0.0]
        mid[0 if aax == 0 else 1] = float(np.median(a))
        mid[0 if cax == 0 else 1] = pos
        if any(p.buffer(-0.1).contains(Point(mid)) for p in polys.values()):
            segs.append([pos, float(a.min()), float(a.max()), [(float(a.min()), float(a.max()))]])
    segs.sort()
    merged = []
    for sg in segs:
        if merged and abs(sg[0] - merged[-1][0]) < tol_line and sg[1] <= merged[-1][2] + 2 * reach:
            merged[-1][2] = max(merged[-1][2], sg[2])
            merged[-1][3] = merged[-1][3] + sg[3]
        else:
            merged.append(list(sg))
    return [(pos, lo - reach, hi + reach, st) for pos, lo, hi, st in merged]


def _crossed(G, carved, cax, pos, t, offsets):
    """Share of positions t along the line where rays passed, at each offset from the line."""
    aax = 2 if cax == 0 else 0
    hit = np.zeros(len(t), bool)
    for off in offsets:
        uv = np.zeros((len(t), 2))
        uv[:, 0 if aax == 0 else 1] = t
        uv[:, 0 if cax == 0 else 1] = pos + off
        ij = G.to_cell(uv)
        ok = (ij[:, 0] >= 0) & (ij[:, 0] < G.wall.shape[0]) & (ij[:, 1] >= 0) & (ij[:, 1] < G.wall.shape[1])
        ij = ij.clip(0, np.array(G.wall.shape) - 1)
        hit |= ok & (carved[ij[:, 0], ij[:, 1]] >= 2)
    return hit


def detect_lines(U, G, polys, carved_low, carved_mid, tol=0.06, band=(0.3, 1.9), bin_m=0.02,
                 n_slices=16, min_door=0.55, max_door=1.3, max_opening=2.6, min_win=0.4,
                 cross_frac=0.5, depth=(0.25, 0.45), sigma_drift=0.0):
    """Doors, openings and windows along the plan's wall lines (see _segments).

    The wall is cut into 2 cm columns along the line, and each column scores the share of 10 cm
    height slices between 0.3 and 1.9 m that hold wall points. Solid wall scores high at every
    height; a chair or a box beside a door fills only the low slices. A gap is a run of
    non-solid columns with solid wall on both sides. It is a door or an opening if rays ending
    below 0.8 m crossed the wall line along at least half of it, the space on one side is open
    for `depth`, and rays reached the floor at least 25 cm beyond the line along a quarter of it
    (so it is a passage, not a stretch of wall the camera never pointed at; the room beyond
    need not be scanned, as in a one-room capture), and a window if the wall below 0.8 m is
    present and rays ending at 0.9 to 1.9 m crossed it.

    Each jamb is the edge of the solid wall beside the gap, found per height slice (the wall
    point nearest the gap) and then the median over slices. Its sigma is the spread over
    slices divided by sqrt(slices), plus 0.5 cm sensor bias and the capture's residual drift
    split over the two jambs, as for a wall face (measure.walls): a ragged jamb (a door leaf,
    clutter) or one seen at few heights gets a wider interval.
    """
    from shapely.geometry import Point
    hm = (U[:, 1] > band[0]) & (U[:, 1] < band[1])
    slice_h = (band[1] - band[0]) / n_slices
    n_low = int(round((0.8 - band[0]) / slice_h))
    out = []
    for cax in (0, 2):
        aax = 2 if cax == 0 else 0
        for pos, s0, s1, stretches in _segments(U, polys, cax, reach=max_opening * 1.1):
            near = hm & (np.abs(U[:, cax] - pos) < tol) & (U[:, aax] > s0) & (U[:, aax] < s1)
            if near.sum() < 50:
                continue
            a, h = U[near, aax], U[near, 1]
            a0 = s0
            nb = int((s1 - s0) / bin_m) + 1
            bi = ((a - a0) / bin_m).astype(int).clip(0, nb - 1)
            si = ((h - band[0]) / slice_h).astype(int).clip(0, n_slices - 1)
            occ = np.zeros((nb, n_slices), bool)
            occ[bi, si] = True
            occ_s = occ | (np.roll(occ, 1, 0) & np.roll(occ, -1, 0))     # 2 cm voxel holes
            frac = occ_s.mean(1)
            ac = a0 + (np.arange(nb) + 0.5) * bin_m
            own = np.zeros(nb, bool)
            for lo, hi in stretches:
                own |= (ac > lo) & (ac < hi)
            ref = np.percentile(frac[own & (frac > 0)], 90) if (own & (frac > 0)).any() else 0
            solid = frac >= max(0.25, 0.5 * ref)
            low_wall = occ_s[:, :n_low].mean(1) >= 0.6
            for k0, k1 in _runs(~solid):
                if not own[k0:k1].any():
                    continue                      # beyond the stretch: only read for far jambs
                if k0 < 3 or k1 > nb - 3 or not (solid[k0 - 3:k0].all() and solid[k1:k1 + 3].all()):
                    continue                      # needs solid wall on both sides
                g0, g1 = a0 + k0 * bin_m, a0 + k1 * bin_m
                if g1 - g0 < min(min_door, min_win) * 0.8 or g1 - g0 > max_opening * 1.1:
                    continue
                t = np.arange(g0 + 0.01, g1, bin_m)
                wall_below = low_wall[k0:k1].mean() >= 0.6
                if not wall_below:
                    through = _crossed(G, carved_low, cax, pos, t, (-0.04, 0.0, 0.04)).mean()
                    side = [min(_crossed(G, carved_low, cax, pos, t, (sg * d_,)).mean() for d_ in depth)
                            for sg in (-1, 1)]
                    far = _crossed(G, carved_low, cax, pos, t, (depth[0] * (1 if side[0] >= side[1] else -1),)).mean()
                    if through < cross_frac or max(side) < cross_frac or far < cross_frac / 2:
                        continue
                    kind = "door"
                else:
                    if _crossed(G, carved_mid, cax, pos, t, (-0.04, 0.0, 0.04)).mean() < cross_frac:
                        continue
                    kind = "window"
                left = (a > g0 - 0.06) & (a <= g0 + bin_m)
                right = (a >= g1 - bin_m) & (a < g1 + 0.06)
                jl, jr = [], []
                for s_ in range(n_low if kind == "window" else 0, n_slices):
                    m = si == s_
                    if (left & m).any():
                        jl.append(a[left & m].max())
                    if (right & m).any():
                        jr.append(a[right & m].min())
                if len(jl) < 3 or len(jr) < 3:
                    continue
                l, r = float(np.median(jl)), float(np.median(jr))
                width = r - l
                sl = np.sqrt((np.std(jl) / np.sqrt(len(jl))) ** 2 + 0.005 ** 2 + sigma_drift ** 2 / 2)
                sr = np.sqrt((np.std(jr) / np.sqrt(len(jr))) ** 2 + 0.005 ** 2 + sigma_drift ** 2 / 2)
                if kind == "door":
                    if not (min_door <= width <= max_opening):
                        continue
                    kind = "door" if width <= max_door else "opening"
                elif not (min_win <= width <= max_opening):
                    continue
                rooms = []
                for side in (-0.25, 0.25):
                    q = [0.0, 0.0]
                    q[0 if aax == 0 else 1] = (l + r) / 2
                    q[0 if cax == 0 else 1] = pos + side
                    rooms += [f"R{k}" for k, p in polys.items() if p.contains(Point(q))]
                rooms = sorted(set(rooms))
                if not rooms:
                    continue                      # not on any room of the plan
                out.append({"type": kind, "axis": "u" if cax == 2 else "v", "line": float(pos),
                            "from": l, "to": r, "width_m": width, "sigma_m": float(np.hypot(sl, sr)),
                            "jambs_found": 2, "jamb_slices": [len(jl), len(jr)], "rooms": rooms})
    return _merge_faces(out)


def _merge_faces(ops, line_tol=0.30):
    """The two faces of a partition are two wall lines, so a door through it is found twice.
    Keep one record: the mean width, with the disagreement folded into its sigma."""
    out = []
    for o in sorted(ops, key=lambda o: -sum(o["jamb_slices"])):
        for m in out:
            if m["axis"] != o["axis"] or abs(m["line"] - o["line"]) > line_tol:
                continue
            ov = min(m["to"], o["to"]) - max(m["from"], o["from"])
            if ov > 0.5 * min(m["width_m"], o["width_m"]):
                m["rooms"] = sorted(set(m["rooms"] + o["rooms"]))
                m["_w"].append((o["width_m"], o["sigma_m"], o["from"], o["to"]))
                break
        else:
            out.append(dict(o, _w=[(o["width_m"], o["sigma_m"], o["from"], o["to"])]))
    for m in out:
        w = np.array(m.pop("_w"))
        m["width_m"] = float(w[:, 0].mean())
        m["from"], m["to"] = float(w[:, 2].mean()), float(w[:, 3].mean())
        spread = (w[:, 0].max() - w[:, 0].min()) / 2 if len(w) > 1 else 0.0
        m["sigma_m"] = float(np.hypot(np.sqrt(np.mean(w[:, 1] ** 2)), spread))
        m["faces_seen"] = int(len(w))
    return out

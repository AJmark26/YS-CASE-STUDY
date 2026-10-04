"""Stitch photo-tier rooms into one plan from doorway stills.

Capture protocol: for each doorway, one photo from each side, standing up to 2 m back and looking
straight through it. The photo taken from room A into room B goes in B's folder and is named
`door-from-A_*.jpg`, so it is reconstructed with B's photos and its pose is known in B's frame.
Its viewing direction, snapped to B's Manhattan axes, is the direction 'into B'; its own depth
shows the door wall around the doorway (the face looking at A) straight ahead. The reverse photo
(door-from-B, in A's folder) gives the same wall's other face in A's frame. The two fix B
relative to A: rotate B so that 'into B' points opposite to 'into A' (a multiple of 90 degrees,
since both frames are Manhattan-aligned), then put the two faces one wall thickness apart, with
both cameras straight in front of the doorway.
Rooms are placed by breadth-first search from the best connected room; rooms without a doorway
photo from both sides are laid out beside the plan and reported unstitched.
"""
import numpy as np
from shapely.affinity import rotate as sh_rotate, translate as sh_translate

WALL = 0.12          # interior wall thickness assumed between the two faces of a door wall (m)
MAX_BACK = 2.2       # the protocol stands at most 2 m back from a doorway


def snap_dir(d):
    """Nearest axis direction (unit vector) to a plan direction."""
    k = int(np.argmax(np.abs(d)))
    out = np.zeros(2)
    out[k] = np.sign(d[k])
    return out


def first_wall(ahead, step=0.05, lo=0.1, hi=3.0, min_share=0.02, min_pts=15):
    """Distance to the first wall face across the viewing axis: the nearest well-supported peak
    of the points' distances along that axis (those seen through the doorway lie further)."""
    a = ahead[(ahead > lo) & (ahead < hi)]
    if len(a) < min_pts:
        return None
    cnt, edges = np.histogram(a, bins=np.arange(lo, hi + step, step))
    k = np.flatnonzero(cnt >= max(min_pts, min_share * len(ahead)))
    if len(k) == 0:
        return None
    near = a[np.abs(a - (edges[k[0]] + step / 2)) < step]
    return float(np.median(near))


def door_anchor(cam, d, wall_dist, default_dist=1.0):
    """Door wall position and the direction into the room, in the room's own frame, from a
    doorway photo taken in the neighbouring room. Protocol: stand up to 2 m back and look straight
    through the doorway, so the direction into the room is the viewing direction snapped to the
    nearest Manhattan axis (right while the photo is less than 45 deg oblique), and the doorway is
    straight ahead, on the near face of the door wall (found in the photo's own depth; if not,
    the protocol's 1 m)."""
    n = snap_dir(d)
    ok = wall_dist is not None and wall_dist < MAX_BACK
    return np.asarray(cam, float) + (wall_dist if ok else default_dist) * n, n, ok


def _R(k):
    th = k * np.pi / 2
    return np.array([[np.cos(th), -np.sin(th)], [np.sin(th), np.cos(th)]]).round(12)


def edge_transform(a_anchor, a_into, b_anchor, b_into):
    """(k, t): x_A = R(k) x_B + t places room B behind the door of room A.
    a_anchor: in A's frame, the door wall's face looking at B (seen from B); b_anchor: in B's
    frame, its face looking at A. The face looking at A lies one wall thickness into A."""
    for k in range(4):
        if np.allclose(_R(k) @ b_into, -a_into):
            t = a_anchor + a_into * WALL - _R(k) @ b_anchor
            return k, t
    return None


def snap_to_door(p, n, doors, tol=0.6):
    """Move a ray anchor to the centre of a detected door on the same wall, if one is near."""
    best = None
    for c, axis in doors or []:
        if (axis == "u") != (abs(n[1]) > 0.5):    # door wall along x has its normal along y
            continue
        dist = np.linalg.norm(np.asarray(c) - p)
        if dist < tol and (best is None or dist < best[0]):
            best = (dist, np.asarray(c, float))
    return (best[1], True) if best else (p, False)


def place(rooms, door_views, doors=None):
    """rooms: {name: Polygon in its own plan frame}.
    door_views: {room shown: [(room stood in, camera xy, viewing direction xy, distance to the
    door wall or None), ...]}, in the frame of the room shown (the folder the photo is in).
    doors: {name: [(centre xy, axis)]} of detected door openings; a door anchor moves to the
    centre of one found on that wall.
    Returns (pose, links, unplaced, one_sided): pose[name] = (k, t) maps the room's own frame to
    the plan, x_plan = R(90 deg * k) x + t."""
    anchors, snapped, measured = {}, {}, {}        # (a, b) -> (point, into b), in b's frame
    for b, views in door_views.items():
        for a, cam, d, dist in views:
            if b in rooms and a in rooms and (a, b) not in anchors:
                p, n, measured[(a, b)] = door_anchor(cam, d, dist)
                p, snapped[(a, b)] = snap_to_door(p, n, (doors or {}).get(b))
                anchors[(a, b)] = (p, n)
    rel = {}                                       # (cur, other) -> (k, t): x_cur = R(k) x_other + t
    for (a, b), (pb, nb) in anchors.items():
        if (b, a) in anchors:
            pa, na = anchors[(b, a)]               # in a's frame, into a
            e = edge_transform(pa, na, pb, nb)
            if e is not None:
                rel[(a, b)] = e
    if not rooms:
        return {}, [], [], []
    deg = {r: sum(r == c for c, _ in rel) for r in rooms}
    root = max(rooms, key=lambda r: (deg[r], rooms[r].area))
    pose = {root: (0, np.zeros(2))}
    links, frontier = [], [root]
    while frontier:
        cur = frontier.pop(0)
        for (c, other), (k_rel, t_rel) in sorted(rel.items()):
            if c != cur or other in pose:
                continue
            kc, tc = pose[cur]
            pose[other] = ((kc + k_rel) % 4, _R(kc) @ t_rel + tc)
            placed = apply_poly(rooms[other], *pose[other])
            overlap = sum(placed.intersection(apply_poly(rooms[r], *pose[r])).area for r in pose if r != other)
            links.append({"rooms": sorted([cur, other]), "via": "doorway photos",
                          "door_walls_measured": int(measured[(cur, other)]) + int(measured[(other, cur)]),
                          "door_centres_used": int(snapped[(cur, other)]) + int(snapped[(other, cur)]),
                          "overlap_m2": round(float(overlap), 3)})
            frontier.append(other)
    one_sided = sorted({tuple(sorted(k)) for k in anchors if k[::-1] not in anchors})
    return pose, links, [r for r in rooms if r not in pose], [list(p) for p in one_sided]


def apply_poly(poly, k, t):
    p = sh_rotate(poly, 90 * k, origin=(0, 0))
    return sh_translate(p, float(t[0]), float(t[1]))


def apply_xy(xy, k, t):
    xy = np.asarray(xy, float)
    return xy @ _R(k).T + t

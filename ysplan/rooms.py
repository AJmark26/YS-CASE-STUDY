"""Room segmentation and rectilinear room polygons from plan grids."""
import cv2
import numpy as np
from scipy import ndimage as ndi

from .grid import RES


def carve(G, cap, poses, frame_ids, stride=8, step_m=0.02, stop_m=0.06, max_end_h=0.8):
    """2D space carving: count, per plan cell, how many LiDAR rays passed through it.

    A ray from the camera to a measured surface proves the space in between is empty, which
    recovers floor area the sensor never looked down at (rooms scanned at wall height).
    Only rays ending below `max_end_h` (window-sill height) carve: rays through window glass
    would otherwise carve the outdoors. Floor-to-ceiling glass can still leak.
    """
    from .fuse import backproject
    c, s = np.cos(G.yaw), np.sin(G.yaw)
    hits = np.zeros(G.wall.shape, np.int32)
    for i in frame_ids:
        P = backproject(cap, i, pose=poses[i], stride=stride)
        h = P[:, 1] - G.floor_y
        P = P[(h > -0.05) & (h < max_end_h)]
        o = poses[i][:3, 3]
        ou = np.array([o[0] * c + o[2] * s, -o[0] * s + o[2] * c])
        pu = np.stack([P[:, 0] * c + P[:, 2] * s, -P[:, 0] * s + P[:, 2] * c], 1)
        d = pu - ou
        L = np.linalg.norm(d, axis=1)
        ok = L > stop_m + step_m
        d, L = d[ok], L[ok]
        if len(L) == 0:
            continue
        n = int(L.max() / step_m) + 1
        t = np.arange(n)[None, :] * step_m
        valid = t < (L[:, None] - stop_m)
        pts = ou + d[:, None, :] / L[:, None, None] * t[..., None]
        ij = G.to_cell(pts[valid])
        m = (ij[:, 0] >= 0) & (ij[:, 0] < hits.shape[0]) & (ij[:, 1] >= 0) & (ij[:, 1] < hits.shape[1])
        cell = np.unique(ij[m, 0] * hits.shape[1] + ij[m, 1])   # count each cell once per frame
        hits.ravel()[cell] += 1
    return hits


def free_space(G, traj_cells, carved=None, min_rays=2, close_m=0.30):
    """Interior free space: carved/observed floor + walked path, small holes closed, walls removed."""
    walls = G.wall & (G.count >= 8)
    seen = G.floor.copy()
    if carved is not None:
        seen |= carved >= min_rays
    seen[traj_cells[:, 0].clip(0, seen.shape[0] - 1), traj_cells[:, 1].clip(0, seen.shape[1] - 1)] = True
    k = int(close_m / RES) | 1
    ker = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))
    seen = cv2.morphologyEx(seen.astype(np.uint8), cv2.MORPH_CLOSE, ker).astype(bool)
    seen = ndi.binary_fill_holes(seen)
    wd = cv2.dilate(walls.astype(np.uint8), np.ones((3, 3), np.uint8)).astype(bool)
    free = seen & ~wd
    # keep the component(s) the camera actually walked through
    lab, n = ndi.label(free)
    keep = np.unique(lab[traj_cells[:, 0].clip(0, lab.shape[0] - 1), traj_cells[:, 1].clip(0, lab.shape[1] - 1)])
    keep = keep[keep > 0]
    return np.isin(lab, keep), walls


def segment(free, door_half_m=0.48, min_room_m2=1.2):
    """Watershed on the distance transform: rooms are blobs joined only by door-width necks."""
    dist = ndi.distance_transform_edt(free) * RES
    core = dist > door_half_m
    markers, n = ndi.label(core)
    # drop tiny cores (furniture gaps) before flooding
    sizes = ndi.sum(core, markers, range(1, n + 1)) * RES * RES
    for k, s in enumerate(sizes, 1):
        if s < 0.15:
            markers[markers == k] = 0
    markers, n = ndi.label(markers > 0)
    img = np.dstack([(~free).astype(np.uint8) * 255] * 3)
    lab = cv2.watershed(img, markers.astype(np.int32))
    lab[~free] = 0
    lab[lab < 0] = 0
    # merge rooms below min area into their largest neighbour
    changed = True
    while changed:
        changed = False
        ids = [i for i in np.unique(lab) if i > 0]
        for i in ids:
            m = lab == i
            if m.sum() * RES * RES >= min_room_m2:
                continue
            ring = cv2.dilate(m.astype(np.uint8), np.ones((5, 5), np.uint8)).astype(bool) & ~m
            nb = lab[ring]; nb = nb[nb > 0]
            if len(nb):
                lab[m] = np.bincount(nb).argmax()
            else:
                lab[m] = 0
            changed = True
            break
    # relabel 1..k
    out = np.zeros_like(lab)
    for k, i in enumerate([i for i in np.unique(lab) if i > 0], 1):
        out[lab == i] = k
    return out

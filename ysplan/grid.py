"""Gravity/Manhattan alignment and 2D occupancy grids from a fused cloud."""
from dataclasses import dataclass

import numpy as np

RES = 0.02  # metres per grid cell


def floor_height(P):
    """Floor = densest horizontal slab among the lowest points (y is up)."""
    y = P[:, 1]
    lo = y[y < np.percentile(y, 30)]
    h, e = np.histogram(lo, bins=np.arange(lo.min(), lo.max() + 0.01, 0.01))
    k = np.argmax(h)
    sel = lo[(lo > e[k] - 0.03) & (lo < e[k] + 0.04)]
    return float(np.median(sel))


def manhattan_angle(xz, step_deg=0.1):
    """Yaw (rad) that best axis-aligns wall points: maximise histogram peakiness on both axes."""
    rng = np.random.default_rng(0)
    if len(xz) > 200_000:
        xz = xz[rng.choice(len(xz), 200_000, replace=False)]
    best, best_a = -1, 0.0
    for a in np.deg2rad(np.arange(0, 90, step_deg)):
        c, s = np.cos(a), np.sin(a)
        u, v = xz[:, 0] * c + xz[:, 1] * s, -xz[:, 0] * s + xz[:, 1] * c
        score = 0
        for w in (u, v):
            h = np.bincount(((w - w.min()) / 0.03).astype(int))
            score += np.sum(h.astype(float) ** 2)
        if score > best:
            best, best_a = score, a
    return best_a


@dataclass
class Grids:
    origin: np.ndarray   # world (u,v) of cell (0,0) in the aligned frame
    yaw: float           # rotation applied: aligned = R(yaw) @ (x,z)
    floor_y: float
    wall: np.ndarray     # bool, cell is part of a tall vertical structure
    floor: np.ndarray    # bool, floor observed in cell
    hmax: np.ndarray     # max point height above floor per cell
    hmin: np.ndarray     # min point height above floor (excluding floor slab)
    count: np.ndarray

    def to_cell(self, uv):
        return ((np.asarray(uv) - self.origin) / RES).astype(int)

    def to_uv(self, ij):
        return np.asarray(ij) * RES + self.origin + RES / 2


def align(P, floor_y=None, yaw=None):
    """Return aligned points U (u, h, v): h = height above floor, (u,v) Manhattan-aligned plan coords."""
    floor_y = floor_height(P) if floor_y is None else floor_y
    h = P[:, 1] - floor_y
    if yaw is None:
        band = (h > 0.5) & (h < 2.0)
        yaw = manhattan_angle(P[band][:, [0, 2]])
    c, s = np.cos(yaw), np.sin(yaw)
    u = P[:, 0] * c + P[:, 2] * s
    v = -P[:, 0] * s + P[:, 2] * c
    return np.stack([u, h, v], 1), floor_y, yaw


def build(U, floor_y, yaw, wall_span=1.0, pad=0.3):
    """Rasterise aligned points. A wall cell has points spread over >= wall_span metres of height."""
    uv = U[:, [0, 2]]
    origin = uv.min(0) - pad
    ij = ((uv - origin) / RES).astype(int)
    shape = tuple(ij.max(0) + int(pad / RES) + 1)
    h = U[:, 1]
    count = np.zeros(shape, np.int32)
    hmax = np.full(shape, -np.inf)
    hmin = np.full(shape, np.inf)
    floor = np.zeros(shape, bool)
    isfloor = np.abs(h) < 0.04
    floor[ij[isfloor, 0], ij[isfloor, 1]] = True
    above = h > 0.08
    np.add.at(count, (ij[above, 0], ij[above, 1]), 1)
    np.maximum.at(hmax, (ij[above, 0], ij[above, 1]), h[above])
    np.minimum.at(hmin, (ij[above, 0], ij[above, 1]), h[above])
    wall = (hmax - hmin) >= wall_span
    return Grids(origin, yaw, floor_y, wall, floor, hmax, hmin, count)


def sharpness(U, lo=0.4, hi=1.8, bin_m=0.01):
    """Wall crispness of an aligned cloud: concentration of wall-band points on both plan axes.

    Doubled walls (drift) spread the histogram mass over more bins and lower this number.
    Used for the drift on/off ablation; higher is better.
    """
    m = (U[:, 1] > lo) & (U[:, 1] < hi)
    s = 0.0
    for ax in (0, 2):
        w = U[m, ax]
        h = np.bincount(((w - w.min()) / bin_m).astype(int)).astype(float)
        s += np.sum(h ** 2) / np.sum(h) ** 2
    return float(s * 1000)

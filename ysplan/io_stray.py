"""Loader for Stray Scanner captures (LiDAR tier).

Layout: rgb.mp4, depth/NNNNNN.png (uint16 mm, 256x192), confidence/NNNNNN.png (0..2),
odometry.csv (ARKit camera->world pose + per-frame intrinsics at RGB resolution),
camera_matrix.csv, imu.csv.
"""
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
from scipy.spatial.transform import Rotation

RGB_W, RGB_H = 1920, 1440


@dataclass
class StrayCapture:
    root: Path
    frames: np.ndarray      # (N,) int frame ids
    timestamps: np.ndarray  # (N,)
    T_wc: np.ndarray        # (N,4,4) camera->world; Stray poses already map OpenCV-convention camera points (x right, y down, z fwd) into the gravity-aligned ARKit world (y up)
    K_rgb: np.ndarray       # (N,3,3) intrinsics at RGB resolution

    def __len__(self):
        return len(self.frames)

    def depth(self, i):
        """Depth in metres (H,W) float32 and confidence (H,W) uint8."""
        f = self.frames[i]
        d = cv2.imread(str(self.root / "depth" / f"{f:06d}.png"), cv2.IMREAD_UNCHANGED).astype(np.float32) / 1000.0
        cpath = self.root / "confidence" / f"{f:06d}.png"
        c = cv2.imread(str(cpath), cv2.IMREAD_UNCHANGED) if cpath.exists() else np.full(d.shape, 2, np.uint8)
        return d, c

    def K_depth(self, i, shape):
        h, w = shape
        K = self.K_rgb[i].copy()
        K[0] *= w / RGB_W
        K[1] *= h / RGB_H
        return K


def load(root) -> StrayCapture:
    root = Path(root)
    rows = np.genfromtxt(root / "odometry.csv", delimiter=",", skip_header=1, usecols=range(0, 13))
    ts, fr = rows[:, 0], rows[:, 1].astype(int)
    t, q = rows[:, 2:5], rows[:, 5:9]
    T = np.tile(np.eye(4), (len(rows), 1, 1))
    T[:, :3, :3] = Rotation.from_quat(q).as_matrix()  # scipy uses (x,y,z,w) like the CSV
    T[:, :3, 3] = t
    K = np.tile(np.eye(3), (len(rows), 1, 1))
    K[:, 0, 0], K[:, 1, 1], K[:, 0, 2], K[:, 1, 2] = rows[:, 9], rows[:, 10], rows[:, 11], rows[:, 12]
    return StrayCapture(root, fr, ts, T, K)

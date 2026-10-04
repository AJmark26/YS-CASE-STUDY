"""Loader for Stray Scanner captures (LiDAR tier).

Layout: rgb.mp4, depth/NNNNNN.png (uint16 mm, 256x192), confidence/NNNNNN.png (0..2),
odometry.csv (ARKit camera->world pose + per-frame intrinsics at RGB resolution),
camera_matrix.csv, imu.csv.

Older Stray Scanner versions differ in two ways, and both are accepted: odometry.csv has only
the 9 pose columns (the intrinsics then come from camera_matrix.csv), and depth maps are .npy
files instead of .png. A capture can also be given as the exported .zip, or as any folder that
contains the capture folder (see `resolve`).
"""
import zipfile
from dataclasses import dataclass, field
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
    rgb_size: tuple = (RGB_W, RGB_H)
    depth_ext: str = ".png"
    notes: list = field(default_factory=list)

    def __len__(self):
        return len(self.frames)

    def depth(self, i):
        """Depth in metres (H,W) float32 and confidence (H,W) uint8."""
        f = self.frames[i]
        path = self.root / "depth" / f"{f:06d}{self.depth_ext}"
        raw = np.load(path) if self.depth_ext == ".npy" else cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
        d = np.nan_to_num(raw.astype(np.float32), nan=0.0)
        if raw.dtype.kind in "ui" or d.max() > 20:                  # millimetres, not metres
            d /= 1000.0
        cpath = self.root / "confidence" / f"{f:06d}.png"
        c = cv2.imread(str(cpath), cv2.IMREAD_UNCHANGED) if cpath.exists() else np.full(d.shape, 2, np.uint8)
        return d, c

    def K_depth(self, i, shape):
        h, w = shape
        K = self.K_rgb[i].copy()
        K[0] *= w / self.rgb_size[0]
        K[1] *= h / self.rgb_size[1]
        return K


def resolve(path) -> Path:
    """The folder holding odometry.csv, given that folder, a folder above it, or the exported
    .zip (unpacked next to itself on first use)."""
    path = Path(path)
    if path.is_file() and path.suffix.lower() == ".zip":
        dest = path.with_suffix("")
        if not dest.exists():
            with zipfile.ZipFile(path) as z:
                z.extractall(dest)
        path = dest
    if (path / "odometry.csv").exists():
        return path
    hits = sorted(path.rglob("odometry.csv"), key=lambda p: len(p.parts)) if path.is_dir() else []
    hits = [h for h in hits if "__MACOSX" not in h.parts]
    if not hits:
        raise SystemExit(f"{path}: no odometry.csv found; expected a Stray Scanner export "
                         "(rgb.mp4, depth/, confidence/, odometry.csv, camera_matrix.csv)")
    if len(hits) > 1:
        print(f"[load] {len(hits)} captures under {path}; using {hits[0].parent}")
    return hits[0].parent


def load(root) -> StrayCapture:
    root = resolve(root)
    notes = []
    rows = np.genfromtxt(root / "odometry.csv", delimiter=",", skip_header=1)
    rows = np.atleast_2d(rows)
    ts, fr = rows[:, 0], rows[:, 1].astype(int)
    t, q = rows[:, 2:5], rows[:, 5:9]
    T = np.tile(np.eye(4), (len(rows), 1, 1))
    T[:, :3, :3] = Rotation.from_quat(q).as_matrix()  # scipy uses (x,y,z,w) like the CSV
    T[:, :3, 3] = t
    K = np.tile(np.eye(3), (len(rows), 1, 1))
    if rows.shape[1] >= 13 and np.isfinite(rows[:, 9:13]).all():
        K[:, 0, 0], K[:, 1, 1], K[:, 0, 2], K[:, 1, 2] = rows[:, 9], rows[:, 10], rows[:, 11], rows[:, 12]
    else:                                             # older export: one camera matrix for all frames
        K[:] = np.loadtxt(root / "camera_matrix.csv", delimiter=",")
        notes.append("intrinsics from camera_matrix.csv (odometry.csv has no per-frame intrinsics)")
    size = (RGB_W, RGB_H)
    if (root / "rgb.mp4").exists():
        v = cv2.VideoCapture(str(root / "rgb.mp4"))
        w, h = int(v.get(cv2.CAP_PROP_FRAME_WIDTH)), int(v.get(cv2.CAP_PROP_FRAME_HEIGHT))
        v.release()
        if w and h:
            size = (w, h)
    ext = ".png" if any((root / "depth").glob("*.png")) else ".npy"
    have = {int(p.stem) for p in (root / "depth").glob(f"*{ext}")}
    # no depth at all is a video-tier capture (an iPhone without LiDAR): every pose is kept
    keep = np.array([f in have for f in fr]) if have else np.ones(len(fr), bool)
    if not keep.all():
        notes.append(f"{int((~keep).sum())} of {len(fr)} poses have no depth map and were skipped")
    ok = keep & np.isfinite(t).all(1) & np.isfinite(q).all(1)
    return StrayCapture(root, fr[ok], ts[ok], T[ok], K[ok], size, ext, notes)

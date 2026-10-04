"""Cut a new Stray Scanner capture out of a sample one, to test the pipeline on scans it has not
seen: a shorter walk, a single room, a different ARKit world frame, an older export format.

The result is a self-contained capture folder (frames renumbered from 0, the video re-encoded to
the kept frames), so the pipeline cannot lean on anything from the original walk.

    python scripts/make_test_capture.py <capture> <out_dir> --start 0 --end 25 [--reframe 37]
           [--old-format] [--zip]

--start/--end  seconds from the start of the original capture
--reframe DEG  rotate the ARKit world about gravity by DEG and move its origin to the first
               camera, as a fresh ARKit session would
--old-format   write the older export: 9-column odometry.csv and .npy depth maps
--zip          also pack the folder as <out_dir>.zip, like the Stray Scanner share sheet
"""
import argparse
import shutil
import subprocess
import sys
from pathlib import Path

import cv2
import numpy as np
from scipy.spatial.transform import Rotation

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from ysplan import io_stray  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("capture", type=Path)
    ap.add_argument("out", type=Path)
    ap.add_argument("--start", type=float, default=0.0)
    ap.add_argument("--end", type=float, default=1e9)
    ap.add_argument("--reframe", type=float, default=None)
    ap.add_argument("--old-format", action="store_true")
    ap.add_argument("--zip", action="store_true")
    a = ap.parse_args()
    cap = io_stray.load(a.capture)
    t = cap.timestamps - cap.timestamps[0]
    keep = np.where((t >= a.start) & (t <= a.end))[0]
    if len(keep) < 30:
        raise SystemExit("fewer than 30 frames in that time range")
    if a.out.exists():
        shutil.rmtree(a.out)
    (a.out / "depth").mkdir(parents=True)
    (a.out / "confidence").mkdir()
    T = cap.T_wc[keep].copy()
    if a.reframe is not None:
        ang = np.radians(a.reframe)
        G = np.eye(4)
        G[:3, :3] = Rotation.from_euler("y", ang).as_matrix()
        G[:3, 3] = -G[:3, :3] @ T[0, :3, 3]
        T = G @ T
    q = Rotation.from_matrix(T[:, :3, :3]).as_quat()
    src = cap.frames[keep]
    with open(a.out / "odometry.csv", "w") as f:
        if a.old_format:
            f.write("timestamp, frame, x, y, z, qx, qy, qz, qw\n")
        else:
            f.write("timestamp, frame, x, y, z, qx, qy, qz, qw, fx, fy, cx, cy, distortion_center_x, distortion_center_y\n")
        for n, i in enumerate(keep):
            row = [f"{cap.timestamps[i]:.9f}", f"{n:06d}", *(f"{v:.8f}" for v in T[n, :3, 3]), *(f"{v:.8f}" for v in q[n])]
            if not a.old_format:
                K = cap.K_rgb[i]
                row += [f"{K[0, 0]:.4f}", f"{K[1, 1]:.4f}", f"{K[0, 2]:.4f}", f"{K[1, 2]:.4f}", "", ""]
            f.write(", ".join(row) + "\n")
    shutil.copy(cap.root / "camera_matrix.csv", a.out / "camera_matrix.csv")
    for n, fr in enumerate(src):
        d = cv2.imread(str(cap.root / "depth" / f"{fr:06d}.png"), cv2.IMREAD_UNCHANGED)
        if a.old_format:
            np.save(a.out / "depth" / f"{n:06d}.npy", d)
        else:
            shutil.copy(cap.root / "depth" / f"{fr:06d}.png", a.out / "depth" / f"{n:06d}.png")
        c = cap.root / "confidence" / f"{fr:06d}.png"
        if c.exists():
            shutil.copy(c, a.out / "confidence" / f"{n:06d}.png")
    imu = np.genfromtxt(cap.root / "imu.csv", delimiter=",", skip_header=1)
    sel = (imu[:, 0] >= cap.timestamps[keep[0]]) & (imu[:, 0] <= cap.timestamps[keep[-1]])
    np.savetxt(a.out / "imu.csv", imu[sel], delimiter=", ", fmt="%.9f",
               header="timestamp, a_x, a_y, a_z, alpha_x, alpha_y, alpha_z", comments="")
    # video: the kept frames only, so frame n of rgb.mp4 is row n of odometry.csv
    fps = 60.0
    v = cv2.VideoCapture(str(cap.root / "rgb.mp4"))
    w, h = int(v.get(cv2.CAP_PROP_FRAME_WIDTH)), int(v.get(cv2.CAP_PROP_FRAME_HEIGHT))
    enc = subprocess.Popen(["ffmpeg", "-loglevel", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24",
                            "-s", f"{w}x{h}", "-r", str(fps), "-i", "-", "-c:v", "libx264", "-preset", "veryfast",
                            "-crf", "18", "-pix_fmt", "yuv420p", str(a.out / "rgb.mp4")], stdin=subprocess.PIPE)
    want, i = set(int(x) for x in src), 0
    last = int(src.max())
    while i <= last:
        ok, im = v.read()
        if not ok:
            break
        if i in want:
            enc.stdin.write(im.tobytes())
        i += 1
    enc.stdin.close()
    enc.wait()
    print(f"wrote {a.out}: {len(keep)} frames, {t[keep[-1]] - t[keep[0]]:.1f} s"
          f"{', reframed by %.0f deg' % a.reframe if a.reframe is not None else ''}"
          f"{', old format' if a.old_format else ''}")
    if a.zip:
        shutil.make_archive(str(a.out), "zip", a.out.parent, a.out.name)
        print(f"wrote {a.out}.zip")


if __name__ == "__main__":
    main()

"""Learned multi-view reconstruction for the photo tier (and pose-free video).

MapAnything (Meta, `facebook/map-anything`) takes a handful of
images plus their intrinsics and returns, in one forward pass, a camera pose and a dense depth
map per image in a shared metric frame. No feature matching or bundle adjustment, which is
what fails on the white walls of the sample captures: classic structure-from-motion found
too few matches there.

Intrinsics come from EXIF (FocalLengthIn35mmFilm). On CPU a room of 6 to 8 photos takes
about a minute.
"""
import os
from pathlib import Path

import numpy as np

# The CC-BY-NC checkpoint is used by default: on the sample captures, with ARKit poses given, its
# depth is within 6-7% of LiDAR (median abs rel), while the Apache-2.0 checkpoint is 27-32% short
# on the same frames. A commercial deployment would set YS_MAPANYTHING=facebook/map-anything-apache
# and accept that, or fine-tune it.
MODEL_ID = os.environ.get("YS_MAPANYTHING", "facebook/map-anything")
MOGE_ID = "Ruicheng/moge-2-vitl-normal"
_MODEL = None
_MOGE = None


def _patch_torch_hub():
    """MapAnything builds its DINOv2 encoder through torch.hub, which asks github.com for the
    default branch name with a bare urllib request. Behind some proxies that request fails even
    though git clones work, so the branch is pinned and a pre-cloned repo is used if present."""
    import torch.hub as hub
    hub._parse_repo_info = lambda github: ("facebookresearch", "dinov2", "main")


def model():
    global _MODEL
    if _MODEL is None:
        import torch
        _patch_torch_hub()
        from mapanything.models import MapAnything
        torch.set_num_threads(max(1, torch.get_num_threads()))
        _MODEL = MapAnything.from_pretrained(MODEL_ID).eval()
    return _MODEL


def moge():
    global _MOGE
    if _MOGE is None:
        from moge.model.v2 import MoGeModel
        _MOGE = MoGeModel.from_pretrained(MOGE_ID).eval()
    return _MOGE


def single_view_depth(path):
    """MoGe-2 metric depth of one photo at its EXIF field of view: (depth (H,W), mask (H,W)).
    On the sample captures it lands within about 7% of LiDAR per photo, against 20-50% for
    Depth Anything V2 metric-indoor, so it sets the metric scale of the multi-view result."""
    import torch
    K, rgb, _ = exif_intrinsics(path)
    H, W = rgb.shape[:2]
    fov_x = float(np.degrees(2 * np.arctan(W / 2 / K[0, 0])))
    x = torch.tensor(rgb / 255.0, dtype=torch.float32).permute(2, 0, 1)
    with torch.no_grad():
        o = moge().infer(x, fov_x=fov_x, use_fp16=False, resolution_level=7)
    d = o["depth"].numpy()
    return d, np.isfinite(d) & (o["mask"].numpy() > 0)


def exif_intrinsics(path):
    """Pinhole K at full image resolution from EXIF FocalLengthIn35mmFilm (35 mm-equivalent focal
    length is defined against the 43.27 mm full-frame diagonal). Principal point at the centre.
    Returns (K, rgb uint8 HxWx3, focal_source)."""
    from PIL import Image, ImageOps
    im = ImageOps.exif_transpose(Image.open(path))
    W, H = im.size
    f35 = im.getexif().get_ifd(0x8769).get(0xA405)
    src = "exif"
    if not f35:
        f35, src = 26.0, "default_iphone_main_camera"   # iPhone 1x lens is 24-26 mm equivalent
    f = float(f35) / 43.27 * np.hypot(W, H)
    K = np.array([[f, 0, W / 2], [0, f, H / 2], [0, 0, 1]], np.float32)
    return K, np.asarray(im.convert("RGB")), src


def reconstruct(paths, poses=None, log=print):
    """Run MapAnything on images (with EXIF intrinsics, and optional known camera->world poses in
    metres, OpenCV convention). Returns a list of dicts per image:
    T_wc (4,4), K (3,3) at depth resolution, depth (H,W) metres, mask (H,W) bool, conf (H,W)."""
    import torch
    from mapanything.utils.image import preprocess_inputs
    views = []
    for i, p in enumerate(paths):
        K, rgb, _ = exif_intrinsics(p)
        v = {"img": torch.from_numpy(rgb), "intrinsics": torch.from_numpy(K)}
        if poses is not None:
            v["camera_poses"] = torch.from_numpy(np.asarray(poses[i], np.float32))
            v["is_metric_scale"] = torch.tensor([True])
        views.append(v)
    views = preprocess_inputs(views)
    with torch.no_grad():
        preds = model().infer(views, memory_efficient_inference=False, use_amp=False,
                              apply_mask=True, mask_edges=True)
    out = []
    for p in preds:
        out.append({"T_wc": p["camera_poses"][0].numpy().astype(np.float64),
                    "K": p["intrinsics"][0].numpy().astype(np.float64),
                    "depth": p["depth_z"][0, ..., 0].numpy(),
                    "mask": p["mask"][0, ..., 0].numpy().astype(bool),
                    "conf": p["conf"][0].numpy()})
    return out


def list_images(folder):
    exts = {".jpg", ".jpeg", ".png", ".heic"}
    return sorted(p for p in Path(folder).iterdir() if p.suffix.lower() in exts)

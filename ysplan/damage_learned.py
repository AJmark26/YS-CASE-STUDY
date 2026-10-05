"""Learned damage detector: finds damage in the camera frames and maps it onto the plan's surfaces.

Three models, all permissively licensed and run on CPU:
1. Grounding DINO (IDEA-Research/grounding-dino-tiny, Apache-2.0) proposes boxes for the phrases
   "stain. crack. mold. damage.". It finds damaged areas but can't tell the phrases apart: on
   photos of real damage the same box scores alike for all of them.
2. CLIP (openai/clip-vit-base-patch32, MIT) checks each box against damage texts and a list of
   clean things (plain wall, ceiling, floor, furniture, curtain...). A box is kept when the damage
   texts together have at least CLIP_MIN probability, and named after the damage class whose
   texts score highest. The crop is square: CLIP keeps only a centred square of its input.
3. SAM 2 (facebook/sam2.1-hiera-small, Apache-2.0) turns each kept box into a mask.

Frames are turned upright first (the phone may have been held sideways), since all three models
were trained on upright photos. Each frame's masks are then mapped onto the plan's surfaces with
the frame's depth and pose, exactly like the colour textures in damage.unwrap: a cell of a wall
or ceiling is damaged when at least MIN_FRAC of the depth points that landed on it came from a
damage mask. That projection also drops detections on objects: a basket or a curtain fold is not
on a wall or ceiling, so its pixels land on no surface. Floors are left out (SURFACES).

`run()` returns the same damage record as damage.run (regions, concealed-damage flags, scope),
so every tier can use it: LiDAR and video keyframes, or the photos of the photo tier.
None of the models was trained on building damage; see scripts/damage_learned_eval.py for how
the thresholds were set and what they find.
"""
import numpy as np

DINO = "IDEA-Research/grounding-dino-tiny"
CLIP = "openai/clip-vit-base-patch32"
SAM = "facebook/sam2.1-hiera-small"
PROMPT = "stain. crack. mold. damage."
BOX_THRESHOLD, TEXT_THRESHOLD = 0.25, 0.20
DINO_SIZE = 800          # shortest image side given to Grounding DINO
MAX_BOX_FRAC = 0.5       # a box over half the image is the whole surface, not a defect
CLIP_MIN = 0.8           # the damage texts together must reach this probability
MIN_FRAC = 0.34          # share of a cell's depth points that must come from damage masks
SURFACES = ("wall", "ceiling")   # on floors, mats, cables and marks on tiles were all false alarms
CRACK_WIDEN = 0.016      # crack masks grow by this share of the image size (about 2 cm each side at 2 m)
VIDEO_WALL_BAND = (-0.5, 0.1)   # video tier: learned depth blurs walls and outlines sit 0.2-0.5 m inside them
CLIP_TEXTS = {
    "water_stain": ["a brown water stain on a wall", "a water stain on a ceiling", "peeling paint from water damage"],
    "dark_spot": ["black mold on a wall", "mold spots on a wall"],
    "crack": ["a crack in a wall", "a thin hairline crack in a painted wall", "cracked plaster"],
    "hole": ["a hole in a wall"],
    "none": ["a clean painted wall", "a plain white ceiling", "a floor", "a tiled floor", "a piece of furniture",
             "a window", "a door", "a picture on a wall", "a shadow on a wall", "a corner of a room",
             "a light switch", "a curtain", "a bed sheet", "a basket", "a lamp", "a plant", "a towel", "a rug",
             "a person", "grout lines between floor tiles", "a marble surface", "a seam between two wall panels",
             "a skirting board", "a cable", "the edge of a door frame"],
}
CHANNEL = {"water_stain": (0, 0), "dark_spot": (0, 1), "crack": (0, 2), "hole": (1, 0)}   # (pass, RGB channel)
_m = {}


def _models():
    if not _m:
        import torch
        from transformers import (AutoModelForZeroShotObjectDetection, AutoProcessor, CLIPModel,
                                  CLIPProcessor, Sam2Model, Sam2Processor)
        _m["dp"] = AutoProcessor.from_pretrained(DINO)
        _m["dm"] = AutoModelForZeroShotObjectDetection.from_pretrained(DINO).eval()
        _m["cp"] = CLIPProcessor.from_pretrained(CLIP)
        _m["cm"] = CLIPModel.from_pretrained(CLIP).eval()
        _m["sp"] = Sam2Processor.from_pretrained(SAM)
        _m["sm"] = Sam2Model.from_pretrained(SAM).eval()
        texts = [t for v in CLIP_TEXTS.values() for t in v]
        _m["owner"] = [k for k, v in CLIP_TEXTS.items() for _ in v]
        with torch.no_grad():
            tf = _m["cm"].get_text_features(**_m["cp"](text=texts, return_tensors="pt", padding=True))
        tf = getattr(tf, "pooler_output", tf)
        _m["tf"] = tf / tf.norm(dim=-1, keepdim=True)
    return _m


def _iou(a, b):
    x0, y0, x1, y1 = max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])
    i = max(0.0, x1 - x0) * max(0.0, y1 - y0)
    return i / ((a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - i + 1e-9)


def detect_image(rgb):
    """Damage in an upright RGB uint8 image: list of (class, mask (H, W) bool, score, box xyxy).
    score is CLIP's probability that the box shows damage."""
    import torch
    from PIL import Image
    m = _models()
    img = Image.fromarray(rgb)
    H, W = rgb.shape[:2]
    with torch.no_grad():
        inp = m["dp"](images=img, text=PROMPT, return_tensors="pt",
                      size={"shortest_edge": DINO_SIZE, "longest_edge": int(DINO_SIZE * 5 / 3)})
        res = m["dp"].post_process_grounded_object_detection(
            m["dm"](**inp), inp.input_ids, threshold=BOX_THRESHOLD, text_threshold=TEXT_THRESHOLD,
            target_sizes=[(H, W)])[0]
    boxes = []
    for b, s in sorted(zip(res["boxes"].tolist(), res["scores"].tolist()), key=lambda x: -x[1]):
        if (b[2] - b[0]) * (b[3] - b[1]) > MAX_BOX_FRAC * H * W or any(_iou(b, q) > 0.7 for q in boxes):
            continue
        boxes.append(b)
    if not boxes:
        return []
    # square crops around each box (10% margin on the long side): CLIP's preprocessing keeps only
    # a centred square of its input, which cut a long thin crack box down to plain wall
    crops = []
    for x0, y0, x1, y1 in boxes:
        h = 0.6 * max(x1 - x0, y1 - y0)
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        crops.append(img.crop((cx - h, cy - h, cx + h, cy + h)))     # outside the image is black
    with torch.no_grad():
        f = m["cm"].get_image_features(**m["cp"](images=crops, return_tensors="pt"))
    f = getattr(f, "pooler_output", f)
    p = (100 * (f / f.norm(dim=-1, keepdim=True)) @ m["tf"].T).softmax(-1).numpy()
    keep = []
    for b, row in zip(boxes, p):
        # damage or not: total probability of all damage texts; which damage: the class whose
        # texts together score highest
        agg = {}
        for k, v in zip(m["owner"], row):
            agg[k] = agg.get(k, 0.0) + float(v)
        dmg = 1.0 - agg["none"]
        if dmg >= CLIP_MIN:
            keep.append((max((k for k in agg if k != "none"), key=agg.get), dmg, b))
    if not keep:
        return []
    with torch.no_grad():
        si = m["sp"](images=img, input_boxes=[[k[2] for k in keep]], return_tensors="pt")
        so = m["sm"](**si, multimask_output=False)
    masks = m["sp"].post_process_masks(so.pred_masks, si["original_sizes"])[0]
    return [(c, masks[i, 0].numpy().astype(bool), s, b) for i, (c, s, b) in enumerate(keep)]


def upright_turns(R_wc):
    """Quarter turns (np.rot90 k) that bring the world's up direction to the top of the image."""
    u = R_wc.T @ np.array([0.0, 1.0, 0.0])            # world up in camera axes (x right, y down)
    d, best = (u[0], u[1]), (0, -np.inf)
    for k in range(4):
        if -d[1] > best[1]:
            best = (k, -d[1])
        d = (d[1], -d[0])                              # np.rot90 turns a direction (x, y) to (y, -x)
    return best[0]


def detect_frames(cap, poses, frames, log=print):
    """Run detect_image on (frame number, BGR) pairs. Returns {frame number: detections} and,
    per frame, the class-coded mask images used for projection."""
    row_of = {int(f): i for i, f in enumerate(cap.frames)}
    out = {}
    for f, bgr in frames:
        k = upright_turns(poses[row_of[int(f)]][:3, :3])
        rgb = np.ascontiguousarray(np.rot90(bgr[..., ::-1], k))
        dets = [(c, np.rot90(mk, -k), s, b) for c, mk, s, b in detect_image(rgb)]
        out[int(f)] = (bgr.shape[:2], dets)
        if dets:
            log(f"[damage] frame {f}: " + ", ".join(f"{c} {s:.2f}" for c, _, s, _ in dets))
    return out


def pick_frames(cap, every_s=1.5, have_depth=None):
    """Capture rows about `every_s` seconds apart (only rows with depth, if given)."""
    rows, last = [], -np.inf
    for i, t in enumerate(cap.timestamps):
        if (have_depth is None or i in have_depth) and t - last >= every_s:
            rows.append(i)
            last = t
    return rows


def video_frames(cap, rows, video):
    """(frame number, BGR) pairs for capture rows, decoded from the capture's video."""
    from .mono import iter_frames
    return list(iter_frames(video, sorted(int(cap.frames[i]) for i in rows)))


def photo_frames(pc, paths):
    """(row, BGR) pairs for the photo tier: each photo at full resolution, cut to the field of view
    of its depth map (MapAnything crops and shrinks its input; pc.K is at depth resolution)."""
    import cv2
    from .recon import exif_intrinsics
    out = []
    for i, p in enumerate(paths):
        K0, rgb, _ = exif_intrinsics(p)
        Kd, (h, w) = pc.K[i], pc.depths[i].shape
        sx, sy = K0[0, 0] / Kd[0, 0], K0[1, 1] / Kd[1, 1]   # full-resolution pixels per depth pixel
        # depth pixel ud = (u0 - c0) / sx + cd, so the depth map's view is the full-resolution crop
        # starting at c0 - sx cd, sx w pixels wide (the same in y)
        M = np.array([[1.0, 0, sx * Kd[0, 2] - K0[0, 2]], [0, 1.0, sy * Kd[1, 2] - K0[1, 2]]], np.float32)
        out.append((i, cv2.warpAffine(np.ascontiguousarray(rgb[..., ::-1]), M, (int(round(w * sx)), int(round(h * sy))),
                                      flags=cv2.INTER_LINEAR, borderValue=(0, 0, 0))))
    return out


def run(cap, poses, plan, frames, wall_band=None, wet=None, log=print, min_area_m2=0.01):
    """Learned damage for any tier. `frames`: list of (frame number, BGR image) whose depth the
    capture holds (LiDAR or video keyframes, or photo_frames for the photos). The plan needs an
    "alignment" (yaw and floor height of the capture's world). Returns (damage record, surfaces)."""
    import cv2
    from . import damage
    t0 = __import__("time").time()
    frames = list(frames)
    found = detect_frames(cap, poses, frames, log=log)
    band = damage.WALL_BAND if wall_band is None else wall_band
    ids = [i for i, f in enumerate(cap.frames) if int(f) in found]
    n_pass = 1 + any(CHANNEL[c][0] == 1 for _, dets in found.values() for c, *_ in dets)
    fracs = {}
    for ps in range(n_pass):
        coded = []
        for f, (shape, dets) in found.items():
            img = np.zeros(shape + (3,), np.uint8)              # RGB: one channel per class
            for c, mk, _, _ in dets:
                p, ch = CHANNEL[c]
                if p == ps:
                    if c == "crack":   # a hairline mask is thinner than a depth pixel: widen it to a crack zone
                        k = max(3, int(CRACK_WIDEN * max(shape))) | 1
                        mk = cv2.dilate(mk.astype(np.uint8), np.ones((k, k), np.uint8)).astype(bool)
                    img[mk, ch] = 255
            coded.append((f, np.ascontiguousarray(img[..., ::-1])))
        surfs = damage.unwrap(cap, poses, plan, ids, None, log=lambda *a: None, wall_band=band, frames=coded)
        for s in surfs:
            ok = s.count >= 2
            for c, (p, ch) in CHANNEL.items():
                if p == ps:
                    fr = np.where(ok, s.rgb_sum[..., ch] / 255.0 / np.maximum(s.count, 1), 0.0)
                    fracs[(s.sid, c)] = fr
    # one surface texture set for the regions (its counts say what was seen)
    regs = []
    for s in surfs:
        if s.kind not in SURFACES:
            continue
        dets = []
        for c in CHANNEL:
            fr = fracs.get((s.sid, c))
            if fr is None:
                continue
            mk = (fr >= MIN_FRAC).astype(np.uint8)
            if s.kind == "wall":
                for a0, a1, _ in s.excluded:
                    mk[:, int(a0 / damage.CELL):int(a1 / damage.CELL) + 1] = 0
            n, lab = cv2.connectedComponents(cv2.morphologyEx(mk, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8)))
            for j in range(1, n):
                mj = lab == j
                if mj.sum() * damage.CELL ** 2 >= min_area_m2:
                    dets.append((c, mj, float(fr[mj].mean())))
        for r in damage.regions([s], detector=lambda tex, ok, kind, d=dets: d):
            r["id"] = f"DMG{len(regs) + 1}"
            regs.append(r)
    # the returned surfaces carry the real colours, for the texture images
    surfs = damage.unwrap(cap, poses, plan, ids, None, log=lambda *a: None, wall_band=band, frames=frames)
    fl = damage.flags(plan, regs, surfs, wet)
    sc = damage.scope(plan, regs, fl, surfs)
    wr = damage.wet_rooms(plan) if wet is None else wet
    n_det = sum(len(d) for _, d in found.values())
    log(f"[damage] learned detector: {n_det} detections in {len(found)} frames -> {len(regs)} regions, "
        f"{len(fl)} flags ({__import__('time').time() - t0:.0f} s)")
    return {"detector": "learned: grounding-dino-tiny + clip-vit-b32 + sam2.1-hiera-small",
            "keyframes": len(found), "frame_detections": n_det, "wet_rooms": wr, "regions": regs,
            "flags": fl, "scope": sc, "rules": damage.RULES}, surfs

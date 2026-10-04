"""Damage on measured surfaces: damage regions with metric extent, concealed-damage flags
(each naming the rule that fired) and scope line items keyed to surface ids.

1. Unwrap. Every surface of the plan gets an orthographic colour texture at 1 cm per cell:
   each wall face as (along the wall, height), each room's floor and ceiling as (x, y).
   Depth pixels from keyframes are placed in the plan with the drift-corrected poses,
   coloured from the matching RGB frame, and averaged into the surface they lie on
   (within 4 cm of it). Door and window spans are left out of the wall textures.
2. Detect. A detector marks damaged cells of a texture with a class. The built-in offline
   detector (`classical_stains`) finds water stains and dark spots as regions whose colour
   departs from the surface's own smoothed colour. A learned detector can be dropped in with
   the same signature.
3. Regions. Connected damaged cells become regions with an area (m^2, with interval), a
   bounding box in surface coordinates and the height range they span.
4. Flags. Concealed-damage rules (RULES) fire on region class, surface and neighbourhood.
5. Scope. Each region and flag becomes priced-unit line items keyed to its surface.
"""
from dataclasses import dataclass, field

import cv2
import numpy as np

CELL = 0.01            # texture resolution (m)
NEAR = 0.04            # a point belongs to a floor or ceiling within this distance
WALL_BAND = (-0.12, 0.04)   # a point belongs to a wall this far behind / in front of its outline
#                             (outlines can sit several cm inside the real wall, never far outside)
CLASSES = ("water_stain", "dark_spot", "crack", "hole")


# ----------------------------------------------------------------------------- surfaces
@dataclass
class Surface:
    sid: str                     # "R2-W3", "R2-ceiling", "R2-floor"
    room: str
    kind: str                    # wall | floor | ceiling
    shape: tuple                 # texture (rows, cols)
    origin: np.ndarray = None    # wall: start corner (plan xy); floor/ceiling: min corner
    axis: np.ndarray = None      # wall: unit vector along the wall
    normal: np.ndarray = None    # wall: unit vector into the room
    length: float = 0.0
    height: float = 0.0          # wall: texture height; floor/ceiling: plane height above floor
    excluded: list = field(default_factory=list)   # wall: [a0, a1] spans of doors/windows
    rgb_sum: np.ndarray = None
    count: np.ndarray = None

    def __post_init__(self):
        self.rgb_sum = np.zeros(self.shape + (3,), np.float32)
        self.count = np.zeros(self.shape, np.int32)

    def texture(self, min_count=2):
        """Mean colour (RGB uint8) and a mask of cells seen in at least `min_count` frames."""
        ok = self.count >= min_count
        tex = np.zeros(self.shape + (3,), np.uint8)
        tex[ok] = np.clip(self.rgb_sum[ok] / self.count[ok, None], 0, 255).astype(np.uint8)
        return tex, ok


def surfaces_from_plan(plan, wall_height_default=2.6):
    from shapely.geometry import Point, Polygon
    out = []
    for room in plan["rooms"]:
        poly = Polygon(room["polygon"])
        ch = room["ceiling_height_m"]
        H = ch["value"] if ch.get("value") else max(ch.get("lower_bound_m") or 0.0, wall_height_default)
        for w in room["walls"]:
            a, b = np.array(w["from"], float), np.array(w["to"], float)
            L = float(np.linalg.norm(b - a))
            if L < 0.3:
                continue
            d = (b - a) / L
            n = np.array([-d[1], d[0]])
            mid = (a + b) / 2
            if not poly.buffer(0.02).contains(Point(*(mid + 0.1 * n))):
                n = -n
            s = Surface(w["id"], room["id"], "wall", (int(H / CELL) + 1, int(L / CELL) + 1),
                        origin=a, axis=d, normal=n, length=L, height=H)
            for o in plan.get("openings", []):
                if room["id"] not in o.get("rooms", []):
                    continue
                oa, ob = opening_ends(o)
                # opening lies on this wall if both ends are within 15 cm of its line
                if max(abs(np.dot(oa - a, n)), abs(np.dot(ob - a, n))) < 0.15:
                    t0, t1 = sorted([np.dot(oa - a, d), np.dot(ob - a, d)])
                    if t1 > 0 and t0 < L:
                        s.excluded.append([max(0.0, t0 - 0.05), min(L, t1 + 0.05), o["type"]])
            out.append(s)
        x0, y0, x1, y1 = poly.bounds
        shp = (int((y1 - y0) / CELL) + 1, int((x1 - x0) / CELL) + 1)
        out.append(Surface(f"{room['id']}-floor", room["id"], "floor", shp, origin=np.array([x0, y0]), height=0.0))
        if ch.get("value"):
            out.append(Surface(f"{room['id']}-ceiling", room["id"], "ceiling", shp,
                               origin=np.array([x0, y0]), height=float(ch["value"])))
    return out


# ----------------------------------------------------------------------------- unwrapping
def opening_ends(o):
    """Plan endpoints of an opening (plan.json stores the wall line and a span along it)."""
    if o["axis"] == "u":                       # wall runs along x at y = line
        return np.array([o["from"], o["line"]]), np.array([o["to"], o["line"]])
    return np.array([o["line"], o["from"]]), np.array([o["line"], o["to"]])


def unwrap(cap, poses, plan, frame_ids, video, log=print, upsample=2):
    """Accumulate coloured depth points of `frame_ids` into every surface texture of `plan`."""
    from shapely import contains_xy
    from shapely.geometry import Polygon
    from .mono import iter_frames
    yaw, floor_y = plan["alignment"]["yaw_rad"], plan["alignment"]["floor_y_world"]
    cy_, sy_ = np.cos(yaw), np.sin(yaw)
    surfs = surfaces_from_plan(plan)
    walls = [s for s in surfs if s.kind == "wall"]
    flats = [s for s in surfs if s.kind != "wall"]
    room_poly = {r["id"]: Polygon(r["polygon"]) for r in plan["rooms"]}
    A = np.array([s.origin for s in walls]); D = np.array([s.axis for s in walls])
    N = np.array([s.normal for s in walls]); Ls = np.array([s.length for s in walls])
    Hs = np.array([s.height for s in walls])
    n_used = 0
    row_of = {int(cap.frames[i]): i for i in frame_ids}        # video frame number -> capture row
    for f, bgr in iter_frames(video, sorted(row_of)):
        i = row_of[f]
        d, c = cap.depth(i)
        H, W = d.shape
        if upsample > 1:
            d = cv2.resize(d, (W * upsample, H * upsample), interpolation=cv2.INTER_NEAREST)
            c = cv2.resize(c, (W * upsample, H * upsample), interpolation=cv2.INTER_NEAREST)
        K = cap.K_depth(i, d.shape)
        v, u = np.nonzero((c >= 2) & (d > 0.2) & (d < 4.0))
        z = d[v, u]
        pc = np.stack([(u - K[0, 2]) * z / K[0, 0], (v - K[1, 2]) * z / K[1, 1], z], 1)
        T = poses[i]
        P = pc @ T[:3, :3].T + T[:3, 3]
        xy = np.stack([P[:, 0] * cy_ + P[:, 2] * sy_, -P[:, 0] * sy_ + P[:, 2] * cy_], 1)
        h = P[:, 1] - floor_y
        sx, sy = bgr.shape[1] / d.shape[1], bgr.shape[0] / d.shape[0]
        col = bgr[np.minimum((v * sy + sy / 2).astype(int), bgr.shape[0] - 1),
                  np.minimum((u * sx + sx / 2).astype(int), bgr.shape[1] - 1)][:, ::-1].astype(np.float32)
        # walls: nearest wall face within NEAR, inside its extent and height
        if len(walls):
            rel = xy[:, None, :] - A[None]                         # (n, walls, 2)
            off = np.einsum("nwk,wk->nw", rel, N)
            along = np.einsum("nwk,wk->nw", rel, D)
            ok = ((off > WALL_BAND[0]) & (off < WALL_BAND[1]) & (along > 0) & (along < Ls[None])
                  & (h[:, None] > 0.02) & (h[:, None] < Hs[None]))
            hit = np.where(ok.any(1), np.argmin(np.where(ok, np.abs(off), 9.0), 1), -1)
            for k in np.unique(hit[hit >= 0]):
                m = hit == k
                s = walls[k]
                r = np.clip((h[m] / CELL).astype(int), 0, s.shape[0] - 1)
                cc = np.clip((along[m, k] / CELL).astype(int), 0, s.shape[1] - 1)
                np.add.at(s.rgb_sum, (r, cc), col[m])
                np.add.at(s.count, (r, cc), 1)
        for s in flats:
            m = np.abs(h - s.height) < (0.03 if s.kind == "floor" else NEAR)
            if not m.any():
                continue
            m[m] = contains_xy(room_poly[s.room], xy[m, 0], xy[m, 1])
            r = np.clip(((xy[m, 1] - s.origin[1]) / CELL).astype(int), 0, s.shape[0] - 1)
            cc = np.clip(((xy[m, 0] - s.origin[0]) / CELL).astype(int), 0, s.shape[1] - 1)
            np.add.at(s.rgb_sum, (r, cc), col[m])
            np.add.at(s.count, (r, cc), 1)
        n_used += 1
    log(f"[damage] unwrapped {n_used} keyframes onto {len(surfs)} surfaces")
    return surfs


# ----------------------------------------------------------------------------- detection
def classical_stains(tex, valid, kind, min_area_m2=0.01, dL=10.0, db=4.0, dL_spot=18.0, dE=20.0, edge_m=0.05):
    """Offline baseline detector on a surface texture.

    A cell is suspicious when it is darker than the surface's own smoothed colour (Lab L* lower
    by `dL`) and either browner/yellower (b* higher by `db`: water stain) or not (dark_spot:
    possible mould or soiling). Components must be at least `min_area_m2`, at most 40% of the
    seen surface, and irregular (straight-edged rectangles are frames, switch plates, furniture).
    Cells within `edge_m` of the edge of what was seen are ignored: there the texture mixes in
    neighbouring surfaces and shadows of objects standing on or against it.
    Returns a list of (class, mask, score).
    """
    if valid.sum() < 2000:
        return []
    e = int(edge_m / CELL) * 2 + 1
    core = cv2.erode(valid.astype(np.uint8), np.ones((e, e), np.uint8)).astype(bool)
    lab = cv2.cvtColor(tex, cv2.COLOR_RGB2LAB).astype(np.float32)
    lab[..., 0] *= 100.0 / 255.0
    lab[..., 1:] -= 128.0
    w = valid.astype(np.float32)
    bg = np.zeros_like(lab)
    k = (0, 0)
    sig = 0.15 / CELL                            # 15 cm background scale
    wb = cv2.GaussianBlur(w, k, sig)
    for ch in range(3):
        bg[..., ch] = cv2.GaussianBlur(lab[..., ch] * w, k, sig) / np.maximum(wb, 1e-3)
    diff = lab - bg
    dark = (diff[..., 0] < -dL) & core & (wb > 0.5)
    brown = diff[..., 2] > db
    spot = (diff[..., 0] < -dL_spot) & (np.linalg.norm(diff, axis=-1) > dE)
    out = []
    for cls, m in (("water_stain", dark & brown), ("dark_spot", dark & ~brown & spot)):
        m = cv2.morphologyEx(m.astype(np.uint8), cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
        m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
        n, lab_cc, stats, _ = cv2.connectedComponentsWithStats(m, connectivity=8)
        for j in range(1, n):
            area = stats[j, cv2.CC_STAT_AREA] * CELL * CELL
            if area < min_area_m2 or area > 0.4 * valid.sum() * CELL * CELL:
                continue
            mj = lab_cc == j
            cnts, _ = cv2.findContours(mj.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            (_, _), (rw, rh), _ = cv2.minAreaRect(max(cnts, key=cv2.contourArea))
            rect_fill = stats[j, cv2.CC_STAT_AREA] / max(rw * rh, 1.0)
            if rect_fill > 0.85:                 # crisp rectangle: an object, not a stain
                continue
            score = float(np.clip(-diff[..., 0][mj].mean() / 30.0, 0, 1))
            out.append((cls, mj, score))
    return out


def regions(surfs, detector=classical_stains, sigma_face=0.01):
    """Run `detector` on every surface texture; return damage regions with metric extent."""
    out = []
    for s in surfs:
        tex, ok = s.texture()
        if s.kind == "wall":
            for a0, a1, _ in s.excluded:
                ok[:, int(a0 / CELL):int(a1 / CELL) + 1] = False
        for cls, m, score in detector(tex, ok, s.kind):
            ys, xs = np.nonzero(m)
            cnts, _ = cv2.findContours(m.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            perim = sum(cv2.arcLength(c, True) for c in cnts) * CELL
            area = m.sum() * CELL * CELL
            sig = perim * np.hypot(CELL, sigma_face)      # boundary placement uncertainty
            reg = {"id": f"DMG{len(out) + 1}", "class": cls, "surface": s.sid, "room": s.room,
                   "surface_kind": s.kind, "score": round(score, 2),
                   "area_m2": {"value": round(area, 4), "sigma": round(sig, 4),
                               "ci95": [round(max(0.0, area - 1.96 * sig), 4), round(area + 1.96 * sig, 4)]},
                   "bbox_m": [round(xs.min() * CELL, 3), round(ys.min() * CELL, 3),
                              round((xs.max() + 1) * CELL, 3), round((ys.max() + 1) * CELL, 3)]}
            if s.kind == "wall":                          # bbox = [along0, h0, along1, h1]
                reg["height_range_m"] = [reg["bbox_m"][1], reg["bbox_m"][3]]
                reg["along_range_m"] = [reg["bbox_m"][0], reg["bbox_m"][2]]
            else:                                         # bbox in plan coordinates
                x0, y0 = s.origin
                reg["plan_bbox_m"] = [round(x0 + reg["bbox_m"][0], 3), round(y0 + reg["bbox_m"][1], 3),
                                      round(x0 + reg["bbox_m"][2], 3), round(y0 + reg["bbox_m"][3], 3)]
            out.append(reg)
    return out


# ----------------------------------------------------------------------------- rules
def wet_rooms(plan, max_area=4.5, override=None):
    """Rooms assumed wet (bathroom, toilet). Small rooms by default; `override` = list of ids."""
    if override is not None:
        return {r: "given" for r in override}
    return {r["id"]: f"assumed: floor area {r['floor_area_m2']['value']:.1f} m2 <= {max_area}"
            for r in plan["rooms"] if r["floor_area_m2"]["value"] <= max_area}


RULES = {
    "CD1-ceiling-stain": "Water stain on a ceiling: possible leak from the roof, plumbing or the unit above.",
    "CD2-wall-shared-with-wet-room": "Stain on a wall shared with a wet room: possible concealed plumbing or waterproofing leak inside the wall.",
    "CD3-stain-at-floor-junction": "Stain reaching within 15 cm of the floor: possible rising damp or a leak behind the skirting.",
    "CD4-stain-below-window": "Stain on the wall below a window: possible window seal or sill leak.",
    "CD5-floor-stain-at-wet-room-door": "Floor stain within 0.6 m of a door into a wet room: water escaping the wet room.",
}


def flags(plan, regs, surfs, wet=None):
    wet = wet_rooms(plan) if wet is None else wet
    by_id = {s.sid: s for s in surfs}
    walls_of = {r["id"]: r["walls"] for r in plan["rooms"]}
    out = []

    def add(rule, reg, evidence):
        out.append({"rule": rule, "description": RULES[rule], "regions": [reg["id"]], "surface": reg["surface"],
                    "evidence": evidence})

    for g in regs:
        stain = g["class"] in ("water_stain", "dark_spot")
        s = by_id[g["surface"]]
        if not stain:
            continue
        if s.kind == "ceiling" and g["class"] == "water_stain":
            add("CD1-ceiling-stain", g, f"{g['area_m2']['value']:.2f} m2 on {s.sid}")
        if s.kind == "wall":
            if g["height_range_m"][0] <= 0.15:
                add("CD3-stain-at-floor-junction", g, f"region bottom {g['height_range_m'][0]:.2f} m above floor")
            for a0, a1, typ in s.excluded:
                if typ == "window" and g["along_range_m"][0] < a1 and g["along_range_m"][1] > a0 and g["height_range_m"][1] < 1.0:
                    add("CD4-stain-below-window", g, f"window spans {a0:.2f}-{a1:.2f} m along {s.sid}")
            for rid, why in wet.items():
                if rid == s.room:
                    continue
                for w in walls_of[rid]:
                    a, b = np.array(w["from"], float), np.array(w["to"], float)
                    # parallel wall within 35 cm (the other face of the same partition), overlapping
                    if abs(np.dot(a - s.origin, s.normal)) < 0.35 and abs(np.dot(b - s.origin, s.normal)) < 0.35:
                        t = sorted([np.dot(a - s.origin, s.axis), np.dot(b - s.origin, s.axis)])
                        if t[0] < g["along_range_m"][1] and t[1] > g["along_range_m"][0]:
                            add("CD2-wall-shared-with-wet-room", g, f"other face is {w['id']} of wet room {rid} ({why})")
                            break
        if s.kind == "floor":
            x0, y0, x1, y1 = g["plan_bbox_m"]
            for o in plan.get("openings", []):
                if o["type"] != "door" or not (set(o.get("rooms", [])) & set(wet)) or s.room in wet:
                    continue
                c = sum(opening_ends(o)) / 2
                dx = max(x0 - c[0], 0, c[0] - x1)
                dy = max(y0 - c[1], 0, c[1] - y1)
                if np.hypot(dx, dy) < 0.6:
                    add("CD5-floor-stain-at-wet-room-door", g, f"{np.hypot(dx, dy):.2f} m from door {o['id']}")
    return out


# ----------------------------------------------------------------------------- scope
def scope(plan, regs, fl, surfs):
    """Line items keyed to surfaces. Quantities carry the interval of what they are derived from."""
    by_id = {s.sid: s for s in surfs}
    face_area = {}
    for r in plan["rooms"]:
        for w in r["walls"]:
            s = by_id.get(w["id"])
            if s is not None:
                open_m = sum(a1 - a0 for a0, a1, _ in s.excluded)
                face_area[w["id"]] = max(0.0, (s.length - open_m) * s.height)
        face_area[f"{r['id']}-ceiling"] = r["floor_area_m2"]["value"]
        face_area[f"{r['id']}-floor"] = r["floor_area_m2"]["value"]
    items = []

    def item(surface, code, desc, qty, unit, sigma, because):
        items.append({"id": f"S{len(items) + 1}", "surface": surface, "code": code, "description": desc,
                      "quantity": round(qty, 3), "unit": unit, "sigma": round(sigma, 3), "because": because})

    done = set()
    for g in regs:
        sid, A = g["surface"], g["area_m2"]
        if g["class"] in ("water_stain", "dark_spot") and g["surface_kind"] in ("wall", "ceiling"):
            if g["class"] == "dark_spot":
                item(sid, "MOLD-TREAT", "Biocide wash of the affected area plus a 0.3 m margin",
                     A["value"] + 0.3 * 4 * np.sqrt(A["value"]), "m2", A["sigma"], [g["id"]])
            if (sid, "PAINT") not in done:     # repaint the whole face so the finish matches
                item(sid, "STAIN-PRIME", "Stain-blocking primer on the affected area", A["value"], "m2", A["sigma"], [g["id"]])
                item(sid, "PAINT", f"Repaint {sid} (whole face for an even finish)", face_area.get(sid, A["value"]),
                     "m2", 0.02 * face_area.get(sid, A["value"]), [g["id"]])
                done.add((sid, "PAINT"))
        elif g["class"] == "crack":
            L = max(g["bbox_m"][2] - g["bbox_m"][0], g["bbox_m"][3] - g["bbox_m"][1])
            item(sid, "CRACK-FILL", "Rake out and fill crack", L, "m", 0.02, [g["id"]])
        elif g["class"] == "hole":
            item(sid, "PATCH", "Patch and make good", 1, "ea", 0.0, [g["id"]])
        elif g["surface_kind"] == "floor":
            item(sid, "FLOOR-CLEAN", "Clean or treat stained flooring", A["value"], "m2", A["sigma"], [g["id"]])
    for f in fl:
        item(f["surface"], "INSPECT-" + f["rule"].split("-")[0], f"Moisture readings and inspection: {f['description']}",
             1, "ea", 0.0, f["regions"] + [f["rule"]])
    return items


def run(cap, poses, plan, video, frame_step=15, frames=None, detector=classical_stains, wet=None, log=print):
    frame_ids = list(range(0, len(cap), frame_step)) if frames is None else list(frames)
    surfs = unwrap(cap, poses, plan, frame_ids, video, log=log)
    regs = regions(surfs, detector)
    fl = flags(plan, regs, surfs, wet)
    sc = scope(plan, regs, fl, surfs)
    wr = wet_rooms(plan) if wet is None else wet
    log(f"[damage] {len(regs)} regions, {len(fl)} concealed-damage flags, {len(sc)} scope items")
    return {"detector": getattr(detector, "__name__", str(detector)), "keyframes": len(frame_ids),
            "wet_rooms": wr, "regions": regs, "flags": fl, "scope": sc, "rules": RULES}, surfs

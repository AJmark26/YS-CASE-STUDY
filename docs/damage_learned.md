# Learned damage detector

`ysplan/damage_learned.py`, chosen with `--damage-detector learned`. It is the default on the
video and photo tiers, where the classical detector cannot run (it needs LiDAR depth).

## Method

Damage is found in the camera frames, not in the unwrapped textures, then mapped onto the plan.

1. **Boxes.** Grounding DINO (`IDEA-Research/grounding-dino-tiny`, Apache-2.0) proposes boxes for
   "stain. crack. mold. damage." (box score 0.25, text score 0.20, shortest side 800 px). It
   finds damaged areas but scores one box alike for every phrase, so it does not name them.
2. **Damage or not, and which.** CLIP (`openai/clip-vit-base-patch32`, MIT) compares a square
   crop around each box with damage texts (water stain, mould, crack, hole) and 25 texts for
   clean things (plain wall, ceiling, floor tiles, furniture, curtain, shadow, marble, skirting
   board...). A box is kept when the damage texts together reach 0.8 probability, and named after
   the damage class whose texts score highest. The crop is square because CLIP keeps only a
   centred square of its input: a tall crack box lost the crack.
3. **Masks.** SAM 2 (`facebook/sam2.1-hiera-small`, Apache-2.0) turns each kept box into a mask.
   Crack masks are widened by about 2 cm on each side, since a hairline is thinner than a depth
   pixel.
4. **Onto the plan.** Each frame's masks go through the frame's depth and pose onto the plan's
   walls and ceilings, at 1 cm cells, exactly as the colour textures do. A cell is damaged when
   at least a third of the depth points that landed on it came from a damage mask. This vote
   drops detections on objects (a basket or a curtain fold lies on no surface) and detections
   that one view makes and the other views of the same spot don't. Floors are left out: on the
   apartment capture all four floor detections were a mat, a cable and marks on the tiles.
5. Regions, concealed-damage flags and scope then come from `damage.py` unchanged, so the record
   in `plan.json` has the same form as the classical detector's.

Frames are turned upright with the pose first, since the models were trained on upright photos.
LiDAR uses a frame every 1.5 s; video uses every second MapAnything keyframe (about one per
1.3 s) and a wider wall band (0.5 m behind the outline, since video outlines sit inside the
blurred walls); photos use each photo, cut to the view of its depth map.

Setup: `pip install -r requirements-learned.txt` and `scripts/fetch_learned.sh` (about 1.5 GB for
these three models). None of the models was trained on building damage.

## How it was tuned and checked

| Check | Data | Result |
|---|---|---|
| Photos of real damage | 11 indoor photos from Wikimedia Commons (`scripts/damage_learned_eval.py`) | damage found in 10 of 11, the right kind named in 7 (02, 04-07, 09, 11); 03 missed |
| Clean walls, LiDAR | bedroom capture, 25 frames | 2 frame detections, 0 regions |
| Clean walls, LiDAR | apartment capture, 77 frames | 4 frame detections, all on the floor; 0 regions |
| Clean walls, video | bedroom capture, 22 keyframes | 1 frame detection, 0 regions |
| Real damage laid on, LiDAR | bedroom capture | mould found (area -34%); stain: only one fleck detected, below the 0.01 m² minimum; crack: in 1 frame only, not detected |
| Real damage laid on, LiDAR | apartment capture | all 3 found: mould +4%, crack +35%, stain found but named a crack, -80% |
| Real damage laid on, video | bedroom capture | mould found (area -2%); stain detected in one keyframe but not kept; crack missed |
| Clean walls and real damage laid on, photos | apartment photo set (`doorways_1a8384c3f6`), 23 photos | clean: 0 detections, 0 regions; laid on: mould found (area -11%); the stain was detected in a doorway photo of an unstitched room and lost; the crack was in one photo only, not detected |

The CLIP texts, threshold and crop were tuned on the boxes from the 11 photos, the bedroom
capture's frames and the apartment capture's frames. The "clean things" texts for floor tiles,
marble, wall panel seams, skirting boards, cables and door frames were added after looking at the
apartment's false alarms, so the apartment is not held out. The third capture
(single_scan_with_ceiling) was kept out of all tuning.

## What it finds in the samples

The bedroom capture's bathroom has a real hairline crack above the cistern (frame 1327). The
detector names it a crack in that frame (CLIP 0.90), but no other frame sees it closely enough,
so the multi-view vote drops it and the plan reports no region.

## Limits

- These are small tests: three laid-on items per capture, and the 11 photos were also used for
  tuning, so "10 of 11" is not a held-out score.
- Damage that only one frame sees closely is dropped by the vote (the bathroom crack), and
  damage that only one frame sees at all is easily missed. Denser frames should help, at a cost
  in run time; not tested.
- Grounding DINO often boxes one fleck of a large stain, so the region covers part of it (the
  apartment's laid-on stain came out 80% small) or nothing, if the fleck is under 0.01 m².
- Stains and cracks are often confused; mould is found most reliably.
- The flat painted blobs of `scripts/damage_synthetic.py` are not detected; the classical
  detector remains the one that test measures.
- Photo-tier rooms are 30 to 85% short (tier-results), and damage seen from a room that is not
  stitched, or on walls outside a room's outline, is lost.
- Run time on 4 CPU cores, detection and mapping together: bedroom LiDAR 25 frames 165 s,
  apartment LiDAR 77 frames 520 s, bedroom video 22 keyframes 140 to 320 s, apartment photo set
  23 photos 220 s.

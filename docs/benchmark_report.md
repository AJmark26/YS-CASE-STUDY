# Benchmark report

All numbers come from `bench/` and regenerate with the commands under "Reproduce every
number" in the README. Code: commit `d340327` or later on `main`; the plans it scores are
identical to those of `9414ce7` (rerun and compared polygon by polygon).

## What could be measured

The brief asks for a self-built benchmark set with laser or tape ground truth. None could be
built: no LiDAR iPhone, no laser measurer and no access to the rooms were available. The
benchmark therefore uses the three Stray Scanner captures supplied with the invitation, all
of one apartment:

| Capture | What it covers | Duration | Frames |
|---|---|---|---|
| `c00a170fe1` (single_room) | bedroom and bathroom, floor and walls | 37 s | 1,715 |
| `1a8384c3f6` (single_scan_floor_only) | whole apartment, floor and walls | 115 s | 5,251 |
| `c7d28f72c6` (single_scan_with_ceiling) | whole apartment, walls and ceilings | 215 s | 9,745 |

Every gate below is measured as consistency, not accuracy:

- **Repeatability** (two captures of the same room): the brief's own definition, so this one
  is a direct measurement of the gate.
- **Openings** and **ceilings**: the same quantity from two captures, or from two interleaved
  halves of one capture (alternating 2 s blocks), since only one capture sees the ceiling.
- **Video and photo tiers**: against the LiDAR plan of the same capture.
- **Walk-in rehearsal**: short cuts of the samples, made to look like an unseen capture (a
  new ARKit origin and heading, the older export format, a zip), scored against the plan of the
  full capture they were cut from.

A bias that both measurements share is invisible to all of these.

## Gates

| Gate (brief) | Measured as | Result | Status |
|---|---|---|---|
| Repeatability: same wall within 1 cm or 0.5% | 29 walls over 3 capture pairs | 37.9% pass; median difference 1.88 cm (1.1%) | fail |
| Opening widths within 2 cm on 85%, misses count | 27 openings over 3 capture pairs | 0% (3 found by both captures, none within 2 cm) | fail |
| Ceiling height: spread within 1 cm per room | 6 rooms, two halves of the ceiling capture | 5 of 6 rooms; median 0.57 cm, largest 1.68 cm | fail (one room) |
| Ceiling height within 1.5 cm of truth | | not measurable without ground truth | not measured |
| Drift accountability | ablation on the whole-apartment capture | loop misalignment 16.4 cm to 2.3 cm; footprint and rooms below | met |
| Video tier: walls within 3% | 19 walls over 2 captures, against LiDAR | 68% (13 of 19); median 2.27% (3.2 cm); 18 of 19 inside the 95% interval | fail |
| Photo tier: walls within 8%, stitched plan | 8 rooms over 2 captures, against LiDAR | floor area within 8% on 2 of 8 rooms, room sides on 3 of 16; not stitched | fail |
| Head-to-head against a consumer app | | not run: needs the rooms and a LiDAR iPhone | not met |

The ceiling result is "repeatable but bias unknown": the halves agree, and nothing here can
show whether both are off by the same amount.

## Repeatability (LiDAR tier)

Run A's rooms are measured in both captures' clouds (`scripts/benchmark.py`, `compare_runs`):
capture B is placed in A's frame by 90-degree turns plus translation, then each room is
re-aligned on its own walls.

| Capture pair | Registration fit | Walls | Within 1 cm or 0.5% | Median difference | Inside 95% interval |
|---|---|---|---|---|---|
| floor_only ~ with_ceiling | 0.81 | 18 | 22% | 2.26 cm (1.55%) | 100% (72% without drift term) |
| floor_only ~ single_room | 0.82 | 4 | 75% | 0.74 cm (0.44%) | 100% |
| with_ceiling ~ single_room | 0.99 | 7 | 57% | 0.84 cm (0.70%) | 100% |
| **All** | | **29** | **37.9%** | **1.88 cm (1.10%)** | **100% (82.8%)** |

The interval check uses the intervals `plan.json` reports, which include each run's residual
loop disagreement (2.3 to 2.4 cm on the whole-home captures, split over the two faces). With
it every difference falls inside; the median combined 1-sigma is 3.6 cm against a median
difference of 1.9 cm, so for measurement noise the intervals are on the wide side. Without
the drift term 82.8% fall inside. Neither covers the outline errors of the walk-in rehearsal
(below).

Five of the six largest differences (3 to 7 cm) are in the floor-only versus with-ceiling
pair, the pair whose room outlines differ most (round 1 traced this to outlines that sit on
furniture and curtains in one walk). 90% of walls differ by 4.3 cm or less.

This is lower than the 43.3% of 30 walls reported for fix-loop round 1. That number was
measured on the outputs of the time; two later commits that changed how rooms are carved
(ceiling rays prove the floor below them open, and the new floor detection) changed the room
outlines whose walls are compared. On outputs from just after those commits and before round
5 the same benchmark gives 34.6% of 26 walls. Round 5 does not touch walls.

## Ceiling height (LiDAR tier)

`scripts/ceiling_repeat.py` splits the ceiling capture into alternating 2 s blocks and
measures every room from each half alone.

| Room | Half A (m) | Half B (m) | Spread | Within 1 cm | Inside 95% interval |
|---|---|---|---|---|---|
| R1 | 2.463 | 2.472 | 0.86 cm | yes | yes |
| R2 | 2.463 | 2.468 | 0.50 cm | yes | yes |
| R3 | 3.076 | 3.059 | 1.68 cm | no | yes |
| R4 | 3.093 | 3.090 | 0.22 cm | yes | yes |
| R5 | 2.356 | 2.362 | 0.63 cm | yes | yes |
| R6 | 3.064 | 3.069 | 0.51 cm | yes | yes |

The two captures without ceiling views report `not_observed` with a lower bound rather than a
number.

## Openings (LiDAR tier)

`compare_openings` places capture B in A's frame and keeps the openings whose centre lies in
the part of the home both captures cover; each is matched to the other capture's nearest
opening on a parallel wall within 35 cm. Misses count as failures.

| Capture pair | Openings | Found by both | Within 2 cm |
|---|---|---|---|
| floor_only ~ with_ceiling | 15 | 2 | 0 |
| floor_only ~ single_room | 5 | 1 | 0 |
| with_ceiling ~ single_room | 7 | 0 | 0 |

The three matches pair different objects (widths 20 cm to 1.4 m apart). Fix-loop round 5
(`docs/fix_loop.md`) has the analysis: of 24 openings found by one capture only, 10 are seen as
gaps by the other capture but rejected by its jamb or depth checks (the diagnostic does not
record which),
8 lie on wall lines the other capture does not search, 4 are solid wall in the other capture
(a closed door or a phantom) and 2 were never crossed by its rays.

## Drift ablation

Whole-apartment capture `1a8384c3f6`, with and without drift correction (`--no-drift`):

| | Drift correction on | Raw ARKit poses |
|---|---|---|
| Mean disagreement at the 8 accepted loop closures | 2.3 cm (16.4 cm before correction) | not corrected |
| Footprint | 51.1 ± 1.1 m² | 47.0 ± 0.5 m² |
| Rooms | 6 | 5 |
| Openings | 9 | 14 |
| Geometry time | 78.5 s | 58.5 s |

Without correction the bedroom and the small room beside it merge into one (12.6 m² against
8.4 + 2.7 m² with correction), the hall shrinks from 16.8 to 13.4 m², and 14 openings are
reported instead of 9 (`plan.png` of both runs). The capture also has an ARKit relocalisation
jump at frame 5,199 of 5,251; with correction on, the 52 frames after it are dropped.

**Sensitivity to where the chunks are cut.** Drift correction cuts the walk into 180-frame
chunks. A change meant to keep them the same (4.0 s from the measured frame rate, so 181 to
184 frames) moved every chunk boundary by a few frames, and that alone changed the result
(`bench/drift_chunk_sensitivity.json`):

| Floor-only capture | 180-frame chunks (shipped) | 4.0 s chunks |
|---|---|---|
| Loop closures accepted | 8 of 9 | 7 of 10 |
| Loop disagreement, before / after correction | 16.4 / 2.3 cm | 11.2 / 4.8 cm |
| Largest pose correction | 51 cm | 20 cm |
| Footprint | 51.1 m² | 45.7 m² |
| Wall repeatability (all pairs) | 37.9%, median 1.88 cm | 37.9%, median 1.86 cm |
| Ceiling split within 1 cm | 5 of 6 rooms | 4 of 6 rooms |

The shipped setting goes back to 180 frames, because every number in this report was measured
with it and the change was meant to keep it. The 4.0 s results were seen before going back, so
this is not an independent choice between the two. The point is the size of the swing: which loop
closures pass the fitness and RMSE thresholds depends on where a chunk starts, and a 10%
change in footprint follows. Overlapping chunks, or solving with several chunk offsets and
keeping the most consistent solution, would make this stable; neither is built.

## Walk-in rehearsal

Four captures cut from the samples with `scripts/make_test_capture.py`, run cold with one
command, scored by `scripts/walkin_check.py` against the full capture's plan.

| Test | Cut from | What changed | Rooms found | Room areas (test vs full) | Walls compared | Median wall difference | Ceilings |
|---|---|---|---|---|---|---|---|
| t1_bedroom | single_room, 16 to 37 s | heading turned 37°, origin moved | 1 | 12.0 vs 14.1 m² | 2 | 12.5 cm | not seen in either |
| t2_three_rooms | with_ceiling, 100 to 141 s | older export format, zip | 3 | 17.1 vs 7.6, 8.6 vs 4.3, 4.6 vs 5.9 m² | 1 | 4.4 cm | 2.459 vs 2.465, 3.062 vs 3.069, 3.069 vs 3.065 m |
| t3_bedroom2 | floor_only, 35 to 54 s | heading turned 200° | 1 | 15.9 vs 11.1 m² | 6 | 2.6 cm (3 of 6 within 2 cm) | not seen in either |
| t4_ceiling_only | with_ceiling, 140 to 172 s | older export format; the phone looks up | 3 | floor not seen, reported as such | 1 | 135 cm | not reported (floor not seen) |

What this shows:

- The loader and the frame handling survive an unknown device setup: all four ran with no
  settings, and the ceilings in t2 agree with the full capture to 0.4 to 0.7 cm.
- t4 found the failure that led to the floor-detection fix: before it, a capture that only
  looks up put the floor 1.75 m too high with nothing in the output saying so. Now the plan says
  the floor was not seen and reports no heights.
- t4's 135 cm wall is a room-split failure, not a measuring error. With the phone pointed up,
  5.7% of the fused points lie below 0.8 m, against 33.6% in the full capture, so the floor
  rays that prove space open and the low wall points that separate rooms are mostly missing.
  Rays that end on the ceiling carve across partitions seen only near the top, and t4's R1
  (10.1 m²) takes in almost all of the full capture's R4 (5.4 m²; IoU 0.54 is about
  5.4/10.1) plus the space beside it, so its 3.44 m wall is compared with a 2.09 m one. A
  plan whose floor was not seen now says in `capture.notes` and on `plan.png` that its room
  outlines are unreliable, not only its heights.
- Room splitting is the weak point on short captures. Where the wall beside a doorway was not
  scanned, two rooms merge (t2 R1: 17.1 against 7.6 m²), and where a short walk sees less of a
  room, its outline takes a different jog (t1, t3: walls 12 cm off with 1 to 2 cm intervals, so
  the intervals are not calibrated for this error).

## Timing

One run at a time, 4 CPU cores (Xeon 2.8 GHz), no GPU; `timing_s` in each `plan.json`.

| Capture | Walk | Geometry (plan, rooms, openings) | Whole run with damage |
|---|---|---|---|
| t3 bedroom cut | 19 s | 12 s | 25 s |
| t1 bedroom cut | 21 s | 13 s | 29 s |
| t4 looking-up cut | 32 s | 19 s | 38 s |
| `c00a170fe1` single_room | 37 s | 23 s | 61 s |
| t2 three-room cut | 41 s | 27 s | 80 s |
| `1a8384c3f6` floor only | 115 s | 66 s (49 s without drift correction) | 5.9 min |
| `c7d28f72c6` with ceiling | 215 s | 123 s | 12.0 min |

The damage stage unwraps every 15th frame onto every surface at 1 cm and takes about 0.9 s
per keyframe on the whole-home captures, most of the run; `--no-damage` skips it. A one-room
capture finishes in well under 2 minutes.

## Tiers against LiDAR

Both lower tiers are scored against the LiDAR plan of the same capture, so they inherit its
errors: the numbers say how far a tier lands from what LiDAR saw. Their inputs are made from the
samples (video: the capture with its depth deleted; photo: stills cut from its video), so they
are not independent captures. The ceiling capture has no video or photo run (its video depth
would take about 40 minutes on CPU).

### Video tier (ARKit poses, MapAnything depth)

Walls are compared as in the repeatability check: each LiDAR room outline, measured in both
tiers' 3D points after a per-room alignment (`compare_runs`, `video_vs_lidar_*` in
`bench/benchmark.json`).

| Capture | Rooms, video (LiDAR) | Footprint, video vs LiDAR | Walls | Within 3% | Median difference | Inside 95% interval |
|---|---|---|---|---|---|---|
| `c00a170fe1` single_room | 1 (2) | 12.1 vs 21.0 m² | 4 | 2 | 2.75% (3.5 cm) | 4 of 4 |
| `1a8384c3f6` floor only | 7 (6) | 37.0 vs 51.1 m² | 15 | 11 | 2.27% (3.2 cm) | 14 of 15 |
| **All** | | | **19** | **13 (68%)** | **2.27% (3.2 cm)** | **18 of 19** |

- Where the video tier sees a wall, it measures it to a few centimetres: 15 of 19 walls within
  3.5%. The six misses include two 0.5 m walls off by 9 and 10 cm (18 and 20%) and a 1.05 m
  wall off by 8.7 cm.
- Its room outlines are the weak point. Rooms fragment or merge (one room for the bedroom and
  bathroom; seven for six), and the footprint comes out 28 to 42% short, most likely because
  predicted depth is too noisy for the ray carving to close rooms (inferred, not traced). Walls
  scored on the LiDAR outline do not show this, so the footprint is reported beside them.
- Intervals: the wall error model is the LiDAR one computed on the video points, multiplied by a
  tier factor of 5 (`measure.TIER_SCALE`). At 1 only 13 of 19 differences fall inside the combined
  interval; 5 is the smallest whole factor that covers 18 of 19. It is fitted on these same 19
  walls, so it is not an independent check. Median video wall 1-sigma: 5.4 cm.
- Ceilings are withheld at this tier: on the floor-only capture it reported a 2.07 m ceiling in
  a room whose LiDAR never saw above 1.86 m, and no sample has both a ceiling sweep and a video
  run to check it against.
- Without the learned models the tier falls back to monocular depth (Depth Anything V2 small,
  scaled by triangulating tracked points with the ARKit poses), which recovered no room on the
  21 s bedroom cut. A plain clip without poses runs MapAnything in chained windows and also
  recovers no room; it is marked experimental.

While the tier was being built, a 2D comparison (wall points flattened to the floor, one
alignment for the whole plan) gave 7 of 7 and 14 of 20 walls within 3% on the same captures.
The table above uses the stricter 3D, per-room method of the LiDAR repeatability check; on the
same outputs the 2D method gives 6 of 7 for `c00a170fe1`.

### Photo tier (2 to 8 stills per room, no poses)

Each room folder is scored on its own against the LiDAR room it was cut from, on floor area and
on the two sides of its bounding box (`scripts/eval_photo.py`, `bench/photo_vs_lidar_*.json`).
Wall-by-wall scoring found no wall to compare: once each photo room is placed in its LiDAR
room, every wall has an end the stills did not see.

| Room | Stills | Area, photo vs LiDAR | Area difference | Interval covers LiDAR | Sides, photo vs LiDAR |
|---|---|---|---|---|---|
| floor only R3 | 8 | 8.50 vs 8.41 m² | +1.0% | yes | 2.89 × 2.92 vs 2.89 × 3.12 m |
| floor only R6 | 4 | 2.71 vs 2.69 m² | +0.7% | yes | 1.60 × 1.69 vs 1.40 × 2.09 m |
| floor only R2 | 3 | 12.86 vs 11.07 m² | +16% | yes | 2.01 × 6.44 vs 3.83 × 3.95 m |
| floor only R4 | 3 | 4.80 vs 7.89 m² | -39% | no | 1.99 × 2.42 vs 2.97 × 3.24 m |
| single_room R1 | 7 | 6.80 vs 14.13 m² | -52% | no | 2.36 × 2.88 vs 2.94 × 7.84 m |
| floor only R5 | 1 | 1.49 vs 4.24 m² | -65% | no | 0.76 × 1.97 vs 2.00 × 2.44 m |
| floor only R1 | 3 | 5.54 vs 16.83 m² | -67% | no | 1.29 × 5.14 vs 4.85 × 4.99 m |
| single_room R2 | 2 | 1.33 vs 6.89 m² | -81% | no | 1.16 × 1.22 vs 2.92 × 3.07 m |

- Two rooms land within 1% on area; only R3 also has both sides within 8%. In the other six the
  stills see part of the room, and the room is measured as the part they saw, so it comes out
  short. The stills are frames of a walkthrough that rarely looks down, not photos taken to the
  protocol.
- The intervals carry the measurement terms and the scale spread between MoGe-2 and the
  multi-view reconstruction (5% of scale, doubled for area). They do not model unseen floor,
  which is the dominant error: 3 of 8 cover LiDAR. These are confident wrong answers, and the
  fix (report a room whose outline rests on unseen floor as a lower bound) is not built.
- Rooms are laid out side by side, not stitched, so there is no photo-tier whole-home plan.
- MapAnything and MoGe-2 take 18 s to 200 s per room on CPU (9 minutes for the six apartment
  rooms); the reconstruction is cached and the geometry then takes seconds.
- An earlier floor finder change (floor must cover 2 m² at least 1.1 m below the camera) shrank
  the apartment's photo rooms by 21 to 58%: their stills see under 0.3 m² of floor, so the floor
  was guessed and furniture sides counted as walls. The photo tier keeps the older finder (the
  densest low slab); its heights are not reported anyway.

# Phone capture to dimensioned floor plan: technical report

<!-- Numbers marked {{...}} are filled from bench/ when the final runs land. -->

## 1. What runs

`python -m ysplan <capture>` turns one phone capture into `plan.json` (rooms, walls, ceiling
heights, floor areas, openings, adjacency, damage regions, concealed-damage flags, scope
items, each measurement with a 95% interval; schema in `docs/plan.schema.json`) and
`plan.png`. The tier is picked from what the capture contains. Capture route: Route 2, the free
Stray Scanner app for LiDAR and the stock Camera app for video and photos, with a one-page
protocol (`docs/capture_protocol.md`).

**What the numbers below are.** No laser ground truth exists for the three supplied captures,
and no hardware was available to make any. Every accuracy number is therefore a consistency
number: two captures of the same room, two interleaved halves of one capture, a short cut of a
capture against the full one, or a lower tier against LiDAR. Each table says which. Consistency
is necessary for accuracy but not sufficient: a bias both measurements share is invisible to it.

## 2. Architecture (LiDAR tier)

| Stage | What it does | Why this way |
|---|---|---|
| Load | Stray Scanner folder or .zip; both export formats | The walk-in device and app version are unknown |
| Drift | 3 s chunks fused locally; loop edges by point-to-plane ICP restricted to yaw and translation; pose graph | ARKit gravity is reliable, heading and position drift are not; four degrees of freedom keep the problem well conditioned |
| Fuse | Confident depth (ARKit confidence 2) into 2 cm voxels with at least 3 hits | Removes flying pixels and most mirror and glass ghosts |
| Floor and axes | Floor = lowest surface covering 2 m² at least 1.1 m below the camera; Manhattan yaw from wall points | A floor guess that is silently wrong ruins every height; the walk-in rehearsal found exactly that |
| Rooms | 2D ray carving (rays that end on the floor or ceiling prove the space between is empty), doorways virtually closed, watershed | Free space is observed directly, not inferred from walls that furniture hides |
| Outlines | Rectilinear polygon over a cell complex of wall lines | Every edge lies on a measured wall line |
| Walls | Each room squared on its own; each face = median of its points within 5 cm; corners where faces meet | Fix loop round 1 |
| Ceilings | Robust ceiling level minus this room's own floor level | Fix loop round 3 |
| Openings | Gaps in solid wall along every wall line, proven open by rays; jambs per height slice | Fix loop round 5 |
| Damage | Surfaces unwrapped at 1 cm per pixel; pluggable detector; masks to m²; rules CD1 to CD5; scope keyed to surface ids | Metric extent comes from geometry, not from the detector |

## 3. Tiers and device matrix

{{tier design and device matrix from the photo and video work}}

## 4. Drift

Drift is corrected on every LiDAR and video run (`ysplan/drift.py`): the trajectory is cut into
3 s chunks (and at ARKit relocalisation jumps), overlapping non-adjacent chunks are registered
with yaw-and-translation ICP, and a pose graph spreads the loop errors. On the whole-home
capture, ablation with `--no-drift` (raw ARKit poses):

{{drift ablation table: loop misalignment before/after, footprint on/off, rooms on/off, wall sharpness}}

## 5. Error budget

Each measurement carries a 1-sigma built from the terms below (`ysplan/measure.py`,
`ysplan/openings.py`). Typical sizes are medians over the three sample captures.

{{error budget table}}

## 6. Calibration

A 95% interval should contain the truth 95% of the time. Without truth, the check is whether
the difference between two measurements of the same thing falls inside the combined interval
(1.96 times the two sigmas in quadrature).

{{calibration table}}

## 7. Benchmark summary

{{gates table: gate, measure, number, pass/fail, what kind of number}}

## 8. Fix loop

`docs/fix_loop.md` has the full declarations; `python scripts/fixloop.py` regenerates every
before and after from git tags.

{{fix loop summary table}}

## 9. Walk-in rehearsal and known failure modes

{{walk-in table and failure modes}}

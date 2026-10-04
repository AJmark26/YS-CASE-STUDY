# Benchmark report

All numbers come from `bench/` and regenerate with the commands under "Reproduce every
number" in the README. Code: commit `e89d462` or later on `main`.

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
| Video tier: walls within 3% | against LiDAR, same capture | {{video_gate}} | {{video_status}} |
| Photo tier: walls within 8%, stitched plan | against LiDAR, same capture | {{photo_gate}} | {{photo_status}} |
| Head-to-head against a consumer app | | not run: needs the rooms and a LiDAR iPhone | not met |

The ceiling result is "repeatable but bias unknown": the halves agree, and nothing here can
show whether both are off by the same amount.

## Repeatability (LiDAR tier)

Run A's rooms are measured in both captures' clouds (`scripts/benchmark.py`, `compare_runs`):
capture B is placed in A's frame by 90-degree turns plus translation, then each room is
re-aligned on its own walls.

| Capture pair | Registration fit | Walls | Within 1 cm or 0.5% | Median difference | Inside 95% interval |
|---|---|---|---|---|---|
| floor_only ~ with_ceiling | 0.81 | 18 | 22% | 2.26 cm (1.55%) | 72% |
| floor_only ~ single_room | 0.82 | 4 | 75% | 0.74 cm (0.44%) | 100% |
| with_ceiling ~ single_room | 0.99 | 7 | 57% | 0.84 cm (0.70%) | 100% |
| **All** | | **29** | **37.9%** | **1.88 cm (1.10%)** | **82.8%** |

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
- Room splitting is the weak point on short captures. Where the wall beside a doorway was not
  scanned, two rooms merge (t2 R1: 17.1 against 7.6 m²), and where a short walk sees less of a
  room, its outline takes a different jog (t1, t3: walls 12 cm off with 1 to 2 cm intervals, so
  the intervals are not calibrated for this error).

## Timing

{{timing table: capture duration, wall-clock including damage, on 4 CPU cores, no GPU}}

## Tiers against LiDAR

{{video and photo tier tables from the photo and video work}}

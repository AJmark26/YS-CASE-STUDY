# Fix loop

Every round names a gate and the failing number, a root cause with its evidence, the fix, and
the number before and after. Rounds that shipped have git tags `fixloop-<n>-before` and
`fixloop-<n>-after`; `python scripts/fixloop.py` checks both tags out and regenerates the
numbers with each tag's own code. Rejected rounds are kept with their numbers.

| Round | Gate | Before | After | Outcome |
|---|---|---|---|---|
| 1 | Wall repeatability (1 cm or 0.5%) | 37.9% of 29 walls | 43.3% of 30 walls | shipped, short of the gate |
| 2 | Wall repeatability | 43.3% | 12% to 30% for seven variants | rejected |
| 3 | Ceiling spread (1 cm) | 4 of 6 rooms | 5 of 6 rooms | shipped, short of the gate |
| 4 | Calibration of wall intervals | 86.7% inside 95% CI | 90% to 100%, but 2 to 10 times wider intervals | rejected |
| 5 | Opening widths (2 cm on 85%) | 0 of {{b5_n}} openings | 0 of {{a5_n}} openings | shipped, prediction badly wrong |

Rounds 1 to 3 were run before this file existed, and no numeric prediction was written down
for them before the fix shipped. Round 5 is the first one declared in advance: its prediction
was committed before any of its code.

## Round 1: rooms turned against the plan axes (shipped)

- **Gate and number.** Repeatability: the same wall in two captures within 1 cm or 0.5%.
  37.9% of 29 walls over 3 capture pairs.
- **Root cause.** Drift that loop closure could not remove leaves single rooms turned against
  the plan axes: the bathroom sits 2.4 degrees off in the whole-home capture and 0.3 degrees
  in the bedroom capture. On the global axes its 1.8 m walls smear over up to 7 cm, and each
  capture's face position depends on which part of the wall it saw.
- **Fix.** Square each room on its own (the rotation within 4 degrees that piles its wall points
  up most sharply) and measure it in that frame.
- **Result.** 37.9% to 43.3%; the bathroom pair 50% to 75%. The median difference over all
  pairs got worse (1.43 to 1.60 cm), driven by the floor-only versus ceiling pair, where
  outlines sit on furniture and curtains. Short of the gate because that second cause remains.

## Round 2: wider wall-face search (rejected)

Six ways of looking further for the wall face behind furniture (nearest, strongest, outermost
or most wall-like peak, over different reaches) and one that measures walls only above
furniture height (1.0 to 2.1 m) all lowered the pass rate, to between 12% and 30%. Behind most walls in this home there is a second surface within reach
(the other side of the partition, or the next room), so searching further finds the wrong one
more often than the right one. Numbers: `bench/fixloop/round2_face_search.json`.

## Round 3: ceiling from each room's own floor (shipped)

- **Gate and number.** Ceiling spread across two measurements of the same room within 1 cm.
  Only one sample capture looks at the ceiling, so the two measurements are two interleaved
  halves of it (alternating 2 s blocks). 4 of 6 rooms; R5 3.97 cm apart, outside its interval.
- **Root cause.** Two: heights were measured from one floor for the whole home although room
  floors sit up to 2 cm above or below it in both walks (a bias the split cannot see, because
  both halves share the floor); and the ceiling was the 1 cm bin with the most points, so R5,
  whose ceiling has two levels 4 cm apart, flipped between them depending on where the camera
  lingered.
- **Fix.** Ceiling and floor are each a robust level inside the room, the height is their
  difference, and the interval includes the ceiling's relief across the room.
- **Result.** 4 of 6 to 5 of 6 rooms within 1 cm; largest spread 3.97 to 1.68 cm; 6 of 6
  inside their intervals (was 5 of 6). Ceiling values move by up to 3.5 cm, mostly from the
  floor offsets. R3 still spreads 1.7 cm: it is 3.06 m high, the farthest ceiling from the
  phone, and its two halves see different parts of it.

## Round 4: widen wall intervals for competing surfaces (rejected)

The walk-in rehearsal (`scripts/walkin_check.py`) found walls 12 cm off with 1 to 2 cm
intervals. The idea: any second surface near a wall face (furniture, a curtain) is a place the
wall could be, so the interval should reach it. On the repeatability walls, searching both
sides of every face lifted coverage from 86.7% to 100% but raised the median combined sigma
from 1.6 to 16.4 cm. Searching only the room side, above 1.4 m, gave 90% coverage at 3.5 cm,
and still missed all three 12 cm walk-in errors (their sigmas stayed at 0.8 to 2.3 cm): those
errors come from the outline taking a different jog, not from a second surface at the face.

## Round 5: opening widths (shipped; prediction badly wrong)

- **Gate and number.** Opening widths within 2 cm on at least 85% of openings, with missed
  and phantom openings counted as failures. Measured with `compare_openings` in
  `scripts/benchmark.py` on the LiDAR outputs of the three sample captures. With no laser ground truth, the benchmark uses
  the same openings in two captures: over the three capture pairs, 37 openings fall in the
  area both captures cover. 6 are found by both captures, and none of those 6 agree within
  2 cm (differences of 5 to 30 cm). Pass rate 0 of 37. The reported intervals were about
  plus or minus 1.2 cm, so most of these differences fall outside them.
- **Root-cause hypothesis.**
  1. *Detection depends on the room split.* Openings are searched for only along each run's
     room outline edges. The two captures split the home into rooms differently (room overlap
     0.3 to 0.7), so a door that sits on an outline edge in one run is inside a merged room or
     on an open boundary in the other, and is never looked for. Evidence: 31 of 37 openings
     are found by one capture only, and most of them are doors (23 of 31).
  2. *Jambs come from whatever wall points sit near the gap.* The jamb is the last wall point
     within 8 cm of the carved gap, at any height from 0.2 to 1.9 m, so a chair or a box
     beside the door moves it, and a stretch of wall that was never scanned looks like part
     of the gap. Evidence: for the five openings matched between the floor-only and the
     ceiling capture, the wall-point profile along the wall has unscanned spans of 0.5 to 2 m
     next to the opening above 1.2 m in the floor-only capture (it has three times as many
     points below 1.2 m as between 1.2 and 1.9 m), and the widths differ by 5 to 30 cm.
  3. *The interval ignores both.* It is 0.6 cm plus the difference between the two rooms'
     views of the same door.
- **Fix to ship.** Look for openings along every wall line of the plan, not along room
  outlines, as gaps where the wall is missing and rays crossed the line. Put each jamb at the
  edge of solid wall evidence: 1 cm columns along the wall that have points over most of the
  0.3 to 1.9 m height range, so low clutter cannot form a jamb. Reject gaps where the wall
  beside them was not observed. Set the width interval from how sharply each jamb edge is
  defined, and widen it when one jamb is weak.
- **Prediction.** Matched openings from 6 to about 15 of 37; among matched ones, about half
  within 2 cm; pass rate from 0% to roughly 15 to 25% of 37. The gate (85%) will not be met
  on these captures: the floor-only capture rarely sees jambs above 1.2 m, and the captures
  still disagree on room coverage. Intervals should cover at least 80% of the matched
  differences (now 1 of 6).

### Round 5 result

| Measure (3 capture pairs) | Before | Predicted | After |
|---|---|---|---|
| Openings either capture reports in the area both cover | {{b5_n}} | | {{a5_n}} |
| Found by both captures | {{b5_m}} | about 15 | {{a5_m}} |
| Within 2 cm (the gate counts misses as failures) | 0% | 15 to 25% | 0% |
| Matched differences inside the 95% interval | {{b5_ci}} | 80% or more | {{a5_ci}} |

Regenerate with `python scripts/fixloop.py --rounds 5` (tags `fixloop-5-before`,
`fixloop-5-after`; tables in `bench/fixloop/round5.md`).

**Post-mortem.** The prediction was badly wrong: fewer openings are found by both captures, not
more, and the few that are matched are different objects (their widths differ by 20 cm to
1.4 m), so neither the pass rate nor the calibration moved. To see why, every opening that one
capture reports and the other misses was looked up in the other capture at the same place
(24 openings over the three pairs; `bench/fixloop/round5_misses.json`, a one-off diagnostic
computed on the pipeline's internal ray-carving grids, which `fixloop.py` does not regenerate):

| What the other capture shows there | Openings |
|---|---|
| A gap that rays crossed, rejected because the wall beside it is not solid at most heights | 10 |
| No searched stretch of wall line passes there | 8 |
| Solid wall (a door closed in one walk, or a phantom in the first) | 4 |
| No rays crossed the line (that side was not scanned) | 2 |

Hypothesis 1 (detection follows the room split) is right for 8 of 24: searching every outline
edge plus tall partitions still leaves wall lines that are neither in the other capture. The
fix's own jamb rule causes the largest group. Requiring solid wall over most of 0.3 to 1.9 m on
both sides of a gap is what keeps clutter out of the jambs (hypothesis 2), but in these walks
the wall beside a door is often seen only below 1.2 m or only on one side, so the other capture
sees the gap and drops it. The trade between clutter-proof jambs and detection was not in the
declaration, and it dominates on these captures.

**What shipped anyway, and why.** The gate number is what was declared, and it did not move.
The code still ships because it removes a failure the old detector had within a single
capture: of the openings the old detector reported on the three samples, {{b5_solid}} of {{b5_rep}} lie mostly
(over half their width) on wall that the same capture saw as solid from 0.3 to 1.9 m; with
the new detector it is {{a5_solid}} of {{a5_rep}}. The new detector looks for gaps in solid wall, so this check
partly restates its own rule and is not evidence of accuracy, but an opening drawn across
wall the capture itself saw is wrong whatever the method. A detected door's width also no
longer moves with clutter beside it: each jamb is measured at many heights and its spread
sets the interval. (`self_check` in `bench/fixloop/round5.json`.)

**Next step if this round were repeated.** Accept a jamb that is solid on one side only, or
solid over at least three height slices, and widen the interval for it, instead of dropping
the opening; and search every wall line of the fused cloud rather than those near room
outlines. Predicted effect: most of the 10 rejected gaps and some of the 8 unsearched ones
become matches; widths would still differ by more than 2 cm wherever one capture sees a jamb
only low down, so the gate would stay well short.

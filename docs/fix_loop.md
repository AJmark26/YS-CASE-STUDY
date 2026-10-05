# Fix loop

Every round names a gate and the failing number, a root cause with its evidence, the fix, and
the number before and after. For each shipped round, `python scripts/fixloop.py` checks out
the commit before the fix and the commit after it into temporary worktrees and regenerates the
numbers with each commit's own code. Rejected rounds are kept with their numbers.

Rounds 1 and 3 change only the measurement, so `fixloop.py` runs both commits' measurement
code on the current pipeline outputs. For round 1 that gives different numbers from the ones
measured when the round shipped, because later commits changed the room outlines whose walls
are compared; both are given below. Round 3 regenerates the same result it shipped with.

| Round | Commit before | Commit after (the fix) |
|---|---|---|
| 1 | `1c3350e` | `c0c8046` |
| 3 | `5eed22a` | `7b57f35` |
| 5 | `a79bf15` (declaration) | `e803d09` |

| Round | Gate | Before | After | Outcome |
|---|---|---|---|---|
| 1 | Wall repeatability (1 cm or 0.5%) | 35.7% of 28 walls (37.9% at the time) | 37.9% of 29 walls (43.3% at the time) | shipped, short of the gate |
| 2 | Wall repeatability | 43.3% (at the time) | 12% to 30% for seven variants | rejected |
| 3 | Ceiling spread (1 cm) | 4 of 6 rooms | 5 of 6 rooms | shipped, short of the gate |
| 4 | Calibration of wall intervals | 86.7% inside 95% CI | 90% to 100%, but 2 to 10 times wider intervals | rejected |
| 5 | Opening widths (2 cm on 85%) | 0 of 38 openings | 0 of 27 openings | shipped, prediction badly wrong |
| 6 | Walk-in room areas | t3 15.9 against 11.1 m² | no room cut | rejected: the captures' rays contradict it |

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
- **Result.** At the time: 37.9% to 43.3%; the bathroom pair 50% to 75%. The median
  difference over all pairs got worse (1.43 to 1.60 cm), driven by the floor-only versus
  ceiling pair, where outlines sit on furniture and curtains. Short of the gate because that
  second cause remains.
- **Regenerated** on the current outputs (`bench/fixloop/round1.md`): 35.7% of 28 walls to
  37.9% of 29; the bathroom pair again 50% to 75%, the bedroom pair 43% to 57%, and the
  floor-only versus ceiling pair 29% to 22%; the median 1.7 to 1.9 cm. The fix still helps
  the pairs it was aimed at and still loses on the pair whose outlines sit on furniture.

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
  inside their intervals (was 5 of 6). Regenerated on the current outputs
  (`bench/fixloop/round3.md`): the same, with R5's spread before at 4.01 cm. Ceiling values move by up to 3.5 cm, mostly from the
  floor offsets. One room got worse: R3 went from 0.42 to 1.68 cm and is now the room that
  fails. It is 3.06 m high, the farthest ceiling from the phone, and its two halves see
  different parts of it; that is a likely reason, not an investigated one.

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
| Openings either capture reports in the area both cover | 38 | | 27 |
| Found by both captures | 6 | about 15 | 3 |
| Within 2 cm (the gate counts misses as failures) | 0% | 15 to 25% | 0% |
| Matched differences inside the 95% interval | 0 of 6 | 80% or more | 0 of 3 |

The declaration quoted 37 openings and 1 of 6 inside the interval from an earlier run of the
same code; regenerated from commit `a79bf15` the before numbers are 38 and 0 of 6, used here.
Regenerate with `python scripts/fixloop.py --rounds 5` (commits `a79bf15` and `e803d09`;
tables in `bench/fixloop/round5.md`).

**Post-mortem.** The prediction was badly wrong: fewer openings are found by both captures, not
more, and the few that are matched are different objects (their widths differ by 20 cm to
1.4 m), so neither the pass rate nor the calibration moved. To see why, every opening that one
capture reports and the other misses was looked up in the other capture at the same place
(24 openings over the three pairs; `bench/fixloop/round5_misses.json`, a one-off diagnostic
computed on the pipeline's internal ray-carving grids, which `fixloop.py` does not regenerate):

| What the other capture shows there | Openings |
|---|---|
| A gap that rays crossed, rejected by the jamb checks or the depth checks (the diagnostic does not record which) | 10 |
| No searched stretch of wall line passes there | 8 |
| Solid wall (a door closed in one walk, or a phantom in the first) | 4 |
| No rays crossed the line (that side was not scanned) | 2 |

Hypothesis 1 (detection follows the room split) is right for 8 of 24: searching every outline
edge plus tall partitions still leaves wall lines that are neither in the other capture. The
largest group, 10 of 24, is gaps the other capture saw and then dropped. The diagnostic records
only that the jamb checks (solid wall over most of 0.3 to 1.9 m on both sides) or the depth
checks (open floor 25 and 45 cm from the line) rejected them, not which. The jamb rule is the
likely cause: it is what keeps clutter out of the jambs (hypothesis 2), and in these walks the
wall beside a door is often seen only below 1.2 m or only on one side. That trade between
clutter-proof jambs and detection was not in the declaration; telling the two checks apart is
the first step if this round were repeated.

**What shipped anyway, and why.** The gate number is what was declared, and it did not move.
The code still ships because it removes a failure the old detector had within a single
capture: of the openings the old detector reported on the three samples, 12 of 37 lie mostly
(over half their width) on wall that the same capture saw as solid from 0.3 to 1.9 m; with
the new detector it is 3 of 24. The new detector looks for gaps in solid wall, so this check
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

## Round 6: cut rooms at doors found inside them (rejected)

Not declared in advance: it was tried after the walk-in rehearsal and is kept because it was
rejected on evidence.

- **Target.** Walk-in rehearsal rooms whose area differs from the full capture's: t3 15.9
  against 11.1 m², t1 12.0 against 14.1 m² (`bench/walkin.json`).
- **Hypothesis.** On a short walk the wall beside a door is not scanned, so
  `rooms.close_doors` (which closes gaps up to 1.05 m between seen wall) leaves the room open
  and it runs on through the door into the space beyond. Evidence: in t1 and t3 the opening
  detector finds a door inside the room, not on its outline.
- **Tried.** Cut the room along the door's wall line out to its outline, but only where rays
  crossed that extension of the line on less than a quarter of its length (wall that was not
  scanned, not open floor), at most 2.5 m on each side, with both parts at least 1.5 m² and
  0.6 m wide; a part the camera never entered is then dropped like any room seen only through
  a doorway. Patch: `bench/fixloop/round6_door_cut.patch`.
- **Result.** No room was cut. Rays ending on the floor crossed the extension on 98% of its
  length in t3, and on 67% and 100% for the two candidate doors in t1: the camera saw floor
  through the place where the wall would have to be, so those spaces are open to each other.
  Without the ray test the cut would happen (t3 would become 10.9 plus 5.0 m², close to the
  full capture's 11.1) but would contradict the capture's own evidence, and the same rule
  without its length limit would have cut the floor-only capture's hall in two along a 3 m
  line. The rehearsal's area differences are therefore not shown to be errors of the short
  walks: where a room ends in an open plan is a judgement both captures make from different
  views, and which one is right cannot be told without the rooms. Not shipped.


## Round 7: doors cut off at the end of the searched wall (declared before the fix)

- **Gate and number.** Opening widths within 2 cm, misses counted, as in round 5:
  0 of 27 openings over the three capture pairs (`bench/benchmark.json`). 3 are found by
  both captures, and those 3 are different objects (one capture reports a single wide
  opening where the other finds a door), so none is inside its 95% interval.
- **Root cause, with evidence.** Round 5's post-mortem could not tell which check dropped the
  doors one capture finds and the other misses. This round reran the detector on all three
  captures with every candidate gap logged with the check that rejected it, and looked each
  of the 24 unmatched openings up in the other capture (scratch scripts, not in the repo):

  | What the other capture's detector did there | Openings |
  |---|---|
  | Saw the gap, but it ran off the end of the stretch of wall line being searched, so one jamb was never seen and the both-jambs check dropped it | 9 |
  | Searched no wall line there | 9 |
  | Found it as part of a wider opening (two doors and the wall between them read as one) | 3 |
  | Saw the gap, but no floor beyond it (the depth check) | 2 |
  | Saw the gap with one jamb only partly seen | 1 |

  The stretch searched is a room outline edge plus 1 m at each end. Where a capture's room
  outline stops at the door (the room was split there), the door runs past the end of the
  stretch.
- **Fix.** Search each stretch further along its wall line, far enough to reach the far jamb
  of the widest opening the detector accepts (2.6 m x 1.1), but keep only gaps that overlap
  the stretch itself, so the new reach adds no gaps out in the next room. Collinear stretches
  whose reaches overlap are searched as one, so a gap is not found twice. Each jamb's
  interval also gets the capture's residual drift, split over the two jambs as for a wall
  face (`measure.walls`): a jamb is the end of a wall face.
- **Prediction.** Before this declaration the change was screened on the detector inputs
  saved from the same three captures (with a 2 m reach and no interval change), so the
  numbers below are not an independent test; the measured ones come from rerunning the
  pipeline at both commits. Found by both captures: from 3 to about 12. Openings in the area
  both captures cover: from 27 to about 57, because the new doors are also found by one
  capture only. Within 2 cm: about 2, so the pass rate goes from 0% to about 3 to 4%. The
  gate (85%) stays far off: on the doors both captures find, each jamb sits 2 to 3 cm apart
  between the captures, about what a wall face does (the wall gate's median difference is
  1.9 cm), so the widths differ by about 5 cm (median). With the drift term about half of
  the matched differences should fall inside their 95% interval (now 0 of 3). Openings
  reported on the three captures: from 24 to about 50; a few of the new ones look like gaps
  between furniture rather than doors when plotted on the capture's own points, so the
  count of openings lying on wall the capture saw as solid (3 now) should stay about the same,
  but the phantom count will rise.

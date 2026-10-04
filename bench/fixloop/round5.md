# Fix loop, round 5: Opening widths

Fix: Search every wall line for gaps in solid wall, jambs per height slice.

Before: commit `a79bf15331` (tag fixloop-5-before). After: commit `e803d09280` (tag fixloop-5-after). Regenerate with `python scripts/fixloop.py --rounds 5`.

| Measure | Before | After |
| --- | --- | --- |
| openings within 2 cm (misses count) | 0.0% | 0.0% |
| openings both captures should find | 38 | 27 |
| found by both captures | 6 | 3 |
| matched ones within 2 cm | 0.0% | 0.0% |
| median width difference | 13.3 cm | 101.5 cm |
| differences inside the 95% interval | 0.0% | 0.0% |
| openings reported on the three captures | 37 | 24 |
| of those, lying mostly on wall the same capture saw as solid | 12 | 3 |

| Capture pair | Openings before | Matched before | Openings after | Matched after |
| --- | --- | --- | --- | --- |
| 1a8384c3f6~c7d28f72c6 | 19 | 5 | 15 | 2 |
| 1a8384c3f6~c00a170fe1 | 10 | 1 | 5 | 1 |
| c7d28f72c6~c00a170fe1 | 9 | 0 | 7 | 0 |

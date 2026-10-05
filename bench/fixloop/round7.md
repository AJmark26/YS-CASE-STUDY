# Fix loop, round 7: Opening widths

Fix: Follow a door past the end of its wall stretch to its far jamb; jamb intervals include residual drift.

Before: commit `0113cce40c`. After: commit `1b05d6b2b1`. Regenerate with `python scripts/fixloop.py --rounds 7`.

| Measure | Before | After |
| --- | --- | --- |
| openings within 2 cm (misses count) | 0.0% | 3.6% |
| openings both captures should find | 27 | 56 |
| found by both captures | 3 | 12 |
| matched ones within 2 cm | 0.0% | 16.7% |
| median width difference | 101.5 cm | 8.1 cm |
| differences inside the 95% interval | 0.0% | 58.3% |
| openings reported on the three captures | 24 | 50 |
| of those, lying mostly on wall the same capture saw as solid | 3 | 3 |

| Capture pair | Openings before | Matched before | Openings after | Matched after |
| --- | --- | --- | --- | --- |
| 1a8384c3f6~c7d28f72c6 | 15 | 2 | 31 | 9 |
| 1a8384c3f6~c00a170fe1 | 5 | 1 | 10 | 2 |
| c7d28f72c6~c00a170fe1 | 7 | 0 | 15 | 1 |

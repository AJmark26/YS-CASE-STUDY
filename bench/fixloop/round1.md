# Fix loop, round 1: Wall repeatability

Fix: Square each room on its own before measuring its walls.

Before: commit `1c3350e5e5` (tag fixloop-1-before). After: commit `c0c8046ad8` (tag fixloop-1-after). Regenerate with `python scripts/fixloop.py --rounds 1`.

| Measure | Before | After |
| --- | --- | --- |
| walls within 1 cm or 0.5% | 35.7% | 37.9% |
| median wall difference | 1.7 cm | 1.9 cm |
| differences inside the 95% interval | 78.6% | 82.8% |
| walls compared | 28 | 29 |

| Capture pair | Pass before | Pass after | Median before | Median after |
| --- | --- | --- | --- | --- |
| 1a8384c3f6~c7d28f72c6 | 29% | 22% | 1.69 cm | 2.26 cm |
| 1a8384c3f6~c00a170fe1 | 50% | 75% | 1.67 cm | 0.74 cm |
| c7d28f72c6~c00a170fe1 | 43% | 57% | 1.75 cm | 0.84 cm |

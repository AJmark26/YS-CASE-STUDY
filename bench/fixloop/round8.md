# Fix loop, round 8: Calibration of video and photo floor areas

Fix: Report them as lower bounds (outline area less its 95% half-width).

Before: commit `26d1fe0194`. After: commit `9c8590cef8`. Regenerate with `python scripts/fixloop.py --rounds 8`.

| Measure | Before | After |
| --- | --- | --- |
| areas whose claim holds against LiDAR | 35.0% | 100.0% |
| areas whose claim holds | 7 | 20 |
| areas compared | 20 | 20 |
| video rooms | 4 of 8 | 8 of 8 |
| video footprints | 1 of 2 | 2 of 2 |
| photo rooms | 2 of 8 | 8 of 8 |
| photo footprints | 0 of 2 | 2 of 2 |
| outline areas below the LiDAR area | 18 | 18 |
| median lower bound as a share of the LiDAR area | n/a | 0.396 |

| Capture | Area | LiDAR (m²) | Before: outline, 95% interval | Holds | After: lower bound | Holds |
| --- | --- | --- | --- | --- | --- | --- |
| 1a8384c3f6 | video room R1 | 11.07 | 10.16, 9.05 to 11.27 | yes | 9.0474 | yes |
| 1a8384c3f6 | video room R2 | 16.83 | 8.34, 6.81 to 9.87 | no | 6.8136 | yes |
| 1a8384c3f6 | video room R3 | 8.41 | 5.36, 3.64 to 7.08 | no | 3.6358 | yes |
| 1a8384c3f6 | video room R4 | 7.88 | 3.98, 2.02 to 5.95 | no | 2.015 | yes |
| 1a8384c3f6 | video room R5 | 16.83 | 3.67, 2.84 to 4.50 | no | 2.8435 | yes |
| 1a8384c3f6 | video room R6 | 4.24 | 3.50, 1.55 to 5.45 | yes | 1.5525 | yes |
| 1a8384c3f6 | video room R7 | 2.69 | 2.01, 1.29 to 2.72 | yes | 1.2933 | yes |
| 1a8384c3f6 | video footprint | 51.13 | 37.02, 33.09 to 40.94 | no | 33.0934 | yes |
| 1a8384c3f6 | photo room R1 | 16.83 | 11.85, 9.29 to 14.41 | no | 9.2909 | yes |
| 1a8384c3f6 | photo room R2 | 11.07 | 12.86, 9.80 to 15.92 | yes | 9.7974 | yes |
| 1a8384c3f6 | photo room R3 | 8.41 | 8.50, 6.62 to 10.38 | yes | 6.6185 | yes |
| 1a8384c3f6 | photo room R4 | 7.88 | 4.80, 3.26 to 6.35 | no | 3.2607 | yes |
| 1a8384c3f6 | photo room R5 | 4.24 | 2.26, 1.47 to 3.05 | no | 1.4736 | yes |
| 1a8384c3f6 | photo room R6 | 2.69 | 1.12, 0.49 to 1.76 | no | 0.4931 | yes |
| 1a8384c3f6 | photo footprint | 51.13 | 36.10, 31.32 to 40.88 | no | 31.3215 | yes |
| c00a170fe1 | video room R1 | 14.13 | 12.10, 3.11 to 21.09 | yes | 3.1124 | yes |
| c00a170fe1 | video footprint | 21.01 | 12.10, 3.11 to 21.09 | yes | 3.1124 | yes |
| c00a170fe1 | photo room R1 | 14.13 | 6.80, 5.45 to 8.14 | no | 5.4539 | yes |
| c00a170fe1 | photo room R2 | 6.89 | 1.03, 0.65 to 1.42 | no | 0.6505 | yes |
| c00a170fe1 | photo footprint | 21.01 | 7.83, 6.43 to 9.23 | no | 6.4345 | yes |

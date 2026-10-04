# Fix loop, round 3: Ceiling height

Fix: Ceiling level minus each room's own floor level.

Before: commit `5eed22a10c` (tag fixloop-3-before). After: commit `7b57f3597b` (tag fixloop-3-after). Regenerate with `python scripts/fixloop.py --rounds 3`.

| Measure | Before | After |
| --- | --- | --- |
| rooms with spread within 1 cm | 66.7% | 83.3% |
| median spread | 0.4 cm | 0.6 cm |
| largest spread | 4.0 cm | 1.7 cm |
| spreads inside the 95% interval | 83.3% | 100.0% |
| rooms measured in both halves | 6 | 6 |

| Room | Before A / B (m) | After A / B (m) | Spread before | Spread after |
| --- | --- | --- | --- | --- |
| R1 | 2.446 / 2.4561 | 2.4632 / 2.4718 | 1.01 cm | 0.86 cm |
| R2 | 2.4603 / 2.4559 | 2.4634 / 2.4684 | 0.44 cm | 0.5 cm |
| R3 | 3.0793 / 3.0835 | 3.0758 / 3.059 | 0.42 cm | 1.68 cm |
| R4 | 3.087 / 3.0854 | 3.0925 / 3.0903 | 0.16 cm | 0.22 cm |
| R5 | 2.3138 / 2.3539 | 2.3555 / 2.3618 | 4.01 cm | 0.63 cm |
| R6 | 3.0746 / 3.0784 | 3.0642 / 3.0693 | 0.38 cm | 0.51 cm |

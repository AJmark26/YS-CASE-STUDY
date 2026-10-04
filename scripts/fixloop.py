"""Regenerate the fix-loop before/after numbers from git tags.

Each round names a gate, a commit before the fix and a commit after it (tagged
fixloop-<n>-before and fixloop-<n>-after). Both commits are checked out into temporary
worktrees and each one's own scripts produce the numbers, so nothing here can quietly make
the "before" look worse.

  round 1  wall repeatability (same wall in two captures within 1 cm or 0.5%):
           square each room on its own before measuring its walls.
  round 2  rejected: six wider wall-face searches, all worse (bench/fixloop/round2_face_search.json).
  round 3  ceiling height (two measurements of the same ceiling within 1 cm):
           ceiling level minus each room's own floor level, instead of the densest height bin
           over the global floor.
  round 4  rejected: wider wall intervals for competing surfaces (docs/fix_loop.md).
  round 5  opening widths (same opening in two captures within 2 cm, misses count):
           search every wall line for gaps in solid wall instead of each room's outline for
           carved gaps.

Rounds 1 and 3 change only the measurement, so they reuse the same pipeline outputs:

    python -m ysplan <data>/<capture> -o out/<capture>_lidar      # for each sample capture
    python scripts/fixloop.py --data <data> [--out out] [--rounds 1 3 5]

Round 5 changes what the pipeline writes, so it reruns the pipeline at each tag (about
10 minutes per tag for the three captures).

Writes bench/fixloop/round<n>.json and bench/fixloop/round<n>.md.
"""
import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CEILING_CAPTURE = "c7d28f72c6"
CAPTURES = ["1a8384c3f6", "c7d28f72c6", "c00a170fe1"]


def sh(cmd, cwd=ROOT):
    print("+", " ".join(map(str, cmd)), flush=True)
    subprocess.run(list(map(str, cmd)), cwd=cwd, check=True)


def at_tag(tag, fn):
    wt = Path(tempfile.mkdtemp(prefix=f"ys_{tag}_"))
    shutil.rmtree(wt)
    sh(["git", "worktree", "add", "--detach", wt, tag])
    try:
        return fn(wt)
    finally:
        sh(["git", "worktree", "remove", "--force", wt])


def walls_round(a):
    def run(wt):
        bench = wt / "bench_run"
        sh([sys.executable, wt / "scripts" / "benchmark.py", a.out.resolve(), bench], cwd=wt)
        b = json.loads((bench / "benchmark.json").read_text())["repeatability_lidar"]
        r = {k: v for k, v in b.items() if k not in ("walls", "pairs")}
        r["pairs"] = {k: {kk: vv for kk, vv in v.items() if kk != "rooms"} for k, v in b["pairs"].items()}
        return r
    rows = [("pass_rate", "walls within 1 cm or 0.5%", 100, "%"),
            ("median_abs_diff_cm", "median wall difference", 1, " cm"),
            ("within_ci95", "differences inside the 95% interval", 100, "%"),
            ("n_walls", "walls compared", 1, "")]
    return run, rows


def ceiling_round(a):
    def run(wt):
        js = wt / "ceiling.json"
        sh([sys.executable, wt / "scripts" / "ceiling_repeat.py", a.data.resolve() / CEILING_CAPTURE, js,
            "--run", (a.out / f"{CEILING_CAPTURE}_lidar").resolve()], cwd=wt)
        return json.loads(js.read_text())
    rows = [("pass_rate", "rooms with spread within 1 cm", 100, "%"),
            ("median_spread_cm", "median spread", 1, " cm"),
            ("max_spread_cm", "largest spread", 1, " cm"),
            ("inside_ci95", "spreads inside the 95% interval", 100, "%"),
            ("rooms_in_both", "rooms measured in both halves", 1, "")]
    return run, rows


def openings_round(a):
    def run(wt):
        out = wt / "out5"
        for c in CAPTURES:
            sh([sys.executable, "-m", "ysplan", a.data.resolve() / c, "-o", out / f"{c}_lidar", "--no-damage"], cwd=wt)
        sh([sys.executable, wt / "scripts" / "benchmark.py", out, wt / "bench_run"], cwd=wt)
        b = json.loads((wt / "bench_run" / "benchmark.json").read_text())["openings_repeatability_lidar"]
        b["pairs"] = {k: {kk: vv for kk, vv in v.items() if kk not in ("matched", "unmatched")}
                      | {"matched": [(m["id_ref"], m["id_test"], m["width_ref"], m["width_test"], m["diff_cm"])
                                     for m in v["matched"]]}
                      for k, v in b["pairs"].items()}
        return b
    rows = [("pass_rate", "openings within 2 cm (misses count)", 100, "%"),
            ("n_openings", "openings both captures should find", 1, ""),
            ("matched", "found by both captures", 1, ""),
            ("matched_within_2cm", "matched ones within 2 cm", 100, "%"),
            ("median_abs_diff_cm", "median width difference", 1, " cm"),
            ("within_ci95", "differences inside the 95% interval", 100, "%")]
    return run, rows


ROUNDS = {1: ("Wall repeatability", "Square each room on its own before measuring its walls", walls_round),
          3: ("Ceiling height", "Ceiling level minus each room's own floor level", ceiling_round),
          5: ("Opening widths", "Search every wall line for gaps in solid wall, jambs per height slice",
              openings_round)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=ROOT / "out")
    ap.add_argument("--data", type=Path, default=ROOT / "data")
    ap.add_argument("--rounds", type=int, nargs="+", default=sorted(ROUNDS))
    a = ap.parse_args()
    d = ROOT / "bench" / "fixloop"
    d.mkdir(parents=True, exist_ok=True)
    for n in a.rounds:
        gate, fix, make = ROUNDS[n]
        run, rows = make(a)
        tags = (f"fixloop-{n}-before", f"fixloop-{n}-after")
        res = {"round": n, "gate": gate, "fix": fix, "tags": tags,
               "before": at_tag(tags[0], run), "after": at_tag(tags[1], run)}
        (d / f"round{n}.json").write_text(json.dumps(res, indent=1))
        lines = [f"# Fix loop, round {n}: {gate}", "", f"Fix: {fix}.", "",
                 f"Before: `{tags[0]}`. After: `{tags[1]}`. Regenerate with "
                 f"`python scripts/fixloop.py --rounds {n}`.", "",
                 "| Measure | Before | After |", "| --- | --- | --- |"]
        for k, label, f, u in rows:
            b, c = res["before"].get(k), res["after"].get(k)
            fmt = lambda v: "n/a" if v is None else f"{v * f:.1f}{u}" if f != 1 or u else f"{v}"
            lines.append(f"| {label} | {fmt(b)} | {fmt(c)} |")
        if n == 3:
            lines += ["", "| Room | Before A / B (m) | After A / B (m) | Spread before | Spread after |",
                      "| --- | --- | --- | --- | --- |"]
            after = {r["room"]: r for r in res["after"]["rooms"]}
            for r in res["before"]["rooms"]:
                q = after.get(r["room"], {})
                lines.append(f"| {r['room']} | {r['A']} / {r['B']} | {q.get('A')} / {q.get('B')} | "
                             f"{r.get('spread_cm', 'n/a')} cm | {q.get('spread_cm', 'n/a')} cm |")
        elif n == 5:
            lines += ["", "| Capture pair | Openings before | Matched before | Openings after | Matched after |",
                      "| --- | --- | --- | --- | --- |"]
            for p_, c in res["after"]["pairs"].items():
                b = res["before"]["pairs"].get(p_, {})
                lines.append(f"| {p_} | {b.get('n_openings')} | {len(b.get('matched', []))} | "
                             f"{c['n_openings']} | {len(c['matched'])} |")
        else:
            lines += ["", "| Capture pair | Pass before | Pass after | Median before | Median after |",
                      "| --- | --- | --- | --- | --- |"]
            for p, c in res["after"]["pairs"].items():
                b = res["before"]["pairs"].get(p, {})
                lines.append(f"| {p} | {(b.get('pass_rate') or 0) * 100:.0f}% | {(c['pass_rate'] or 0) * 100:.0f}% | "
                             f"{b.get('median_abs_diff_cm')} cm | {c['median_abs_diff_cm']} cm |")
        (d / f"round{n}.md").write_text("\n".join(lines) + "\n")
        print("\n".join(lines))


if __name__ == "__main__":
    main()

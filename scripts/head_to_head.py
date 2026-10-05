"""Head-to-head against a consumer app (magicplan or Polycam) on the same rooms, scored on
laser-measured truth. The brief's rule: beat or tie the app on at least 70% of dimensions.

    python scripts/head_to_head.py template out/<capture>_lidar/plan.json h2h.csv
    # measure each listed dimension with a laser, read the app's value off its plan,
    # fill truth and app (metres, or m² for areas); leave a row blank to skip it
    python scripts/head_to_head.py score out/<capture>_lidar/plan.json h2h.csv [--json bench/head_to_head.json]

A row names one of our dimensions by id: a wall (`R1-W3`), a room's floor area (`R1:area`), its
ceiling height (`R1:ceiling`) or an opening (`D2`). Each error is the absolute difference from
the laser. We win a row when our error is smaller than the app's by more than the tie band, tie
when the two errors are within it, and lose otherwise. The tie band is 0.5 cm for lengths (about
a laser measurer's own repeatability) and 0.05 m² for areas. The score also says whether the
laser value lies inside our 95% interval, since the brief scores calibration too.
"""
import argparse
import csv
import json
import sys
from pathlib import Path

TIE = {"length": 0.005, "area": 0.05}
FIELDS = ["dimension", "kind", "what", "ours", "truth", "app", "note"]


def our_values(plan):
    """Every dimension of the plan as id -> (kind, description, value, ci95 or None)."""
    out = {}
    for r in plan["rooms"]:
        for w in r["walls"]:
            v = w["length_m"]
            out[w["id"]] = ("length", f"{r['id']} wall, {v['value']:.2f} m", v["value"], v.get("ci95"))
        a = r["floor_area_m2"]
        if a.get("value") is not None:              # video and photo tiers give a lower bound only
            out[f"{r['id']}:area"] = ("area", f"{r['id']} floor area, {a['value']:.1f} m²", a["value"], a.get("ci95"))
        c = r.get("ceiling_height_m") or {}
        if c.get("value") is not None:
            out[f"{r['id']}:ceiling"] = ("length", f"{r['id']} ceiling height, {c['value']:.2f} m", c["value"], c.get("ci95"))
    for o in plan.get("openings", []):
        v = o["width_m"]
        out[o["id"]] = ("length", f"{o['type']} between {' and '.join(o['rooms'])}, {v['value']:.2f} m wide",
                        v["value"], v.get("ci95"))
    return out


def template(plan, path):
    rows = [{"dimension": k, "kind": kind, "what": what, "ours": f"{val:.4f}", "truth": "", "app": "", "note": ""}
            for k, (kind, what, val, _) in our_values(plan).items()]
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)
    print(f"wrote {len(rows)} dimensions to {path}")


def score(plan, path):
    ours = our_values(plan)
    rows, skipped = [], []
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            k = row["dimension"].strip()
            if not row.get("truth", "").strip() or not row.get("app", "").strip():
                continue
            if k not in ours:
                skipped.append(k)
                continue
            kind, what, val, ci = ours[k]
            truth, app = float(row["truth"]), float(row["app"])
            e_ours, e_app = abs(val - truth), abs(app - truth)
            tie = TIE[kind]
            result = "win" if e_ours < e_app - tie else "tie" if abs(e_ours - e_app) <= tie else "loss"
            rows.append({"dimension": k, "kind": kind, "what": what, "truth": truth, "ours": round(val, 4),
                         "app": app, "error_ours": round(e_ours, 4), "error_app": round(e_app, 4), "result": result,
                         "truth_inside_our_ci95": bool(ci[0] <= truth <= ci[1]) if ci else None})
    n = len(rows)
    won = sum(r["result"] != "loss" for r in rows)
    covered = [r["truth_inside_our_ci95"] for r in rows if r["truth_inside_our_ci95"] is not None]
    summary = {"dimensions": n, "win": sum(r["result"] == "win" for r in rows),
               "tie": sum(r["result"] == "tie" for r in rows), "loss": n - won,
               "win_or_tie": round(won / n, 3) if n else None,
               "gate": "beat or tie the app on >= 70% of dimensions",
               "passes": bool(n and won / n >= 0.7),
               "truth_inside_our_ci95": f"{sum(covered)} of {len(covered)}" if covered else None,
               "unknown_dimension_ids": skipped, "tie_band": TIE}
    return {"summary": summary, "rows": rows}


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("mode", choices=["template", "score"])
    ap.add_argument("plan", type=Path, help="our plan.json")
    ap.add_argument("csv", type=Path)
    ap.add_argument("--json", type=Path, help="write the score here")
    a = ap.parse_args()
    plan = json.loads(a.plan.read_text())
    if a.mode == "template":
        template(plan, a.csv)
        return
    res = score(plan, a.csv)
    s = res["summary"]
    for r in res["rows"]:
        print(f"{r['dimension']:>12}  truth {r['truth']:.3f}  ours {r['ours']:.3f}  app {r['app']:.3f}  {r['result']}")
    print(f"{s['dimensions']} dimensions: {s['win']} win, {s['tie']} tie, {s['loss']} loss; "
          f"win or tie {s['win_or_tie']} ({'passes' if s['passes'] else 'fails'} the 70% gate); "
          f"truth inside our 95% interval: {s['truth_inside_our_ci95']}")
    if s["unknown_dimension_ids"]:
        print("not in this plan (skipped):", ", ".join(s["unknown_dimension_ids"]), file=sys.stderr)
    if a.json:
        a.json.write_text(json.dumps(res, indent=1))


if __name__ == "__main__":
    main()

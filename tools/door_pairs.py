"""Door pair agreement: which multi-door cells can be converted as one rigid interior.

A cell with several loading doors out can only be placed if one rigid transform puts every
interior door under its exterior door. Vanilla often cheats (the load door teleports and turns
the player), so most cells fail. This lists them instead of building them broken.

Usage: py -I door_pairs.py [--limit N] [--tolerance U]
"""

import argparse
import csv
import json
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from si import inventory  # noqa: E402
from si.master import Master  # noqa: E402
from si.plan import BuildingPlan, DOOR_AGREEMENT  # noqa: E402

OUT = os.path.join(HERE, "out", "door_pairs.csv")


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--tolerance", type=float, default=DOOR_AGREEMENT)
    args = ap.parse_args(argv)
    master = Master()
    inv = inventory.load(master)
    triage = {r["id"]: r for r in csv.DictReader(open(os.path.join(HERE, "out", "triage.csv"),
                                                      encoding="utf-8"))}
    rows = []
    cells = [c for c in sorted(inv["interior_doors"]) if len(inv["interior_doors"][c]) > 1]
    for cell in cells[:args.limit or None]:
        tr = triage[cell]
        doors = [tr["ext_door"]]
        for d in inv["interior_doors"][cell]:
            t = inv["doors"][d]["xtel"]
            if t and t not in doors:
                doors.append(t)
        cfg = {"id": "probe" + cell, "exterior_doors": doors,
               "building_ref": tr["building_ref"], "strict_doors": False}
        try:
            pl = BuildingPlan(master, cfg)
        except Exception as exc:
            rows.append({"cell": cell, "edid": tr["edid"], "doors": len(doors),
                         "fit": tr["fit_xy"], "category": tr["category"],
                         "worst_units": "", "worst_turn": "", "ok": "no", "notes": str(exc)})
            continue
        devs = [d.deviation or 0.0 for d in pl.doors[1:]]
        turns = [math.degrees(d.turn or 0.0) for d in pl.doors[1:]]
        ok = all(d <= args.tolerance for d in devs) and all(t <= 5.0 for t in turns)
        rows.append({"cell": cell, "edid": tr["edid"], "doors": len(doors), "fit": tr["fit_xy"],
                     "category": tr["category"],
                     "worst_units": "%.0f" % max(devs) if devs else "",
                     "worst_turn": "%.0f" % max(turns) if turns else "",
                     "ok": "yes" if ok else "no",
                     "notes": "; ".join(n for n in pl.notes if "lands" in n)})
        print("%-28s %-32s %d door(s) fit %-7s worst %5s u / %3s deg  %s"
              % (cell, tr["edid"], len(doors), tr["fit_xy"], rows[-1]["worst_units"],
                 rows[-1]["worst_turn"], "OK" if ok else "not rigid"))
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["cell", "edid", "doors", "fit", "category",
                                           "worst_units", "worst_turn", "ok", "notes"])
        w.writeheader()
        for r in rows:
            w.writerow(r)
    good = sum(1 for r in rows if r["ok"] == "yes")
    print("multi-door cells: %d, rigid within %.0f units: %d (wrote %s)"
          % (len(rows), args.tolerance, good, OUT))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

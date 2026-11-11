"""Which quests/scripts/packages test "being in this interior cell" (Fase 4.3, detection).

Reads the inventory cache and lists every condition (CTDA) whose parameter is the interior cell
or one of its refs, plus the scripts whose SCRO names the cell or a ref of it. The CTDA function
index lives in a table of its own, so the report groups by function index instead of guessing
names; conditions that take a cell are almost always GetInCell.

Usage: py -I cell_refs.py [--only CELLID ...]
"""

import argparse
import collections
import csv
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from si import inventory  # noqa: E402
from si.master import Master, fid  # noqa: E402

OUT = os.path.join(HERE, "out", "cell_refs.csv")


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", nargs="*", default=None)
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)
    master = Master()
    inv = inventory.load(master)
    cells = sorted(inv["interior_doors"])
    if args.only:
        cells = ["%08X" % fid(c) for c in args.only]

    rows = []
    fn = collections.Counter()
    for cell in cells:
        conds = inv.get("conditions", {}).get(cell, [])
        scripts = inv.get("scripts", {}).get(cell, [])
        if not conds and not scripts:
            continue
        for c in conds:
            fn[c["function"]] += 1
            rows.append({"cell": cell, "edid": inv["interiors"][cell].get("edid") or "",
                         "kind": "condition", "record": c["record"], "name": c["edid"],
                         "function": c["function"], "param": c["param"], "param_what": c["what"]})
        for s in scripts:
            rows.append({"cell": cell, "edid": inv["interiors"][cell].get("edid") or "",
                         "kind": "script", "record": "SCPT:" + s, "name": "", "function": "",
                         "param": "", "param_what": "scro"})
    if not args.quiet:
        by_cell = collections.defaultdict(list)
        for r in rows:
            by_cell[r["cell"]].append(r)
        for cell, rs in sorted(by_cell.items(), key=lambda kv: -len(kv[1])):
            print("== %s %-32s %d reference(s)" % (cell, rs[0]["edid"], len(rs)))
            for r in rs[:8]:
                if r["kind"] == "condition":
                    print("   cond %-16s %-30s fn %-5s param %s (%s)"
                          % (r["record"], r["name"] or "-", r["function"], r["param"],
                             r["param_what"]))
                else:
                    print("   script %s" % r["record"])
        print()
        print("function indices seen (a cell parameter is almost always GetInCell):",
              dict(fn.most_common(6)))
    with open(OUT, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["cell", "edid", "kind", "record", "name", "function",
                                           "param", "param_what"])
        w.writeheader()
        for r in rows:
            w.writerow(r)
    print("cells with references: %d, rows: %d (wrote %s)"
          % (len(set(r["cell"] for r in rows)), len(rows), OUT))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

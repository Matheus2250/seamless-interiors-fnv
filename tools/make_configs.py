"""Write tools/buildings/<id>.json from the triage CSV (PLANO-GERAL Fase 1 -> 2 bridge).

Only interiors whose triage row looks buildable (a single building mesh, one door out, no
links to other interiors) are offered by default; --ids takes explicit cell formIDs anyway.

Usage: py -I make_configs.py [--ids 001057F1 ...] [--categories A B] [--force] [--dry-run]
"""

import argparse
import csv
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

TRIAGE = os.path.join(HERE, "out", "triage.csv")
BUILDINGS = os.path.join(HERE, "buildings")
PACKAGE_RADIUS = 512      # "near editor location" radius for packages that used "in cell"


def config_for(row, inv, multi_door=False):
    cid = row["id"]
    ident = row["edid"] or ("CELL" + cid)
    ident = ident.replace(" ", "")
    packages = {}
    for pack in (row.get("packages") or "").split():
        packages[pack] = PACKAGE_RADIUS
    doors = [row["ext_door"]]
    if multi_door and inv is not None:
        # every door out of the cell is converted, or none: converting just one leaves the others
        # teleporting into the old interior cell
        for d in inv["interior_doors"].get(cid, []):
            t = inv["doors"][d]["xtel"]
            if t and t not in doors:
                doors.append(t)
    cfg = {"enabled": False, "id": ident, "exterior_doors": doors,
           "building_ref": row["building_ref"], "cell": cid}
    if packages:
        cfg["package_radius"] = packages
    return cfg


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--ids", nargs="*", default=None)
    ap.add_argument("--categories", nargs="*", default=["A"])
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--enable", action="store_true", help="publish these in the release build")
    ap.add_argument("--multi-door", action="store_true",
                    help="also offer cells with several doors out, converting all of them")
    args = ap.parse_args(argv)
    rows = list(csv.DictReader(open(TRIAGE, encoding="utf-8")))
    if args.ids:
        want = {"%08X" % int(i, 16) for i in args.ids}
        rows = [r for r in rows if r["id"] in want]
    else:
        rows = [r for r in rows if r["category"] in args.categories]
    inv = None
    if args.multi_door:
        inv = json.load(open(os.path.join(HERE, "out", "inventory.json"), encoding="utf-8"))
    n_doors = (lambda r: int(r.get("n_ext_doors") or 0) >= 1) if args.multi_door else (
        lambda r: int(r.get("n_ext_doors") or 0) == 1)
    rows = [r for r in rows if n_doors(r)
            and int(r.get("n_int_links") or 0) == 0 and r.get("building_ref")]
    # several interiors can share a building: the pipeline cuts one mesh for all of them
    shared = {}
    for row in rows:
        shared.setdefault(row["building_ref"], []).append(row)
    for bld, rs in shared.items():
        if len(rs) > 1:
            print("  note  building %s hosts %d interior(s): %s"
                  % (bld, len(rs), ", ".join(r["edid"] for r in rs)))
    rows = sorted(rows, key=lambda r: r["id"])
    written = 0
    for row in rows:
        cfg = config_for(row, inv, args.multi_door)
        if args.enable:
            cfg.pop("enabled", None)
        path = os.path.join(BUILDINGS, cfg["id"] + ".json")
        if os.path.exists(path) and not args.force:
            print("  keep  %s (exists)" % cfg["id"])
            continue
        print("  write %s <- %s %s (fit %s, %s actor(s))"
              % (cfg["id"], row["id"], row["edid"], row["fit_xy"], row["n_actors"]))
        if not args.dry_run:
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(cfg, fh, indent=1, ensure_ascii=False)
                fh.write("\n")
        written += 1
    print("configs written: %d" % written)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

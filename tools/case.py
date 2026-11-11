"""One-interior case report: everything needed to debug an in-game report."""

import json
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from si import inventory  # noqa: E402
from si.master import Master  # noqa: E402
from si.plan import BuildingPlan  # noqa: E402


def main(argv):
    if not argv:
        print("usage: py -I case.py <interior id>")
        return 2
    ident = argv[0]
    cfg = json.load(open(os.path.join(HERE, "buildings", ident + ".json"), encoding="utf-8"))
    master = Master()
    pl = BuildingPlan(master, cfg, building=None)
    d = pl.door
    print("%s  cell %s" % (ident, "%08X" % pl.int_cell.form_id))
    print("  door     %08X pos (%.0f, %.0f, %.0f) rz %.1f  normal (%.2f, %.2f)"
          % (d.ext.form_id, d.pos[0], d.pos[1], d.pos[2], math.degrees(d.rot[2]),
             d.normal[0], d.normal[1]))
    print("  building %08X  mesh %s  scale %.2f"
          % (int(str(cfg.get("building_ref", "0")), 16), os.path.basename(pl.bld_model or "-"),
     pl.bld_scale))
    print("  copies   %d (%d structural), skipped %d, doors %d, actors %d, packages %d"
          % (len(pl.copies), sum(1 for c in pl.copies if c.structural), len(pl.skipped),
             len(pl.doors), len(pl.actor_moves), len(pl.package_overrides)))
    print("  facade   %d faces cut" % len(pl.facade_cuts))
    if getattr(pl, "nav_link", None):
        lk = pl.nav_link
        print("  navlink  yes: ext %08X tri %d edge %d at %.0f units"
              % (lk["ext_navm"], lk["ext_tri"], lk["ext_edge"], lk["dist_ext"]))
    else:
        print("  navlink  no")
    print("  navmesh  %d tris in %d grid(s)" % (len(pl.nav_tris_world), len(set(
        (int(t[0][1] // 4096), int(t[0][0] // 4096)) for t in pl.nav_tris_world))))
    inv = inventory.load(master)
    cell = "%08X" % pl.int_cell.form_id
    conds = inv.get("conditions", {}).get(cell, [])
    print("  refs     %d condition(s), %d script(s) name the cell"
          % (len(conds), len(inv.get("scripts", {}).get(cell, []))))
    for note in pl.notes:
        print("  note     %s" % note)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
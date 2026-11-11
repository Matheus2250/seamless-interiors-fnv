"""Navmesh link feasibility: can each converted interior be joined to the exterior navmesh?

For every config: the exterior door's cell, the exterior navmesh records of that cell, and the
distance from the door to the nearest exterior navmesh triangle and to the interior navmesh
boundary. A link is worth trying when both are close (the doorway is a real opening in the mesh).

Usage: py -I navlink_report.py [--limit N]
"""

import argparse
import glob
import json
import math
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from si import geom  # noqa: E402
from si.master import Master, fid, ref_pos_rot  # noqa: E402
from si.plan import BuildingPlan  # noqa: E402

OUT = os.path.join(HERE, "out", "navlink.csv")


def navmesh_tris(master, navm):
    """World-space triangles of a NAVM record living outside (no transform)."""
    vx = navm.get("NVVX")
    V = [struct.unpack_from("<3f", vx, i * 12) for i in range(len(vx) // 12)]
    out = []
    tr = navm.get("NVTR")
    for i in range(len(tr) // 16):
        t = struct.unpack_from("<3H", tr, i * 16)
        out.append((i, [V[j] for j in t], t))
    return out


def seg_point_dist(a, b, p):
    ax, ay, az = a
    bx, by, bz = b
    dx, dy, dz = bx - ax, by - ay, bz - az
    L = dx * dx + dy * dy + dz * dz
    if L <= 1e-9:
        return math.dist(a, p)
    t = max(0.0, min(1.0, ((p[0] - ax) * dx + (p[1] - ay) * dy + (p[2] - az) * dz) / L))
    q = (ax + t * dx, ay + t * dy, az + t * dz)
    return math.dist(q, p)


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args(argv)
    master = Master()
    configs = []
    for path in sorted(glob.glob(os.path.join(HERE, "buildings", "*.json"))):
        configs.append(json.load(open(path, encoding="utf-8")))
    rows = []
    for cfg in configs[:args.limit or None]:
        plan = BuildingPlan(master, cfg, building=None)
        door = plan.door
        # the door ref is persistent, so its parent cell is the world's persistent cell; the
        # navmesh we need is the one of the GRID cell that contains the door position
        gx, gy = int(door.pos[0] // 4096.0), int(door.pos[1] // 4096.0)
        ext_cell_rec = master.exterior_cell(fid(plan.world), gx, gy)
        ext_cell = ext_cell_rec.form_id if ext_cell_rec is not None else 0
        navms = [r for r, _g in master.cell_children(ext_cell) if r.sig == "NAVM"] if ext_cell else []
        best_ext = None
        for navm in navms:
            for idx, tri, _t in navmesh_tris(master, navm):
                for a, b in ((tri[0], tri[1]), (tri[1], tri[2]), (tri[2], tri[0])):
                    d = seg_point_dist(a, b, door.pos)
                    if best_ext is None or d < best_ext[0]:
                        best_ext = (d, navm.form_id, idx)
        # interior navmesh edge nearest the door (outside face)
        best_int = None
        for tri in plan.nav_tris_world:
            for a, b in ((tri[0], tri[1]), (tri[1], tri[2]), (tri[2], tri[0])):
                d = seg_point_dist(a, b, door.pos)
                if best_int is None or d < best_int:
                    best_int = d
        rows.append({"id": cfg["id"], "cell": cfg.get("cell", ""), "ext_cell": "%08X" % ext_cell,
                     "ext_navms": len(navms),
                     "dist_ext": "%.1f" % best_ext[0] if best_ext else "",
                     "dist_int": "%.1f" % best_int if best_int is not None else "",
                     "linkable": "yes" if best_ext and best_int is not None
                                 and best_ext[0] <= 150 and best_int <= 150 else "no"})
        print("%-28s ext cell %08X navms %d  door->ext %6s  door->int %6s  %s"
              % (cfg["id"], ext_cell, len(navms), rows[-1]["dist_ext"], rows[-1]["dist_int"],
                 rows[-1]["linkable"]))
    import csv
    with open(OUT, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["id", "cell", "ext_cell", "ext_navms", "dist_ext",
                                           "dist_int", "linkable"])
        w.writeheader()
        for r in rows:
            w.writerow(r)
    ok = sum(1 for r in rows if r["linkable"] == "yes")
    print("interiors: %d, linkable (both within 150): %d -> %s" % (len(rows), ok, OUT))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

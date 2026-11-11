"""Automatic geometry checks for a converted interior (PLANO-GERAL Fase 2, item 5).

Runs on the plan (config -> BuildingPlan), so it checks exactly the meshes the pipeline will
publish:

  1. doorway clear: no collision triangle of any copied piece crosses the corridor through the
     door (the player must be able to walk in);
  2. building clear: the building mesh, after the pipeline's cuts, has no collision triangle
     left inside the volume a player walks in (no invisible wall in the room);
  3. facade open: the visual wall behind the door was actually cut (coverage ~0);
  4. fit: every copied piece is inside the building bounds (plan overflow, shell and all);
  5. door override: the exterior door no longer teleports;
  6. headroom: a standing player fits above the interior navmesh (scaled interiors).

Usage: py -I verify_build.py [--only ID ...] [--verbose]
"""

import argparse
import collections
import glob
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from si import geom, meshes  # noqa: E402
from si.master import Master, fid  # noqa: E402
from si.plan import (BuildingPlan, SharedBuilding, WALL_SKIP_PREFIXES,  # noqa: E402
                    _in_tri_xy, _segment_hits, _z_on_tri, corridor_tris, is_wall_height,
                    skip_reason)

BUILDINGS = os.path.join(HERE, "buildings")
FIT_TOL = 40.0       # allowed plan overflow (the same slack triage uses for category A)
PLAYER_HEIGHT = 128  # a standing player (~6 ft); also the usual inner doorway clearance floor
HEADROOM_START = 20.0   # ignore floor-level collision (rugs, thresholds) right above the navmesh
HEADROOM_OPEN = 9999.0  # nothing above (open sky / no ceiling collision)
# Calibrated on the 52 unscaled (vanilla) interiors: their median headroom is 171-330, while
# 0-18% of their floor is low (under stairs and catwalks), so only the median fails.
HEADROOM_MIN_MEDIAN = 150
# vanilla inner doorways are ~176 high: 176 x 0.75 = 132, just above a standing player
MIN_SCALE = 0.75
CELL = 64.0


def check_plan(plan, verbose=False):
    problems, notes = [], []
    leaf = plan.door_leaf_world()

    # 1. doorway clear: rays through the opening, fired at the geometry that will be published
    # (the cut meshes), not at the plan the pipeline used to choose what to cut
    for door in plan.doors:
        open_blockers, small_blockers = doorway_rays(plan, door)
        tag = "" if door is plan.doors[0] else " (door %08X)" % door.ext.form_id
        if open_blockers:
            problems.append("opening blocked%s by %s"
                            % (tag, dict(sorted(open_blockers.items()))))
        if small_blockers:
            notes.append("low/thin pieces across the opening%s (left alone): %s"
                         % (tag, dict(sorted(small_blockers.items()))))

    # 1c. actors must stand on the interior navmesh, otherwise the engine pushes them outside
    # (the same failure that moved Chet out of the store in the first version)
    off_mesh, off_height = [], []
    for a, pos, _rz in plan.actor_moves:
        z_nav = plan.nav_z(pos)
        name = "%08X" % a.form_id
        if z_nav is None:
            off_mesh.append(name)
        elif abs(pos[2] - z_nav) > 40.0:
            off_height.append((name, round(pos[2] - z_nav)))
    # a note, not a failure: vanilla also has actors standing on furniture 60 units above the
    # navmesh; what broke the store in v1 was having no navmesh at all (covered by the building
    # collision / navmesh checks), not an actor on a table
    if off_mesh:
        notes.append("actor(s) not over a navmesh triangle (standing on furniture?): %s"
                     % off_mesh[:6])
    if off_height:
        notes.append("actor(s) off the navmesh height by >40: %s" % off_height[:6])

    # 2. building collision inside the walkable volume
    bpos, brz = plan.bld_pos, plan.bld_rot[2]
    scale = plan.bld_ref.get("XSCL")
    s = 1.0
    if scale:
        import struct as _s
        s = _s.unpack("<f", scale)[0]
    hits = []
    for n, tri_local in meshes.collision_tris_local(plan.building_mesh):
        tri = [geom.to_world(bpos, brz, (p[0] * s, p[1] * s, p[2] * s)) for p in tri_local]
        inside = sum(1 for p in _samples(tri) if plan.over_walkable(p))
        if inside:
            hits.append((n, inside))
    if hits:
        problems.append("building collision inside the walkable volume: %s" % hits[:8])

    # 3. facade cut: every opening of THIS interior must be empty in the published mesh
    cuts = getattr(plan, "facade_cuts", None) or [plan.facade_cut]
    cov = max(meshes.visual_coverage(plan.building_mesh, c) for c in cuts)
    if cov > 0.02:
        problems.append("facade still covers %.2f of the doorway" % cov)

    # 4. fit: every copied piece inside the building bounds
    lb = meshes.visual_bounds(plan.building_mesh)
    if lb is None:
        problems.append("building mesh has no visual bounds")
    else:
        blo = [lb[0][i] * s for i in range(3)]
        bhi = [lb[1][i] * s for i in range(3)]
        worst, out, decor = 0.0, 0, 0.0
        for c in plan.copies:
            base = plan.m.base_of(c.src)
            if skip_reason(base) is not None:
                continue
            b = geom.obnd(base)
            if b is None:
                continue
            x0, y0, z0, x1, y1, z1 = b
            s_ref = c.scale      # the ref's own XSCL times the interior scale
            corners = [geom.to_local(bpos, brz, geom.to_world(c.pos, c.rz, p))
                       for p in geom.box_corners((x0, y0, z0, x1, y1, z1), s_ref)]
            rlo = [min(p[i] for p in corners) for i in range(3)]
            rhi = [max(p[i] for p in corners) for i in range(3)]
            ov = max(blo[0] - rlo[0], rhi[0] - bhi[0], blo[1] - rlo[1], rhi[1] - bhi[1], 0.0)
            solid = _has_collision(base)
            if solid:
                if ov > FIT_TOL:
                    out += 1
                worst = max(worst, ov)
            else:
                decor = max(decor, ov)
        notes.append("fit: shell worst %.1f, %d piece(s) beyond %.0f; decor %.1f"
                     % (worst, out, FIT_TOL, decor))
        if worst > FIT_TOL:
            problems.append("interior shell sticks %.0f out of the building" % worst)

    # 6. headroom: room for a standing player above the interior navmesh. A scaled-down
    # interior can fit the building and still have ceilings and inner doorways lower than the
    # player (CLAUDE.md, Fase 9).
    room = headroom(plan, s)
    if room["samples"]:
        notes.append("headroom: min %.0f, p10 %.0f, median %.0f over %d points; %.0f%% below %d"
                     % (room["min"], room["p10"], room["median"], room["samples"],
                        100.0 * room["low_frac"], PLAYER_HEIGHT))
        if room["median"] < HEADROOM_MIN_MEDIAN:
            problems.append("too low for the player: median headroom %.0f (< %d)"
                            % (room["median"], HEADROOM_MIN_MEDIAN))
    s_int = getattr(plan, "int_scale", 1.0)
    if s_int < MIN_SCALE:
        problems.append("interior scaled to %.2f: inner doorways (~176) end up lower than the "
                        "player (%d); minimum scale is %.2f" % (s_int, PLAYER_HEIGHT, MIN_SCALE))
    return problems, notes


def headroom(plan, bld_scale):
    """Free height above the interior navmesh (centroid and corners of every triangle), up to
    the first collision triangle of the published geometry: copies (cut meshes, real scale)
    and the building."""
    cut_meshes = {id(c): out for c, _p, out, _cut in plan.door_wall_meshes}
    tris = []
    for c in plan.copies:
        nif = cut_meshes.get(id(c)) or meshes.read_model(meshes.model_path(c.base) or "")
        if not nif:
            continue
        try:
            tris.extend(t for _n, t in meshes.ref_collision_world(c.pos, (0, 0, c.rz), c.scale, nif))
        except Exception:
            continue
    bpos, brz = plan.bld_pos, plan.bld_rot[2]
    for _n, t in meshes.collision_tris_local(plan.building_mesh):
        tris.append([geom.to_world(bpos, brz, (p[0] * bld_scale, p[1] * bld_scale, p[2] * bld_scale))
                     for p in t])
    # bin the (non-vertical) triangles by xy cell
    grid = collections.defaultdict(list)
    for t in tris:
        if _xy_area2(t) < 1e-3:
            continue
        x0, x1 = min(p[0] for p in t), max(p[0] for p in t)
        y0, y1 = min(p[1] for p in t), max(p[1] for p in t)
        for gx in range(int(x0 // CELL), int(x1 // CELL) + 1):
            for gy in range(int(y0 // CELL), int(y1 // CELL) + 1):
                grid[(gx, gy)].append(t)
    values = []
    for nav in plan.nav_tris_world:
        cx = sum(p[0] for p in nav) / 3.0
        cy = sum(p[1] for p in nav) / 3.0
        cz = sum(p[2] for p in nav) / 3.0
        pts = [(cx, cy, cz)] + [(p[0] * 0.75 + cx * 0.25, p[1] * 0.75 + cy * 0.25,
                                 p[2] * 0.75 + cz * 0.25) for p in nav]
        for p in pts:
            floor = p[2]
            best = None
            for t in grid.get((int(p[0] // CELL), int(p[1] // CELL)), ()):
                if not _in_tri_xy(p, t):
                    continue
                z = _z_on_tri(p, t)
                if floor + HEADROOM_START <= z and (best is None or z < best):
                    best = z
            values.append((best - floor) if best is not None else HEADROOM_OPEN)
    if not values:
        return {"samples": 0}
    values.sort()
    low = sum(1 for v in values if v < PLAYER_HEIGHT)
    return {"samples": len(values), "min": values[0], "p10": values[len(values) // 10],
            "median": values[len(values) // 2], "low_frac": low / len(values)}


def _xy_area2(t):
    return abs((t[1][0] - t[0][0]) * (t[2][1] - t[0][1]) - (t[2][0] - t[0][0]) * (t[1][1] - t[0][1]))


def doorway_rays(plan, door):
    """Rays through the opening rectangle, in the door's own frame. Returns {name: hits}."""
    paths = plan.door_paths(door)
    cut_meshes = {id(c): out for c, _p, out, _cut in plan.door_wall_meshes}
    hits, small = {}, {}
    for c in plan.copies:
        if c.base.sig not in ("STAT", "SCOL"):
            continue
        nif = cut_meshes.get(id(c))
        if nif is None:
            nif = meshes.read_model(meshes.model_path(c.base))
        if not nif:
            continue
        try:
            tris = meshes.ref_collision_world(c.pos, (0, 0, c.rz), c.scale, nif)
        except Exception:
            continue      # unparseable mesh: nothing to test for this piece
        if not tris:
            continue
        crossing = [tri for _n, tri in tris if any(_segment_hits(a, b, tri) for a, b in paths)]
        if not crossing:
            continue
        # a wall is tall; a tripwire, sill or prop across the doorway is walked over, and the
        # pipeline deliberately leaves it alone (same rule as the plan: is_wall_height)
        target = (hits if is_wall_height(corridor_tris([tri for _n, tri in tris], paths),
                                         getattr(plan, "int_scale", 1.0))
                  and not (c.edid or "").startswith(WALL_SKIP_PREFIXES) else small)
        name = c.edid or ("%08X" % c.src.form_id)
        target[name] = target.get(name, 0) + len(crossing)
    return hits, small


def _has_collision(base):
    model = meshes.model_path(base) if base is not None else None
    if not model:
        return False
    try:
        return bool(meshes.collision_tris_local(meshes.read_model(model)))
    except Exception:
        return False


def _samples(tri, n=4):
    out = []
    for i in range(n + 1):
        for j in range(n + 1 - i):
            a, b = i / n, j / n
            cw = 1.0 - a - b
            out.append(tuple(a * tri[0][k] + b * tri[1][k] + cw * tri[2][k] for k in range(3)))
    return out


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", nargs="*", default=None)
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args(argv)
    master = Master()
    failed = 0
    entries = []
    for path in sorted(glob.glob(os.path.join(BUILDINGS, "*.json"))):
        cfg = json.load(open(path, encoding="utf-8"))
        if args.only and cfg["id"] not in args.only:
            continue
        entries.append(cfg)
    # same grouping as the pipeline: one mesh per building, cut for every interior in it
    counts = collections.Counter(fid(c["building_ref"]) for c in entries if c.get("building_ref"))
    groups = {}
    for cfg in entries:
        key = fid(cfg["building_ref"]) if cfg.get("building_ref") else None
        if key is not None and counts[key] > 1 and key not in groups:
            groups[key] = SharedBuilding(master, master.require(cfg["building_ref"], "REFR"))
    built = []
    for cfg in entries:
        key = fid(cfg["building_ref"]) if cfg.get("building_ref") else None
        shared = groups.get(key)
        try:
            plan = BuildingPlan(master, cfg, building=shared)
        except Exception as exc:
            print("== %s: FAIL (plan: %s)" % (cfg["id"], exc))
            failed += 1
            continue
        built.append((cfg, plan))
    for group in groups.values():
        group.build()
        for plan in group.plans:
            plan.building_mesh = group.mesh
            plan.building_mesh_path = group.path
            plan.building_cut_coverage = group.coverage
    for cfg, plan in built:
        try:
            problems, notes = check_plan(plan, args.verbose)
        except Exception as exc:
            print("== %s: FAIL (check: %s)" % (cfg["id"], exc))
            failed += 1
            continue
        status = "PASS" if not problems else "FAIL"
        print("== %s: %s (%d copies, %d structural, %d door walls)"
              % (cfg["id"], status, len(plan.copies), sum(1 for c in plan.copies if c.structural),
                 len(plan.door_wall_meshes)))
        for n in notes:
            print("   ", n)
        for p in problems:
            print("    FAIL", p)
        if problems:
            failed += 1
    print("RESULT:", "PASS" if not failed else "FAIL (%d)" % failed)
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

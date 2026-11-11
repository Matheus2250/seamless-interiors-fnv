"""Triage: every interior with a loading door to a worldspace -> tools/out/triage.csv.

Measures, for each interior: the building outside that holds its door, the alignment, how far
the interior plan sticks out of that building, the terrain above the interior floor, and the
complexity signals (extra doors, links to other interiors, actors).

Categories (PLANO-GERAL Fase 1):
  A  fits on plan (0 overflow), one door out, no links to other interiors, no cell scripts
  B  fits with a small overflow (<= 40) or has conditions/scripts tied to the cell
  C  overflow > 40, several doors out, or links to other interiors (floors, basements)
  X  no building around the door, unmanageable overflow, or terrain above the floor

Usage: py -I triage.py [--only CELLID ...] [--verbose] [--limit N] [--no-write]
"""

import argparse
import collections
import csv
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from si import inventory  # noqa: E402
from si.master import Master, fid  # noqa: E402
from si.terrain import Terrain  # noqa: E402
from si.triage import BuildingFinder, InteriorTriage, door_leaf_centre  # noqa: E402

OUT = os.path.join(HERE, "out", "triage.csv")
COLUMNS = [
    "id", "edid", "world", "world_edid", "grid", "ext_door", "int_door", "n_refs", "n_kept",
    "n_actors", "n_ext_doors", "n_int_links", "n_packs", "n_scripts", "n_quests", "n_links_in",
    "n_cell_conds", "n_cell_scripts", "cell_refs",
    "building_ref", "n_cands", "building_edid", "building_model",
    "bounds", "fit_xy", "fit_other", "fit_refs_out", "fit_mean", "z_above", "z_below", "hidden",
    "interior_size", "building_size", "terrain_above", "score", "category", "notes",
]


def summarize_refs(inv, cell_fid):
    """Short list of the records that name the cell (or a ref of it) in a condition/script."""
    out = []
    for c in inv.get("conditions", {}).get(cell_fid, []):
        tag = "%s:%s(fn%s,%s)" % (c["record"].split(":")[0], c["edid"] or c["record"].split(":")[1],
                                  c["function"], c["what"])
        if tag not in out:
            out.append(tag)
    for s in inv.get("scripts", {}).get(cell_fid, []):
        tag = "script:%s" % s
        if tag not in out:
            out.append(tag)
    return " ".join(out[:6])


def size_str(bounds):
    lo, hi = bounds
    return "%.0fx%.0fx%.0f" % (hi[0] - lo[0], hi[1] - lo[1], hi[2] - lo[2])


def analyze(master, inv, finder, terrain, cell_fid, door_fid, leaf_c):
    """door_fid is the EXTERIOR door ref; the interior door comes from its XTEL."""
    t = InteriorTriage(master, inv, finder, cell_fid, door_fid, leaf_c)
    notes = []
    b = t.choose_building()
    row = {
        "id": cell_fid,
        "edid": t.cell.get("edid") or "",
        "world": t.world,
        "grid": "%d,%d" % tuple(t.ext.get("grid") or (0, 0)),
        "ext_door": door_fid,
        "int_door": t.ext["xtel"],
        "n_refs": len(t.cell["refs"]),
        "n_actors": len(t.cell["achr"]),
        "n_ext_doors": len(inv["interior_doors"].get(cell_fid, [])),
        "n_int_links": len([d for d in inv["doors"].values()
                            if d["cell"] == cell_fid and d["xtel"]
                            and inv["doors"].get(d["xtel"], {}).get("world") is None]),
        "n_packs": len(inv["packages"].get(cell_fid, [])),
        "n_scripts": len(inv["scripts"].get(cell_fid, [])),
        "n_quests": len(inv["quest_refs"].get(cell_fid, [])),
        "n_links_in": inv["links_in"].get(cell_fid, 0),
        "n_cell_conds": len(inv.get("conditions", {}).get(cell_fid, [])),
        "n_cell_scripts": len(inv.get("scripts", {}).get(cell_fid, [])),
        "cell_refs": summarize_refs(inv, cell_fid),
    }
    if b is None:
        row.update(category="X", score="0", notes="no building around the exterior door")
        return row, t

    t.fit()
    terrain_above = t.terrain_check(terrain)
    base = t.finder.base(b["base"])
    row.update({
        "n_kept": t.kept_refs,
        "building_ref": b["fid"],
        "n_cands": len(t.scored),
        "building_edid": (base.edid() or "") if base else "",
        "building_model": ((base.get("MODL") or b"").rstrip(b"\x00").decode("latin-1") if base else ""),
        "bounds": t.building_local[2],
        "fit_xy": "%.1f" % t.fit_xy,
        "fit_other": "%.1f" % t.fit_other,
        "fit_refs_out": t.fit_refs_out,
        "fit_mean": "%.1f" % t.fit_mean_overflow,
        "z_above": "%.0f" % t.z_above,
        "z_below": "%.0f" % t.z_below,
        "hidden": t.hidden,
        "interior_size": size_str(t.interior_bounds),
        "building_size": size_str((t.building_local[0], t.building_local[1])),
        "terrain_above": ("%.0f" % terrain_above) if terrain_above is not None else "",
    })
    score = 100.0
    cat = "A"
    if t.fit_xy > 0.5:
        notes.append("plan overflow %.0f on %d ref(s)" % (t.fit_xy, t.fit_refs_out))
        if t.fit_xy > 400:
            cat = "X"
            score -= 60
        elif t.fit_xy > 40:
            cat = "C"
            score -= min(45, 10 + (t.fit_xy - 40) / 8.0)
        else:
            cat = "B"
            score -= 5
    if row["n_ext_doors"] > 1:
        cat = "C"
        score -= 10 * (row["n_ext_doors"] - 1)
        notes.append("%d doors out" % row["n_ext_doors"])
    if row["n_int_links"]:
        cat = "C"
        score -= 8 * row["n_int_links"]
        notes.append("%d door(s) to other interiors" % row["n_int_links"])
    if t.fit_xy > 40 and getattr(t, "fit_offenders", None):
        worst = t.fit_offenders[:2]
        notes.append("worst: " + ", ".join("%s +%.0f" % (e, o) for o, _f, e in worst))
    if t.fit_other > 40:
        notes.append("%.0f of decals/props outside (cosmetic)" % t.fit_other)
    if t.z_above > 20:
        notes.append("sticks %.0f above the roof" % t.z_above)
        score -= 5
    if t.hidden:
        notes.append("%d activator(s) above the roof (moved under the floor)" % t.hidden)
    conds = inv.get("conditions", {}).get(cell_fid, [])
    if conds:
        quests = [c for c in conds if c["record"].startswith("QUST") or c["record"].startswith("INFO")]
        as_cell = [c for c in conds if c["what"] == "cell"]
        notes.append("%d condition(s) name the cell (%d as a cell); %d in quests/dialogue"
                     % (len(conds), len(as_cell), len(quests)))
        if quests:
            cat = "C"
            score -= 25
        elif cat == "A":
            cat = "B"
            score -= 8
    if row["n_cell_scripts"]:
        notes.append("%d script(s) reference the cell or its refs" % row["n_cell_scripts"])
        if cat == "A":
            cat = "B"
        score -= 5

    if row["n_scripts"] or row["n_quests"]:
        notes.append("%d script(s), %d quest record(s) reference the cell"
                     % (row["n_scripts"], row["n_quests"]))
        if row["n_scripts"]:
            cat = "B" if cat == "A" else cat
            score -= 5
    if row["n_actors"]:
        notes.append("%d actor(s)" % row["n_actors"])
    if terrain_above is not None:
        if terrain_above > 0:
            notes.append("terrain %.0f above floor" % terrain_above)
            if terrain_above > 120:
                cat = "X"
                score -= 30
            elif cat == "A":
                cat = "B"
            score -= 10
    row["score"] = "%.0f" % max(0.0, score)
    row["category"] = cat
    row["notes"] = "; ".join(notes)
    return row, t


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", nargs="*", default=None)
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--no-write", action="store_true")
    args = ap.parse_args(argv)
    t0 = time.time()
    master = Master()
    inv = inventory.load(master)
    finder = BuildingFinder(master, inv["struct_refs"])
    terrain = Terrain(master)

    leaf_cache = {}
    rows = []
    ids = sorted(inv["interior_doors"])
    if args.only:
        ids = ["%08X" % fid(a) for a in args.only]
    RANK = {"A": 0, "B": 1, "C": 2, "X": 3}
    for cell_fid in ids[:args.limit or None]:
        # a cell can have several doors out; the best placement wins (normally the first)
        best = None
        doors = sorted(inv["interior_doors"][cell_fid])
        for int_door in doors[:4]:
            ext_door = inv["doors"][int_door]["xtel"]
            door_base = inv["doors"][ext_door]["base"]
            if door_base not in leaf_cache:
                leaf_cache[door_base] = door_leaf_centre(master, master.get(door_base))
            row, t = analyze(master, inv, finder, terrain, cell_fid, ext_door,
                             leaf_cache[door_base])
            key = (RANK[row["category"]], float(row.get("fit_xy") or 1e9))
            if best is None or key < best[0]:
                best = (key, row, t, ext_door)
        _key, row, t, ext_door = best
        if len(doors) > 1:
            row["notes"] = ("; ".join(x for x in [row["notes"], "best of %d doors" % len(doors)] if x))
        rows.append(row)
        if args.verbose:
            print("== %s %-28s %s %s" % (cell_fid, row.get("edid") or "", row["category"],
                                         row.get("notes") or ""))
            print("   building %s (%d cands) %s %s | fit %s (%s refs out) mean %s | z +%s/-%s | %s vs %s"
                  % (row.get("building_ref"), row.get("n_cands"), row.get("building_edid"),
                     row.get("building_size"), row.get("fit_xy"), row.get("fit_refs_out"),
                     row.get("fit_mean"), row.get("z_above"), row.get("z_below"),
                     row.get("interior_size"), row.get("bounds")))
            for ov, rfid, edid in getattr(t, "fit_offenders", [])[:4]:
                print("      offender %.0f %s %s" % (ov, rfid, edid))
            if getattr(t, "candidates", None):
                print("   candidates: %s" % ([(r[1], r[2]) for r in t.candidates[:4]],))

    if not args.no_write and not args.only:
        os.makedirs(os.path.dirname(OUT), exist_ok=True)
        with open(OUT, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=COLUMNS, extrasaction="ignore")
            w.writeheader()
            for r in rows:
                w.writerow(r)
        print("wrote %s (%d rows, %.1fs)" % (OUT, len(rows), time.time() - t0))
    print("categories:", dict(collections.Counter(r["category"] for r in rows)))
    by_world = collections.defaultdict(collections.Counter)
    for r in rows:
        by_world[r.get("world")][r["category"]] += 1
    for w, c in sorted(by_world.items(), key=lambda kv: -sum(kv[1].values())):
        print("  %s: %s" % (w, dict(c)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

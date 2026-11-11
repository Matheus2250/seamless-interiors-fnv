"""Seamless Interiors pipeline: every config in tools/buildings/*.json -> one plugin.

Output: tools/out/build/Data/ (SeamlessInteriors.esp + meshes/SeamlessInteriors/<id>/...),
ready to copy into the game's Data folder. FormIDs come from tools/formids.json (stable
across builds; only ever appended to).

Usage: py -I pipeline.py [--only ID ...] [--out DIR]
"""

import argparse
import collections
import glob
import json
import os
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from si import inventory  # noqa: E402
from si.formids import Registry  # noqa: E402
from si.master import Master, fid  # noqa: E402
from si.plan import BuildingPlan, SharedBuilding  # noqa: E402
from si.writer import Esp  # noqa: E402

BUILDINGS = os.path.join(HERE, "buildings")
DEFAULT_OUT = os.path.join(HERE, "out", "build", "Data")
REGISTRY = os.path.join(HERE, "formids.json")


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", nargs="*", help="build only these ids")
    ap.add_argument("--all", action="store_true",
                    help="also build configs marked enabled=false (experiments)")
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--navlink", action="store_true",
                    help="also join each interior navmesh to the exterior one (overrides the "
                         "exterior cell navmesh: meant for a separate plugin)")
    ap.add_argument("--esm", action="store_true",
                    help="set the TES4 master (ESM) flag on the .esp (navmesh in plain ESPs is buggy)")
    ap.add_argument("--merge-navmesh", action="store_true",
                    help="append interior navmeshes to the cells' own vanilla navmeshes instead of "
                         "adding new NAVMs (plan B for the 0x6A55A6 crash)")
    args = ap.parse_args(argv)

    t0 = time.time()
    failures = []
    master = Master()
    registry = Registry(REGISTRY)
    esp = Esp(master, registry)
    esp.esm_flag = args.esm
    esp.merge_navmesh = args.merge_navmesh
    configs = sorted(glob.glob(os.path.join(BUILDINGS, "*.json")))
    entries = []
    for path in configs:
        with open(path, encoding="utf-8") as fh:
            cfg = json.load(fh)
        if args.only and cfg["id"] not in args.only:
            continue
        if not args.all and cfg.get("enabled", True) is False:
            continue
        if args.navlink or args.merge_navmesh:     # merged pieces are joined at the door
            cfg = dict(cfg, navlink=True)
        entries.append(cfg)

    # configs that share a building ref get one cut mesh for the whole building
    counts = collections.Counter(fid(c["building_ref"]) for c in entries if c.get("building_ref"))
    groups = {}
    for cfg in entries:
        key = fid(cfg["building_ref"]) if cfg.get("building_ref") else None
        if key is not None and counts[key] > 1 and key not in groups:
            groups[key] = SharedBuilding(master, master.require(cfg["building_ref"], "REFR"))

    plans = []
    for cfg in entries:
        key = fid(cfg["building_ref"]) if cfg.get("building_ref") else None
        shared = groups.get(key)
        try:
            pl = BuildingPlan(master, cfg, building=shared)
        except Exception as exc:
            failures.append((cfg["id"], "%s: %s" % (type(exc).__name__, exc)))
            print("== %s: SKIPPED (%s: %s)" % (cfg["id"], type(exc).__name__, exc))
            continue
        plans.append(pl)

    for group in groups.values():
        group.build()
        for i, pl in enumerate(group.plans):
            pl.building_mesh = group.mesh
            pl.building_mesh_path = group.path
            pl.building_cut_coverage = group.coverage
            if i == 0:      # the note is about the building, print it once
                pl.notes.append(group.note)

    cell_refs = inventory.load(master) if plans else {}
    for pl in plans:
        print("== %s: %d copies (%d structural), %d skipped, %d actors, %d packages, %d door walls"
              % (pl.id, len(pl.copies), sum(c.structural for c in pl.copies), len(pl.skipped),
                 len(pl.actor_moves), len(pl.package_overrides), len(pl.door_wall_meshes)))
        for note in pl.notes:
            print("   ", note)
        cell = "%08X" % pl.int_cell.form_id
        conds = cell_refs.get("conditions", {}).get(cell, [])
        scripts = cell_refs.get("scripts", {}).get(cell, [])
        if conds or scripts:
            print("    WARNING: %d condition(s) and %d script(s) name this cell "
                  "(see cell_refs.py): %s" % (len(conds), len(scripts),
                                              ", ".join(sorted({c["edid"] or c["record"]
                                                                for c in conds}))[:80]))
        esp.add(pl)

    data = esp.build()
    os.makedirs(args.out, exist_ok=True)
    # drop meshes from a previous build so removing an interior does not leave files behind
    stale = os.path.join(args.out, "meshes", "SeamlessInteriors")
    if os.path.isdir(stale):
        shutil.rmtree(stale)
    esp_path = os.path.join(args.out, "SeamlessInteriors.esp")
    with open(esp_path, "wb") as fh:
        fh.write(data)
    for rel, blob in sorted(esp.mesh_files.items()):
        full = os.path.join(args.out, "meshes", rel.replace("\\", os.sep))
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "wb") as fh:
            fh.write(blob)
    registry.save()
    # what is in this build (package.py reads it; it is not copied into the game's Data)
    with open(os.path.join(args.out, "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump({"interiors": sorted(pl.id for pl in plans),
                   "scales": {pl.id: pl.int_scale for pl in plans if pl.int_scale != 1.0},
                   "failed": dict(failures)}, fh, indent=1)
    print("wrote %s (%d bytes), %d meshes, %.1fs" % (esp_path, len(data), len(esp.mesh_files), time.time() - t0))
    if esp.nav_links:
        print("navmesh links: %d (exterior navmeshes overridden: %d)"
              % (len(esp.nav_links), len(esp.ext_links)))
    for note in esp.nav_notes:
        print("   ", note)
    for ident, why in failures:
        print("FAILED %s: %s" % (ident, why))

    res = subprocess.run([sys.executable, "-I", os.path.join(HERE, "validate_esp.py"), esp_path, "--quiet"],
                         capture_output=True, text=True)
    tail = [line for line in res.stdout.splitlines() if "RESULT" in line or "ERROR" in line or "WARN" in line]
    print("\n".join(tail[-15:]))
    return 0 if "PASS" in res.stdout else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

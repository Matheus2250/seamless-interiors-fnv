"""Inventory of every interior with a loading door to a worldspace (Fase 1 input).

One scan of FalloutNV.esm produces the raw material for triage:
  - door refs (interior and exterior) with their XTEL links,
  - interior cells: EDID, owner, refs (base + placement), actors, navmeshes,
  - outdoor structural refs (STAT/SCOL) near every exterior door whose bounds could be the
    building that contains the door,
  - exterior door refs of every interior cell (doors that lead out to a worldspace).

The scan is cached in tools/out/inventory.json; triage.py reads the cache.

Usage: py -I inventory.py [--refresh] [--stats]
"""

import collections
import json
import os
import re
import struct
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
import esplib  # noqa: E402

from si.master import Master, grid_of, u32  # noqa: E402

CACHE = os.path.join(HERE, "out", "inventory.json")
NEAR_DOOR = 2500.0     # radius around an exterior door where candidate buildings are collected
STRUCT_SIGS = ("STAT", "SCOL")


def _cell_world(rec):
    cell = world = None
    for g in rec.path:
        if g.group_type == esplib.GROUP_CELL_CHILDREN:
            cell = g.label_value()
        elif g.group_type == esplib.GROUP_WORLD_CHILDREN:
            world = g.label_value()
    return cell, world


def _ref(rec):
    """(formID, base, pos, rot, scale) of a placed ref."""
    data = rec.get("DATA")
    if data is None or len(data) < 24:
        return None
    pos = struct.unpack_from("<3f", data, 0)
    rot = struct.unpack_from("<3f", data, 12)
    xscl = rec.get("XSCL")
    scale = struct.unpack("<f", xscl)[0] if xscl else 1.0
    name = rec.get("NAME")
    if not name:
        return None
    return {"fid": "%08X" % rec.form_id, "base": "%08X" % u32(name),
            "pos": [round(v, 3) for v in pos], "rot": [round(v, 5) for v in rot], "scale": round(scale, 4)}


def scan(master, progress=True):
    p = master.plugin
    # pass 1: which base forms are doors / structure
    door_bases, struct_bases = set(), {}
    for sig in ("DOOR",):
        for r in p.walk_top(sig):
            if r.kind == "record" and r.sig == sig:
                door_bases.add(r.form_id)
    for sig in STRUCT_SIGS:
        for r in p.walk_top(sig):
            if r.kind == "record" and r.sig == sig:
                struct_bases[r.form_id] = r.form_id
    if progress:
        print("  base forms: %d doors, %d structure" % (len(door_bases), len(struct_bases)))

    # pass 2: every door ref, to learn where doors are and which of them are exterior
    doors = {}
    for top in ("CELL", "WRLD"):
        for rec in p.walk_top(top):
            if rec.kind != "record" or rec.sig not in ("REFR", "ACHR"):
                continue
            name = rec.get("NAME")
            if not name or u32(name) not in door_bases:
                continue
            info = _ref(rec)
            if info is None:
                continue
            cell, world = _cell_world(rec)
            xtel = rec.get("XTEL")
            info["cell"] = "%08X" % cell if cell else None
            info["world"] = "%08X" % world if world else None
            info["xtel"] = "%08X" % u32(xtel) if xtel else None
            doors[info["fid"]] = info
    if progress:
        print("  door refs: %d" % len(doors))

    # interior cells with at least one load door to a worldspace
    interior_doors = {}
    for fid, d in doors.items():
        if d["world"] is not None or not d["xtel"]:
            continue
        tgt = doors.get(d["xtel"])
        if tgt is None or tgt["world"] is None:
            continue
        interior_doors.setdefault(d["cell"], []).append(fid)
    if progress:
        print("  interior cells with a door to a worldspace: %d" % len(interior_doors))

    # grids near every exterior door of those cells: candidates for the building
    ext_doors = sorted({doors[f]["xtel"] for fids in interior_doors.values() for f in fids})
    door_pos = {f: doors[f]["pos"] for f in ext_doors}
    need_grids = set()
    for f in ext_doors:
        x, y, _z = doors[f]["pos"]
        gx, gy = grid_of(x, y)
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                need_grids.add((gx + dx, gy + dy))

    def near_door(x, y):
        for f in ext_doors:
            px, py, _ = door_pos[f]
            if abs(x - px) <= NEAR_DOOR and abs(y - py) <= NEAR_DOOR:
                if (x - px) ** 2 + (y - py) ** 2 <= NEAR_DOOR ** 2:
                    return True
        return False

    # pass 3: interior cells (all refs) + exterior structural refs near doors
    interiors, struct_refs, ext_cells = {}, [], {}
    seen_struct = set()
    cur = None
    cur_world = None
    for top in ("CELL", "WRLD"):
        for item in p.walk_top(top):
            if item.kind == "group":
                if item.group_type == esplib.GROUP_CELL_CHILDREN:
                    cur = item.label_value()
                elif item.group_type == esplib.GROUP_WORLD_CHILDREN:
                    cur_world = item.label_value()
                continue
            if item.sig == "CELL":
                cur = item.form_id
                xclc = item.get("XCLC")
                data = item.get("DATA")
                if top == "CELL":
                    if ("%08X" % cur) in interior_doors:
                        interiors.setdefault("%08X" % cur, {
                            "fid": "%08X" % cur, "edid": item.edid(),
                            "owner": ("%08X" % u32(item.get("XOWN"))) if item.get("XOWN") else None,
                            "refs": [], "achr": [], "navm": 0,
                        })
                elif xclc:
                    gx, gy = struct.unpack_from("<ii", xclc)
                    key = "%08X" % cur
                    # a world can put a cell in several groups; the grid identifies it
                    if key not in ext_cells:
                        ext_cells[key] = {"grid": [gx, gy],
                                          "world": "%08X" % cur_world if cur_world else None}
                continue
            if item.sig == "NAVM" and top == "CELL" and cur is not None:
                cell = interiors.get("%08X" % cur)
                if cell is not None:
                    cell["navm"] += 1
                continue
            if item.sig not in ("REFR", "ACHR"):
                continue
            name = item.get("NAME")
            if not name:
                continue
            base = u32(name)
            if top == "CELL":
                cell = interiors.get("%08X" % cur)
                if cell is None:
                    continue
                info = _ref(item)
                if info is None:
                    continue
                if item.sig == "ACHR":
                    cell["achr"].append(info)
                else:
                    cell["refs"].append(info)
            elif base in struct_bases:
                if item.form_id in seen_struct:
                    continue
                info = _ref(item)
                if info is None:
                    continue
                x, y = info["pos"][0], info["pos"][1]
                if near_door(x, y):
                    seen_struct.add(item.form_id)
                    info["cell"] = "%08X" % cur if cur else None
                    struct_refs.append(info)
    # references to the interior cells: packages, scripts, quests, doors leading in
    interior_ids = {int(k, 16): k for k in interior_doors}
    packages, scripts, quest_refs, conditions = {}, {}, {}, {}
    links_in = collections.Counter()
    for f, d in doors.items():
        if d["xtel"] and d["world"] is not None:
            tgt = doors.get(d["xtel"])
            if tgt and tgt["world"] is None and tgt["cell"]:
                links_in[tgt["cell"]] += 1
    ref_cell = {r["fid"]: key for key, cell in interiors.items() for r in cell["refs"]}
    for r in p.walk_top("PACK"):
        if r.kind != "record" or r.sig != "PACK":
            continue
        for t in ("PLDT", "PTDT"):
            data = r.get(t)
            if data and len(data) >= 8 and struct.unpack_from("<i", data)[0] == 1:
                key = interior_ids.get(struct.unpack_from("<I", data, 4)[0])
                if key:
                    packages.setdefault(key, []).append("%08X" % r.form_id)
        # conditions that name the cell (or one of its refs) as a parameter
        for d in r.get_all("CTDA"):
            if len(d) < 28:
                continue
            for off in (12, 16, 24):
                v = struct.unpack_from("<I", d, off)[0]
                key = interior_ids.get(v) or ref_cell.get("%08X" % v)
                if key:
                    conditions.setdefault(key, []).append(
                        {"record": "PACK:%08X" % r.form_id, "edid": r.edid() or "",
                         "function": struct.unpack_from("<I", d, 8)[0],
                         "param": "%08X" % v, "what": "cell" if v in interior_ids else "ref"})
    # a script "in the cell" is one whose SCRO list names a ref of that cell (or the cell itself)
    for r in p.walk_top("SCPT"):
        if r.kind != "record" or r.sig != "SCPT":
            continue
        scro = r.get("SCRO")
        if not scro:
            continue
        hit = set()
        for i in range(0, len(scro) - 3, 4):
            v = u32(scro, i)
            key = interior_ids.get(v) or ref_cell.get("%08X" % v)
            if key:
                hit.add(key)
        for key in hit:
            scripts.setdefault(key, []).append("%08X" % r.form_id)
    if interior_ids:
        # conditions that name the cell (or one of its refs); the function index lives in a table
        # of its own, but the parameter is a plain formID, which is what matters here
        for r in p.walk_top("QUST"):
            if r.kind != "record" or r.sig not in ("QUST", "INFO"):
                continue
            for d in r.get_all("CTDA"):
                if len(d) < 28:
                    continue
                for off in (12, 16, 24):
                    v = struct.unpack_from("<I", d, off)[0]
                    key = interior_ids.get(v) or ref_cell.get("%08X" % v)
                    if key:
                        conditions.setdefault(key, []).append(
                            {"record": "%s:%08X" % (r.sig, r.form_id), "edid": r.edid() or "",
                             "function": struct.unpack_from("<I", d, 8)[0],
                             "param": "%08X" % v,
                             "what": "cell" if v in interior_ids else "ref"})
        # coarse: any 4-byte mention anywhere in the payload (scripts, scene data, ...)
        pattern = re.compile(b"|".join(re.escape(struct.pack("<I", c)) for c in interior_ids))
        by_bytes = {struct.pack("<I", c): k for c, k in interior_ids.items()}
        for r in p.walk_top("QUST"):
            if r.kind != "record" or r.sig not in ("QUST", "INFO"):
                continue
            for b in set(pattern.findall(r.payload())):
                quest_refs.setdefault(by_bytes[b], []).append("%08X" % r.form_id)

    if progress:
        print("  references: %d cells in packages, %d in scripts, %d in quests, %d with a "
              "condition naming the cell or a ref" % (len(packages), len(scripts), len(quest_refs),
                                                     len(conditions)))
        print("  interior cells scanned: %d (%d refs, %d actors)"
              % (len(interiors), sum(len(c["refs"]) for c in interiors.values()),
                 sum(len(c["achr"]) for c in interiors.values())))
        print("  outdoor structure refs near doors: %d" % len(struct_refs))

    return {"doors": doors, "interiors": interiors, "struct_refs": struct_refs,
            "ext_cells": ext_cells, "interior_doors": interior_doors,
            "packages": packages, "scripts": scripts, "quest_refs": quest_refs,
            "conditions": conditions,
            "links_in": dict(links_in),
            "ext_doors": {f: doors[f] for f in ext_doors}}


def load(master, refresh=False):
    if not refresh and os.path.exists(CACHE):
        with open(CACHE, encoding="utf-8") as fh:
            return json.load(fh)
    data = scan(master)
    os.makedirs(os.path.dirname(CACHE), exist_ok=True)
    with open(CACHE, "w", encoding="utf-8") as fh:
        json.dump(data, fh)
    return data


def main(argv):
    import time
    t0 = time.time()
    master = Master()
    data = load(master, refresh="--refresh" in argv)
    print("cache: %s (%.1fs)" % (CACHE, time.time() - t0))
    if "--stats" in argv:
        import collections
        worlds = collections.Counter()
        for fid, fids in data["interior_doors"].items():
            w = data["doors"][data["doors"][fids[0]]["xtel"]]["world"]
            worlds[w] += 1
        print("interior cells per worldspace:")
        for w, n in worlds.most_common():
            wrld = master.get(w)
            print("  %s %-24s %d" % (w, wrld.edid() if wrld else "?", n))
        multi = sum(1 for f in data["interior_doors"].values() if len(f) > 1)
        print("cells with more than one door out: %d" % multi)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

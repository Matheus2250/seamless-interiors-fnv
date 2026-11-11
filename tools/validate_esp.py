#!/usr/bin/env python3
"""Validate a produced plugin: structure, sizes, references, group nesting, and a tree dump.

Usage: py -I validate_esp.py [path\\to\\plugin.esp] [--master PATH] [--quiet]

Checks:
  * strict byte-level parse: every GRUP and record size fits its parent, children fill
    the group exactly, every record's subrecords add up to its data size
  * group labels/types match their parent (WRLD children, cell children, blocks, top types)
  * top-level group order follows the master's top-group order
  * esplib re-read: walks the whole file, decodes every record
  * every NAME (REFR base), SCRI and SCRO formID exists in this plugin or the master
  * new formIDs carry load-order index 01; overrides exist in the master with the same type
  * persistent / temporary placement: flags vs group kind, positions inside cell bounds
Stdlib only.
"""

import argparse
import collections
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from esplib import (GROUP_HEADER, GROUP_TYPE_NAMES, RECORD_HEADER, Plugin,  # noqa: E402
                    iter_subrecords)

DEFAULT_PLUGIN = os.path.join(HERE, "out", "SeamlessInteriors.esp")
MASTER_PATH = r"C:\Program Files (x86)\Steam\steamapps\common\Fallout New Vegas\Data\FalloutNV.esm"
FLAG_PERSISTENT = 0x400
FLAG_COMPRESSED = 0x40000
# any worldspace of the master is valid; the checks below read the cells from the master
WORLD_CELL = 0x000846EA
CELL_SIZE = 4096.0
TOP_ORDER_MASTER = None  # filled from the master


class Problems:
    def __init__(self):
        self.errors = []
        self.warnings = []

    def error(self, msg):
        self.errors.append(msg)

    def warn(self, msg):
        self.warnings.append(msg)


class Node:
    def __init__(self, kind, sig=None, fid=None, label=None, gtype=None, size=None,
                 flags=None, payload=None, offset=0):
        self.kind = kind          # "group" | "record"
        self.sig = sig
        self.fid = fid
        self.label = label        # raw bytes (groups)
        self.gtype = gtype
        self.size = size
        self.flags = flags
        self.payload = payload
        self.offset = offset
        self.children = []
        self.parent = None


def strict_parse(buf, start, end, parent, prob, depth=0):
    """Byte-accurate walk of [start, end). Fills parent.children."""
    pos = start
    while pos < end:
        if pos + 24 > end:
            prob.error("truncated item at %d (parent end %d)" % (pos, end))
            return
        if buf[pos:pos + 4] == b"GRUP":
            _, size, label, gtype, stamp, unk = GROUP_HEADER.unpack_from(buf, pos)
            if size < 24 or pos + size > end:
                prob.error("GRUP at %d size %d overruns parent end %d" % (pos, size, end))
                return
            node = Node("group", label=label, gtype=gtype, size=size, offset=pos)
            node.parent = parent
            parent.children.append(node)
            strict_parse(buf, pos + 24, pos + size, node, prob, depth + 1)
            pos += size
        else:
            sig, dsize, flags, fid, vc, fv, unk = RECORD_HEADER.unpack_from(buf, pos)
            if pos + 24 + dsize > end:
                prob.error("record %s %08X at %d overruns parent end" % (sig, fid, pos))
                return
            payload = buf[pos + 24:pos + 24 + dsize]
            node = Node("record", sig=sig.decode("latin-1"), fid=fid, size=dsize,
                        flags=flags, payload=payload, offset=pos)
            node.parent = parent
            parent.children.append(node)
            if not flags & FLAG_COMPRESSED:
                check_subrecords(node, prob)
            pos += 24 + dsize
    if pos != end:
        prob.error("children of group at %d do not fill it exactly (ended %d, end %d)" % (
            start - 24, pos, end))


def check_subrecords(node, prob):
    p = node.payload
    pos = 0
    while pos < len(p):
        if pos + 6 > len(p):
            prob.error("record %s %08X: trailing bytes in payload" % (node.sig, node.fid))
            return
        sig, size = struct.unpack_from("<4sH", p, pos)
        pos += 6
        if sig == b"XXXX" and size == 4:
            size = struct.unpack_from("<I", p, pos)[0]
            pos += 4
            # next subrecord header carries size 0 and the real size is in XXXX
            sig2, _ = struct.unpack_from("<4sH", p, pos)
            pos += 6
            pos += size
            continue
        if pos + size > len(p):
            prob.error("record %s %08X: subrecord %s overruns payload" % (node.sig, node.fid,
                                                                          sig.decode("latin-1")))
            return
        pos += size
    if pos != len(p):
        prob.error("record %s %08X: subrecords do not add up" % (node.sig, node.fid))


def subrecords(node):
    """(sig, data) list for a strictly parsed record."""
    out = []
    p = node.payload
    pos = 0
    override = None
    while pos + 6 <= len(p):
        sig, size = struct.unpack_from("<4sH", p, pos)
        pos += 6
        if sig == b"XXXX" and size == 4:
            override = struct.unpack_from("<I", p, pos)[0]
            pos += 4
            continue
        if override is not None:
            size = override
            override = None
        out.append((sig.decode("latin-1"), p[pos:pos + size]))
        pos += size
    return out


def label_text(node):
    g = node.gtype
    if g == 0:
        return node.label.decode("latin-1")
    if g in (2, 3):
        return str(struct.unpack("<i", node.label)[0])
    if g in (4, 5):
        y, x = struct.unpack("<hh", node.label)
        return "y=%d,x=%d" % (y, x)
    return "0x%08X" % struct.unpack("<I", node.label)[0]


def check_structure(root, prob, master, top_order, valid_worlds=None):
    """Group label/type rules and top-level ordering."""
    tops = [c for c in root.children if c.kind == "group"]
    if any(c.kind == "record" for c in root.children):
        prob.error("top level contains a record other than TES4")
    last_index = -1
    for g in tops:
        if g.gtype != 0:
            prob.error("top-level group at %d has type %d" % (g.offset, g.gtype))
            continue
        name = g.label.decode("latin-1")
        if name not in top_order:
            prob.error("top group %s is not a master top group" % name)
        else:
            idx = top_order.index(name)
            if idx <= last_index:
                prob.error("top group %s out of master order" % name)
            last_index = idx
        check_group_node(g, name, prob, None, valid_worlds)


def check_group_node(group, top_name, prob, parent_cell, valid_worlds=None):
    """Recursive check of child groups."""
    kids = group.children
    for i, c in enumerate(kids):
        if c.kind == "record":
            if group.gtype == 0 and c.sig != top_name:
                prob.error("record %s inside top group %s" % (c.sig, top_name))
            if group.gtype == 1 and c.sig not in ("CELL",):
                prob.error("record %s directly in world children group" % c.sig)
            if group.gtype == 6 and c.sig not in ("REFR", "ACHR", "NAVM", "PGRE", "PHZD"):
                prob.error("record %s in cell children" % c.sig)
            continue
        expect = {
            1: lambda gg: gg.parent is not None and gg.parent.gtype == 0 and
               gg.parent.label == b"WRLD",
            4: lambda gg: gg.parent is not None and gg.parent.gtype == 1,
            5: lambda gg: gg.parent is not None and gg.parent.gtype == 4,
            6: lambda gg: gg.parent is not None and gg.parent.gtype in (0, 1, 2, 3, 4, 5),
            8: lambda gg: gg.parent is not None and gg.parent.gtype == 6,
            9: lambda gg: gg.parent is not None and gg.parent.gtype == 6,
        }.get(c.gtype)
        if c.gtype == 0:
            prob.error("nested top group at %d" % c.offset)
        if expect is not None and not expect(c):
            prob.error("group type %d at %d in unexpected parent (type %d)" % (
                c.gtype, c.offset, group.gtype))
        if c.gtype == 1:
            # world children label must be a WRLD formID of the master (raw label bytes)
            label = struct.unpack("<I", c.label)[0]
            if valid_worlds is not None and label not in valid_worlds:
                prob.error("world children label 0x%08X is not a WRLD of the master" % label)
        if c.gtype in (8, 9) and (c.label != group.label):
            prob.error("group type %d label %s != cell group label %s" % (
                c.gtype, c.label.hex(), group.label.hex()))
        if c.gtype == 6:
            # label must be a CELL formID found as a record directly before it in the parent
            cell_fid = struct.unpack("<I", c.label)[0]
            prev = kids[i - 1] if i > 0 else None
            if not (prev is not None and prev.kind == "record" and prev.sig == "CELL"
                    and prev.fid == cell_fid):
                prob.error("cell children group for %08X not preceded by its CELL" % cell_fid)
        if c.gtype == 4 and c.size and c.label is None:
            pass
        check_group_node(c, top_name, prob, parent_cell, valid_worlds)


def walk_records(node, out, ancestors=()):
    for c in node.children:
        if c.kind == "record":
            out.append((c, ancestors))
        else:
            walk_records(c, out, ancestors + (c,))


def group_under(ancestors, gtype):
    for g in ancestors:
        if g.gtype == gtype:
            return g
    return None


# Hardcoded engine references that are not records in the master (0x14 = player).
ENGINE_REFS = {0x00000014}


def check_references(records, esp_fids, master, prob):
    """NAME/SCRI/SCRO targets must exist in this plugin or the master."""
    missing = collections.Counter()
    checked = 0
    for node, _anc in records:
        for sig, data in subrecords(node):
            if sig == "NAME" and node.sig == "REFR":
                want = [struct.unpack("<I", data)[0]]
            elif sig in ("SCRI", "SCRO"):
                want = [struct.unpack("<I", data)[0]]
            else:
                continue
            for fid in want:
                checked += 1
                if fid in esp_fids or fid in ENGINE_REFS:
                    continue
                rec = master.get_record(fid)
                if rec is None:
                    missing[(node.sig, sig, "%08X" % fid)] += 1
    for key, n in missing.items():
        prob.error("%s %s references missing formID %s (x%d)" % (key[0], key[1], key[2], n))
    return checked


def check_overrides_and_ids(records, master, prob):
    seen = {}
    overrides = []
    new_count = 0
    for node, _anc in records:
        if node.fid in seen:
            prob.error("duplicate formID %08X (%s and %s)" % (node.fid, seen[node.fid], node.sig))
        seen[node.fid] = node.sig
        if node.fid >> 24 == 0x01:
            new_count += 1
            continue
        if node.fid >> 24 != 0x00:
            prob.error("formID %08X has unexpected load-order index" % node.fid)
            continue
        m = master.get_record(node.fid)
        if m is None:
            prob.error("override %08X %s has no master record" % (node.fid, node.sig))
        elif m.sig != node.sig:
            prob.error("override %08X type %s differs from master %s" % (
                node.fid, node.sig, m.sig))
        else:
            overrides.append((node.fid, node.sig))
    return new_count, overrides


def check_cell_placement(root, prob, master):
    """Every temporary ref sits in the cell that contains its position; persistence flags match
    their group (CLAUDE.md section 10)."""
    stats = collections.Counter()
    for node, anc in []:
        pass
    # walk explicitly so we know the ancestors
    def visit(node, anc):
        for c in node.children:
            if c.kind == "group":
                visit(c, anc + (c,))
                continue
            if c.sig != "REFR":
                continue
            cell = group_under(anc, 6)
            cell_fid = struct.unpack("<I", cell.label)[0] if cell else None
            kind = "persistent" if group_under(anc, 8) else (
                "temporary" if group_under(anc, 9) else "?")
            persistent_flag = bool(c.flags & FLAG_PERSISTENT)
            if kind == "persistent" and not persistent_flag:
                prob.error("REFR %08X in persistent group without persistent flag" % c.fid)
            if kind == "temporary" and persistent_flag:
                prob.error("REFR %08X in temporary group has persistent flag" % c.fid)
            data = None
            for sig, d in subrecords(c):
                if sig == "DATA":
                    data = d
            if data is None or len(data) != 24:
                prob.error("REFR %08X missing 24-byte DATA" % c.fid)
                continue
            x, y, z = struct.unpack_from("<3f", data)
            stats[(hex(cell_fid) if cell_fid else "?", kind)] += 1
            if cell_fid and kind == "temporary":
                cell = master.get_record(cell_fid)
                xclc = cell.get("XCLC") if cell is not None else None
                if xclc:
                    gx, gy = struct.unpack_from("<ii", xclc)
                    want = (int(x // CELL_SIZE), int(y // CELL_SIZE))
                    if want != (gx, gy):
                        prob.error("REFR %08X at (%.1f, %.1f) is in cell (%d,%d) but stored in cell (%d,%d)"
                                   % (c.fid, x, y, want[0], want[1], gx, gy))
    visit(root, ())
    return stats


def print_tree(node, lines, depth=0, max_listed=6):
    pad = "  " * depth
    if node.kind == "group":
        lines.append("%sGRUP type=%d(%s) label=%s size=%d" % (
            pad, node.gtype, GROUP_TYPE_NAMES.get(node.gtype, "?"), label_text(node), node.size))
        recs = [c for c in node.children if c.kind == "record"]
        groups = [c for c in node.children if c.kind == "group"]
        # records of the group, summarised by type when long
        if recs:
            if len(recs) <= max_listed:
                for r in recs:
                    lines.append("%s  %s %08X %s" % (pad, r.sig, r.fid, record_name(r)))
            else:
                counts = collections.Counter(r.sig for r in recs)
                for sig, n in counts.items():
                    lines.append("%s  %s x%d" % (pad, sig, n))
        for g in groups:
            print_tree(g, lines, depth + 1, max_listed)
    else:
        lines.append("%s%s %08X %s" % (pad, node.sig, node.fid, record_name(node)))


def record_name(node):
    for sig, data in subrecords(node):
        if sig == "EDID":
            return data.rstrip(b"\x00").decode("latin-1")
    return ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("plugin", nargs="?", default=DEFAULT_PLUGIN)
    ap.add_argument("--master", default=MASTER_PATH)
    ap.add_argument("--quiet", action="store_true", help="do not print the full tree")
    args = ap.parse_args()

    prob = Problems()
    plugin_path = os.path.abspath(args.plugin)
    size = os.path.getsize(plugin_path)
    print("validating %s (%d bytes)" % (plugin_path, size))

    with open(plugin_path, "rb") as fh:
        buf = fh.read()

    # --- TES4 header ---------------------------------------------------------
    if buf[:4] != b"TES4":
        prob.error("file does not start with TES4")
    hdr_sig, hdr_size, hdr_flags, _, _, hdr_fv, _ = RECORD_HEADER.unpack_from(buf, 0)
    tes4_payload = buf[24:24 + hdr_size]
    tes4_subs = [(s.type, s.data) for s in iter_subrecords(tes4_payload)]
    tes4_map = dict(tes4_subs)
    hedr = tes4_map.get("HEDR")
    if hedr is None or len(hedr) != 12:
        prob.error("TES4 HEDR missing or wrong size")
        hedr = b"\x00" * 12
    version, num_records, next_obj = struct.unpack("<fII", hedr)
    masters = [d.rstrip(b"\x00").decode("latin-1") for t, d in tes4_subs if t == "MAST"]
    print("TES4: flags=0x%X formVersion=%d HEDR version=%.2f numRecords=%d nextObjectID=%08X "
          "masters=%s" % (hdr_flags, hdr_fv, version, num_records, next_obj, masters))
    if masters != ["FalloutNV.esm"]:
        prob.error("unexpected masters %r" % (masters,))
    if hdr_flags & ~0x1:
        prob.error("unexpected TES4 flags 0x%X" % hdr_flags)
    if hdr_flags & 0x1:
        # deliberate (pipeline --esm): navmesh in plain ESPs is buggy in FO3/FNV
        print("note: plugin is ESM-flagged")
    if next_obj <= 0x01000800:
        prob.error("nextObjectID %08X does not follow the new formID range" % next_obj)

    # --- master top order & master record access -------------------------------
    master = Plugin(args.master)
    master.build_index()
    top_order = [g.label_text() for g in master.top_groups()]

    # --- strict structural parse ----------------------------------------------
    root = Node("group", label=b"", gtype=-1, size=len(buf), offset=0)
    strict_parse(buf, 24 + hdr_size, len(buf), root, prob)
    valid_worlds = {r.form_id for r in master.records(sig="WRLD", top="WRLD")}
    valid_worlds |= {n.fid for n, _a in []}
    check_structure(root, prob, master, top_order, valid_worlds)

    records = []
    walk_records(root, records)
    groups = 0

    def count_groups(n):
        nonlocal groups
        for c in n.children:
            if c.kind == "group":
                groups += 1
                count_groups(c)
    count_groups(root)
    total_items = len(records) + groups
    if num_records != total_items:
        prob.error("HEDR numRecords %d but file holds %d records+groups" % (num_records,
                                                                             total_items))

    # --- esplib re-read ---------------------------------------------------------
    with Plugin(plugin_path) as ep:
        esp_count = 0
        esp_groups = 0
        for item in ep.walk():
            if item.kind == "group":
                esp_groups += 1
            else:
                esp_count += 1
                try:
                    item.subrecords()  # decodes and bounds-checks every subrecord
                except Exception as exc:  # noqa: BLE001
                    prob.error("esplib failed on %s %08X: %s" % (item.sig, item.form_id, exc))
        if esp_count != len(records) or esp_groups != groups:
            prob.error("esplib counts (%d rec, %d grp) disagree with strict parse (%d, %d)" % (
                esp_count, esp_groups, len(records), groups))
        esp_fids = {}
        for node, _ in records:
            esp_fids[node.fid] = node.sig
        for node, _ in records:
            pass

    # --- references, ids, placement ---------------------------------------------
    checked = check_references(records, esp_fids, master, prob)
    new_count, overrides = check_overrides_and_ids(records, master, prob)
    placement = check_cell_placement(root, prob, master)

    # --- TES4 ONAM: overridden temporary references --------------------------------
    onam = tes4_map.get("ONAM")
    onam_list = []
    if onam is not None:
        if len(onam) % 4:
            prob.error("TES4 ONAM size %d is not a multiple of 4" % len(onam))
        onam_list = list(struct.unpack("<%dI" % (len(onam) // 4), onam[:len(onam) // 4 * 4]))
    if len(set(onam_list)) != len(onam_list):
        prob.error("TES4 ONAM lists a formID twice")
    # the official DLCs list every overridden temporary REFR/ACHR/ACRE/NAVM/LAND here; a NAVM
    # override missing from ONAM crashed the game (0x6A55A6) when an actor pathed near it
    temp_overrides = set()
    for node, anc in records:
        if (node.sig in ("REFR", "ACHR", "ACRE", "NAVM", "LAND") and group_under(anc, 9) is not None
                and node.fid >> 24 == 0x00):
            temp_overrides.add(node.fid)
    for fid in onam_list:
        if fid not in temp_overrides:
            prob.error("TES4 ONAM lists %08X, which is not an overridden temporary record here" % fid)
    for fid in sorted(temp_overrides - set(onam_list)):
        prob.error("override %08X of a temporary record is missing from TES4 ONAM" % fid)

    # --- STAT model paths should exist under tools/out/meshes (warning only) -------
    for node, _anc in records:
        if node.sig == "STAT":
            for sig, data in subrecords(node):
                if sig == "MODL":
                    rel = data.rstrip(b"\x00").decode("latin-1").replace("\\", os.sep)
                    beside = os.path.join(os.path.dirname(os.path.abspath(plugin_path)), "meshes", rel)
                    if not (os.path.isfile(os.path.join(HERE, "out", "meshes", rel))
                            or os.path.isfile(beside)):
                        prob.warn("STAT %08X MODL %s not found under tools/out/meshes" % (node.fid, rel))

    # --- anything left over that should not be there ---------------------------
    leftover_xtel = [n.fid for n, _ in records for s, _ in subrecords(n) if s == "XTEL"]
    if leftover_xtel:
        prob.warn("XTEL still present on %s" % ", ".join("%08X" % f for f in leftover_xtel))
    compressed = [n.fid for n, _ in records if n.flags & FLAG_COMPRESSED]
    if compressed:
        prob.error("compressed records written: %s" % compressed[:5])

    # --- tree and summary ------------------------------------------------------
    sig_counts = collections.Counter(n.sig for n, _ in records)
    print()
    print("record totals: " + ", ".join("%s %d" % (k, v) for k, v in sorted(sig_counts.items())))
    print("groups: %d; records: %d; esplib walked %d records and %d groups" % (
        groups, len(records), esp_count, esp_groups))
    print("reference checks: %d NAME/SCRI/SCRO targets checked" % checked)
    print("new formIDs (index 01): %d" % new_count)
    print("overrides of master records: " + ", ".join(
        "%08X %s" % (f, s) for f, s in overrides))
    print("cell placement: " + ", ".join(
        "%s/%s=%d" % (k[0], k[1], v) for k, v in sorted(placement.items())))
    if not args.quiet:
        print()
        lines = []
        for item in root.children:
            if item.kind == "record":
                lines.append("%s %08X %s" % (item.sig, item.fid, record_name(item)))
            else:
                print_tree(item, lines, 0)
        print("\n".join(lines))
    master.close()

    print()
    for w in prob.warnings:
        print("WARNING: " + w)
    if prob.errors:
        for e in prob.errors:
            print("ERROR: " + e)
        print("RESULT: FAIL (%d errors)" % len(prob.errors))
        return 1
    print("RESULT: PASS (0 errors, %d warnings)" % len(prob.warnings))
    return 0


if __name__ == "__main__":
    sys.exit(main())

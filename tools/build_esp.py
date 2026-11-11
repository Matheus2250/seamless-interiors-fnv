#!/usr/bin/env python3
"""Build SeamlessInteriors.esp (Fallout: New Vegas seamless-interior prototype).

Copies the Goodsprings General Store interior (CELL GSGeneralStore, 0x001057F1) into a
hidden pocket underground in exterior cell (-17, 0) of WastelandNV, and turns the two
load doors (interior door 0x00105BD6 and exterior door 0x00106373) into normal doors that
teleport the player with scripts instead of XTEL.

Usage:
    py -I build_esp.py            # no scripts: doors are plain doors (always works)
    py -I build_esp.py --scripts  # also compile scripts via tools/scriptc.py

Standard library only. FalloutNV.esm is opened read-only; the only file written is the
output plugin (default tools/out/SeamlessInteriors.esp).
"""

import argparse
import importlib.util
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    # py -I does not put the script directory on sys.path, so add it explicitly.
    sys.path.insert(0, HERE)

from esplib import GROUP_HEADER, RECORD_HEADER, Plugin, iter_subrecords  # noqa: E402

MASTER_PATH = r"C:\Program Files (x86)\Steam\steamapps\common\Fallout New Vegas\Data\FalloutNV.esm"
DEFAULT_OUT = os.path.join(HERE, "out", "SeamlessInteriors.esp")
SCRIPT_SRC_DIR = os.path.join(HERE, "scripts")
SCRIPTC_PATH = os.path.join(HERE, "scriptc.py")
AUTHOR = "SeamlessInteriors prototype"

FLAG_PERSISTENT = 0x00000400
FLAG_COMPRESSED = 0x00040000
FORM_VERSION = 15
NEW_FORM_BASE = 0x01000800  # load-order index 01 (this plugin, one master)

GS_INTERIOR_CELL = 0x001057F1
GS_INTERIOR_DOOR = 0x00105BD6        # persistent REFR inside the store, has XTEL -> exterior
GS_INTERIOR_DOOR_BASE = 0x000FB730   # NVCraftsmanHomeDoor (DOOR)
EXT_DOOR_REF = 0x00106373            # persistent REFR in WastelandNV persistent cell, XTEL -> interior
WORLD = 0x000DA726                   # WastelandNV
WORLD_PERSIST_CELL = 0x000846EA
POCKET_CELL = 0x000DAEB9             # exterior cell (-17, 0)
XMARKER_HEADING_BASE = 0x00000034    # STAT XMarkerHeading (verified in master)

CELL_CENTER = (-67584.0, 2048.0)     # centre of exterior cell (-17, 0)
CELL_X_RANGE = (-69632.0, -65536.0)  # [min, max) for cell (-17, 0)
CELL_Y_RANGE = (0.0, 4096.0)
# The engine pushes the player back up when they end up below the landscape,
# so the pocket floats high above the terrain (ground here is ~8400).
POCKET_MIN_Z = 20000.0

# Subrecords dropped from every copied / moved reference.
DROP_ALWAYS = {"XTEL", "XNDP", "XOWN", "XLOC", "XESP", "XLKR", "XEZN", "XRGD", "XPRM"}

# Base record types that may be placed as normal references.
PLACEABLE_BASE_SIGS = {
    "STAT", "MSTT", "FURN", "ACTI", "CONT", "LIGH", "ALCH", "MISC", "WEAP", "ARMO",
    "AMMO", "BOOK", "NOTE", "KEYM", "INGR", "TACT", "IDLM", "DOOR", "SCOL", "TERM",
}

# Names of the new records. Fixed IDs are reserved in every mode so toggling --scripts
# never renumbers anything.
NAME_EXIT_DOOR = "SLGSStoreExitDoor"
NAME_ENTER_DOOR = "SLGSStoreEnterDoor"
NAME_DEST_IN = "SLGSStoreDestInREF"
NAME_DEST_OUT = "SLGSStoreDestOutREF"
NAME_ENTER_SCRIPT = "SLGSStoreEnterSCRIPT"
NAME_EXIT_SCRIPT = "SLGSStoreExitSCRIPT"


# ----------------------------------------------------------------------------
# Binary encoding helpers
# ----------------------------------------------------------------------------

def edid_bytes(name):
    return name.encode("latin-1") + b"\x00"


def sub_bytes(sig, data):
    """One subrecord; sizes over 65535 use the XXXX prefix."""
    assert len(sig) == 4, sig
    data = bytes(data)
    if len(data) > 0xFFFF:
        return (struct.pack("<4sHI", b"XXXX", 4, len(data))
                + struct.pack("<4sH", sig.encode("latin-1"), 0) + data)
    return struct.pack("<4sH", sig.encode("latin-1"), len(data)) + data


def record_bytes(sig, form_id, flags, subs, vc_info=0, version=FORM_VERSION, unknown=0):
    """New record, always uncompressed."""
    payload = b"".join(sub_bytes(s, d) for s, d in subs)
    header = RECORD_HEADER.pack(sig.encode("latin-1"), len(payload),
                                flags & ~FLAG_COMPRESSED, form_id, vc_info, version, unknown)
    return header + payload


def override_record_bytes(src, subs, flags):
    """Record override that keeps the master's header fields (vc info, version, unknown)."""
    payload = b"".join(sub_bytes(s, d) for s, d in subs)
    header = RECORD_HEADER.pack(src.sig.encode("latin-1"), len(payload),
                                flags & ~FLAG_COMPRESSED, src.form_id, src.vc_info,
                                src.form_version, src.unknown)
    return header + payload


def group_bytes(label, group_type, content):
    """GRUP with size including its 24-byte header. label: 4 raw bytes or a formID int."""
    if isinstance(label, int):
        label = struct.pack("<I", label)
    assert len(label) == 4
    return GROUP_HEADER.pack(b"GRUP", GROUP_HEADER.size + len(content), label,
                             group_type, 0, 0) + content


def count_items(buf, pos=0, end=None):
    """Records + groups in a byte range (TES4 excluded). Matches the TES4 numRecords convention."""
    end = len(buf) if end is None else end
    n = 0
    while pos < end:
        if buf[pos:pos + 4] == b"GRUP":
            size = GROUP_HEADER.unpack_from(buf, pos)[1]
            n += 1 + count_items(buf, pos + GROUP_HEADER.size, pos + size)
            pos += size
        else:
            dsize = RECORD_HEADER.unpack_from(buf, pos)[1]
            n += 1
            pos += RECORD_HEADER.size + dsize
    return n


def pack_pos_rot(pos, rot_bytes):
    return struct.pack("<3f", *pos) + rot_bytes


def parse_xtel(data):
    """XTEL: formID, pos(3f), rot(3f), flags(u32). Returns dict."""
    if data is None or len(data) != 32:
        raise SystemExit("unexpected XTEL size %r" % (None if data is None else len(data)))
    ref, px, py, pz, rx, ry, rz, flags = struct.unpack("<I3f3fI", data)
    return {"ref": ref, "pos": (px, py, pz), "rot": (rx, ry, rz), "rot_bytes": data[16:28],
            "flags": flags}


def fmt_pos(p):
    return "(%.2f, %.2f, %.2f)" % tuple(p)


# ----------------------------------------------------------------------------
# Master lookups
# ----------------------------------------------------------------------------

def find_cell_children_group(plugin, cell_form_id):
    """The GRUP 6 (cell children) for an interior cell in the CELL top group."""
    def descend(g):
        return g.group_type in (2, 3)  # interior block / sub-block only
    for item in plugin.walk_top("CELL", descend=descend):
        if (item.kind == "group" and item.group_type == 6
                and struct.unpack("<I", item.label)[0] == cell_form_id):
            return item
    raise SystemExit("cell children group for %08X not found" % cell_form_id)


def require_master(plugin, form_id, sig):
    rec = plugin.get_record(form_id)
    if rec is None or rec.sig != sig:
        raise SystemExit("master record %08X is not a %s (got %r)" % (
            form_id, sig, None if rec is None else rec.sig))
    return rec


class MasterEdids:
    """Lazy EDID -> formID map over the master, used only for script name resolution."""

    def __init__(self, plugin):
        self.plugin = plugin
        self.map = None

    def lookup(self, name):
        if self.map is None:
            self.map = {}
            for rec in self.plugin.records():
                edid = rec.edid()
                if edid is not None:
                    self.map.setdefault(edid.lower(), rec.form_id)
        return self.map.get(name.lower())


# ----------------------------------------------------------------------------
# Classification of interior references
# ----------------------------------------------------------------------------

def classify_interior_refs(plugin, cell_group):
    """Returns (copy_refs, skipped). copy_refs is a list of Record (REFR) to copy."""
    copies = []
    skipped = []
    for item in cell_group.children():
        if item.kind != "record":
            continue
        if item.sig == "ACHR":
            skipped.append((item.form_id, "ACHR", "", "actor placement (Chet), not copied"))
            continue
        if item.sig == "NAVM":
            skipped.append((item.form_id, "NAVM", "", "navmesh, pocket gets no navmesh"))
            continue
        if item.sig != "REFR":
            skipped.append((item.form_id, item.sig, "", "unexpected record type"))
            continue
        name_data = item.get("NAME")
        base_id = struct.unpack("<I", name_data)[0]
        base = plugin.get_record(base_id)
        base_desc = "%08X %s" % (base_id, base.edid() if base else "MISSING")
        if item.form_id == GS_INTERIOR_DOOR:
            skipped.append((item.form_id, "REFR", base_desc,
                            "interior door: replaced by persistent pocket door copy"))
        elif item.get("XTEL") is not None:
            skipped.append((item.form_id, "REFR", base_desc, "door teleport marker (XTEL)"))
        elif base is not None and base.edid() == "NorthMarker":
            skipped.append((item.form_id, "REFR", base_desc, "interior-only compass marker"))
        elif base is None:
            skipped.append((item.form_id, "REFR", base_desc, "base record missing in master"))
        elif base.sig not in PLACEABLE_BASE_SIGS:
            skipped.append((item.form_id, "REFR", base_desc,
                            "base type %s not placeable" % base.sig))
        else:
            copies.append(item)
    return copies, skipped


# ----------------------------------------------------------------------------
# Record construction
# ----------------------------------------------------------------------------

def moved_subrecords(src, offset, drop, name_override=None):
    """Subrecords of src with DATA position moved by offset and drop-list removed."""
    out = []
    dx, dy, dz = offset
    for s in src.subrecords():
        if s.type in drop:
            continue
        if s.type == "NAME" and name_override is not None:
            out.append(("NAME", struct.pack("<I", name_override)))
        elif s.type == "DATA" and src.sig == "REFR":
            if len(s.data) != 24:
                raise SystemExit("unexpected REFR DATA size %d in %08X" % (len(s.data), src.form_id))
            px, py, pz = struct.unpack_from("<3f", s.data)
            out.append(("DATA", pack_pos_rot((px + dx, py + dy, pz + dz), s.data[12:24])))
        else:
            out.append((s.type, s.data))
    return out


def position_of(rec):
    return struct.unpack_from("<3f", rec.get("DATA"))


def make_door_record(src_door, new_form_id, edid, script_fid):
    subs = []
    for s in src_door.subrecords():
        if s.type == "EDID":
            subs.append(("EDID", edid_bytes(edid)))
            continue
        if s.type == "SNAM" and script_fid is not None:
            subs.append(("SCRI", struct.pack("<I", script_fid)))
        subs.append((s.type, s.data))
    if script_fid is not None and not any(t == "SCRI" for t, _ in subs):
        raise SystemExit("could not place SCRI in DOOR %08X" % src_door.form_id)
    return record_bytes("DOOR", new_form_id, src_door.flags, subs)


def make_marker_ref(form_id, edid, pos, rot_bytes):
    subs = [("EDID", edid_bytes(edid)),
            ("NAME", struct.pack("<I", XMARKER_HEADING_BASE)),
            ("DATA", pack_pos_rot(pos, rot_bytes))]
    return record_bytes("REFR", form_id, FLAG_PERSISTENT, subs)


def inside_cell(pos):
    x, y, _ = pos
    return (CELL_X_RANGE[0] <= x < CELL_X_RANGE[1]) and (CELL_Y_RANGE[0] <= y < CELL_Y_RANGE[1])


# ----------------------------------------------------------------------------
# Scripts (optional)
# ----------------------------------------------------------------------------

def load_scriptc():
    if not os.path.isfile(SCRIPTC_PATH):
        raise SystemExit("--scripts requested but %s does not exist" % SCRIPTC_PATH)
    spec = importlib.util.spec_from_file_location("scriptc", SCRIPTC_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    for attr in ("compile_script", "build_subrecords"):
        if not callable(getattr(mod, attr, None)):
            raise SystemExit("scriptc.py does not expose %s()" % attr)
    return mod


def normalize_subrecords(subs):
    """build_subrecords() output -> list of (sig str, bytes). Accepts bytes, (sig, data)
    pairs, or objects with .type/.data."""
    if isinstance(subs, (bytes, bytearray)):
        return [(s.type, s.data) for s in iter_subrecords(bytes(subs))]
    out = []
    for item in subs:
        if isinstance(item, tuple) and len(item) == 2:
            sig, data = item
        elif hasattr(item, "type") and hasattr(item, "data"):
            sig, data = item.type, item.data
        else:
            raise SystemExit("unsupported subrecord item from build_subrecords: %r" % (item,))
        if isinstance(sig, bytes):
            sig = sig.decode("latin-1")
        out.append((sig, bytes(data)))
    return out


def compile_scripts(plugin, our_ids):
    mod = load_scriptc()
    master_edids = MasterEdids(plugin)
    ours = {k.lower(): v for k, v in our_ids.items()}

    def resolve(name):
        key = name.lower()
        if key in ours:
            return ours[key]
        fid = master_edids.lookup(name)
        if fid is None:
            raise KeyError(name)
        return fid

    compiled = {}
    for edid, filename in ((NAME_ENTER_SCRIPT, "SLGSStoreEnterSCRIPT.txt"),
                           (NAME_EXIT_SCRIPT, "SLGSStoreExitSCRIPT.txt")):
        with open(os.path.join(SCRIPT_SRC_DIR, filename), "r", encoding="utf-8") as fh:
            source = fh.read()
        result = mod.compile_script(source, resolve)
        subs = normalize_subrecords(mod.build_subrecords(result))
        if not any(sig == "EDID" for sig, _ in subs):
            subs.insert(0, ("EDID", edid_bytes(edid)))
        compiled[edid] = subs
    return compiled


# ----------------------------------------------------------------------------
# Main build
# ----------------------------------------------------------------------------

def build(args):
    out_path = os.path.abspath(args.out)
    game_data = os.path.normcase(os.path.dirname(MASTER_PATH)) + os.sep
    if os.path.normcase(out_path).startswith(game_data):
        raise SystemExit("refusing to write into the game folder: %s" % out_path)

    plugin = Plugin(args.master)  # read-only memory map
    plugin.build_index()
    print("master: %s (%d records indexed)" % (args.master, len(plugin._index)))

    # --- master records we rely on -------------------------------------------
    cell = require_master(plugin, GS_INTERIOR_CELL, "CELL")
    int_door = require_master(plugin, GS_INTERIOR_DOOR, "REFR")
    ext_door = require_master(plugin, EXT_DOOR_REF, "REFR")
    base_door = require_master(plugin, GS_INTERIOR_DOOR_BASE, "DOOR")
    wrld = require_master(plugin, WORLD, "WRLD")
    wrld_cell = require_master(plugin, WORLD_PERSIST_CELL, "CELL")
    pocket_cell = require_master(plugin, POCKET_CELL, "CELL")
    marker_base = require_master(plugin, XMARKER_HEADING_BASE, "STAT")
    assert marker_base.edid() == "XMarkerHeading", marker_base.edid()
    assert int_door.get("NAME") == struct.pack("<I", GS_INTERIOR_DOOR_BASE)
    assert ext_door.get("NAME") == struct.pack("<I", GS_INTERIOR_DOOR_BASE)
    assert ext_door.flags & FLAG_PERSISTENT and int_door.flags & FLAG_PERSISTENT
    assert wrld_cell.flags & FLAG_PERSISTENT
    for r in (wrld, wrld_cell, pocket_cell, base_door, int_door, ext_door):
        assert not r.is_compressed, "%r is compressed; raw copy needs uncompressed" % r
    assert pocket_cell.get("XCLC") is not None
    xclc_x, xclc_y = struct.unpack_from("<ii", pocket_cell.get("XCLC"))
    assert (xclc_x, xclc_y) == (-17, 0), (xclc_x, xclc_y)
    assert pocket_cell.path[0].label == b"WRLD"
    assert [g.group_type for g in pocket_cell.path] == [0, 1, 4, 5], pocket_cell.path_description()
    assert [g.group_type for g in wrld_cell.path] == [0, 1]

    ext_xtel = parse_xtel(ext_door.get("XTEL"))
    int_xtel = parse_xtel(int_door.get("XTEL"))
    assert ext_xtel["ref"] == GS_INTERIOR_DOOR, hex(ext_xtel["ref"])
    assert int_xtel["ref"] == EXT_DOOR_REF, hex(int_xtel["ref"])

    # --- collect and place the store refs -------------------------------------
    cell_group = find_cell_children_group(plugin, GS_INTERIOR_CELL)
    copy_refs, skipped = classify_interior_refs(plugin, cell_group)
    print("interior refs: %d to copy, %d skipped" % (len(copy_refs), len(skipped)))
    for fid, sig, base, reason in skipped:
        print("  skip %08X %s %s : %s" % (fid, sig, base, reason))

    xs = [position_of(r)[0] for r in copy_refs]
    ys = [position_of(r)[1] for r in copy_refs]
    zs = [position_of(r)[2] for r in copy_refs]
    bbox_before = (min(xs), max(xs), min(ys), max(ys), min(zs), max(zs))
    print("bbox before: x [%.2f, %.2f] y [%.2f, %.2f] z [%.2f, %.2f]" % bbox_before)
    cx = (bbox_before[0] + bbox_before[1]) / 2.0
    cy = (bbox_before[2] + bbox_before[3]) / 2.0
    offset = (CELL_CENTER[0] - cx, CELL_CENTER[1] - cy, POCKET_MIN_Z - bbox_before[4])
    print("offset: (%.4f, %.4f, %.4f)" % offset)
    bbox_after = (bbox_before[0] + offset[0], bbox_before[1] + offset[0],
                  bbox_before[2] + offset[1], bbox_before[3] + offset[1],
                  bbox_before[4] + offset[2], bbox_before[5] + offset[2])
    print("bbox after : x [%.2f, %.2f] y [%.2f, %.2f] z [%.2f, %.2f]" % bbox_after)
    print("pocket centre xy after: (%.2f, %.2f), min z after: %.2f" % (
        (bbox_after[0] + bbox_after[1]) / 2, (bbox_after[2] + bbox_after[3]) / 2, bbox_after[4]))
    assert abs((bbox_after[0] + bbox_after[1]) / 2 - CELL_CENTER[0]) < 1e-3
    assert abs((bbox_after[2] + bbox_after[3]) / 2 - CELL_CENTER[1]) < 1e-3
    assert abs(bbox_after[4] - POCKET_MIN_Z) < 1e-3

    # --- new form IDs --------------------------------------------------------
    next_id = [NEW_FORM_BASE]

    def alloc():
        fid = next_id[0]
        next_id[0] += 1
        return fid

    ids = {
        NAME_EXIT_DOOR: alloc(),
        NAME_ENTER_DOOR: alloc(),
        NAME_DEST_IN: alloc(),
        NAME_DEST_OUT: alloc(),
        NAME_ENTER_SCRIPT: alloc(),
        NAME_EXIT_SCRIPT: alloc(),
    }
    pocket_door_fid = alloc()
    copy_fids = [alloc() for _ in copy_refs]

    # --- positions of the markers --------------------------------------------
    dest_in_pos = tuple(p + o for p, o in zip(ext_xtel["pos"], offset))
    dest_out_pos = int_xtel["pos"]
    int_door_pos = position_of(int_door)
    pocket_door_pos = tuple(p + o for p, o in zip(int_door_pos, offset))
    for label, p in (("pocket door", pocket_door_pos), ("DestIn", dest_in_pos)):
        assert inside_cell(p), "%s %s outside cell (-17,0)" % (label, fmt_pos(p))
    assert inside_cell(dest_out_pos), "DestOut %s outside cell" % fmt_pos(dest_out_pos)
    for r, fid in zip(copy_refs, copy_fids):
        p = tuple(v + o for v, o in zip(position_of(r), offset))
        assert inside_cell(p), "copy %08X at %s outside cell" % (r.form_id, fmt_pos(p))
    print("pocket door at %s, DestIn %s, DestOut %s" % (
        fmt_pos(pocket_door_pos), fmt_pos(dest_in_pos), fmt_pos(dest_out_pos)))

    # --- scripts -------------------------------------------------------------
    scpt_records = []
    script_ids = {}
    if args.scripts:
        compiled = compile_scripts(plugin, ids)
        for edid in (NAME_ENTER_SCRIPT, NAME_EXIT_SCRIPT):
            scpt_records.append(record_bytes("SCPT", ids[edid], 0, compiled[edid]))
            script_ids[edid] = ids[edid]
        print("scripts: compiled %s" % ", ".join(sorted(script_ids)))
    else:
        print("scripts: none (doors are plain doors)")

    def door_script(edid):
        return script_ids.get(edid) if args.scripts else None

    # --- records -------------------------------------------------------------
    exit_door = make_door_record(base_door, ids[NAME_EXIT_DOOR], NAME_EXIT_DOOR,
                                 door_script(NAME_EXIT_SCRIPT))
    enter_door = make_door_record(base_door, ids[NAME_ENTER_DOOR], NAME_ENTER_DOOR,
                                  door_script(NAME_ENTER_SCRIPT))

    pocket_door_subs = moved_subrecords(int_door, offset, DROP_ALWAYS | {"XTEL"},
                                        name_override=ids[NAME_EXIT_DOOR])
    pocket_door = record_bytes("REFR", pocket_door_fid, FLAG_PERSISTENT, pocket_door_subs)

    copy_records = []
    for r, fid in zip(copy_refs, copy_fids):
        subs = moved_subrecords(r, offset, DROP_ALWAYS)
        copy_records.append(record_bytes("REFR", fid, r.flags & ~FLAG_PERSISTENT, subs))

    # Exterior door override: same record, XTEL and its navmesh link removed, new base DOOR.
    ext_door_subs = moved_subrecords(ext_door, (0.0, 0.0, 0.0), DROP_ALWAYS,
                                     name_override=ids[NAME_ENTER_DOOR])
    ext_door_override = override_record_bytes(ext_door, ext_door_subs, ext_door.flags)

    dest_in = make_marker_ref(ids[NAME_DEST_IN], NAME_DEST_IN, dest_in_pos,
                              ext_xtel["rot_bytes"])
    dest_out = make_marker_ref(ids[NAME_DEST_OUT], NAME_DEST_OUT, dest_out_pos,
                               int_xtel["rot_bytes"])

    # --- group hierarchy -----------------------------------------------------
    # Labels for the existing groups come straight from the master.
    block_label = pocket_cell.path[2].label
    sub_label = pocket_cell.path[3].label

    # Persistent exterior refs (doors, markers) all live in the world persistent cell.
    world_persist = group_bytes(WORLD_PERSIST_CELL, 8,
                                ext_door_override + dest_in + dest_out + pocket_door)
    world_cell_children = group_bytes(WORLD_PERSIST_CELL, 6, world_persist)

    pocket_temp = group_bytes(POCKET_CELL, 9, b"".join(copy_records))
    pocket_children = group_bytes(POCKET_CELL, 6, pocket_temp)
    sub_block = group_bytes(sub_label, 5, pocket_cell.raw() + pocket_children)
    cell_block = group_bytes(block_label, 4, sub_block)

    world_children = group_bytes(WORLD, 1,
                                 wrld_cell.raw() + world_cell_children + cell_block)
    # OFST is an offset table that is only valid inside the master file, so drop it.
    wrld_subs = [(sub.type, sub.data) for sub in wrld.subrecords() if sub.type != "OFST"]
    wrld_override = override_record_bytes(wrld, wrld_subs, wrld.flags)
    wrld_top = group_bytes(b"WRLD", 0, wrld_override + world_children)

    door_top = group_bytes(b"DOOR", 0, exit_door + enter_door)

    body_parts = []
    if scpt_records:
        body_parts.append(group_bytes(b"SCPT", 0, b"".join(scpt_records)))
    body_parts.append(door_top)
    body_parts.append(wrld_top)  # master top-group order: SCPT < DOOR < WRLD
    body = b"".join(body_parts)

    # --- TES4 header ---------------------------------------------------------
    hedr_version = struct.unpack_from("<f", plugin.header.get("HEDR"), 0)[0]
    num_records = count_items(body)
    total_ids = next_id[0] - NEW_FORM_BASE
    tes4_subs = [
        ("HEDR", struct.pack("<fII", hedr_version, num_records, next_id[0])),
        ("CNAM", AUTHOR.encode("latin-1") + b"\x00"),
        ("MAST", MASTER_NAME_BYTES + b"\x00"),
        ("DATA", b"\x00" * 8),
    ]
    tes4 = record_bytes("TES4", 0, 0, tes4_subs, version=plugin.header.form_version,
                        unknown=plugin.header.unknown)
    data = tes4 + body

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    tmp = out_path + ".tmp"
    with open(tmp, "wb") as fh:
        fh.write(data)
    os.replace(tmp, out_path)

    # --- report --------------------------------------------------------------
    print()
    print("wrote %s (%d bytes)" % (out_path, len(data)))
    print("HEDR version %.2f, numRecords %d, nextObjectID %08X" % (
        hedr_version, num_records, next_id[0]))
    print("new formIDs allocated: %d (%08X..%08X)" % (
        total_ids, NEW_FORM_BASE, next_id[0] - 1))
    print("copied refs: %d, skipped refs: %d" % (len(copy_refs), len(skipped)))
    print("final formID list:")
    listing = [
        (ids[NAME_EXIT_DOOR], "DOOR", NAME_EXIT_DOOR),
        (ids[NAME_ENTER_DOOR], "DOOR", NAME_ENTER_DOOR),
        (ids[NAME_DEST_IN], "REFR", NAME_DEST_IN),
        (ids[NAME_DEST_OUT], "REFR", NAME_DEST_OUT),
        (pocket_door_fid, "REFR", "pocket door copy of 00105BD6 (persistent)"),
    ]
    if args.scripts:
        listing[4:4] = [(ids[NAME_ENTER_SCRIPT], "SCPT", NAME_ENTER_SCRIPT),
                        (ids[NAME_EXIT_SCRIPT], "SCPT", NAME_EXIT_SCRIPT)]
    for fid, sig, name in listing:
        print("  %08X %-4s %s" % (fid, sig, name))
    print("  %08X..%08X REFR  %d temporary copies of GSGeneralStore refs" % (
        copy_fids[0], copy_fids[-1], len(copy_fids)))
    print("overrides (master formIDs): 00106373 REFR (persistent, XTEL removed),"
          " 00846EA CELL, 0DA726 WRLD, 0DAEB9 CELL (unchanged raw copies)")
    plugin.close()


MASTER_NAME_BYTES = b"FalloutNV.esm"


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--scripts", action="store_true",
                    help="compile the two door scripts via tools/scriptc.py")
    ap.add_argument("--out", default=DEFAULT_OUT, help="output .esp path")
    ap.add_argument("--master", default=MASTER_PATH, help="FalloutNV.esm path (read-only)")
    args = ap.parse_args()
    build(args)


if __name__ == "__main__":
    main()

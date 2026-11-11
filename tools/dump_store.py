"""Dump the Goodsprings General Store interior cell from FalloutNV.esm to tools/out/gsstore.json.

Read-only: the game file is only ever opened for reading (memory-mapped).

Usage:  py -I dump_store.py            (run with -I so no local module can shadow stdlib)
"""

import collections
import json
import os
import struct
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from esplib import (  # noqa: E402
    Plugin, decode_formid, iter_subrecords,
    GROUP_TOP, GROUP_WORLD_CHILDREN, GROUP_EXTERIOR_CELL_BLOCK, GROUP_EXTERIOR_SUB_BLOCK,
    GROUP_CELL_CHILDREN, GROUP_PERSISTENT_CHILDREN, GROUP_TEMPORARY_CHILDREN,
    GROUP_VISIBLE_DISTANT_CHILDREN, INTERIOR_CELL_FLAG,
)

ESM = r"C:\Program Files (x86)\Steam\steamapps\common\Fallout New Vegas\Data\FalloutNV.esm"
OUT_PATH = os.path.join(HERE, "out", "gsstore.json")
TARGET_EDID = "GoodspringsGeneralStore"
CHILD_KIND = {
    GROUP_PERSISTENT_CHILDREN: "persistent",
    GROUP_TEMPORARY_CHILDREN: "temporary",
    GROUP_VISIBLE_DISTANT_CHILDREN: "visible_distant",
}
# Record types that can carry an XTEL (teleport) subrecord.
XTEL_SIGS = {"REFR", "ACHR", "ACRE", "PGRE", "PMIS", "PHZD", "PBAR", "PBEA", "PCBE",
             "PFLA", "PGRN", "PARW", "PLAT"}
CELL_HEX_WANTED = ["DATA", "XCLL", "LTMP", "XCAS", "XCMO", "XCIM", "XEZN", "XOWN"]


def hex_of(b):
    return b.hex()


def decode_pos_rot(data):
    """DATA of REFR/ACHR: 6 floats posX posY posZ rotX rotY rotZ (radians)."""
    if data is None or len(data) < 24:
        return None
    x, y, z, rx, ry, rz = struct.unpack_from("<6f", data, 0)
    return {"pos": [x, y, z], "rot_rad": [rx, ry, rz]}


def decode_xtel(data):
    """XTEL: door ref formID (u32), 6 floats pos/rot, u32 flags (32 bytes in FNV)."""
    if data is None:
        return None
    out = {"door_ref_formid": "%08X" % decode_formid(data, 0),
           "size": len(data), "hex": hex_of(data)}
    if len(data) >= 32:
        out["pos_rot"] = decode_pos_rot(data[4:28])
        out["flags"] = struct.unpack_from("<I", data, 28)[0]
    return out


def decode_cell_data(data):
    """CELL DATA: flags, uint8 in FNV (uint16 accepted if the size says so)."""
    if data is None:
        return None
    if len(data) == 1:
        return data[0]
    if len(data) == 2:
        return struct.unpack_from("<H", data, 0)[0]
    return None


def decode_xclc(data):
    if data is None or len(data) < 8:
        return None
    x, y = struct.unpack_from("<ii", data, 0)
    out = {"grid_x": x, "grid_y": y, "size": len(data), "hex": hex_of(data)}
    return out


def decode_lighting(data):
    """XCLL / LGTM DATA. Layout used (UESP-style, verify against sizes seen):
    0 ambient RGBA, 4 directional RGBA, 8 fog-near RGBA, 12 fog near f32, 16 fog far f32,
    20 dir rot X i32, 24 dir rot Y i32, 28 dir fade f32, 32 fog clip f32, 36 fog power f32."""
    if data is None:
        return None
    out = {"size": len(data), "hex": hex_of(data)}
    if len(data) >= 36:
        out["ambient_rgba"] = list(data[0:4])
        out["directional_rgba"] = list(data[4:8])
        out["fog_near_color_rgba"] = list(data[8:12])
        out["fog_near"], out["fog_far"] = struct.unpack_from("<ff", data, 12)
        out["directional_rot_x"], out["directional_rot_y"] = struct.unpack_from("<ii", data, 20)
        out["directional_fade"], out["fog_clip"] = struct.unpack_from("<ff", data, 28)
    if len(data) >= 40:
        out["fog_power"] = struct.unpack_from("<f", data, 36)[0]
    return out


def raw_sig_list(payload):
    """Subrecord type names exactly as stored, XXXX included (no size override applied)."""
    out = []
    pos = 0
    n = len(payload)
    override = None
    while pos + 6 <= n:
        sig = payload[pos:pos + 4].decode("latin-1")
        size = struct.unpack_from("<H", payload, pos + 4)[0]
        out.append(sig)
        if sig == "XXXX" and size == 4:
            override = struct.unpack_from("<I", payload, pos + 6)[0]
            pos += 10
            continue
        if override is not None:
            size = override
            override = None
        pos += 6 + size
    return out


def group_desc(g):
    return {"group_type": g.group_type, "type_name": g.type_name, "label": g.label_text(),
            "offset": g.offset, "size": g.size}


def chain_text(path, rec=None):
    parts = []
    for g in path:
        parts.append("GRUP[%s %s]" % (g.type_name, g.label_text()))
    if rec is not None:
        parts.append("%s[%08X]" % (rec.sig, rec.form_id))
    return " > ".join(parts)


def nearest_child_kind(path):
    for g in reversed(path):
        if g.group_type in CHILD_KIND:
            return CHILD_KIND[g.group_type]
    return "cell_children"


def path_value(path, gtype):
    for g in reversed(path):
        if g.group_type == gtype:
            return g.label_value()
    return None


def base_info(plugin, base_fid):
    if base_fid is None:
        return None
    rec = plugin.get_record(base_fid)
    if rec is None:
        return {"formid": "%08X" % base_fid, "found": False}
    return {"formid": "%08X" % base_fid, "found": True,
            "record_type": rec.sig, "edid": rec.edid()}


def describe_child(plugin, rec):
    subs = list(iter_subrecords(rec.payload()))
    first = {}
    for s in subs:
        first.setdefault(s.type, s.data)
    base = None
    if "NAME" in first and len(first["NAME"]) >= 4:
        base = decode_formid(first["NAME"], 0)
    return {
        "ref_type": rec.sig,
        "formid": "%08X" % rec.form_id,
        "base": base_info(plugin, base),
        # DATA is pos/rot only for placed objects; NAVM DATA is navmesh data, not a transform.
        "position_rotation": decode_pos_rot(first.get("DATA")) if rec.sig in XTEL_SIGS else None,
        "child_group": nearest_child_kind(rec.path),
        "group_path": [group_desc(g) for g in rec.path],
        "subrecord_types": [s.type for s in subs],
        "xtel": decode_xtel(first.get("XTEL")),
    }


def wrld_info(plugin, fid):
    if fid is None:
        return None
    rec = plugin.get_record(fid)
    if rec is None:
        return {"formid": "%08X" % fid, "found": False}
    return {"formid": "%08X" % fid, "record_type": rec.sig, "edid": rec.edid()}


def cell_info(plugin, fid):
    if fid is None:
        return None
    rec = plugin.get_record(fid)
    if rec is None:
        return {"formid": "%08X" % fid, "found": False}
    subs = {s.type: s.data for s in iter_subrecords(rec.payload()) if s.type in ("DATA", "XCLC")}
    flags = decode_cell_data(subs.get("DATA"))
    return {"formid": "%08X" % fid, "record_type": rec.sig, "edid": rec.edid(),
            "flags": flags, "is_interior": bool(flags is not None and flags & INTERIOR_CELL_FLAG),
            "xclc": decode_xclc(subs.get("XCLC"))}


def main():
    t_all = time.time()
    stats = {}
    with Plugin(ESM) as plugin:
        stats["file_bytes"] = plugin.size
        stats["tes4_header"] = {"flags": plugin.header.flags, "form_version": plugin.header.form_version,
                                "data_size": plugin.header.data_size}

        # Full index: one walk over every record (headers only, no decompression).
        t = time.time()
        index = plugin.build_index()
        stats["index_build_seconds"] = round(time.time() - t, 2)
        stats["records_indexed"] = len(index)
        stats["duplicate_formids"] = plugin.duplicate_form_ids

        # Format checks gathered from the index and records.
        top_byte = collections.Counter()
        form_versions = collections.Counter()
        compressed = 0
        for rec in index.values():
            top_byte["%02X" % (rec.form_id >> 24)] += 1
            form_versions[rec.form_version] += 1
            if rec.is_compressed:
                compressed += 1
        stats["formid_top_byte_histogram"] = dict(top_byte)
        stats["record_form_version_histogram"] = {str(k): v for k, v in form_versions.most_common()}
        stats["compressed_records"] = compressed

        # --- (a) pick the cell -------------------------------------------------
        t = time.time()
        candidates = plugin.search_edid("goodsprings", sig="CELL", top="CELL")
        stats["goodsprings_cell_search_seconds"] = round(time.time() - t, 2)
        cand_list = []
        for c in candidates:
            fl = decode_cell_data(next((s.data for s in c.subrecords() if s.type == "DATA"), None))
            cand_list.append({"formid": "%08X" % c.form_id, "edid": c.edid(), "flags": fl,
                              "is_interior": bool(fl is not None and fl & INTERIOR_CELL_FLAG)})
        exact = [c for c in candidates if c.edid() == TARGET_EDID]
        store_like = plugin.search_edid("generalstore", sig="CELL", top="CELL")
        store_list = [{"formid": "%08X" % c.form_id, "edid": c.edid()} for c in store_like]
        stats["general_store_cells"] = store_list
        if exact:
            cell = exact[0]
            selection = "exact EDID match: %s" % TARGET_EDID
        else:
            # Not an exact match. The Goodsprings-named CELL search above finds only a cave
            # (SLGoodspringsCaveINT). The Goodsprings general store is GSGeneralStore
            # ("GS" prefix; the Goodsprings door in section c leads into it).
            fallback = "GSGeneralStore"
            picked = [c for c in store_like if c.edid() == fallback]
            if not picked:
                raise SystemExit("no Goodsprings general store CELL found; store-like cells: %r" % store_list)
            cell = picked[0]
            selection = ("no exact EDID match for %s; picked %s (store-like CELLs: %s)" % (
                TARGET_EDID, fallback, ", ".join(s["edid"] for s in store_list)))

        cell_subs = list(iter_subrecords(cell.payload()))
        cell_flags_raw = next((s.data for s in cell_subs if s.type == "DATA"), None)
        cell_flags = decode_cell_data(cell_flags_raw)
        hexes = {}
        for want in CELL_HEX_WANTED:
            entries = [{"type": s.type, "size": len(s.data), "hex": hex_of(s.data)}
                       for s in cell_subs if s.type == want]
            if entries:
                hexes[want] = entries
        a_cell = {
            "selection": selection,
            "candidates_containing_goodsprings": cand_list,
            "formid": "%08X" % cell.form_id,
            "edid": cell.edid(),
            "record_flags": "0x%08X" % cell.flags,
            "record_data_size": cell.data_size,
            "flags_byte": cell_flags,
            "is_interior": bool(cell_flags is not None and cell_flags & INTERIOR_CELL_FLAG),
            "subrecords": [{"type": s.type, "size": len(s.data)} for s in cell_subs],
            "subrecords_absent_from_wanted_list": [w for w in CELL_HEX_WANTED if w not in hexes],
            "subrecord_hex": hexes,
            "group_path": [group_desc(g) for g in cell.path],
        }
        stats["cell_raw_subrecord_types"] = raw_sig_list(cell.payload())

        # --- (b) children of the cell -----------------------------------------
        # Walk only the top CELL group; skip exterior blocks and other cells' children.
        def prune(g):
            return g.group_type not in (GROUP_EXTERIOR_CELL_BLOCK, GROUP_EXTERIOR_SUB_BLOCK,
                                        GROUP_CELL_CHILDREN)

        t = time.time()
        child_recs = None
        for item in plugin.walk_top("CELL", prune):
            if (item.kind == "group" and item.group_type == GROUP_CELL_CHILDREN
                    and item.label_value() == cell.form_id):
                child_recs = [r for r in item.children() if r.kind == "record"]
                children_group = group_desc(item)
                break
        if child_recs is None:
            raise SystemExit("cell %08X has no children group" % cell.form_id)
        children = [describe_child(plugin, r) for r in child_recs]
        stats["children_walk_seconds"] = round(time.time() - t, 2)
        by_type = collections.Counter(c["ref_type"] for c in children)
        by_group = collections.Counter(c["child_group"] for c in children)

        b_children = {
            "cell_children_group": children_group,
            "count": len(children),
            "count_by_ref_type": dict(sorted(by_type.items())),
            "count_by_child_group": dict(sorted(by_group.items())),
            "refs": children,
        }

        # --- (c) exterior doors whose XTEL points at an interior door ref ----------
        door_ids = {c["formid"]: c for c in children
                    if c["ref_type"] == "REFR" and c["base"] and c["base"].get("record_type") == "DOOR"}
        door_fid_set = {int(k, 16) for k in door_ids}
        child_fids = {int(c["formid"], 16) for c in children}

        t = time.time()
        xtel_sizes = collections.Counter()
        xxxx_records = 0
        xtel_refs_scanned = 0
        matches = []
        xtel_any_dest = 0
        for rec in index.values():
            if rec.sig not in XTEL_SIGS:
                continue
            payload = rec.payload()
            if b"XXXX" in payload and "XXXX" in raw_sig_list(payload):
                xxxx_records += 1
            xtel = None
            for s in iter_subrecords(payload):
                if s.type == "XTEL":
                    xtel = s.data
                    break
            xtel_refs_scanned += 1
            if xtel is None:
                continue
            xtel_sizes[len(xtel)] += 1
            dest = decode_formid(xtel, 0)
            xtel_any_dest += 1
            if dest in door_fid_set and rec.form_id not in child_fids:
                matches.append(rec)
        stats["xtel_scan_seconds"] = round(time.time() - t, 2)
        stats["xtel_refs_scanned"] = xtel_refs_scanned
        stats["xtel_records_with_xtel"] = xtel_any_dest
        stats["xtel_size_histogram"] = {str(k): v for k, v in xtel_sizes.items()}
        stats["xxxx_in_scanned_ref_records"] = xxxx_records

        c_doors = []
        for rec in matches:
            d = describe_child(plugin, rec)
            cell_fid = path_value(rec.path, GROUP_CELL_CHILDREN)
            wrld_fid = path_value(rec.path, GROUP_WORLD_CHILDREN)
            parent = cell_info(plugin, cell_fid)
            d["target_interior_door"] = d["xtel"]["door_ref_formid"] if d["xtel"] else None
            d["parent_cell"] = parent
            d["parent_worldspace"] = wrld_info(plugin, wrld_fid)
            d["group_kind"] = d["child_group"]
            c_doors.append(d)
        c_doors.sort(key=lambda d: bool((d["parent_cell"] or {}).get("is_interior")))

        # --- (d) nesting path and raw size of the exterior cell of the first exterior door
        exterior_doors = [d for d in c_doors if d["parent_cell"] and not d["parent_cell"]["is_interior"]]
        d_entries = []
        seen_cells = set()
        for d in exterior_doors:
            cfid = int(d["parent_cell"]["formid"], 16)
            if cfid in seen_cells:
                continue
            seen_cells.add(cfid)
            crec = plugin.get_record(cfid)
            d_entries.append({
                "cell_formid": "%08X" % crec.form_id,
                "cell_edid": crec.edid(),
                "worldspace": wrld_info(plugin, path_value(crec.path, GROUP_WORLD_CHILDREN)),
                "nesting_path": [group_desc(g) for g in crec.path],
                "nesting_chain": chain_text(crec.path, crec),
                "record_raw_bytes_incl_header": crec.total_size,
                "record_data_size": crec.data_size,
                "record_compressed": crec.is_compressed,
                "record_flags": "0x%08X" % crec.flags,
                "xclc": cell_info(plugin, cfid)["xclc"],
            })
        # Verify exterior block labels against XCLC grid for every exterior cell in the file.
        grid_pairs = []
        for rec in index.values():
            if rec.sig != "CELL" or len(rec.path) < 2:
                continue
            if rec.path[-1].group_type != GROUP_EXTERIOR_SUB_BLOCK:
                continue
            xclc = next((s.data for s in iter_subrecords(rec.payload()) if s.type == "XCLC"), None)
            if xclc is None:
                continue
            gx, gy = struct.unpack_from("<ii", xclc, 0)
            grid_pairs.append((rec.path[-1].label_value(), (gx, gy)))  # (block y, block x), (grid x, grid y)
        grid_check = {"exterior_cells_checked": len(grid_pairs), "samples": grid_pairs[:5]}
        for div in (1, 2, 4, 8, 16, 32):
            hits = sum(1 for (by, bx), (gx, gy) in grid_pairs
                       if by == gy // div and bx == gx // div)
            grid_check["match_block_eq_grid_div_%d" % div] = hits
        stats["exterior_block_label_vs_xclc"] = grid_check
        stats["exterior_cell_block_label_fact"] = "block label bytes decoded as <hh: (y, x)"

        # --- (e) lighting -----------------------------------------------------
        ltmp_entries = hexes.get("LTMP", [])
        xcll_entries = hexes.get("XCLL", [])
        e_lighting = {
            "interior_xcll": decode_lighting(bytes.fromhex(xcll_entries[0]["hex"]) if xcll_entries else None),
            "ltmp_present": bool(ltmp_entries),
            "ltmp_template": None,
        }
        if ltmp_entries:
            lfid = decode_formid(bytes.fromhex(ltmp_entries[0]["hex"]), 0)
            lrec = plugin.get_record(lfid)
            lsubs = list(iter_subrecords(lrec.payload())) if lrec is not None else []
            ldata = next((s.data for s in lsubs if s.type == "DATA"), None)
            e_lighting["ltmp_template"] = {
                "formid": "%08X" % lfid,
                "record_type": lrec.sig if lrec else None,
                "edid": lrec.edid() if lrec else None,
                "values": decode_lighting(ldata),
            }

        result = {
            "generated_by": "tools/dump_store.py",
            "source_file": ESM,
            "scan_stats": stats,
            "a_interior_cell": a_cell,
            "b_child_refs": b_children,
            "c_exterior_doors_pointing_at_interior_door": c_doors,
            "c_exterior_doors_only": exterior_doors,
            "d_exterior_cell_nesting": d_entries,
            "e_lighting": e_lighting,
        }

    total = time.time() - t_all
    result["scan_stats"]["total_seconds"] = round(total, 2)
    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2)

    # ---- stdout summary -----------------------------------------------------
    print("cell: %s  formid=%s  interior=%s  (%s)" % (
        a_cell["edid"], a_cell["formid"], a_cell["is_interior"], selection))
    print("children: %d  by ref type: %s" % (b_children["count"], b_children["count_by_ref_type"]))
    print("doors in cell (REFR -> DOOR): %d" % len(door_fid_set))
    print("exterior door refs pointing at them: %d (interior matches: %d)" % (
        len(exterior_doors), len(c_doors) - len(exterior_doors)))
    for d in exterior_doors:
        pc = d["parent_cell"]
        print("  door %s base=%s pos=%s cell=%s %s grid=(%s,%s) world=%s group=%s" % (
            d["formid"], (d["base"] or {}).get("edid"), (d["position_rotation"] or {}).get("pos"),
            pc["formid"], pc["edid"], pc["xclc"]["grid_x"], pc["xclc"]["grid_y"],
            (d["parent_worldspace"] or {}).get("edid"), d["group_kind"]))
    for e in d_entries:
        print("  exterior cell nesting: %s" % e["nesting_chain"])
        print("    raw record bytes (incl. 24B header): %d, data size %d, compressed=%s" % (
            e["record_raw_bytes_incl_header"], e["record_data_size"], e["record_compressed"]))
    print("lighting: LTMP present=%s, XCLL=%s" % (
        e_lighting["ltmp_present"], "yes" if e_lighting["interior_xcll"] else "no"))
    print("index build %.2fs, xtel scan %.2fs, total %.2fs -> %s" % (
        stats["index_build_seconds"], stats["xtel_scan_seconds"], total, OUT_PATH))


if __name__ == "__main__":
    main()

"""Oracle tests for scriptc.

Compiles every SCPT record of FalloutNV.esm whose source uses only the supported subset and
compares SCDA, SCHR, SCRO and the local variables with the record stored in the ESM.

Usage:  py -I test_scriptc.py            (add -v to print every mismatch)

Read-only with respect to the game folder: FalloutNV.esm is only opened for reading.
"""

import json
import os
import struct
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import esplib  # noqa: E402
import scriptc  # noqa: E402

ESM = r"C:\Program Files (x86)\Steam\steamapps\common\Fallout New Vegas\Data\FalloutNV.esm"
EDID_CACHE = os.path.join(HERE, "out", "edid_index.json")


def build_edid_index():
    """Return {lowercased EDID: (formID, record signature)} for every record in the ESM (cached)."""
    if os.path.exists(EDID_CACHE):
        with open(EDID_CACHE, encoding="utf-8") as f:
            raw = json.load(f)
        return {k: (int(v[0], 16), v[1]) for k, v in raw.items()}
    index = {}
    with esplib.Plugin(ESM) as p:
        for rec in p.walk():
            if rec.kind != "record":
                continue
            try:
                e = rec.edid()
            except Exception:
                continue
            if e:
                index.setdefault(e.lower(), (rec.form_id, rec.sig))
    os.makedirs(os.path.dirname(EDID_CACHE), exist_ok=True)
    with open(EDID_CACHE, "w", encoding="utf-8") as f:
        json.dump({k: ["%08X" % v[0], v[1]] for k, v in index.items()}, f)
    return index


def load_oracle():
    recs = []
    with esplib.Plugin(ESM) as p:
        for r in p.records("SCPT"):
            d = {"formid": r.form_id, "order": []}
            slsd = []
            scvr = []
            for s in r.subrecords():
                d["order"].append(s.type)
                if s.type == "EDID":
                    d["edid"] = s.data.rstrip(b"\0").decode("cp1252", errors="replace")
                elif s.type == "SCHR":
                    d["schr"] = s.data
                elif s.type == "SCDA":
                    d["scda"] = s.data
                elif s.type == "SCTX":
                    d["sctx"] = s.data
                elif s.type == "SLSD":
                    slsd.append(s.data)
                elif s.type == "SCVR":
                    scvr.append(s.data.rstrip(b"\0").decode("cp1252", errors="replace"))
                elif s.type == "SCRV":
                    d.setdefault("scrv", []).append(struct.unpack("<I", s.data)[0])
                elif s.type == "SCRO":
                    d.setdefault("scro", []).append(struct.unpack("<I", s.data)[0])
            d.setdefault("scrv", [])
            d.setdefault("scro", [])
            d["vars"] = []
            for sl, name in zip(slsd, scvr):
                idx = struct.unpack_from("<I", sl, 0)[0]
                d["vars"].append((idx, name, sl[16]))
            d["sctx"] = d.get("sctx", b"")
            recs.append(d)
    return recs


def hexdiff(a, b, width=16, context=1, max_lines=6):
    lines = []
    n = max(len(a), len(b))
    first = None
    for i in range(n):
        x = a[i] if i < len(a) else None
        y = b[i] if i < len(b) else None
        if x != y:
            first = i
            break
    if first is None:
        return "  (identical)"
    start = max(0, (first // width - context) * width)
    end = min(n, ((first // width) + context + 1) * width)
    lines.append("  first difference at byte %d (ours %d bytes, oracle %d bytes)" % (first, len(a), len(b)))
    for off in range(start, end, width):
        ha = " ".join("%02x" % c for c in a[off:off + width]) if off < len(a) else "(end)"
        hb = " ".join("%02x" % c for c in b[off:off + width]) if off < len(b) else "(end)"
        lines.append("  %04x ours:   %s" % (off, ha))
        lines.append("  %04x oracle: %s" % (off, hb))
    return "\n".join(lines[: max_lines * 3])


def compare(result, rec):
    """Return dict field -> bool, plus textual details."""
    det = {}
    det["SCDA"] = result["scda"] == rec["scda"]
    our_schr = struct.unpack("<IIIIHH", result["schr"])
    if "schr" in rec:
        orc = struct.unpack("<IIIIHH", rec["schr"])
        det["SCHR_refcount"] = our_schr[1] == orc[1]
        det["SCHR_size"] = our_schr[2] == orc[2]
        det["SCHR_varcount"] = our_schr[3] == orc[3]
        det["SCHR_type"] = our_schr[4] == orc[4]
        det["SCHR_flags"] = our_schr[5] == orc[5]
    det["SCRO"] = result["scro"] == rec["scro"]
    det["SCRV"] = result["scrv"] == rec["scrv"]
    ours = sorted((i, n.lower(), t) for i, n, t in result["vars"])
    theirs = sorted((i, n.lower(), t) for i, n, t in rec["vars"])
    det["VARS"] = ours == theirs
    return det


def main():
    verbose = "-v" in sys.argv
    t0 = time.time()
    edid = build_edid_index()
    print("EDID index: %d entries (%.1fs)" % (len(edid), time.time() - t0))

    def resolve(name):
        hit = edid.get(name.lower())
        return hit[0] if hit else None

    def resolve_kind(name):
        hit = edid.get(name.lower())
        return hit[1] if hit else None

    oracle = load_oracle()
    print("oracle SCPT records: %d" % len(oracle))

    stats = {"total": len(oracle), "in_subset": 0, "unsupported": 0, "compile_error": 0}
    field_ok = {}
    exact = {"default_indices": 0, "oracle_indices": 0, "oracle_layout": 0}
    mismatch_examples = []
    unsupported_reasons = {}
    compile_errors = []
    subset_names = []

    for rec in oracle:
        src = rec["sctx"].decode("cp1252", errors="replace")
        # Script type (object / quest / magic effect) is a property of the SCPT record, not of the
        # source text, so it is passed in from the record in every mode.
        script_type = struct.unpack("<IIIIHH", rec["schr"])[4] if "schr" in rec else 0
        try:
            res_default = scriptc.compile_script(src, resolve, script_type=script_type,
                                                 resolve_kind=resolve_kind)
        except scriptc.UnsupportedError as e:
            stats["unsupported"] += 1
            key = str(e)[:60]
            unsupported_reasons[key] = unsupported_reasons.get(key, 0) + 1
            continue
        except scriptc.CompileError as e:
            stats["compile_error"] += 1
            compile_errors.append((rec["edid"], str(e)))
            continue
        stats["in_subset"] += 1
        subset_names.append(rec["edid"])

        # Variable indices exactly as the GECK stored them (history-dependent in general).
        oracle_idx = {n.lower(): i for i, n, _ in rec["vars"]}
        try:
            res_oracle = scriptc.compile_script(src, resolve, script_type=script_type,
                                                var_indices=oracle_idx, resolve_kind=resolve_kind)
        except (scriptc.UnsupportedError, scriptc.CompileError):
            res_oracle = None
        # Variable indices and the stored SCRO order (layout inherited from earlier compiles).
        try:
            res_layout = scriptc.compile_script(src, resolve, script_type=script_type,
                                                var_indices=oracle_idx, scro_seed=rec["scro"],
                                                resolve_kind=resolve_kind)
        except (scriptc.UnsupportedError, scriptc.CompileError):
            res_layout = None

        for label, res in (("default_indices", res_default), ("oracle_indices", res_oracle),
                           ("oracle_layout", res_layout)):
            if res is None:
                continue
            det = compare(res, rec)
            det["SCRO_as_set"] = sorted(res["scro"]) == sorted(rec["scro"])
            if label == "oracle_layout":
                # subrecord order (EDID, SCHR, SCDA, SCTX, SLSD/SCVR pairs, SCRV, SCRO)
                det["SUBREC_ORDER"] = [t for t, _ in scriptc.build_subrecords(res)] == rec["order"]
            if all(det.values()):
                exact[label] += 1
            for k, v in det.items():
                field_ok.setdefault(label, {}).setdefault(k, [0, 0])
                field_ok[label][k][0 if v else 1] += 1
            if label == "oracle_layout" and not all(det.values()) and len(mismatch_examples) < 6:
                mismatch_examples.append((rec["edid"], det, res, rec))

    print()
    print("Scripts total:            %d" % stats["total"])
    print("In subset (compiled):     %d" % stats["in_subset"])
    print("Unsupported (skipped):    %d" % stats["unsupported"])
    print("Compile errors (in subset but malformed or unresolved): %d" % stats["compile_error"])
    print()
    for label in ("default_indices", "oracle_indices", "oracle_layout"):
        print("Exact matches with %s: %d / %d" % (label, exact[label], stats["in_subset"]))
        for k, (ok, bad) in sorted(field_ok.get(label, {}).items()):
            print("    %-16s ok=%-5d mismatch=%d" % (k, ok, bad))
    print()
    print("Top unsupported reasons:")
    for k, v in sorted(unsupported_reasons.items(), key=lambda kv: -kv[1])[:12]:
        print("    %5d  %s" % (v, k))
    if compile_errors:
        print("Compile errors (first 10):")
        for name, msg in compile_errors[:10]:
            print("    %s: %s" % (name, msg))
    print()
    print("First mismatches (oracle_indices run):")
    for edid_name, det, res, rec in mismatch_examples:
        bad = [k for k, v in det.items() if not v]
        print("- %s  failing fields: %s" % (edid_name, ", ".join(bad)))
        if "SCDA" in bad:
            print(hexdiff(res["scda"], rec["scda"]))
        if "SCRO" in bad:
            print("  SCRO ours:   %s" % [hex(x) for x in res["scro"]])
            print("  SCRO oracle: %s" % [hex(x) for x in rec["scro"]])
        if "VARS" in bad:
            print("  vars ours:   %s" % res["vars"])
            print("  vars oracle: %s" % rec["vars"])
    named_report(oracle, resolve, resolve_kind)
    print("time: %.1fs" % (time.time() - t0))
    return 0


NAMED = [
    # statements the task asks about: IsActionRef player, Activate, GetPos, SetPos/SetAngle with a variable
    "GenericPlayerCantActivateSCRIPT",
    "MedicalClinicAutoDocScript",
    "SVComputerRoomDoor02Script",
    "REPCONDavisonDialogScript",
    "VPitPlayerFighterGo",
    "NellisArtilleryScript",
]


def named_report(oracle, resolve, resolve_kind):
    """Compile named oracle scripts in layout mode and show whether their bytes match."""
    print()
    print("Named oracle scripts (layout mode):")
    by_name = {r["edid"]: r for r in oracle}
    for name in NAMED:
        rec = by_name.get(name)
        if rec is None:
            print("  %s: not in ESM" % name)
            continue
        src = rec["sctx"].decode("cp1252", errors="replace")
        st = struct.unpack("<IIIIHH", rec["schr"])[4]
        idx = {n.lower(): i for i, n, _ in rec["vars"]}
        try:
            res = scriptc.compile_script(src, resolve, script_type=st, var_indices=idx,
                                         scro_seed=rec["scro"], resolve_kind=resolve_kind)
        except (scriptc.UnsupportedError, scriptc.CompileError) as e:
            print("  %s: outside subset (%s)" % (name, e))
            continue
        ok = res["scda"] == rec["scda"]
        print("  %s: %s (%d bytes)" % (name, "EXACT" if ok else "differs", len(rec["scda"])))
        if not ok:
            ours = scriptc.annotate(res["scda"], res["scro"], res["scrv"], res["vars"])
            theirs = scriptc.annotate(rec["scda"], rec["scro"], rec["scrv"], rec["vars"])
            for a, b in zip(ours, theirs):
                if a != b:
                    print("      ours:   %s" % a)
                    print("      oracle: %s" % b)
                    break


if __name__ == "__main__":
    sys.exit(main())

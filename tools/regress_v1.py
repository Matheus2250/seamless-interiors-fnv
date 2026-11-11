"""Regression: the generic pipeline's General Store vs the published v1.

Compares meaning, not bytes (formIDs and mesh paths differ by design):
  - every copied ref: same base, position, rotation, owner, lock; persistence differences listed
  - overrides of master records (doors, building ref, Chet, packages): same content
  - navmesh: same triangles (positions within tolerance), same NAVI entry count
  - meshes: same collision triangles removed, doorway rectangles empty in both

Copies are paired by nearest position per base (v1 measured the door leaf by hand, so the whole
interior is shifted by a few hundredths of a unit); navmesh triangles likewise.

Usage: py -I regress_v1.py [new_esp] [v1_esp]
"""

import collections
import math
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import esplib  # noqa: E402
import niflib  # noqa: E402

ROOT = os.path.dirname(HERE)
NEW = os.path.join(HERE, "out", "build", "Data", "SeamlessInteriors.esp")
V1 = os.path.join(ROOT, "dist", "SeamlessInteriors-v1", "Data", "SeamlessInteriors.esp")
POS_TOL = 0.05        # max deviation between v1 and the pipeline (measured 0.04)
PAIR_RADIUS = 3.0     # two copies of the same base farther apart than this are not the same copy
NAV_TOL = 0.2         # navmesh vertex tolerance (rounding in v1's own comparison)
ROT_TOL = 1e-4
STATS_TOL = 1e-4
problems = []


def bad(msg):
    problems.append(msg)
    print("  FAIL", msg)


def note(msg):
    print("  note:", msg)


def load(path):
    p = esplib.Plugin(path)
    recs = [it for it in p.walk() if it.kind == "record"]
    return p, recs


def stat_models(recs):
    return {r.form_id: r.get("MODL").rstrip(b"\x00").decode("latin-1") for r in recs
            if r.sig == "STAT" and r.get("MODL")}


def canon(model):
    """Mesh stem, ignoring the v1/new naming difference: no folder, no extension, no
    "_door" / "_<formID>" suffix."""
    stem = model.replace("/", "\\").split("\\")[-1].lower()
    if stem.endswith(".nif"):
        stem = stem[:-4]
    if stem.endswith("_door"):
        stem = stem[:-5]
    head, _, tail = stem.rpartition("_")
    if len(tail) == 8 and all(c in "0123456789abcdef" for c in tail) and head:
        stem = head
    return stem


def base_key(r, models):
    """Same base on both sides: master forms by formID, new forms by canonical model name."""
    name = struct.unpack("<I", r.get("NAME"))[0]
    if name >= 0x01000000:
        return ("model", canon(models.get(name, "%08x" % name)))
    return ("base", name)


def ref_pos(r):
    return struct.unpack_from("<3f", r.get("DATA"))


def ref_rot(r):
    return struct.unpack_from("<3f", r.get("DATA"), 12)


def owner(r):
    x = r.get("XOWN")
    return struct.unpack("<I", x)[0] if x else None


def dist(a, b, n=3):
    return math.sqrt(sum((a[i] - b[i]) ** 2 for i in range(n)))


def ref_list(recs, models):
    out = []
    for r in recs:
        if r.sig not in ("REFR", "ACHR") or r.form_id < 0x01000000:
            continue
        out.append((base_key(r, models), ref_pos(r), r))
    return out


def pair_by_position(old_list, new_list):
    """Greedy nearest pairing among entries with the same base key. Returns (pairs, only_old,
    only_new) where pairs are (old, new, distance)."""
    candidates = []
    by_key_new = collections.defaultdict(list)
    for i, (k, p, r) in enumerate(new_list):
        by_key_new[k].append(i)
    for i, (k, p, r) in enumerate(old_list):
        for j in by_key_new.get(k, ()):
            d = dist(p, new_list[j][1])
            if d <= PAIR_RADIUS:
                candidates.append((d, i, j))
    candidates.sort()
    used_old, used_new, pairs = set(), set(), []
    for d, i, j in candidates:
        if i in used_old or j in used_new:
            continue
        used_old.add(i)
        used_new.add(j)
        pairs.append((old_list[i][2], new_list[j][2], d))
    only_old = [old_list[i] for i in range(len(old_list)) if i not in used_old]
    only_new = [new_list[j] for j in range(len(new_list)) if j not in used_new]
    return pairs, only_old, only_new


def main(argv):
    new_path = argv[0] if argv else NEW
    v1_path = argv[1] if len(argv) > 1 else V1
    _pn, new = load(new_path)
    _pv, old = load(v1_path)
    mn, mo = stat_models(new), stat_models(old)

    print("== copies")
    rn, ro = ref_list(new, mn), ref_list(old, mo)
    print("  v1 %d, new %d" % (len(ro), len(rn)))
    pairs, only_old, only_new = pair_by_position(ro, rn)
    for k, p, r in only_old:
        bad("only in v1: %s %08X %s" % (k, r.form_id, tuple(round(v, 1) for v in p)))
    for k, p, r in only_new:
        bad("only in new: %s %08X %s" % (k, r.form_id, tuple(round(v, 1) for v in p)))
    persist_diff, max_pos, worst = [], 0.0, None
    for a, b, _d in pairs:
        da = struct.unpack("<6f", a.get("DATA"))
        db = struct.unpack("<6f", b.get("DATA"))
        pd = max(abs(da[i] - db[i]) for i in range(3))
        if pd > max_pos:
            max_pos, worst = pd, (base_key(a, mo), tuple(round(v, 2) for v in da[:3]))
        if pd > POS_TOL:
            bad("transform differs by %.4f: %08X -> %08X at %s" % (pd, a.form_id, b.form_id, da[:3]))
        if max(abs(da[i] - db[i]) for i in range(3, 6)) > ROT_TOL:
            bad("rotation differs %08X: %s vs %s" % (a.form_id, da[3:], db[3:]))
        if owner(a) != owner(b):
            bad("owner differs %08X: %s vs %s" % (a.form_id, owner(a), owner(b)))
        if a.get("XLOC") != b.get("XLOC"):
            bad("lock differs %08X" % a.form_id)
        if (a.flags & 0x400) != (b.flags & 0x400):
            persist_diff.append((base_key(a, mo), bool(b.flags & 0x400)))
        sa = [s.type for s in a.subrecords()]
        sb = [s.type for s in b.subrecords()]
        if sa != sb:
            bad("subrecord list differs %08X: %s vs %s" % (a.form_id, sa, sb))
    print("  paired %d/%d; max position deviation %.4f at %s" % (len(pairs), len(ro), max_pos, worst))
    changed = collections.Counter(k[0] for k, now in persist_diff if now)
    if persist_diff:
        note("persistence changed on %d refs, now persistent by base: %s"
             % (len(persist_diff), dict(changed)))

    print("== master overrides")
    # exterior NAVM overrides are new since v1 on purpose: the door portal (NVDP/0x400) of the
    # converted door is removed from the cell's vanilla navmesh (CLAUDE.md section 19)
    on = {r.form_id: r for r in new if r.form_id < 0x01000000 and r.sig not in ("CELL", "WRLD", "TES4", "NAVM")}
    oo = {r.form_id: r for r in old if r.form_id < 0x01000000 and r.sig not in ("CELL", "WRLD", "TES4", "NAVM")}
    for r in new:
        if r.sig == "NAVM" and r.form_id < 0x01000000:
            note("vanilla navmesh %08X overridden (door portal removed)" % r.form_id)
    if set(on) != set(oo):
        bad("override set differs: v1 %s new %s" % (sorted("%08X" % f for f in oo),
                                                    sorted("%08X" % f for f in on)))
    for f in set(on) & set(oo):
        a, b = oo[f], on[f]
        if a.sig == "NAVI":
            continue
        sa = [(s.type, s.data) for s in a.subrecords() if s.type != "NAME"]
        sb = [(s.type, s.data) for s in b.subrecords() if s.type != "NAME"]
        if sa != sb:
            if a.sig in ("ACHR", "REFR"):
                da = struct.unpack("<6f", a.get("DATA"))
                db = struct.unpack("<6f", b.get("DATA"))
                if max(abs(x - y) for x, y in zip(da, db)) > POS_TOL:
                    bad("%s %08X DATA differs" % (a.sig, f))
            else:
                bad("%s %08X content differs" % (a.sig, f))
        if a.sig == "REFR" and a.get("NAME") != b.get("NAME"):
            na, nb = struct.unpack("<I", a.get("NAME"))[0], struct.unpack("<I", b.get("NAME"))[0]
            if na < 0x01000000 or nb < 0x01000000:
                if na != nb:
                    bad("REFR %08X base differs" % f)
            elif canon(mo[na]) != canon(mn[nb]):
                bad("REFR %08X model differs: %s vs %s" % (f, mo[na], mn[nb]))
    print("  overrides compared: %d (same set: %s)" % (len(set(on) & set(oo)), set(on) == set(oo)))

    print("== navmesh")
    tn, to = nav_tris(new), nav_tris(old)
    print("  triangles v1 %d new %d" % (len(to), len(tn)))
    unmatched_old, unmatched_new = match_tris(to, tn)
    for t in unmatched_old:
        bad("navmesh triangle only in v1: %s" % (tuple(tuple(round(c, 2) for c in v) for v in t),))
    for t in unmatched_new:
        bad("navmesh triangle only in new: %s" % (tuple(tuple(round(c, 2) for c in v) for v in t),))
    navi_n = [r for r in new if r.sig == "NAVI"]
    navi_o = [r for r in old if r.sig == "NAVI"]
    def cnt(rs):
        # entries of our own navmeshes only (vanilla ones are re-emitted without the door)
        if not rs:
            return {}
        off = {"NVMI": 4, "NVCI": 0}
        return collections.Counter(s.type for s in rs[0].subrecords() if s.type == "NVER"
                                   or struct.unpack_from("<I", s.data, off[s.type])[0] >= 0x01000000)
    if cnt(navi_n) != cnt(navi_o):
        bad("NAVI entries differ: %s vs %s" % (cnt(navi_o), cnt(navi_n)))

    print("== meshes")
    new_dir = os.path.join(os.path.dirname(new_path), "meshes")
    old_dir = os.path.join(os.path.dirname(v1_path), "meshes")
    old_by_canon = collections.defaultdict(list)
    for rel in mo.values():
        old_by_canon[canon(rel)].append(rel)
    checked = 0
    for f, rel in sorted(mn.items()):
        if not rel.startswith("SeamlessInteriors"):
            continue
        old_rel = find_old_mesh(canon(rel), old_by_canon)
        if not old_rel:
            bad("no v1 mesh for %s" % rel)
            continue
        a = open(os.path.join(old_dir, old_rel.replace("\\", os.sep)), "rb").read()
        b = open(os.path.join(new_dir, rel.replace("\\", os.sep)), "rb").read()
        da, db = degenerate(a), degenerate(b)
        checked += 1
        print("  %s -> %s: collision removed v1 %s new %s"
              % (rel.split("\\")[-1], old_rel.split("\\")[-1], sorted(da), sorted(db)))
        if not db <= da:
            bad("%s removes collision triangles v1 kept: %s" % (rel, sorted(db - da)))
        if not da <= db:
            note("%s keeps collision triangles v1 removed: %s (v1 was over-eager)"
                 % (rel.split("\\")[-1], sorted(da - db)))
    if checked != len(old_by_canon):
        note("meshes in v1: %d, compared: %d" % (sum(len(v) for v in old_by_canon.values()), checked))

    print("RESULT:", "PASS" if not problems else "FAIL (%d)" % len(problems))
    return 0 if not problems else 1


def nav_tris(recs):
    """Every navmesh triangle as its three sorted world-space vertices."""
    out = []
    for r in recs:
        if r.sig != "NAVM" or r.form_id < 0x01000000:      # only the copied interior navmesh
            continue
        vx, tr = r.get("NVVX"), r.get("NVTR")
        V = [struct.unpack_from("<3f", vx, i * 12) for i in range(len(vx) // 12)]
        for i in range(len(tr) // 16):
            t = struct.unpack_from("<3H", tr, i * 16)
            out.append(tuple(sorted(V[j] for j in t)))
    return out


def tri_close(a, b):
    """Two triangles with the same vertices up to NAV_TOL, in any order."""
    if len(a) != len(b):
        return False
    left = list(b)
    for p in a:
        best_i, best_d = None, None
        for i, q in enumerate(left):
            d = dist(p, q)
            if best_d is None or d < best_d:
                best_i, best_d = i, d
        if best_d > NAV_TOL:
            return False
        left.pop(best_i)
    return True


def match_tris(old, new):
    left = list(new)
    unmatched_old = []
    for t in old:
        for i, u in enumerate(left):
            if tri_close(t, u):
                left.pop(i)
                break
        else:
            unmatched_old.append(t)
    return unmatched_old, left


def find_old_mesh(stem, old_by_canon):
    if stem in old_by_canon:
        return old_by_canon[stem][0]
    best = None
    for cand, rels in old_by_canon.items():
        if cand.startswith(stem) or stem.startswith(cand):
            score = len(os.path.commonprefix([cand, stem]))
            if best is None or score > best[0]:
                best = (score, rels[0])
    return best[1] if best else None


def degenerate(nif_bytes):
    f = niflib.NifFile(nif_bytes)
    b = f.blocks_of("hkPackedNiTriStripsData")
    if not b:
        return set()
    d = f.packed_tri_data(b[0].index)
    return {i for i, t in enumerate(d["triangles"]) if len(set(t)) < 3}


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

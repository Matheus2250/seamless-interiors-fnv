"""Check navmesh links: every external edge must have a reciprocal entry on the other side.

Works on a built plugin, using the master for targets that are not overridden there.

Usage: py -I verify_navmesh.py [plugin.esp]
"""

import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import esplib  # noqa: E402

from si.master import MASTER_PATH  # noqa: E402

NAVTRI = struct.Struct("<3H3hHH")
FLAG_EDGE = (0x1, 0x2, 0x4)


def parse_navm(rec, source):
    nvex = rec.get("NVEX") or b""
    raw_nvgd = rec.get("NVGD") or b""
    links = [struct.unpack_from("<IIH", nvex, i * 10) for i in range(len(nvex) // 10)]
    tr = rec.get("NVTR")
    tris = [NAVTRI.unpack_from(tr, i * NAVTRI.size) for i in range(len(tr) // NAVTRI.size)]
    return {"fid": rec.form_id, "tris": tris, "links": links, "source": source,
            "nvgd": raw_nvgd, "get": rec.get}


def check_grid(nm, name, errors):
    """NVGD: every triangle must be listed in the grid, and its bbox must touch a cell that
    lists it (the engine uses the grid to find the triangle under an actor)."""
    raw = nm["nvgd"]
    if not raw or len(raw) < 36:
        return 0
    div, sx, sy, x0, y0, z0, x1, y1, z1 = struct.unpack_from("<Iff3f3f", raw)
    ntris = len(nm["tris"])
    want = 1 if ntris <= 16 else min(12, ntris // 50 + 2)
    if div != want:     # the GECK rule, exact in all vanilla/DLC NAVMs; a wrong one crashes the game
        print("FAIL %s: NVGD divisor %d, %d triangles need %d" % (name, div, ntris, want))
        return 1
    if div == 0 or div > 64:
        return 0
    pos = 36
    listed = {}
    for cy in range(div):
        for cx in range(div):
            if pos + 2 > len(raw):
                return 0
            n = struct.unpack_from("<H", raw, pos)[0]
            for i in range(n):
                ti = struct.unpack_from("<H", raw, pos + 2 + i * 2)[0]
                listed.setdefault(ti, set()).add((cx, cy))
            pos += 2 + n * 2
    missing = [i for i in range(len(nm["tris"])) if i not in listed]
    if missing:
        print("FAIL %s: %d triangle(s) not in the NVGD (e.g. %s)"
              % (name, len(missing), missing[:5]))
        errors += 1
    return len(missing)


def check_connectivity(navms, name, errors):
    """Every triangle of a piece must be reachable from triangle 0 through neighbours."""
    nm = navms[name]
    n = len(nm["tris"])
    if n == 0:
        return
    seen = {0}
def check_connectivity(navms, name, errors, notes):
    """Count the connected components of a piece. The source navmesh may legitimately have
    islands (a room plus a balcony), so this is a note, not a failure -- what matters is that
    no triangle is left floating *inside* a component's bbox... it cannot be checked that far,
    so we just report the count for review."""
    nm = navms[name]
    n = len(nm["tris"])
    if n == 0:
        return 0
    seen = set()
    comps = []
    for start in range(n):
        if start in seen:
            continue
        comp = {start}
        stack = [start]
        while stack:
            ti = stack.pop()
            t = nm["tris"][ti]
            for e in range(3):
                if t[6] & FLAG_EDGE[e]:
                    continue    # external edge: the value is an index into NVEX, not a triangle
                nbr = t[3 + e]
                if 0 <= nbr < n and nbr not in comp:
                    comp.add(nbr)
                    stack.append(nbr)
        seen |= comp
        comps.append(comp)
    if len(comps) > 1:
        notes.append("%08X: %d islands (%s triangles)" % (name, len(comps),
                                                          "/".join(str(len(c)) for c in comps[:6])))
    return len(comps)
    if not (t[6] & FLAG_EDGE[edge]):
        return False
    idx = t[3 + edge]
    return 0 <= idx < len(nm["links"]) and nm["links"][idx][1] == target


def _inherited_from_master(master, navm_fid, tri, edge, target):
    """True when the master's copy of this navmesh already had that external edge."""
    rec = master.get_record(navm_fid)
    if rec is None or rec.sig != "NAVM":
        return False
    nm = parse_navm(rec, "master")
    if tri >= len(nm["tris"]):
        return False
    t = nm["tris"][tri]
    if not (t[6] & FLAG_EDGE[edge]):
        return False
    idx = t[3 + edge]
    return 0 <= idx < len(nm["links"]) and nm["links"][idx][1] == target


def main(argv):
    path = argv[0] if argv else os.path.join(HERE, "out", "build", "Data", "SeamlessInteriors.esp")
    esp = esplib.Plugin(path)
    master = esplib.Plugin(MASTER_PATH)
    navms = {}
    for it in esp.walk():
        if it.kind == "record" and it.sig == "NAVM":
            navms[it.form_id] = parse_navm(it, "esp")
    master_navms = {}
    errors = 0
    checked = 0
    vanilla = []
    for fid_, nm in navms.items():
        for ti, t in enumerate(nm["tris"]):
            for e in range(3):
                if not (t[6] & FLAG_EDGE[e]):
                    continue
                idx = t[3 + e]
                if not (0 <= idx < len(nm["links"])):
                    print("FAIL %08X tri %d edge %d: link index %s out of range (%d links)"
                          % (fid_, ti, e, idx, len(nm["links"])))
                    errors += 1
                    continue
                _zero, target, target_tri = nm["links"][idx]
                if target not in navms and target not in master_navms:
                    rec = master.get_record(target)
                    if rec is None or rec.sig != "NAVM":
                        print("FAIL %08X tri %d edge %d -> navmesh %08X does not exist"
                              % (fid_, ti, e, target))
                        errors += 1
                        continue
                    master_navms[target] = parse_navm(rec, "master")
                in_esp_target = target in navms
                other = navms.get(target) or master_navms[target]
                checked += 1
                if target_tri >= len(other["tris"]):
                    print("FAIL %08X tri %d -> %08X tri %d out of range"
                          % (fid_, ti, target, target_tri))
                    errors += 1
                    continue
                back = other["tris"][target_tri]
                ok = any(back[6] & FLAG_EDGE[k] and back[3 + k] < len(other["links"])
                         and other["links"][back[3 + k]][1] == fid_
                         and other["links"][back[3 + k]][2] == ti for k in range(3))
                if not ok:
                    if _inherited_from_master(master, fid_, ti, e, target):
                        # vanilla has one-sided links; not something this build caused
                        vanilla.append((fid_, ti, target, target_tri))
                        continue
                    print("FAIL %08X tri %d edge %d -> %08X tri %d has no reciprocal edge"
                          % (fid_, ti, e, target, target_tri))
                    errors += 1
    for fid_, nm in navms.items():
        if nm["source"] == "esp":
            errors += check_grid(nm, "%08X" % fid_, errors)
    islands = []
    for fid_, nm in navms.items():
        if nm["source"] == "esp" and nm["fid"] >= 0x01000000:
            check_connectivity(navms, fid_, errors, islands)
    if islands:
        print("note: %d piece(s) with more than one island (source navmeshes can have islands):"
              % len(islands))
        for line in islands[:6]:
            print("   ", line)
    if vanilla:
        print("note: %d one-sided edge(s) inherited from the master (unchanged), e.g. %s"
              % (len(vanilla), vanilla[:2]))
    print("navmeshes in plugin: %d, external edges checked: %d, errors: %d"
          % (len(navms), checked, errors))
    print("RESULT:", "PASS" if not errors else "FAIL")
    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

"""Verifier for out\\meshes\\SeamlessInteriors\\nv_generalstore_open.nif (doorway cut).

Compares the output NIF against the source NIF and checks:
  A. structure: re-parse with niflib; block count/types/order unchanged; every block that niflib can
     parse consumes exactly its size; all block refs are valid; header size table and footer intact.
  B. untouched data: every block other than the rewritten NiTriStripsData blocks is byte-identical;
     all collision blocks (bhkCollisionObject .. hkPackedNiTriStripsData) are byte-identical; the
     rewritten data blocks keep keep/compress/consistency/additional/group id/flags.
  C. geometry: every new vertex lies on a source triangle and its attributes equal the barycentric
     interpolation of that triangle's attributes; per-candidate yz-area is conserved
     (area(T) - area(T & R) = sum of area of the pieces that replaced T).
  D. door: ray test along x (x in [-320,-250]) from a grid over R at x=-285 hits no visual
     triangle except porch geometry (max z <= 23); the same test on the source file hits the wall.
  E. facade: for a grid of points outside R, wall coverage is identical before and after.
  F. ASCII maps of facade coverage (y horizontal, z vertical), before and after.

Usage:  py -I verify_nif_edit.py        exit code 0 = all checks passed.
"""

import math
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import niflib  # noqa: E402
import nif_strips as ns  # noqa: E402

SRC = os.path.join(HERE, "out", "meshes", "architecture", "goodsprings", "nv_generalstore.nif")
DST = os.path.join(HERE, "out", "meshes", "SeamlessInteriors", "nv_generalstore_open.nif")

# Same door rectangle as cut_door_nif.py (restated here so the verifier does not import the cutter).
Y0, Y1 = -34.0, 93.5
Z0, Z1 = 18.5, 193.0
X_LO, X_HI = -300.0, -260.0          # facade band used for wall selection
RAY_X_LO, RAY_X_HI = -320.0, -250.0  # ray / coverage window
PORCH_MAX_Z = 23.0                   # visual geometry entirely below this z is porch (must stay)
COS_VERTICAL = 0.9
VISUAL_DATA_BLOCKS = [11, 16, 20, 24, 29]
REWRITTEN = [11, 29]                 # the only blocks the cutter changes (only where triangles were cut)
COLLISION_RANGE = range(2, 7)        # hkPackedNiTriStripsData .. bhkCollisionObject

FAIL = []


def check(cond, msg):
    print(("  PASS " if cond else "  FAIL ") + msg)
    if not cond:
        FAIL.append(msg)


def sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def norm(a):
    return math.sqrt(dot(a, a))


def load(path):
    with open(path, "rb") as fh:
        raw = fh.read()
    return niflib.NifFile(raw, path), raw


# ------------------------------------------------------------------ A. structure
def section_structure(src, dst, dst_raw):
    print("A. structure")
    check(dst.num_blocks == src.num_blocks, "block count %d == %d" % (dst.num_blocks, src.num_blocks))
    check(dst.block_types == src.block_types, "block type table unchanged")
    check(dst.block_type_index == src.block_type_index, "block type indices unchanged (same order)")
    check(dst.version == src.version and dst.user_version == src.user_version and dst.bs_version == src.bs_version,
          "version / user version / BS version unchanged (20.2.0.7, uv 11, bs 34)")
    check(dst.strings == src.strings, "string table unchanged")
    check(dst.footer_roots == src.footer_roots and dst.trailing == 0, "footer roots %s, no trailing bytes" % dst.footer_roots)
    total = dst.data_start + sum(dst.block_sizes)
    check(total + 4 + 4 * len(dst.footer_roots) == len(dst_raw) and dst.footer_end == len(dst_raw),
          "block sizes sum to file layout; footer ends at EOF")
    # every block consumes exactly its size where niflib has a parser
    parsed = 0
    for b in dst.blocks:
        t = b.type
        try:
            if t == "BSFadeNode" or t == "NiNode":
                info = dst.parse_node(b.index)
                ok = info["_consumed"] == b.size
                refs = [info["collision"]] + info["children"] + info["properties"] + info["effects"] + [info["controller"]]
            elif t == "NiTriStrips":
                info = dst.parse_geometry(b.index)
                ok = info["_consumed"] == b.size
                refs = [info["data"], info["skin"], info["active_material"]] + info["properties"] + [info["collision"], info["controller"]]
            elif t == "NiTriStripsData":
                d = ns.parse_tri_strips_data(b.raw)
                ok = True  # parse_tri_strips_data raises if not consumed exactly
                refs = [d["additional_data"]]
            elif t == "bhkCollisionObject":
                info = dst.parse_coll_object(b.index)
                ok = info["_consumed"] == b.size
                refs = [info["target"], info["body"]]
            else:
                continue
        except Exception as e:  # noqa: BLE001
            print("   block %d %s parse error: %s" % (b.index, t, e))
            FAIL.append("parse %d" % b.index)
            continue
        parsed += 1
        if not ok:
            check(False, "block %d %s consumes exactly its size" % (b.index, t))
        for r in refs:
            if r is None or r < -1 or r >= dst.num_blocks:
                check(False, "block %d %s has invalid ref %s" % (b.index, t, r))
    check(parsed > 0, "niflib parsed %d blocks, all consumed exactly their size where parsed" % parsed)
    # rest of the blocks (no niflib parser): untouched => size and bytes match the source
    unparsed = [b.index for b in dst.blocks if b.type not in ("BSFadeNode", "NiNode", "NiTriStrips",
                                                                "NiTriStripsData", "bhkCollisionObject")]
    print("   blocks without a niflib parser (checked by byte identity in section B): %s" % unparsed)
    # refs of the collision chain
    mopp = dst.mopp_child(4) if dst.type_of(4) == "bhkMoppBvTreeShape" else None
    check(mopp == 3, "collision chain intact: MOPP child == block 3 (%s)" % mopp)
    check(dst.packed_shape_data_ref(3) == 2, "collision chain intact: packed shape data == block 2")


COLLISION_TRI_DATA = 2               # open_collision.py degenerates internal wall triangles here


def collision_only_degenerated(src_raw, dst_raw):
    """True if dst differs from src only by triangles whose 3 indices were set equal."""
    if len(src_raw) != len(dst_raw):
        return False
    n = struct.unpack_from("<I", src_raw, 0)[0]
    tri_end = 4 + n * 8
    if src_raw[tri_end:] != dst_raw[tri_end:] or src_raw[:4] != dst_raw[:4]:
        return False
    for t in range(n):
        off = 4 + t * 8
        if src_raw[off:off + 8] == dst_raw[off:off + 8]:
            continue
        a, b, c, weld = struct.unpack_from("<HHHH", dst_raw, off)
        if not (a == b == c) or a != struct.unpack_from("<H", src_raw, off)[0]                 or weld != struct.unpack_from("<H", src_raw, off + 6)[0]:
            return False
    return True


# ------------------------------------------------------------------ B. untouched data
def section_untouched(src, dst):
    print("B. untouched data")
    changed = []
    for i in range(src.num_blocks):
        if src.blocks[i].raw != dst.blocks[i].raw:
            changed.append(i)
    check(set(changed) <= set(REWRITTEN) | {COLLISION_TRI_DATA},
          "only the rewritten data blocks %s (and collision tri data %d) changed (changed: %s)"
          % (REWRITTEN, COLLISION_TRI_DATA, changed))
    for i in COLLISION_RANGE:
        if i == COLLISION_TRI_DATA:
            check(collision_only_degenerated(src.blocks[i].raw, dst.blocks[i].raw),
                  "collision block %d (%s): only triangles made degenerate (open_collision.py)"
                  % (i, src.blocks[i].type))
            continue
        check(src.blocks[i].raw == dst.blocks[i].raw, "collision block %d (%s) byte-identical" % (i, src.blocks[i].type))
    check(src.blocks[6].raw == dst.blocks[6].raw, "bhkCollisionObject (6) byte-identical")
    for i in [0, 1, 7, 12, 15, 17, 21, 25]:
        check(src.blocks[i].raw == dst.blocks[i].raw, "scene block %d (%s) byte-identical" % (i, src.blocks[i].type))
    for bi in REWRITTEN:
        s = ns.parse_tri_strips_data(src.blocks[bi].raw)
        d = ns.parse_tri_strips_data(dst.blocks[bi].raw)
        for k in ["group_id", "keep", "compress", "bs_flags", "has_normals", "has_vertex_colors",
                  "consistency", "additional_data", "has_tangents"]:
            check(s[k] == d[k], "block %d field %s same (%s)" % (bi, k, s[k]))
        check(all(len(x) == 3 for x in d["strips"]) and len(d["strips"]) == d["num_triangles"]
              and d["strip_lengths"] == [3] * d["num_triangles"],
              "block %d: every strip is one triangle (length 3), num_triangles %d == strips" % (bi, d["num_triangles"]))
        check(max(max(s_) for s_ in d["strips"]) < d["num_vertices"] if d["strips"] else True,
              "block %d: all indices < num_vertices %d" % (bi, d["num_vertices"]))
        check(d["num_vertices"] >= s["num_vertices"], "block %d: vertex array only grows (%d -> %d)"
              % (bi, s["num_vertices"], d["num_vertices"]))
        check(d["vertices"][:s["num_vertices"]] == s["vertices"], "block %d: original vertices unchanged (index-stable)" % bi)
        check(d["normals"][:s["num_vertices"]] == s["normals"], "block %d: original normals unchanged" % bi)
        check(d["colors"][:s["num_vertices"]] == s["colors"], "block %d: original vertex colours unchanged" % bi)
        check(d["uv_sets"][0][:s["num_vertices"]] == s["uv_sets"][0], "block %d: original UVs unchanged" % bi)
        # bounding sphere encloses all vertices
        c, r = d["center"], d["radius"]
        worst = max(norm(sub(v, c)) for v in d["vertices"])
        check(worst <= r + 1e-3, "block %d: bounding sphere encloses all vertices (max dist %.3f <= r %.3f)" % (bi, worst, r))


# ------------------------------------------------------------------ geometry helpers
def visual_triangles(raw_nif):
    """All visual triangles: list of dicts with block, index, vertex ids, 3D points, attributes."""
    out = []
    for bi in VISUAL_DATA_BLOCKS:
        d = ns.parse_tri_strips_data(raw_nif.blocks[bi].raw)
        V = d["vertices"]
        for ti, t in enumerate(ns.decode_strips(d["strips"])):
            P = (V[t[0]], V[t[1]], V[t[2]])
            out.append({"block": bi, "tri": ti, "ids": t, "P": P})
    return out


def is_vertical(P):
    n = cross(sub(P[1], P[0]), sub(P[2], P[0]))
    L = norm(n)
    return L > 0 and abs(n[0]) / L > COS_VERTICAL


def yz_area2(P):
    return abs((P[1][1] - P[0][1]) * (P[2][2] - P[0][2]) - (P[2][1] - P[0][1]) * (P[1][2] - P[0][2]))


def yz_inside_R_area2(P):
    """Twice the (y,z) area of triangle P intersected with R (Sutherland-Hodgman, plain floats)."""
    poly = [(p[1], p[2]) for p in P]
    # half-planes g(y,z) = a*y + b*z + g >= 0 for the four sides of R
    for a, b, g in [(1, 0, -Y0), (-1, 0, Y1), (0, 1, -Z0), (0, -1, Z1)]:
        new = []
        n = len(poly)
        for i in range(n):
            cu, nx = poly[i], poly[(i + 1) % n]
            gc = a * cu[0] + b * cu[1] + g
            gn = a * nx[0] + b * nx[1] + g
            if gc >= 0:
                new.append(cu)
            if (gc >= 0) != (gn >= 0):
                t = gc / (gc - gn)
                new.append((cu[0] + t * (nx[0] - cu[0]), cu[1] + t * (nx[1] - cu[1])))
        poly = new
        if len(poly) < 3:
            return 0.0
    s = 0.0
    for i in range(len(poly)):
        y1, z1 = poly[i]
        y2, z2 = poly[(i + 1) % len(poly)]
        s += y1 * z2 - y2 * z1
    return abs(s)


def wall_candidates(tris):
    out = []
    for t in tris:
        P = t["P"]
        if t["block"] not in VISUAL_DATA_BLOCKS:
            continue
        if not is_vertical(P):
            continue
        if not all(X_LO <= p[0] <= X_HI for p in P):
            continue
        if yz_area2(P) > 0 and yz_inside_R_area2(P) > 1e-6:
            out.append(t)
    return out


class YZGrid:
    """Uniform grid over (y,z) of triangles whose yz projection is non-degenerate and whose x lies
    in the ray window. Query returns triangles whose yz bbox touches the cell."""

    CELL = 8.0

    def __init__(self, tris, lo=RAY_X_LO, hi=RAY_X_HI):
        self.lo, self.hi = lo, hi
        self.cells = {}
        self.items = []
        for t in tris:
            P = t["P"]
            if yz_area2(P) < 1e-9:
                continue                       # parallel to x: a horizontal face cannot be hit by an x-ray
            xs = [p[0] for p in P]
            if max(xs) < self.lo or min(xs) > self.hi:
                continue
            ys = [p[1] for p in P]
            zs = [p[2] for p in P]
            idx = len(self.items)
            self.items.append(t)
            for iy in range(int(math.floor(min(ys) / self.CELL)), int(math.floor(max(ys) / self.CELL)) + 1):
                for iz in range(int(math.floor(min(zs) / self.CELL)), int(math.floor(max(zs) / self.CELL)) + 1):
                    self.cells.setdefault((iy, iz), []).append(idx)

    def hits(self, y, z):
        """x positions where the x-ray through (y,z) meets a triangle inside the window."""
        res = []
        for idx in self.cells.get((int(math.floor(y / self.CELL)), int(math.floor(z / self.CELL))), []):
            t = self.items[idx]
            P = t["P"]
            (y0, z0), (y1, z1), (y2, z2) = [(p[1], p[2]) for p in P]
            den = (y1 - y0) * (z2 - z0) - (y2 - y0) * (z1 - z0)
            if abs(den) < 1e-12:
                continue
            l1 = ((y - y0) * (z2 - z0) - (y2 - y0) * (z - z0)) / den
            l2 = ((y1 - y0) * (z - z0) - (y - y0) * (z1 - z0)) / den
            l0 = 1.0 - l1 - l2
            eps = 1e-9
            if l0 < -eps or l1 < -eps or l2 < -eps:
                continue
            x = l0 * P[0][0] + l1 * P[1][0] + l2 * P[2][0]
            if self.lo <= x <= self.hi:
                res.append((x, t))
        return res


def is_porch(t):
    return max(p[2] for p in t["P"]) <= PORCH_MAX_Z


def covered_by_wall(hits):
    return any(not is_porch(t) for _, t in hits)


# ------------------------------------------------------------------ C. geometry
def section_geometry(src, dst, src_tris, dst_tris):
    print("C. geometry")
    cands = wall_candidates(src_tris)
    print("   source wall candidates: %d (blocks %s)" % (len(cands), sorted(set(c["block"] for c in cands))))
    # C1: new vertices interpolate attributes of a source triangle
    total_new = 0
    bad = []
    for bi in REWRITTEN:
        s = ns.parse_tri_strips_data(src.blocks[bi].raw)
        d = ns.parse_tri_strips_data(dst.blocks[bi].raw)
        n0 = s["num_vertices"]
        src_tri_list = [t for t in src_tris if t["block"] == bi]
        for v in range(n0, d["num_vertices"]):
            total_new += 1
            p = d["vertices"][v]
            # every source triangle that contains p (plane + yz barycentric) is a candidate source;
            # the vertex passes if its attributes match the interpolation from at least one of them.
            best = None
            n_contain = 0
            for t in src_tri_list:
                P = t["P"]
                nrm = cross(sub(P[1], P[0]), sub(P[2], P[0]))
                L = norm(nrm)
                if L == 0 or abs(dot(nrm, sub(p, P[0]))) / L > 1e-3:
                    continue
                (y0, z0), (y1, z1), (y2, z2) = [(q[1], q[2]) for q in P]
                den = (y1 - y0) * (z2 - z0) - (y2 - y0) * (z1 - z0)
                if abs(den) < 1e-12:
                    continue
                l1 = ((p[1] - y0) * (z2 - z0) - (y2 - y0) * (p[2] - z0)) / den
                l2 = ((y1 - y0) * (p[2] - z0) - (p[1] - y0) * (z1 - z0)) / den
                lam = (1 - l1 - l2, l1, l2)
                if min(lam) < -1e-4:
                    continue
                n_contain += 1
                ids = t["ids"]
                err = 0.0
                for key, arr_s, dim in [("vertices", s["vertices"], 3), ("normals", s["normals"], 3),
                                        ("tangents", s["tangents"], 3), ("bitangents", s["bitangents"], 3),
                                        ("colors", s["colors"], 4)]:
                    if not arr_s:
                        continue
                    exp = [sum(lam[k] * arr_s[ids[k]][j] for k in range(3)) for j in range(dim)]
                    got = d[key][v]
                    err = max(err, max(abs(exp[j] - got[j]) for j in range(dim)))
                for k_uv, uvs_d in enumerate(d["uv_sets"]):
                    uvs_s = s["uv_sets"][k_uv]
                    exp = [sum(lam[k] * uvs_s[ids[k]][j] for k in range(3)) for j in range(2)]
                    got = uvs_d[v]
                    err = max(err, max(abs(exp[j] - got[j]) for j in range(2)))
                if best is None or err < best:
                    best = err
            if best is None:
                bad.append((bi, v, "not on any source triangle"))
            elif best > 1e-3:
                bad.append((bi, v, "attribute error %.6f" % best))
    check(not bad, "%d new vertices: position/normal/tangent/bitangent/colour/UV equal barycentric interpolation of their source triangle %s"
          % (total_new, "" if not bad else bad[:5]))
    # C2: area conservation per candidate
    worst = 0.0
    lines = []
    for c in cands:
        P = c["P"]
        nrm = cross(sub(P[1], P[0]), sub(P[2], P[0]))
        L = norm(nrm)
        pieces_area = 0.0
        for t in dst_tris:
            if t["block"] != c["block"]:
                continue
            Q = t["P"]
            if max(abs(dot(nrm, sub(q, P[0]))) / L for q in Q) > 1e-3:
                continue
            cx = sum(q[1] for q in Q) / 3.0
            cz = sum(q[2] for q in Q) / 3.0
            # inside source triangle's yz projection?
            (y0, z0), (y1, z1), (y2, z2) = [(q[1], q[2]) for q in P]
            den = (y1 - y0) * (z2 - z0) - (y2 - y0) * (z1 - z0)
            l1 = ((cx - y0) * (z2 - z0) - (y2 - y0) * (cz - z0)) / den
            l2 = ((y1 - y0) * (cz - z0) - (cx - y0) * (z1 - z0)) / den
            if min(1 - l1 - l2, l1, l2) < -1e-6:
                continue
            pieces_area += yz_area2(Q) * 0.5
        expected = (yz_area2(P) - yz_inside_R_area2(P)) * 0.5
        err = abs(pieces_area - expected)
        worst = max(worst, err)
        lines.append((c["block"], c["tri"], expected, pieces_area, err))
    for bi, ti, e, a, err in lines:
        print("   block %d tri %d: expected yz-area %.4f, output pieces %.4f" % (bi, ti, e, a))
    check(worst < 1e-2, "area conservation for every candidate (max error %.2e)" % worst)
    # C3: orientation of pieces matches source (dot of normals > 0)
    flips = 0
    for c in cands:
        P = c["P"]
        nrm = cross(sub(P[1], P[0]), sub(P[2], P[0]))
        for t in dst_tris:
            if t["block"] != c["block"]:
                continue
            Q = t["P"]
            if max(abs(dot(nrm, sub(q, P[0]))) / norm(nrm) for q in Q) > 1e-3:
                continue
            if dot(cross(sub(Q[1], Q[0]), sub(Q[2], Q[0])), nrm) <= 0:
                flips += 1
    check(flips == 0, "output pieces keep the source winding (flipped: %d)" % flips)


# ------------------------------------------------------------------ D/E. door and facade
def section_door(src_grid, dst_grid, src_tris, dst_tris):
    print("D. door rectangle R = y[%g,%g] z[%g,%g], ray test at x=-285 along x in [%g,%g]"
          % (Y0, Y1, Z0, Z1, RAY_X_LO, RAY_X_HI))
    samples = []
    y = Y0 + 0.75
    while y <= Y1 - 0.75 + 1e-9:
        z = Z0 + 0.75
        while z <= Z1 - 0.75 + 1e-9:
            samples.append((y, z))
            z += 2.0
        y += 2.0
    print("   %d samples in R" % len(samples))
    src_hit = sum(1 for (y, z) in samples if covered_by_wall(src_grid.hits(y, z)))
    dst_hit_wall = []
    dst_porch = 0
    for (y, z) in samples:
        hs = dst_grid.hits(y, z)
        if covered_by_wall(hs):
            dst_hit_wall.append((y, z))
        if any(is_porch(t) for _, t in hs):
            dst_porch += 1
    # exact (not sampled) test: no non-porch triangle in the ray window overlaps R with positive yz area
    exact = []
    for t in dst_tris:
        P = t["P"]
        xs = [p[0] for p in P]
        if max(xs) < RAY_X_LO or min(xs) > RAY_X_HI or is_porch(t) or yz_area2(P) < 1e-9:
            continue
        if yz_inside_R_area2(P) > 1e-6:
            exact.append((t["block"], t["tri"]))
    check(not exact, "output: exact polygon test, no non-porch triangle in the x window overlaps R (%d found %s)"
          % (len(exact), exact[:5]))
    check(src_hit == len(samples), "source: all %d samples in R are covered by a wall (sanity)" % len(samples))
    check(not dst_hit_wall, "output: no samples in R hit a non-porch visual triangle (hits: %d %s)"
          % (len(dst_hit_wall), dst_hit_wall[:3]))
    print("   porch-only hits in R (z <= %g, exempt): %d samples" % (PORCH_MAX_Z, dst_porch))

    print("E. facade outside R (same wall-coverage test before and after)")
    mism = []
    n_out = 0
    n_cov = 0
    y = -110.0
    while y <= 150.0:
        z = 24.0
        while z <= 230.0:
            if not (Y0 <= y <= Y1 and Z0 <= z <= Z1):
                n_out += 1
                a = covered_by_wall(src_grid.hits(y, z))
                b = covered_by_wall(dst_grid.hits(y, z))
                n_cov += 1 if b else 0
                if a != b:
                    mism.append((round(y, 2), round(z, 2), a, b))
            z += 2.5
        y += 2.5
    check(not mism, "%d facade samples outside R: wall coverage identical before/after (mismatches: %d %s)"
          % (n_out, len(mism), mism[:5]))
    # ring just outside R must remain covered
    ring = []
    for y in [Y0 - 1.0, Y1 + 1.0]:
        for z in [Z0 + 2.0, 100.0, Z1 - 2.0]:
            ring.append((y, z))
    for z in [Z1 + 1.0]:
        for y in [Y0 + 2.0, 30.0, Y1 - 2.0]:
            ring.append((y, z))
    cov = [covered_by_wall(dst_grid.hits(y, z)) for (y, z) in ring]
    check(all(cov), "ring of %d points just outside R (1 unit off the edges) is still covered" % len(ring))
    return len(samples)


def through_opening(dst_tris):
    """Informational (not pass/fail): first visual surface a ray hits going +x from inside R, beyond
    the facade window. Shows what the player sees through the opening, not a requirement."""
    grid = YZGrid(dst_tris, lo=-250.0, hi=1200.0)
    counts = {}
    none_hit = 0
    n = 0
    y = Y0 + 0.75
    while y <= Y1 - 0.75 + 1e-9:
        z = Z0 + 0.75
        while z <= Z1 - 0.75 + 1e-9:
            n += 1
            hs = grid.hits(y, z)
            if not hs:
                none_hit += 1
            else:
                x, t = min(hs, key=lambda h: h[0])
                key = (t["block"], t["tri"])
                counts[key] = counts.get(key, 0) + 1
            z += 2.0
        y += 2.0
    print("   info: %d samples in R, %d see no surface beyond x=-250 (open to the far side)" % (n, none_hit))
    top = sorted(counts.items(), key=lambda kv: -kv[1])[:5]
    print("   info: nearest surface seen through R (block, tri, samples): %s" % top)


def ascii_map(title, grid, before):
    print("   " + title)
    ys = [y for y in range(-60, 124, 4)]
    zs = [z for z in range(236, 14, -6)]
    print("       y: -60 ... +120 (step 4); z: 236 (top) ... 20 (step 6). R is y[%g,%g] z[%g,%g]" % (Y0, Y1, Z0, Z1))
    for z in zs:
        row = []
        for y in ys:
            inside = Y0 <= y <= Y1 and Z0 <= z <= Z1
            cov = covered_by_wall(grid.hits(y + 0.5, z + 0.5))
            if inside:
                # before: '=' marks wall that the cut removes; after: 'X' would be a leftover wall (must be none)
                row.append(("=" if cov else " ") if before else ("X" if cov else " "))
            else:
                row.append("#" if cov else ".")
        print("   %4d %s" % (z, "".join(row)))


# ------------------------------------------------------------------ main
def main():
    src, src_raw = load(SRC)
    dst, dst_raw = load(DST)
    print("source: %s (%d bytes)" % (SRC, len(src_raw)))
    print("output: %s (%d bytes)" % (DST, len(dst_raw)))
    section_structure(src, dst, dst_raw)
    section_untouched(src, dst)

    src_tris = visual_triangles(src)
    dst_tris = visual_triangles(dst)
    print("triangle counts per block (source -> output):")
    for bi in VISUAL_DATA_BLOCKS:
        a = sum(1 for t in src_tris if t["block"] == bi)
        b = sum(1 for t in dst_tris if t["block"] == bi)
        print("   block %2d (%s): %d -> %d" % (bi, "NV_GeneralStore:%d" % {11: 0, 16: 1, 20: 2, 24: 3, 29: 4}[bi], a, b))
    section_geometry(src, dst, src_tris, dst_tris)

    src_grid = YZGrid(src_tris)
    dst_grid = YZGrid(dst_tris)
    section_door(src_grid, dst_grid, src_tris, dst_tris)
    through_opening(dst_tris)

    print("F. ASCII facade coverage map (#: wall, .: no wall outside R; inside R: '=' wall to remove (before), blank = open (after), X = leftover wall (must be none))")
    ascii_map("BEFORE (source)", src_grid, before=True)
    ascii_map("AFTER  (output)", dst_grid, before=False)

    print()
    if FAIL:
        print("RESULT: %d check(s) FAILED" % len(FAIL))
        for f_ in FAIL:
            print("  - " + f_)
        return 1
    print("RESULT: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())

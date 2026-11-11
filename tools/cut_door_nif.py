"""Cut a doorway hole in the visual facade of nv_generalstore.nif (FNV, NIF 20.2.0.7, BS 34).

Pipeline
  1. Decode every visual NiTriStripsData (blocks 11, 16, 20, 24, 29) into triangles.
  2. Select wall triangles: near-vertical (|n.x| / |n| > COS_VERTICAL), all vertices with
     x in [X_LO, X_HI], and a non-zero-area overlap with the door rectangle R in the (y, z) plane.
  3. Clip each selected triangle against R in (y, z). The part outside R is the union of four
     disjoint convex regions (left, right, below, above). Each region is clipped with
     Sutherland-Hodgman in barycentric coordinates of the ORIGINAL triangle, so every new vertex
     interpolates position, normal, tangent, bitangent, vertex colour and UV linearly.
  4. Rewrite only the affected NiTriStripsData blocks: the vertex arrays are extended with the new
     vertices (original indices unchanged); the triangles are written as one strip of 3 per
     triangle. This keeps the NiTriStrips/NiTriStripsData types, so the header type table and every
     other block stay unchanged. Bounding sphere and counts are recomputed.
  5. Patch only the block-size table in the header; every other block is copied byte-for-byte.

Usage:  py -I cut_door_nif.py            (writes the output NIF and prints a report)
        py -I cut_door_nif.py --analyze  (prints candidate triangles only, writes nothing)
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

# Door rectangle R (game units, root space). Chosen inside the door leaf (y -34.7..94.2, z 18.3..193.7)
# so the cut edges are hidden behind the door. z0 is 18.5 rather than 18.0 because the leaf bottom is
# at z 18.3: with 18.0 the hole would extend 0.3 units below the door and show a sliver of interior.
Y0, Y1 = -34.0, 93.5
Z0, Z1 = 18.5, 193.0

X_LO, X_HI = -300.0, -260.0      # facade band
RAY_X_LO, RAY_X_HI = -320.0, -250.0  # wider band used by the verifier's ray test
COS_VERTICAL = 0.9               # |n.x|/|n| above this = wall
EPS_AREA = 1e-6
EPS_BARY = 1e-9

# Visual blocks: block index -> (name). Block indices come from the header of the source file.
VISUAL_DATA_BLOCKS = [11, 16, 20, 24, 29]
GEOM_BLOCK_OF_DATA = {11: 7, 16: 12, 20: 17, 24: 21, 29: 25}


# ---------------------------------------------------------------- vector helpers
def sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def length(a):
    return math.sqrt(a[0] * a[0] + a[1] * a[1] + a[2] * a[2])


def tri_normal(P):
    return cross(sub(P[1], P[0]), sub(P[2], P[0]))


def poly_area2(pts):
    """Twice the signed area of a 2D polygon [(y, z), ...]."""
    s = 0.0
    n = len(pts)
    for i in range(n):
        y1, z1 = pts[i]
        y2, z2 = pts[(i + 1) % n]
        s += y1 * z2 - y2 * z1
    return s


# ---------------------------------------------------------------- R as half-planes
# A half-plane is (a, b, c) meaning g(y, z) = a*y + b*z + c >= 0 is the kept side.
R_LEFT_IN = (1.0, 0.0, -Y0)     # y >= Y0
R_RIGHT_IN = (-1.0, 0.0, Y1)    # y <= Y1
R_BELOW_IN = (0.0, 1.0, -Z0)    # z >= Z0
R_ABOVE_IN = (0.0, -1.0, Z1)    # z <= Z1
INSIDE_R = [R_LEFT_IN, R_RIGHT_IN, R_BELOW_IN, R_ABOVE_IN]

# Outside R = disjoint union of these convex regions (each list of half-planes must all hold).
OUTSIDE_REGIONS = [
    [(-1.0, 0.0, Y0)],                                # y <= Y0
    [(1.0, 0.0, -Y1)],                                # y >= Y1
    [R_LEFT_IN, R_RIGHT_IN, (0.0, -1.0, Z0)],         # Y0 <= y <= Y1 and z <= Z0
    [R_LEFT_IN, R_RIGHT_IN, (0.0, 1.0, -Z1)],         # Y0 <= y <= Y1 and z >= Z1
]


def hp_value_at_bary(hp, yz_corners, bary):
    """Evaluate half-plane at a barycentric point of a triangle whose corners have (y, z) coords."""
    a, b, c = hp
    y = sum(w * p[0] for w, p in zip(bary, yz_corners))
    z = sum(w * p[1] for w, p in zip(bary, yz_corners))
    return a * y + b * z + c


def clip_bary_poly(poly, hp, yz_corners):
    """Sutherland-Hodgman clip of a polygon given as barycentric triples, keeping g >= 0."""
    out = []
    n = len(poly)
    if n == 0:
        return out
    vals = [hp_value_at_bary(hp, yz_corners, w) for w in poly]
    for i in range(n):
        cur, nxt = poly[i], poly[(i + 1) % n]
        gc, gn = vals[i], vals[(i + 1) % n]
        if gc >= 0:
            out.append(cur)
        if (gc >= 0) != (gn >= 0):
            t = gc / (gc - gn)
            out.append(tuple(cur[k] + t * (nxt[k] - cur[k]) for k in range(3)))
    return out


def clip_tri_to_regions(yz_corners, regions):
    """Return list of barycentric polygons = triangle intersected with each convex region."""
    base = [(1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)]
    pieces = []
    for region in regions:
        poly = base
        for hp in region:
            poly = clip_bary_poly(poly, hp, yz_corners)
            if len(poly) < 3:
                break
        if len(poly) >= 3:
            pieces.append(poly)
    return pieces


def piece_area(poly, yz_corners):
    pts = []
    for w in poly:
        pts.append((sum(wk * p[0] for wk, p in zip(w, yz_corners)),
                    sum(wk * p[1] for wk, p in zip(w, yz_corners))))
    return abs(poly_area2(pts)) * 0.5


def overlaps_R(yz_corners):
    """True if the triangle's yz projection has positive-area intersection with R."""
    pieces = clip_tri_to_regions(yz_corners, [INSIDE_R])
    return any(piece_area(p, yz_corners) > EPS_AREA for p in pieces)


# ---------------------------------------------------------------- scene data
class VisualBlock:
    def __init__(self, f, data_block):
        self.block = data_block
        self.geom_block = GEOM_BLOCK_OF_DATA[data_block]
        self.d = ns.parse_tri_strips_data(f.blocks[data_block].raw)
        self.tris = ns.decode_strips(self.d["strips"])


def load_visual(f):
    blocks = {}
    for bi in VISUAL_DATA_BLOCKS:
        blocks[bi] = VisualBlock(f, bi)
    return blocks


def tri_points(vb, t):
    V = vb.d["vertices"]
    return (V[t[0]], V[t[1]], V[t[2]])


def yz_of(P):
    return [(p[1], p[2]) for p in P]


def is_vertical(P):
    n = tri_normal(P)
    L = length(n)
    return L > 0 and abs(n[0]) / L > COS_VERTICAL


def in_band(P):
    return all(X_LO <= p[0] <= X_HI for p in P)


def select_candidates(blocks):
    """Wall triangles that overlap R. Returns list of (block, tri_index) and a report of all
    triangles overlapping R in any orientation and any x (so nothing is hidden by the filter)."""
    walls = []
    others = []
    for bi, vb in blocks.items():
        for ti, t in enumerate(vb.tris):
            P = tri_points(vb, t)
            yz = yz_of(P)
            if not overlaps_R(yz):
                continue
            xs = [p[0] for p in P]
            rec = (bi, ti, t, P)
            if is_vertical(P) and in_band(P):
                walls.append(rec)
            elif max(xs) >= RAY_X_LO and min(xs) <= RAY_X_HI:
                # non-wall triangles in the ray band (porch, threshold, slanted faces): reported, not cut
                others.append((bi, ti, t, P, min(xs), max(xs), tri_normal(P)))
    return walls, others


# ---------------------------------------------------------------- cutting
class Vertex_Pool:
    """Extends a vertex array; dedups new vertices by their (global vertex id, weight) signature."""

    def __init__(self, d):
        self.d = d
        self.base = d["num_vertices"]
        self.new_weights = []     # per new vertex: tuple of (vid, weight)
        self.cache = {}

    def get(self, weights):
        """weights: list of (vid, w) with w>0 summing to 1. Returns vertex index (possibly new)."""
        if len(weights) == 1 and abs(weights[0][1] - 1.0) < EPS_BARY:
            return weights[0][0]
        key = tuple(sorted((vid, round(w, 9)) for vid, w in weights if w > EPS_BARY))
        if key in self.cache:
            return self.cache[key]
        idx = self.base + len(self.new_weights)
        self.new_weights.append(key)
        self.cache[key] = idx
        return idx


def interpolate_into_arrays(d, pool):
    """Append interpolated attributes for every new vertex in pool to the data dict d."""
    has_v = d["has_vertices"]
    has_n = d["has_normals"]
    has_t = d["has_tangents"]
    has_c = d["has_vertex_colors"]
    new_vertices, new_normals, new_tangents, new_bitangents, new_colors = [], [], [], [], []
    new_uv = [[] for _ in d["uv_sets"]]

    def lerp_vec(arr, key, dim):
        acc = [0.0] * dim
        for vid, w in key:
            v = arr[vid]
            for k in range(dim):
                acc[k] += w * v[k]
        return tuple(acc)

    for key in pool.new_weights:
        if has_v:
            new_vertices.append(lerp_vec(d["vertices"], key, 3))
        if has_n:
            new_normals.append(lerp_vec(d["normals"], key, 3))
        if has_t:
            new_tangents.append(lerp_vec(d["tangents"], key, 3))
            new_bitangents.append(lerp_vec(d["bitangents"], key, 3))
        if has_c:
            new_colors.append(lerp_vec(d["colors"], key, 4))
        for s, uvs in enumerate(d["uv_sets"]):
            new_uv[s].append(lerp_vec(uvs, key, 2))

    if has_v:
        d["vertices"] = d["vertices"] + new_vertices
    if has_n:
        d["normals"] = d["normals"] + new_normals
    if has_t:
        d["tangents"] = d["tangents"] + new_tangents
        d["bitangents"] = d["bitangents"] + new_bitangents
    if has_c:
        d["colors"] = d["colors"] + new_colors
    for s in range(len(d["uv_sets"])):
        d["uv_sets"][s] = d["uv_sets"][s] + new_uv[s]
    d["num_vertices"] = d["num_vertices"] + len(pool.new_weights)


def cut_block(vb, tri_selection):
    """tri_selection: dict tri_index -> the triangle's vertex ids (already known to overlap R).
    Returns (new_triangle_list, stats)."""
    d = vb.d
    pool = Vertex_Pool(d)
    new_tris = []
    stats = {"cut": 0, "removed": 0, "pieces": 0}
    for ti, t in enumerate(vb.tris):
        if ti not in tri_selection:
            new_tris.append(t)
            continue
        P = tri_points(vb, t)
        yz = yz_of(P)
        pieces = clip_tri_to_regions(yz, OUTSIDE_REGIONS)
        stats["cut"] += 1
        kept = 0
        for poly in pieces:
            if piece_area(poly, yz) <= EPS_AREA:
                continue
            # barycentric -> global vertex weights
            vids = []
            for w in poly:
                weights = [(t[k], w[k]) for k in range(3) if w[k] > EPS_BARY]
                vids.append(pool.get(weights))
            for i in range(1, len(vids) - 1):
                tri = (vids[0], vids[i], vids[i + 1])
                if len(set(tri)) < 3:
                    continue
                new_tris.append(tri)
                kept += 1
        stats["pieces"] += kept
        if kept == 0:
            stats["removed"] += 1
    interpolate_into_arrays(d, pool)
    return new_tris, stats


def bounding_sphere(vertices):
    xs = [v[0] for v in vertices]
    ys = [v[1] for v in vertices]
    zs = [v[2] for v in vertices]
    c = ((min(xs) + max(xs)) * 0.5, (min(ys) + max(ys)) * 0.5, (min(zs) + max(zs)) * 0.5)
    r = 0.0
    for v in vertices:
        dx, dy, dz = v[0] - c[0], v[1] - c[1], v[2] - c[2]
        r = max(r, math.sqrt(dx * dx + dy * dy + dz * dz))
    return c, r


def build_data_block(d, tris):
    """Serialize the rewritten NiTriStripsData: one strip of 3 per triangle."""
    d = dict(d)
    d["strips"] = [[a, b, c] for (a, b, c) in tris]
    d["strip_lengths"] = [3] * len(tris)
    d["num_triangles"] = len(tris)
    if d["has_vertices"]:
        c, r = bounding_sphere(d["vertices"])
        d["center"] = c
        d["radius"] = r
    d["has_points"] = 1
    return ns.write_tri_strips_data(d)


def splice_nif(src_bytes, f, replacements):
    """Copy the file, replacing the raw bytes of selected blocks and patching the block-size table.
    Header bytes other than the size table are copied as-is."""
    r = niflib.Reader(src_bytes, src_bytes.index(b"\n") + 1)
    r.u32()
    r.u8()
    r.u32()
    num_blocks = r.u32()
    r.u32()
    for _ in range(3):
        r.short_string()
    num_types = r.u16()
    for _ in range(num_types):
        r.sized_string()
    type_idx_off = r.pos
    r.skip(2 * num_blocks)
    sizes_off = r.pos
    r.skip(4 * num_blocks)
    sizes_end = r.pos
    data_start = f.data_start
    head = bytearray(src_bytes[:sizes_off])
    sizes = list(f.block_sizes)
    raws = [b.raw for b in f.blocks]
    for bi, newraw in replacements.items():
        raws[bi] = newraw
        sizes[bi] = len(newraw)
    head += b"".join(struct.pack("<I", s) for s in sizes)
    head += src_bytes[sizes_end:data_start]
    body = b"".join(raws)
    tail = src_bytes[data_start + sum(f.block_sizes):]
    return bytes(head) + body + tail, type_idx_off


# ---------------------------------------------------------------- driver
def run_analyze(blocks):
    walls, others = select_candidates(blocks)
    print("wall candidates (vertical, x in [%g,%g], overlap R): %d" % (X_LO, X_HI, len(walls)))
    from collections import Counter
    print("  per block:", dict(Counter(w[0] for w in walls)))
    for bi, ti, t, P in walls:
        n = tri_normal(P)
        L = length(n)
        print("  blk %2d tri %5d verts %s x=%s y=%s z=%s n=(%.3f,%.3f,%.3f)" % (
            bi, ti, t,
            tuple(round(p[0], 2) for p in P), tuple(round(p[1], 2) for p in P),
            tuple(round(p[2], 2) for p in P), n[0] / L, n[1] / L, n[2] / L))
    print("non-wall triangles overlapping R: %d" % len(others))
    for bi, ti, t, P, xmin, xmax, n in others:
        L = length(n) or 1.0
        print("  blk %2d tri %5d x=[%.2f,%.2f] n=(%.3f,%.3f,%.3f) z=[%.2f,%.2f]" % (
            bi, ti, xmin, xmax, n[0] / L, n[1] / L, n[2] / L,
            min(p[2] for p in P), max(p[2] for p in P)))
    return walls, others


def main(argv):
    with open(SRC, "rb") as fh:
        src = fh.read()
    f = niflib.NifFile(src, SRC)
    blocks = load_visual(f)
    walls, others = run_analyze(blocks)
    if "--analyze" in argv:
        return 0

    select = {}
    for bi, ti, t, P in walls:
        select.setdefault(bi, {})[ti] = True
    replacements = {}
    report = {}
    for bi, vb in blocks.items():
        if bi not in select:
            continue
        before_tris = len(vb.tris)
        before_verts = vb.d["num_vertices"]
        new_tris, stats = cut_block(vb, select[bi])
        raw = build_data_block(vb.d, new_tris)
        replacements[bi] = raw
        report[bi] = (before_tris, len(new_tris), before_verts, vb.d["num_vertices"], stats)
    os.makedirs(os.path.dirname(DST), exist_ok=True)
    out, _ = splice_nif(src, f, replacements)
    with open(DST, "wb") as fh:
        fh.write(out)
    for bi in sorted(report):
        b0, b1, v0, v1, st = report[bi]
        print("block %d (%s): tris %d -> %d, verts %d -> %d, cut %d, removed %d, pieces kept %d" % (
            bi, "NV_GeneralStore:%d" % {11: 0, 16: 1, 20: 2, 24: 3, 29: 4}[bi],
            b0, b1, v0, v1, st["cut"], st["removed"], st["pieces"]))
    print("wrote", DST, len(out), "bytes")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

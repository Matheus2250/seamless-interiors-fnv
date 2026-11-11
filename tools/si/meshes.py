"""Meshes: reading models from the BSA, collision in world space, doorway cuts.

Visual cuts reuse the verified clipper of cut_door_nif.py (barycentric clipping, one strip of 3
per triangle). Collision is removed by making triangles degenerate in place, which keeps the
MOPP code valid (CLAUDE.md section 8).
"""

import math
import os
import struct
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
import bsalib  # noqa: E402
import cut_door_nif as cd  # noqa: E402
import niflib  # noqa: E402

from si import geom  # noqa: E402
from si.master import MESHES_BSA  # noqa: E402

HAVOK_SCALE = 7.0
_bsa = None
_nif_cache = {}


def model_path(base):
    """BSA path of a base form's model ("meshes\\...", lower case), or None."""
    modl = base.get("MODL") if base is not None else None
    if not modl:
        return None
    return ("meshes\\" + modl.rstrip(b"\x00").decode("latin-1")).lower()


def _parse(data):
    try:
        return niflib.NifFile(data)
    except Exception:
        return None


def read_model(path):
    global _bsa
    if path not in _nif_cache:
        if _bsa is None:
            _bsa = bsalib.BSA(MESHES_BSA)
        try:
            _nif_cache[path] = _bsa.read(path)
        except Exception:
            _nif_cache[path] = None
    return _nif_cache[path]


def collision_tris_local(nif_bytes):
    """Collision triangles in the mesh's own space (game units), with their indices.
    Only packed tri-strip shapes (the kit/building case); other shapes return []."""
    if not nif_bytes:
        return []
    try:
        f = _parse(nif_bytes)
    except Exception:
        return []      # some meshes in the BSA are not fully parseable; treat as no collision
    blocks = f.blocks_of("hkPackedNiTriStripsData")
    if not blocks:
        return []
    data = f.packed_tri_data(blocks[0].index)
    verts = [tuple(c * HAVOK_SCALE for c in v) for v in data["vertices"]]
    return [(n, [verts[i] for i in t]) for n, t in enumerate(data["triangles"]) if len(set(t)) == 3]


def ref_collision_world(ref_pos, ref_rot, scale, nif_bytes):
    """Collision triangles of a placed ref in world space (rotation z only; refs with x/y
    rotation are rare for structural pieces)."""
    out = []
    for n, tri in collision_tris_local(nif_bytes):
        out.append((n, [geom.to_world(ref_pos, ref_rot[2], (p[0] * scale, p[1] * scale, p[2] * scale))
                        for p in tri]))
    return out


def tri_box(tri):
    return ([min(p[i] for p in tri) for i in range(3)], [max(p[i] for p in tri) for i in range(3)])


def boxes_overlap(lo1, hi1, lo2, hi2):
    return all(hi1[i] > lo2[i] and lo1[i] < hi2[i] for i in range(3))


# ---- doorway rectangle -------------------------------------------------------------------
def dot(p, d):
    return p[0] * d[0] + p[1] * d[1] + p[2] * d[2]


def cut_coords(cut, p):
    """(w, u, v) of a point in the cut's frame: w along the wall normal, u across the opening,
    v is z. Axis-aligned cuts use the piece's own axes; oriented cuts use the door's frame."""
    if "frame" in cut:
        n, s = cut["frame"]
        return dot(p, n), dot(p, s), p[2]
    return p[cut["axis"]], p[cut["u"]], p[cut["v"]]


def doorway_cut(local_corners, band_half=20.0, shrink=0.5, normal=None, side=None):
    """From the door leaf's corners in a mesh's local space: the wall plane, the in-plane axes and
    the rectangle to cut, shrunk so the edges stay hidden behind the door leaf.

    With normal/side (unit vectors in the mesh's local space) the rectangle follows the door's own
    frame. A piece rotated relative to the door (a shack wall at 20 degrees, say) needs this: its
    own axes would give a rectangle that does not line up with the opening."""
    if normal is not None:
        u0 = min(dot(p, side) for p in local_corners) + shrink
        u1 = max(dot(p, side) for p in local_corners) - shrink
        v0 = min(p[2] for p in local_corners) + shrink
        v1 = max(p[2] for p in local_corners) - shrink
        plane = sum(dot(p, normal) for p in local_corners) / len(local_corners)
        return {"frame": (normal, side), "band": (plane - band_half, plane + band_half),
                "rect": (u0, u1, v0, v1), "plane": plane}
    spans = [max(p[i] for p in local_corners) - min(p[i] for p in local_corners) for i in range(3)]
    axis = min((0, 1), key=lambda i: spans[i])
    u = 1 - axis
    plane = sum(p[axis] for p in local_corners) / len(local_corners)
    u0 = min(p[u] for p in local_corners) + shrink
    u1 = max(p[u] for p in local_corners) - shrink
    v0 = min(p[2] for p in local_corners) + shrink
    v1 = max(p[2] for p in local_corners) - shrink
    return {"axis": axis, "u": u, "v": 2, "band": (plane - band_half, plane + band_half),
            "rect": (u0, u1, v0, v1), "plane": plane}


# ---- cutting ----------------------------------------------------------------------------
def _configure(cut, f):
    cd.Y0, cd.Y1, cd.Z0, cd.Z1 = cut["rect"]
    cd.X_LO, cd.X_HI = cut["band"]
    cd.RAY_X_LO, cd.RAY_X_HI = cut["band"]
    cd.R_LEFT_IN = (1.0, 0.0, -cd.Y0)
    cd.R_RIGHT_IN = (-1.0, 0.0, cd.Y1)
    cd.R_BELOW_IN = (0.0, 1.0, -cd.Z0)
    cd.R_ABOVE_IN = (0.0, -1.0, cd.Z1)
    cd.INSIDE_R = [cd.R_LEFT_IN, cd.R_RIGHT_IN, cd.R_BELOW_IN, cd.R_ABOVE_IN]
    cd.OUTSIDE_REGIONS = [
        [(-1.0, 0.0, cd.Y0)],
        [(1.0, 0.0, -cd.Y1)],
        [cd.R_LEFT_IN, cd.R_RIGHT_IN, (0.0, -1.0, cd.Z0)],
        [cd.R_LEFT_IN, cd.R_RIGHT_IN, (0.0, 1.0, -cd.Z1)],
    ]
    if "frame" in cut:
        n, s = cut["frame"]
        cd.yz_of = lambda P: [(dot(p, s), p[2]) for p in P]
        cd.in_band = lambda P: all(cut["band"][0] <= dot(p, n) <= cut["band"][1] for p in P)
    else:
        axis, u, v = cut["axis"], cut["u"], cut["v"]
        cd.yz_of = lambda P: [(p[u], p[v]) for p in P]
        cd.in_band = lambda P: all(cut["band"][0] <= p[axis] <= cut["band"][1] for p in P)
    # every face inside the wall band belongs to the wall (baseboard and moulding bevels too)
    cd.is_vertical = lambda P: cd.length(cd.tri_normal(P)) > 0
    data_blocks = [b.index for b in f.blocks if b.type == "NiTriStripsData"]
    geom_blocks = [b.index for b in f.blocks if b.type == "NiTriStrips"]
    cd.VISUAL_DATA_BLOCKS = data_blocks
    cd.GEOM_BLOCK_OF_DATA = dict(zip(data_blocks, geom_blocks))


def cut_visual(nif_bytes, cut):
    """Clip the wall faces inside cut["band"] around cut["rect"]. Returns (bytes, n_cut)."""
    f = _parse(nif_bytes)
    _configure(cut, f)
    blocks = {}
    for bi in cd.VISUAL_DATA_BLOCKS:
        blocks[bi] = cd.VisualBlock(f, bi)
    walls, _others = cd.select_candidates(blocks)
    select = {}
    for bi, ti, _t, _P in walls:
        select.setdefault(bi, {})[ti] = True
    replacements = {}
    for bi, vb in blocks.items():
        if bi in select:
            new_tris, _stats = cd.cut_block(vb, select[bi])
            replacements[bi] = cd.build_data_block(vb.d, new_tris)
    if not replacements:
        return nif_bytes, 0
    out, _ = cd.splice_nif(nif_bytes, f, replacements)
    return out, len(walls)


def degenerate_collision(nif_bytes, should_remove):
    """Make collision triangles degenerate where should_remove(index, tri_local) is true.
    Returns (bytes, removed indices)."""
    f = _parse(nif_bytes)
    blocks = f.blocks_of("hkPackedNiTriStripsData")
    if not blocks:
        return nif_bytes, []
    blk = blocks[0]
    raw = bytearray(nif_bytes)
    removed = []
    for n, tri in collision_tris_local(nif_bytes):
        if should_remove(n, tri):
            off = blk.offset + 4 + n * 8
            a = struct.unpack_from("<H", raw, off)[0]
            struct.pack_into("<HHH", raw, off, a, a, a)
            removed.append(n)
    out = bytes(raw)
    assert len(_parse(out).blocks) == len(f.blocks)
    return out, removed


def visual_coverage(nif_bytes, cut, samples=12):
    """Fraction of sample points inside cut["rect"] still covered by wall faces of the band
    (0.0 after a successful cut)."""
    import nif_strips as ns
    f = _parse(nif_bytes)
    lo, hi = cut["band"]
    tris = []
    for b in f.blocks:
        if b.type == "NiTriStripsData":
            d = ns.parse_tri_strips_data(b.raw)
            V = d["vertices"]
            for t in ns.decode_strips(d["strips"]):
                if len(set(t)) == 3:
                    P = [V[i] for i in t]
                    if all(lo <= w <= hi for w, _u, _v in (cut_coords(cut, p) for p in P)):
                        tris.append([(u, v) for _w, u, v in (cut_coords(cut, p) for p in P)])
    u0, u1, v0, v1 = cut["rect"]
    pts = [(u0 + 1 + (u1 - u0 - 2) * i / samples, v0 + 1 + (v1 - v0 - 2) * j / samples)
           for i in range(samples + 1) for j in range(samples + 1)]
    covered = sum(1 for p in pts if any(_inside2(p, t) for t in tris))
    return covered / len(pts)


def _inside2(pt, P):
    (x1, y1), (x2, y2), (x3, y3) = P
    x, y = pt
    d = (y2 - y3) * (x1 - x3) + (x3 - x2) * (y1 - y3)
    if abs(d) < 1e-9:
        return False
    a = ((y2 - y3) * (x - x3) + (x3 - x2) * (y - y3)) / d
    b = ((y3 - y1) * (x - x3) + (x1 - x3) * (y - y3)) / d
    return a >= -1e-6 and b >= -1e-6 and a + b <= 1 + 1e-6


def dist(a, b):
    return math.sqrt(sum((a[i] - b[i]) ** 2 for i in range(3)))


def visual_bounds(nif_bytes):
    """Root-space (lo, hi) bounds of all visual geometry, following node transforms."""
    try:
        f = _parse(nif_bytes)
    except Exception:
        return None    # unparseable mesh (seen in HELIOSOneDeck): no bounds, no crash
    pts = []

    def walk(idx, world):
        b = f.block(idx)
        if b is None:
            return
        if b.type in niflib.NODE_TYPES:
            info = f.parse_node(idx)
            w = world.compose(niflib.Xform(info["rotation"], info["scale"], info["translation"]))
            for c in info["children"]:
                walk(c, w)
        elif b.type in niflib.GEOM_TYPES:
            info = f.parse_geometry(idx)
            w = world.compose(niflib.Xform(info["rotation"], info["scale"], info["translation"]))
            dtype = f.type_of(info["data"])
            if dtype == "NiTriStripsData":
                verts = f.parse_tri_strips_data(info["data"])["vertices"]
            elif dtype == "NiTriShapeData":
                verts = f.parse_tri_shape_data(info["data"])["vertices"]
            else:
                verts = []
            pts.extend(w.apply(p) for p in verts)

    walk(0, niflib.Xform())
    if not pts:
        return None
    return ([min(p[i] for p in pts) for i in range(3)], [max(p[i] for p in pts) for i in range(3)])

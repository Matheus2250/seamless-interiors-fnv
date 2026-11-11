"""Read-only NIF reader for Gamebryo 20.2.0.7 files (Fallout 3 / Fallout New Vegas, user version 11, BS version 34).

Scope: header, block table (raw slices), string table, scene-graph nodes (NiNode family),
geometry (NiTriStrips / NiTriShape with NiTriStripsData / NiTriShapeData vertex and triangle data),
collision chain (bhkCollisionObject -> body -> shape, including hkPackedNiTriStripsData and
bhkConvexVerticesShape), NiControllerSequence names and NiTextKeyExtraData keys.
Anything not parsed is still available as raw bytes via Block.raw.

Layouts were checked by requiring that each parsed block consume exactly its recorded size
(see probe/report scripts). Fields that could not be verified that way are marked in comments.
"""

import math
import struct

HEADER_PREFIX = b"Gamebryo File Format, Version "

HAVOK_SCALE = 7.0  # Havok units -> NIF/game units (FO3/FNV collision)

NODE_TYPES = {
    "NiNode", "BSFadeNode", "BSMultiBoundNode", "NiBSAnimationNode", "NiBSPartNode",
    "BSOrderedNode", "BSValueNode", "NiSwitchNode", "NiLODNode", "NiSortAdjustNode",
    "BSLeafAnimNode", "BSTreeNode", "AvoidNode", "NiBillboardNode", "RootCollisionNode",
}
GEOM_TYPES = {"NiTriShape", "NiTriStrips"}


class NifError(Exception):
    pass


class Reader:
    """Little-endian cursor over a bytes object."""

    def __init__(self, data, pos=0):
        self.data = data
        self.pos = pos

    def _take(self, n):
        if self.pos + n > len(self.data):
            raise NifError("read past end at %d (+%d) of %d" % (self.pos, n, len(self.data)))
        b = self.data[self.pos:self.pos + n]
        self.pos += n
        return b

    def u8(self):
        return self._take(1)[0]

    def bool(self):
        return self.u8() != 0

    def u16(self):
        return struct.unpack("<H", self._take(2))[0]

    def u32(self):
        return struct.unpack("<I", self._take(4))[0]

    def i32(self):
        return struct.unpack("<i", self._take(4))[0]

    def f32(self):
        return struct.unpack("<f", self._take(4))[0]

    def vec3(self):
        return struct.unpack("<3f", self._take(12))

    def vec4(self):
        return struct.unpack("<4f", self._take(16))

    def mat3(self):
        return struct.unpack("<9f", self._take(36))

    def sized_string(self):  # u32 length + chars (no terminator)
        n = self.u32()
        return self._take(n).decode("latin-1")

    def short_string(self):  # u8 length + chars (usually includes a trailing NUL)
        n = self.u8()
        return self._take(n).decode("latin-1").rstrip("\x00")

    def skip(self, n):
        self._take(n)

    def remaining(self):
        return len(self.data) - self.pos


# ---------------------------------------------------------------------------
# Math helpers (3x3 matrices are row-major 9-tuples, applied as M * v)
# ---------------------------------------------------------------------------

IDENTITY3 = (1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0)


def mat_mul(a, b):
    return tuple(sum(a[i * 3 + k] * b[k * 3 + j] for k in range(3)) for i in range(3) for j in range(3))


def mat_vec(m, v):
    return (m[0] * v[0] + m[1] * v[1] + m[2] * v[2],
            m[3] * v[0] + m[4] * v[1] + m[5] * v[2],
            m[6] * v[0] + m[7] * v[1] + m[8] * v[2])


def quat_to_mat(x, y, z, w):
    n = math.sqrt(x * x + y * y + z * z + w * w) or 1.0
    x, y, z, w = x / n, y / n, z / n, w / n
    return (1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w),
            2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w),
            2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y))


class Xform:
    """NIF transform: p' = R * (s * p) + t."""

    __slots__ = ("R", "s", "t")

    def __init__(self, R=IDENTITY3, s=1.0, t=(0.0, 0.0, 0.0)):
        self.R = tuple(R)
        self.s = float(s)
        self.t = tuple(t)

    def compose(self, child):
        """Return self * child (child applied first, then self)."""
        R = mat_mul(self.R, child.R)
        s = self.s * child.s
        rt = mat_vec(self.R, child.t)
        t = (self.s * rt[0] + self.t[0], self.s * rt[1] + self.t[1], self.s * rt[2] + self.t[2])
        return Xform(R, s, t)

    def apply(self, p):
        q = mat_vec(self.R, (p[0] * self.s, p[1] * self.s, p[2] * self.s))
        return (q[0] + self.t[0], q[1] + self.t[1], q[2] + self.t[2])


def bounds(points):
    if not points:
        return None
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    zs = [p[2] for p in points]
    return ((min(xs), min(ys), min(zs)), (max(xs), max(ys), max(zs)))


def scale_pts(points, k):
    return [(p[0] * k, p[1] * k, p[2] * k) for p in points]


# ---------------------------------------------------------------------------

class Block:
    __slots__ = ("index", "type", "size", "offset", "raw")

    def __init__(self, index, type_name, size, offset, raw):
        self.index = index
        self.type = type_name
        self.size = size
        self.offset = offset
        self.raw = raw

    def __repr__(self):
        return "<Block %d %s %dB>" % (self.index, self.type, self.size)


class NifFile:
    def __init__(self, data, path="<bytes>"):
        self.path = path
        self.data = data
        self.blocks = []
        self._parse_header()

    # ------------------------------------------------------------------
    def _parse_header(self):
        d = self.data
        nl = d.index(b"\n")
        magic = d[:nl + 1]
        if not magic.startswith(HEADER_PREFIX):
            raise NifError("not a NIF file: %r" % magic[:60])
        self.header_string = magic.rstrip(b"\n").decode("latin-1")
        r = Reader(d, nl + 1)
        self.version = r.u32()
        self.endian = r.u8()  # 1 = little endian
        self.user_version = r.u32()
        self.num_blocks = r.u32()
        self.bs_version = r.u32()  # "User Version 2" / BS Stream version
        self.author = r.short_string()
        self.process_script = r.short_string()
        self.export_script = r.short_string()
        num_types = r.u16()
        self.block_types = [r.sized_string() for _ in range(num_types)]
        self.block_type_index = [r.u16() for _ in range(self.num_blocks)]
        self.block_sizes = [r.u32() for _ in range(self.num_blocks)]
        num_strings = r.u32()
        self.max_string_length = r.u32()
        self.strings = [r.sized_string() for _ in range(num_strings)]
        num_groups = r.u32()
        self.groups = [r.u32() for _ in range(num_groups)]
        self.data_start = r.pos

        off = self.data_start
        for i in range(self.num_blocks):
            size = self.block_sizes[i]
            raw = d[off:off + size]
            if len(raw) != size:
                raise NifError("block %d overruns file" % i)
            self.blocks.append(Block(i, self.block_types[self.block_type_index[i]], size, off, raw))
            off += size
        fr = Reader(d, off)
        self.footer_roots = [fr.i32() for _ in range(fr.u32())]
        self.footer_end = fr.pos
        self.trailing = len(d) - fr.pos

    # ------------------------------------------------------------------
    def string(self, idx):
        if idx is None or idx < 0 or idx >= len(self.strings):
            return None
        return self.strings[idx]

    def block(self, idx):
        if idx is None or idx < 0 or idx >= len(self.blocks):
            return None
        return self.blocks[idx]

    def type_of(self, idx):
        b = self.block(idx)
        return b.type if b else None

    def blocks_of(self, *type_names):
        return [b for b in self.blocks if b.type in type_names]

    def type_counts(self):
        counts = {}
        for b in self.blocks:
            counts[b.type] = counts.get(b.type, 0) + 1
        return counts

    # ------------------------------------------------------------------
    # NiObjectNET + NiAVObject prefix (BS34 layout)
    def parse_av(self, r):
        info = {}
        info["name"] = self.string(r.i32())
        n_extra = r.u32()
        info["extra"] = [r.i32() for _ in range(n_extra)]
        info["controller"] = r.i32()
        info["flags"] = r.u32()          # u32 for BS34 (u16 would not consume the block exactly)
        info["translation"] = r.vec3()
        info["rotation"] = r.mat3()      # row-major
        info["scale"] = r.f32()
        n_props = r.u32()
        info["properties"] = [r.i32() for _ in range(n_props)]
        info["collision"] = r.i32()
        return info

    def parse_node(self, idx):
        b = self.blocks[idx]
        r = Reader(b.raw)
        info = self.parse_av(r)
        n_children = r.u32()
        info["children"] = [r.i32() for _ in range(n_children)]
        n_effects = r.u32()
        info["effects"] = [r.i32() for _ in range(n_effects)]
        info["_consumed"] = r.pos
        info["_size"] = b.size
        return info

    # ------------------------------------------------------------------
    # Geometry (NiTriStrips / NiTriShape: NiAVObject + NiGeometry)
    def parse_geometry(self, idx):
        b = self.blocks[idx]
        r = Reader(b.raw)
        info = self.parse_av(r)
        info["data"] = r.i32()
        info["skin"] = r.i32()
        n_mat = r.u32()
        info["materials"] = []
        for _ in range(n_mat):
            name = self.string(r.i32())
            extra = r.i32()
            info["materials"].append((name, extra))
        info["active_material"] = r.i32()
        info["dirty"] = r.u8()
        info["_consumed"] = r.pos
        info["_size"] = b.size
        return info

    def _parse_geom_data(self, r):
        """NiGeometryData, BS34 layout (no material CRC; tangents follow normals when flag 0x1000)."""
        info = {}
        info["group_id"] = r.i32()
        info["num_vertices"] = r.u16()
        info["keep_flags"] = r.u8()
        info["compress_flags"] = r.u8()
        info["has_vertices"] = r.bool()
        n = info["num_vertices"]
        info["vertices"] = [r.vec3() for _ in range(n)] if info["has_vertices"] else []
        info["data_flags"] = r.u16()  # low 6 bits: UV set count; 0x1000: tangents present
        info["has_normals"] = r.bool()
        info["normals"] = [r.vec3() for _ in range(n)] if info["has_normals"] else []
        if info["has_normals"] and (info["data_flags"] & 0x1000):
            info["tangents"] = [r.vec3() for _ in range(n)]
            info["bitangents"] = [r.vec3() for _ in range(n)]
        info["center"] = r.vec3()
        info["radius"] = r.f32()
        info["has_vertex_colors"] = r.bool()
        info["vertex_colors"] = [r.vec4() for _ in range(n)] if info["has_vertex_colors"] else []
        num_uv = info["data_flags"] & 0x3F
        info["num_uv_sets"] = num_uv
        info["uv_sets"] = [[(r.f32(), r.f32()) for _ in range(n)] for _ in range(num_uv)]
        info["consistency"] = r.u16()
        info["additional_data"] = r.i32()
        return info

    def parse_tri_strips_data(self, idx):
        b = self.blocks[idx]
        r = Reader(b.raw)
        try:
            g = self._parse_geom_data(r)
        except NifError:
            # truncated mesh in the BSA (HELIOSOneDeck): treat as empty geometry
            return {"vertices": [], "normals": [], "uv_sets": [], "num_triangles": 0,
                    "triangles": [], "strips": []}
        g["num_triangles"] = r.u16()
        n_strips = r.u16()
        lengths = [r.u16() for _ in range(n_strips)]
        has_points = r.bool()
        strips = []
        if has_points:
            for L in lengths:
                strips.append([r.u16() for _ in range(L)])
        tris = []
        for s in strips:
            for i in range(len(s) - 2):
                a, bb, c = s[i], s[i + 1], s[i + 2]
                if a == bb or bb == c or a == c:
                    continue
                tris.append((a, bb, c) if i % 2 == 0 else (bb, a, c))
        g["strips"] = strips
        g["triangles"] = tris
        g["_consumed"] = r.pos
        g["_size"] = b.size
        return g

    def parse_tri_shape_data(self, idx):
        b = self.blocks[idx]
        r = Reader(b.raw)
        g = self._parse_geom_data(r)
        g["num_triangles"] = r.u16()
        g["num_triangle_points"] = r.u32()
        g["has_triangles"] = r.bool()
        tris = []
        if g["has_triangles"]:
            tris = [(r.u16(), r.u16(), r.u16()) for _ in range(g["num_triangles"])]
        g["triangles"] = tris
        g["_consumed"] = r.pos
        g["_size"] = b.size
        return g

    # ------------------------------------------------------------------
    # Animation
    def controller_sequence_name(self, idx):
        """NiControllerSequence: NiObjectNET name is a string-table index."""
        b = self.blocks[idx]
        r = Reader(b.raw)
        return self.string(r.i32())

    def text_keys(self, idx):
        """NiTextKeyExtraData: name index, count, then (float time, string-table index) pairs."""
        b = self.blocks[idx]
        r = Reader(b.raw)
        r.i32()
        n = r.u32()
        keys = []
        for _ in range(n):
            t = r.f32()
            keys.append((t, self.string(r.i32())))
        return keys

    # ------------------------------------------------------------------
    # Collision
    def parse_coll_object(self, idx):
        """bhkCollisionObject (bhkNiCollisionObject): target ref, flags u16, body ref."""
        b = self.blocks[idx]
        r = Reader(b.raw)
        target = r.i32()
        flags = r.u16()
        body = r.i32()
        return {"target": target, "flags": flags, "body": body,
                "_consumed": r.pos, "_size": b.size}

    def body_shape(self, idx):
        """bhkRigidBody / bhkRigidBodyT: the first field is the shape ref (bhkWorldObject)."""
        return Reader(self.blocks[idx].raw).i32()

    def packed_tri_data(self, idx):
        """hkPackedNiTriStripsData layout (BS34, verified by exact consumption):
            u32 num_triangles; per triangle: 3 x u16 indices + u16 welding info (8 bytes)
            u32 num_vertices; u8 flag; num_vertices x vec3 (Havok units)
            trailing bytes (sub-part table, not decoded) recorded as 'trailing'."""
        b = self.blocks[idx]
        r = Reader(b.raw)
        n_tris = r.u32()
        tris = []
        for _ in range(n_tris):
            tris.append((r.u16(), r.u16(), r.u16()))
            r.u16()  # welding
        n_verts = r.u32()
        flag = r.u8()
        verts = [r.vec3() for _ in range(n_verts)]
        return {"num_triangles": n_tris, "num_vertices": n_verts, "flag": flag,
                "triangles": tris, "vertices": verts,
                "trailing": len(b.raw) - r.pos, "_size": b.size}

    def packed_shape_data_ref(self, idx):
        """bhkPackedNiTriStripsShape: the hkPackedNiTriStripsData ref is the final i32 of the block."""
        raw = self.blocks[idx].raw
        return struct.unpack_from("<i", raw, len(raw) - 4)[0]

    def packed_shape_scale(self, idx):
        """bhkPackedNiTriStripsShape: vec4 scale at offset 16 (Havok units multiplier)."""
        raw = self.blocks[idx].raw
        return struct.unpack_from("<4f", raw, 16)[:3]

    def mopp_child(self, idx):
        """bhkMoppBvTreeShape: first field is the child shape ref."""
        return Reader(self.blocks[idx].raw).i32()

    def convex_vertices(self, idx):
        """bhkConvexVerticesShape: material u32, radius f32, 24 bytes unknown,
        u32 num_vertices + vec4 each, u32 num_normals + vec4 each (plane normals)."""
        b = self.blocks[idx]
        r = Reader(b.raw)
        material = r.u32()
        radius = r.f32()
        r.skip(24)
        n_v = r.u32()
        verts = [r.vec4()[:3] for _ in range(n_v)]
        n_n = r.u32()
        normals = [r.vec4()[:3] for _ in range(n_n)]
        return {"material": material, "radius": radius, "vertices": verts,
                "normals": normals, "_consumed": r.pos, "_size": b.size}

    def box_shape(self, idx):
        """bhkBoxShape: bhkConvexShape prefix then half extents vec4 (Havok units). Returned raw-ish."""
        raw = self.blocks[idx].raw
        return {"raw_floats": struct.unpack_from("<%df" % (len(raw) // 4), raw[:len(raw) // 4 * 4])}

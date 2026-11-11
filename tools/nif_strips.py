"""Full-field NiTriStripsData reader / writer for NIF 20.2.0.7, BS version 34 (FNV).

Built on niflib.Reader. Field order follows reference/nif.xml:
  NiGeometryData (BS202 branch: "BS Data Flags" u16 replaces "Data Flags"; no Material CRC at BS34)
  NiTriBasedGeomData: Num Triangles u16
  NiTriStripsData:    Num Strips u16, Strip Lengths u16[], Has Points bool, Points u16[] per strip

write_tri_strips_data(parse_tri_strips_data(raw)) == raw is checked by verify_nif_edit.py for
every block that is rewritten, so the writer is known to reproduce the reader's layout exactly.
"""

import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from niflib import Reader  # noqa: E402


class StripsFormatError(Exception):
    pass


def parse_tri_strips_data(raw):
    r = Reader(raw)
    d = {}
    d["group_id"] = r.i32()
    d["num_vertices"] = r.u16()
    d["keep"] = r.u8()
    d["compress"] = r.u8()
    d["has_vertices"] = r.u8()
    n = d["num_vertices"]
    d["vertices"] = [r.vec3() for _ in range(n)] if d["has_vertices"] else []
    d["bs_flags"] = r.u16()                       # BSGeometryDataFlags: bit0 = has UV, bit12 = tangents
    d["has_normals"] = r.u8()
    d["normals"] = [r.vec3() for _ in range(n)] if d["has_normals"] else []
    has_tan = bool(d["has_normals"]) and bool(d["bs_flags"] & 0x1000)
    d["has_tangents"] = has_tan
    d["tangents"] = [r.vec3() for _ in range(n)] if has_tan else []
    d["bitangents"] = [r.vec3() for _ in range(n)] if has_tan else []
    d["center"] = r.vec3()
    d["radius"] = r.f32()
    d["has_vertex_colors"] = r.u8()
    d["colors"] = [r.vec4() for _ in range(n)] if d["has_vertex_colors"] else []
    # nif.xml: UV Sets length = (Data Flags & 63) | (BS Data Flags & 1); Data Flags is absent for BS202.
    n_uv = d["bs_flags"] & 1
    d["num_uv_sets"] = n_uv
    d["uv_sets"] = [[(r.f32(), r.f32()) for _ in range(n)] for _ in range(n_uv)]
    d["consistency"] = r.u16()
    d["additional_data"] = r.i32()
    d["num_triangles"] = r.u16()
    n_strips = r.u16()
    d["strip_lengths"] = [r.u16() for _ in range(n_strips)]
    d["has_points"] = r.u8()
    d["strips"] = ([[r.u16() for _ in range(L)] for L in d["strip_lengths"]]
                   if d["has_points"] else [])
    if r.pos != len(raw):
        raise StripsFormatError("consumed %d of %d bytes" % (r.pos, len(raw)))
    return d


def write_tri_strips_data(d):
    """Serialize a dict produced by parse_tri_strips_data (lists must match num_vertices)."""
    n = d["num_vertices"]
    out = bytearray()
    out += struct.pack("<iHBBB", d["group_id"], n, d["keep"], d["compress"], d["has_vertices"])
    if d["has_vertices"]:
        out += _pack_vec3(d["vertices"], n)
    out += struct.pack("<H", d["bs_flags"])
    out += struct.pack("<B", d["has_normals"])
    if d["has_normals"]:
        out += _pack_vec3(d["normals"], n)
    if d["has_normals"] and (d["bs_flags"] & 0x1000):
        out += _pack_vec3(d["tangents"], n)
        out += _pack_vec3(d["bitangents"], n)
    out += struct.pack("<3ff", *d["center"], d["radius"])
    out += struct.pack("<B", d["has_vertex_colors"])
    if d["has_vertex_colors"]:
        if len(d["colors"]) != n:
            raise StripsFormatError("colors length mismatch")
        out += b"".join(struct.pack("<4f", *c) for c in d["colors"])
    if len(d["uv_sets"]) != (d["bs_flags"] & 1):
        raise StripsFormatError("uv set count mismatch")
    for uvs in d["uv_sets"]:
        if len(uvs) != n:
            raise StripsFormatError("uv length mismatch")
        out += b"".join(struct.pack("<2f", u, v) for (u, v) in uvs)
    out += struct.pack("<Hi", d["consistency"], d["additional_data"])
    out += struct.pack("<H", d["num_triangles"])
    out += struct.pack("<H", len(d["strip_lengths"]))
    out += b"".join(struct.pack("<H", L) for L in d["strip_lengths"])
    out += struct.pack("<B", d["has_points"])
    if d["has_points"]:
        for strip in d["strips"]:
            out += b"".join(struct.pack("<H", i) for i in strip)
    return bytes(out)


def _pack_vec3(vecs, n):
    if len(vecs) != n:
        raise StripsFormatError("vec3 array length %d != num_vertices %d" % (len(vecs), n))
    return b"".join(struct.pack("<3f", *v) for v in vecs)


def decode_strips(strips):
    """Triangle list from strips, same winding convention as niflib.parse_tri_strips_data.
    Degenerate (repeated index) triangles are dropped."""
    tris = []
    for s in strips:
        for i in range(len(s) - 2):
            a, b, c = s[i], s[i + 1], s[i + 2]
            if a == b or b == c or a == c:
                continue
            tris.append((a, b, c) if i % 2 == 0 else (b, a, c))
    return tris

"""Remove the building's internal collision walls that cut through the aligned interior.

The General Store building collision has a full-height wall at building-local x = 608
(and a short one at y = -32 behind it) that would block the back of the store interior.
Rebuilding the MOPP tree is not needed: the matching triangles are made degenerate
(all three indices equal) in place, so triangle indices and the MOPP code stay valid
and a zero-area triangle never collides.

Usage: py -I open_collision.py [nif]   (edits tools\\out\\meshes\\SeamlessInteriors\\nv_generalstore_open.nif in place)
"""

import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import niflib  # noqa: E402

HAVOK_SCALE = 7.0
DEFAULT_NIF = r"out\meshes\SeamlessInteriors\nv_generalstore_open.nif"

# Walkable volume of the aligned interior in building-local game units.
REGION_X = (-270.0, 720.0)
REGION_Y = (-240.0, 310.0)
REGION_Z = (25.0, 150.0)


def blocking_triangles(nif, idx):
    data = nif.packed_tri_data(idx)
    verts = [tuple(c * HAVOK_SCALE for c in v) for v in data["vertices"]]
    hits = []
    for n, tri in enumerate(data["triangles"]):
        pts = [verts[i] for i in tri]
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        zs = [p[2] for p in pts]
        if (min(zs) < REGION_Z[1] and max(zs) > REGION_Z[0]
                and max(xs) > REGION_X[0] and min(xs) < REGION_X[1]
                and max(ys) > REGION_Y[0] and min(ys) < REGION_Y[1]):
            hits.append((n, tri, pts))
    return hits


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_NIF
    raw = bytearray(open(path, "rb").read())
    nif = niflib.NifFile(bytes(raw), path)
    (block,) = nif.blocks_of("hkPackedNiTriStripsData")
    idx = block.index if hasattr(block, "index") else block
    blk = nif.block(idx)

    hits = blocking_triangles(nif, idx)
    print("triangles blocking the interior: %d" % len(hits))
    for n, tri, pts in hits:
        print("  tri %d %s %s" % (n, tri, [tuple(round(c) for c in p) for p in pts]))
        # Layout: u32 num_triangles, then 8 bytes per triangle (3 x u16 index + u16 welding).
        off = blk.offset + 4 + n * 8
        a = struct.unpack_from("<H", raw, off)[0]
        struct.pack_into("<HHH", raw, off, a, a, a)

    open(path, "wb").write(bytes(raw))

    check = niflib.NifFile(bytes(raw), path)
    left = blocking_triangles(check, idx)
    left = [h for h in left if len(set(h[1])) == 3]
    print("non-degenerate triangles still blocking: %d" % len(left))
    assert not left
    assert len(raw) == len(nif.data)


if __name__ == "__main__":
    main()

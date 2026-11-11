"""Cut a doorway in an interior wall piece (generalisation of cut_door_nif.py).

In the original interior the store's load door sat flush against a solid wall made of two
room-kit corner pieces (NVGS_GS_RmCorIn01/02). Once the interior is placed inside the real
building that wall blocks the doorway, so each piece gets:
  - its visual wall triangles clipped around the door rectangle (same clipping as
    cut_door_nif.py, but on a configurable wall axis), and
  - its collision wall triangles made degenerate (MOPP stays valid, see open_collision.py).
    The building's own collision facade, which already has the door gap, lies in the same
    plane and keeps the wall solid around the door.

Usage: py -I cut_wall_nif.py [--analyze]
"""

import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import bsalib  # noqa: E402
import cut_door_nif as cd  # noqa: E402
import niflib  # noqa: E402
import nif_strips as ns  # noqa: E402

MESHES_BSA = r"C:\Program Files (x86)\Steam\steamapps\common\Fallout New Vegas\Data\Fallout - Meshes.bsa"
OUT_DIR = os.path.join(HERE, "out", "meshes", "SeamlessInteriors")
HAVOK_SCALE = 7.0

# Door rectangle in each piece's local space, from the exterior door leaf
# (world x -69383.5..-69256, z 8380.6..8555.1) mapped through the placed refs
# 01000847 (CorIn02, rot z 90.62) and 01000848 (CorIn01, rot z 180.62).
# axis = index of the wall normal; (u, v) = the in-plane axes of the rectangle.
PIECES = [
    {"src": r"meshes\dungeons\nv_goodsprings\nvgs_gs_rmcorin02.nif",
     "dst": "nvgs_gs_rmcorin02_door.nif",
     "axis": 0, "u": 1, "v": 2, "band": (100.0, 135.0),
     "rect": (-192.0, -65.5, 1.5, 175.5)},
    {"src": r"meshes\dungeons\nv_goodsprings\nvgs_gs_rmcorin01.nif",
     "dst": "nvgs_gs_rmcorin01_door.nif",
     "axis": 1, "u": 0, "v": 2, "band": (100.0, 135.0),
     "rect": (-190.5, -64.0, 1.5, 175.5)},
]


def configure(piece, f):
    """Point cut_door_nif's module-level settings at this piece."""
    axis, u, v = piece["axis"], piece["u"], piece["v"]
    cd.Y0, cd.Y1, cd.Z0, cd.Z1 = piece["rect"]
    cd.X_LO, cd.X_HI = piece["band"]
    cd.RAY_X_LO, cd.RAY_X_HI = piece["band"]
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
    cd.yz_of = lambda P: [(p[u], p[v]) for p in P]

    # Any face lying inside the wall band is part of the wall (including the slanted
    # baseboard and chair-rail bevels), so it is cut too; floor faces never fit the band.
    def is_vertical(P):
        return cd.length(cd.tri_normal(P)) > 0

    cd.is_vertical = is_vertical
    cd.in_band = lambda P: all(piece["band"][0] <= p[axis] <= piece["band"][1] for p in P)
    # every NiTriStripsData is a candidate; map it to its NiTriStrips owner by data link order
    data_blocks = [b.index for b in f.blocks if b.type == "NiTriStripsData"]
    geom_blocks = [b.index for b in f.blocks if b.type == "NiTriStrips"]
    cd.VISUAL_DATA_BLOCKS = data_blocks
    cd.GEOM_BLOCK_OF_DATA = dict(zip(data_blocks, geom_blocks))


def degenerate_collision(raw, f, piece):
    """Degenerate collision triangles crossing the door rectangle. Returns (raw, count)."""
    axis, u, v = piece["axis"], piece["u"], piece["v"]
    u0, u1, v0, v1 = piece["rect"]
    lo, hi = piece["band"]
    (blk,) = [b for b in f.blocks if b.type == "hkPackedNiTriStripsData"]
    data = f.packed_tri_data(blk.index)
    verts = [tuple(c * HAVOK_SCALE for c in p) for p in data["vertices"]]
    hits = 0
    for n, tri in enumerate(data["triangles"]):
        P = [verts[i] for i in tri]
        if not all(lo - 15 <= p[axis] <= hi + 15 for p in P):
            continue
        if max(p[u] for p in P) <= u0 or min(p[u] for p in P) >= u1:
            continue
        if max(p[v] for p in P) <= v0 or min(p[v] for p in P) >= v1:
            continue
        off = blk.offset + 4 + n * 8
        a = struct.unpack_from("<H", raw, off)[0]
        struct.pack_into("<HHH", raw, off, a, a, a)
        hits += 1
    return raw, hits


def main(argv):
    bsa = bsalib.BSA(MESHES_BSA)
    os.makedirs(OUT_DIR, exist_ok=True)
    for piece in PIECES:
        src = bsa.read(piece["src"])
        f = niflib.NifFile(src, piece["src"])
        configure(piece, f)
        print("==", piece["src"])
        blocks = cd.load_visual(f)
        walls, _others = cd.run_analyze(blocks)
        if "--analyze" in argv:
            continue
        select = {}
        for bi, ti, t, P in walls:
            select.setdefault(bi, {})[ti] = True
        replacements = {}
        for bi, vb in blocks.items():
            if bi not in select:
                continue
            before = len(vb.tris)
            new_tris, stats = cd.cut_block(vb, select[bi])
            replacements[bi] = cd.build_data_block(vb.d, new_tris)
            print("  block %d: tris %d -> %d, cut %d" % (bi, before, len(new_tris), stats["cut"]))
        out, _ = cd.splice_nif(src, f, replacements)
        # collision offsets refer to the spliced file, so re-read it before patching
        out = bytearray(out)
        out, hits = degenerate_collision(out, niflib.NifFile(bytes(out), piece["dst"]), piece)
        print("  collision triangles degenerated: %d" % hits)
        dst = os.path.join(OUT_DIR, piece["dst"])
        with open(dst, "wb") as fh:
            fh.write(bytes(out))
        check = niflib.NifFile(bytes(out), dst)
        assert len(check.blocks) == len(f.blocks)
        print("  wrote", dst, len(out), "bytes")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

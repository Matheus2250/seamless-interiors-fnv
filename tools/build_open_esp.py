#!/usr/bin/env python3
"""Build SeamlessInteriors.esp: the Goodsprings General Store interior placed INSIDE the real
exterior building, aligned to the real exterior door, so the player walks in through the
ordinary (animated, script-free) door. No XTEL, no pocket cell, no teleport.

Usage:
    py -I build_open_esp.py                  # write tools/out/SeamlessInteriors.esp + fit report
    py -I build_open_esp.py --report-only    # alignment + fit report + collision check, no ESP
    py -I build_open_esp.py --check-only     # re-read the existing ESP and print key records

Inputs (read only): FalloutNV.esm (master), tools/out/meshes/architecture/goodsprings/
nv_generalstore.nif (building collision and visual bounds, via niflib).
Outputs: tools/out/SeamlessInteriors.esp, tools/out/fit_report.txt.
The NIF for the open building (SeamlessInteriors\\nv_generalstore_open.nif) is produced by a
separate tool; this builder only references it by path.

Standard library only. Nothing is written into the game folder.
"""

import argparse
import collections
import math
import os
import struct
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)  # py -I does not put the script directory on sys.path

from esplib import GROUP_HEADER, RECORD_HEADER, Plugin, iter_subrecords  # noqa: E402
import build_esp as be  # noqa: E402  (reuses the verified helpers; build_esp.py is unchanged)
import niflib  # noqa: E402

MASTER_PATH = be.MASTER_PATH
OUT_DIR = os.path.join(HERE, "out")
OUT_PATH = os.path.join(OUT_DIR, "SeamlessInteriors.esp")
FIT_REPORT = os.path.join(OUT_DIR, "fit_report.txt")
BUILDING_NIF = os.path.join(OUT_DIR, "meshes", "architecture", "goodsprings", "nv_generalstore.nif")
OPEN_MODL = b"SeamlessInteriors\\nv_generalstore_open.nif\x00"
OPEN_NIF_FILE = os.path.join(OUT_DIR, "meshes", "SeamlessInteriors", "nv_generalstore_open.nif")
VALIDATOR = os.path.join(HERE, "validate_esp.py")

AUTHOR = "SeamlessInteriors prototype (open building)"
FLAG_PERSISTENT = 0x00000400
FLAG_COMPRESSED = 0x00040000
FORM_VERSION = 15
NEW_FORM_BASE = 0x01000800

# ---- master records ---------------------------------------------------------------------
GS_CELL = 0x001057F1            # interior CELL GSGeneralStore
GS_INT_DOOR = 0x00105BD6        # interior door REFR (persistent, XTEL): NOT copied
GS_INT_DOOR_BASE = 0x000FB730   # NVCraftsmanHomeDoor (DOOR, animated)
EXT_DOOR = 0x00106373           # exterior door REFR (persistent, world cell 846EA)
BUILDING_STAT = 0x00101CA3      # STAT NVGeneralStore
BUILDING_REF = 0x00101CA6       # building REFR, TEMPORARY child of exterior cell DAEB9
WORLD = 0x000DA726              # WastelandNV
WORLD_PERSIST = 0x000846EA      # persistent cell of WastelandNV
EXT_CELL = 0x000DAEB9           # exterior cell (-17, 0)
EXT_GRID = (-17, 0)             # its grid x, y
NORTH_CELL = 0x000DAEB8         # exterior cell (-17, 1): the back of the store is in it
DOORFRAME_BASE_EDID = "NVCraftsDoorframe"

CELL_X = (-69632.0, -65536.0)   # [min, max) of exterior cell (-17, 0)
CELL_Y = (0.0, 4096.0)

# Subrecords dropped from copied refs and from the exterior door override.
DROP = set(be.DROP_ALWAYS)  # XTEL XNDP XOWN XLOC XESP XLKR XEZN XRGD XPRM
# Copied refs keep their own owner and lock; the others pointed at interior refs/cells.
COPY_DROP = DROP - {"XOWN", "XLOC"}
# In the interior, refs without an owner inherited the cell owner (GSGeneralStore: XOWN
# GSChet), which is what makes the store's items "Steal". The exterior cell has no owner,
# so the copies get it explicitly.
STORE_OWNER = 0x00104C78  # NPC_ GSChet
PRE_XOWN = {"EDID", "NAME", "XEZN", "XRGD", "XRGB", "XPRM", "XTRI", "XMBO", "XTEL", "MMRK",
            "FULL", "CNAM", "BNAM", "MNAM", "NNAM", "XSRF", "XSRD", "XTRG", "XLCM", "XRDO"}

# Door leaf centre in door-mesh local coordinates (bounds x -11..5, y 0..128.9, z 1..176.4).
LEAF_C = (-3.0, 64.45, 88.7)

# Building visual bounds (root space) are read from the NIF at run time; these are the
# values recorded in out/nif_report.txt, used only as a fallback message.
VIS_FACADE_X = -285.84          # front facade plane (building local x)
OVERSHOOT_TOL = 8.0
INNER_MARGIN = 30.0

SKIP_BASE_EDID_SUBSTR = ("FX", "Marker", "Glow", "Beam", "Particle")

# ----------------------------------------------------------------------------------------
# geometry helpers (Gamebryo convention: world = P + Rz(-rz) * local)
# ----------------------------------------------------------------------------------------

def rot2(a, x, y):
    """2-D rotation by angle a (standard math orientation): Rz(a) applied to (x, y)."""
    c, s = math.cos(a), math.sin(a)
    return (c * x - s * y, s * x + c * y)


def mat_mul3(a, b):
    return [[sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)] for i in range(3)]


def euler_matrix(rx, ry, rz):
    """Bethesda ref rotation: R = Rz(-rz) * Ry(-ry) * Rx(-rx), row-major, applied to column vectors."""
    def rx_m(a):
        c, s = math.cos(a), math.sin(a)
        return [[1, 0, 0], [0, c, -s], [0, s, c]]

    def ry_m(a):
        c, s = math.cos(a), math.sin(a)
        return [[c, 0, s], [0, 1, 0], [-s, 0, c]]

    def rz_m(a):
        c, s = math.cos(a), math.sin(a)
        return [[c, -s, 0], [s, c, 0], [0, 0, 1]]

    return mat_mul3(rz_m(-rz), mat_mul3(ry_m(-ry), rx_m(-rx)))


def mat_apply(m, v):
    return tuple(sum(m[i][k] * v[k] for k in range(3)) for i in range(3))


def norm_angle(a):
    two_pi = 2.0 * math.pi
    a = math.fmod(a, two_pi)
    if a < 0:
        a += two_pi
    return a


def ref_transform(plugin_rec):
    """(pos, rot) of a REFR from DATA."""
    d = plugin_rec.get("DATA")
    return struct.unpack_from("<3f", d, 0), struct.unpack_from("<3f", d, 12)


def leaf_center(pos, rz):
    """World centre of the door leaf for a door at pos with heading rz."""
    x, y = rot2(-rz, LEAF_C[0], LEAF_C[1])
    return (pos[0] + x, pos[1] + y, pos[2] + LEAF_C[2])


def map_point(p, ext_leaf, int_leaf, delta):
    """Interior-cell point -> exterior world point: ext_leaf + Rz(-delta) (p - int_leaf)."""
    x, y = rot2(-delta, p[0] - int_leaf[0], p[1] - int_leaf[1])
    return (ext_leaf[0] + x, ext_leaf[1] + y, ext_leaf[2] + (p[2] - int_leaf[2]))


# ----------------------------------------------------------------------------------------
# NIF: building visual bounds and collision
# ----------------------------------------------------------------------------------------

def read_building_nif():
    with open(BUILDING_NIF, "rb") as fh:
        nif = niflib.NifFile(fh.read(), BUILDING_NIF)
    pts = []
    tri_data_blocks = []
    for b in nif.blocks_of("NiTriStrips"):
        geom = nif.parse_geometry(b.index)
        data_idx = geom["data"]
        if nif.type_of(data_idx) != "NiTriStripsData":
            continue
        g = nif.parse_tri_strips_data(data_idx)
        pts.extend(g["vertices"])
    coll_blocks = nif.blocks_of("hkPackedNiTriStripsData")
    if len(coll_blocks) != 1:
        raise SystemExit("expected one hkPackedNiTriStripsData in %s" % BUILDING_NIF)
    coll = nif.packed_tri_data(coll_blocks[0].index)
    coll_scale = niflib.HAVOK_SCALE
    coll_verts = [(x * coll_scale, y * coll_scale, z * coll_scale) for x, y, z in coll["vertices"]]
    lo = tuple(min(p[i] for p in pts) for i in range(3))
    hi = tuple(max(p[i] for p in pts) for i in range(3))
    return {"vis_min": lo, "vis_max": hi, "coll_verts": coll_verts,
            "coll_tris": coll["triangles"], "coll_raw_count": coll["num_vertices"],
            "n_vis_points": len(pts)}


# ----------------------------------------------------------------------------------------
# Master lookups
# ----------------------------------------------------------------------------------------

def require(plugin, fid, sig):
    rec = plugin.get_record(fid)
    if rec is None or rec.sig != sig:
        raise SystemExit("master %08X is not %s (got %r)" % (fid, sig, None if rec is None else rec.sig))
    return rec


def load_master():
    plugin = Plugin(MASTER_PATH)  # read-only mmap
    plugin.build_index()
    recs = {
        "cell": require(plugin, GS_CELL, "CELL"),
        "int_door": require(plugin, GS_INT_DOOR, "REFR"),
        "int_door_base": require(plugin, GS_INT_DOOR_BASE, "DOOR"),
        "ext_door": require(plugin, EXT_DOOR, "REFR"),
        "bld_stat": require(plugin, BUILDING_STAT, "STAT"),
        "bld_ref": require(plugin, BUILDING_REF, "REFR"),
        "world": require(plugin, WORLD, "WRLD"),
        "world_cell": require(plugin, WORLD_PERSIST, "CELL"),
        "ext_cell": require(plugin, EXT_CELL, "CELL"),
    }
    for name, r in recs.items():
        assert not r.is_compressed, "%s (%08X) is compressed" % (name, r.form_id)
    assert recs["int_door"].get("NAME") == struct.pack("<I", GS_INT_DOOR_BASE)
    assert recs["ext_door"].get("NAME") == struct.pack("<I", GS_INT_DOOR_BASE)
    assert recs["ext_door"].flags & FLAG_PERSISTENT
    assert recs["bld_ref"].get("NAME") == struct.pack("<I", BUILDING_STAT)
    assert recs["bld_stat"].edid() == "NVGeneralStore"
    assert recs["bld_ref"].path[-1].type_name == "temporary_children"
    assert recs["bld_ref"].path[-1].label_text() == "0x%08X" % EXT_CELL
    assert recs["world_cell"].flags & FLAG_PERSISTENT
    assert recs["ext_cell"].get("XCLC") is not None
    assert struct.unpack_from("<ii", recs["ext_cell"].get("XCLC")) == (-17, 0)
    assert [g.group_type for g in recs["ext_cell"].path] == [0, 1, 4, 5]
    assert [g.group_type for g in recs["world_cell"].path] == [0, 1]
    assert recs["ext_door"].path[-1].type_name == "persistent_children"
    top_order = [g.label_text() for g in plugin.top_groups()]
    assert top_order.index("STAT") < top_order.index("WRLD"), top_order
    return plugin, recs, top_order


def find_interior_copies(plugin):
    """Copied interior refs (be.classify_interior_refs rules) plus the skip list."""
    cell_group = be.find_cell_children_group(plugin, GS_CELL)
    copies, skipped = be.classify_interior_refs(plugin, cell_group)
    kept = []
    for ref in copies:
        base = base_of(plugin, ref)
        edid = base.edid() or ""
        if edid.startswith(WINDOW_LIGHT_FX):
            skipped.append((ref.form_id, "REFR", "%08X %s" % (base.form_id, edid),
                            "window sunlight effect (shows through the facade outside)"))
        else:
            kept.append(ref)
    return kept, skipped


# Fake "sunlight through the window" planes of the interior. Outside there is real sun, and
# these planes stick out of the building as glowing strips on the facade.
WINDOW_LIGHT_FX = ("IndFXLightRays", "FXLightBeam")


def base_of(plugin, ref):
    return plugin.get_record(struct.unpack("<I", ref.get("NAME"))[0])


def ref_xscl(ref):
    d = ref.get("XSCL")
    return struct.unpack("<f", d)[0] if d is not None else 1.0


def obnd_of(base):
    d = base.get("OBND")
    if d is None:
        return None
    return struct.unpack("<6h", d)


def skip_for_bbox(base):
    if base.sig == "LIGH":
        return "light base"
    edid = base.edid() or ""
    for key in SKIP_BASE_EDID_SUBSTR:
        if key in edid:
            return "FX/marker base (%s)" % key
    return None


# ----------------------------------------------------------------------------------------
# Alignment
# ----------------------------------------------------------------------------------------

class Copy:
    __slots__ = ("src", "base", "edid", "obnd", "xscl", "pos", "rot", "bbox_skip",
                 "new_pos", "new_rz", "name_override")

    def __init__(self, src, base, obnd, xscl, pos, rot, bbox_skip):
        self.src = src
        self.base = base
        self.edid = base.edid() if base is not None else "?"
        self.obnd = obnd
        self.xscl = xscl
        self.pos = pos
        self.rot = rot
        self.bbox_skip = bbox_skip
        self.new_pos = None
        self.new_rz = None
        self.name_override = None


def make_copies(plugin, raw_copies):
    out = []
    for ref in raw_copies:
        base = base_of(plugin, ref)
        pos, rot = ref_transform(ref)
        out.append(Copy(ref, base, obnd_of(base), ref_xscl(ref), pos, rot, skip_for_bbox(base)))
    return out


def building_frame(bld_pos, bld_rz):
    def to_local(w):
        lx, ly = rot2(bld_rz, w[0] - bld_pos[0], w[1] - bld_pos[1])
        return (lx, ly, w[2] - bld_pos[2])
    return to_local


def copy_corners_world(c, new_pos, new_rot):
    """World corners of the copy's OBND box with the written (new) transform."""
    x1, y1, z1, x2, y2, z2 = c.obnd
    m = euler_matrix(new_rot[0], new_rot[1], new_rot[2])
    corners = []
    for cx in (x1, x2):
        for cy in (y1, y2):
            for cz in (z1, z2):
                v = mat_apply(m, (cx * c.xscl, cy * c.xscl, cz * c.xscl))
                corners.append((new_pos[0] + v[0], new_pos[1] + v[1], new_pos[2] + v[2]))
    return corners


def evaluate_candidate(copies, ext_leaf, int_leaf, delta, to_local):
    """Apply delta to every copy (new_pos / new_rz), return bbox stats in building-local space."""
    lo = [float("inf")] * 3
    hi = [float("-inf")] * 3
    n_bbox = 0
    n_out_facade = 0
    n_rot_xy = 0
    for c in copies:
        if c.obnd is None:
            continue
        if c.bbox_skip:
            continue
        n_bbox += 1
        if any(abs(v) > 1e-9 for v in c.rot[:2]):
            n_rot_xy += 1
        new_pos = map_point(c.pos, ext_leaf, int_leaf, delta)
        new_rz = norm_angle(c.rot[2] + delta)
        corners = copy_corners_world(c, new_pos, (c.rot[0], c.rot[1], new_rz))
        local = [to_local(w) for w in corners]
        if any(p[0] < VIS_FACADE_X for p in local):
            n_out_facade += 1
        for p in local:
            for i in range(3):
                lo[i] = min(lo[i], p[i])
                hi[i] = max(hi[i], p[i])
    return {"delta": delta, "lo": tuple(lo), "hi": tuple(hi), "n_bbox": n_bbox,
            "n_past_facade": n_out_facade, "n_rot_xy": n_rot_xy}


def fmt_box(st):
    lo, hi = st["lo"], st["hi"]
    return ("x [%.1f, %.1f]  y [%.1f, %.1f]  z [%.1f, %.1f]" % (
        lo[0], hi[0], lo[1], hi[1], lo[2], hi[2]))


# ----------------------------------------------------------------------------------------
# ESP records
# ----------------------------------------------------------------------------------------

def inside_cell(pos):
    return CELL_X[0] <= pos[0] < CELL_X[1] and CELL_Y[0] <= pos[1] < CELL_Y[1]


# Talking activators the interior hides up near the ceiling. Outside they would float
# above the roof with a visible terminal mesh, so they go under the terrain instead
# (the terrain is flat at ~27 units below the store floor; sound still plays).
HIDDEN_SOURCES = {"VGSStoreRadio"}
HIDDEN_DEPTH = 200.0


def hide_above_roof_sources(copies, floor_z):
    for c in copies:
        if c.base is not None and c.base.edid() in HIDDEN_SOURCES:
            c.new_pos = (c.new_pos[0], c.new_pos[1], floor_z - HIDDEN_DEPTH)
            print("hid %s %08X under the terrain at z %.1f" % (c.base.edid(), c.src.form_id, c.new_pos[2]))


# Room-kit pieces carry the store's floor and wall collision. They are made persistent
# (world persistent cell) so they are loaded together with Chet, who is persistent too:
# as temporary refs they only appear once cell (-17,0) attaches, and Chet, placed
# earlier, would fall to the terrain and be pushed to the nearest navmesh outside.
STRUCTURAL_PREFIXES = ("NVGSGSRm", "ShackCornerIn", "ShackExit")


def is_structural(c):
    edid = c.base.edid() if c.base is not None else ""
    return bool(c.name_override) or edid.startswith(STRUCTURAL_PREFIXES)


def make_refr_copy(c, new_fid):
    flags = c.src.flags | FLAG_PERSISTENT if is_structural(c) else c.src.flags & ~FLAG_PERSISTENT
    has_owner = c.src.get("XOWN") is not None
    subs = []
    for s in c.src.subrecords():
        if s.type in COPY_DROP:
            continue
        if s.type == "DATA":
            subs.append(("DATA", struct.pack("<3f", *c.new_pos)
                         + struct.pack("<3f", c.rot[0], c.rot[1], c.new_rz)))
        elif s.type == "NAME" and c.name_override:
            subs.append(("NAME", struct.pack("<I", c.name_override)))
        else:
            subs.append((s.type, s.data))
    if not has_owner:
        # REFR subrecord order puts XOWN after the name/teleport/map-marker/radio block and
        # before everything else (XLOC, XRDS, XEMI, XIBS, XSCL, DATA...).
        at = max([i for i, (t, _d) in enumerate(subs) if t in PRE_XOWN] or [0]) + 1
        subs.insert(at, ("XOWN", struct.pack("<I", STORE_OWNER)))
    return be.record_bytes("REFR", new_fid, flags, subs)


# In the original interior the load door sat flush against a solid wall made of two
# room-kit corner pieces. The copies of those two pieces at the door use versions of
# their meshes with the doorway cut out (visual + collision, see cut_wall_nif.py).
DOOR_WALL_MODELS = {
    "NVGSGSRmCorIn01": ("SLGSRmCorIn01Door", b"SeamlessInteriors\\nvgs_gs_rmcorin01_door.nif\x00"),
    "NVGSGSRmCorIn02": ("SLGSRmCorIn02Door", b"SeamlessInteriors\\nvgs_gs_rmcorin02_door.nif\x00"),
}
DOOR_WALL_RADIUS = 300.0  # only the pieces whose origin is this close to the door leaf


def door_wall_copies(copies, ext_leaf):
    """[(copy, base edid)] for the wall pieces that block the doorway."""
    out = []
    for c in copies:
        edid = c.base.edid() if c.base is not None else None
        if edid in DOOR_WALL_MODELS and math.hypot(c.new_pos[0] - ext_leaf[0],
                                                   c.new_pos[1] - ext_leaf[1]) < DOOR_WALL_RADIUS:
            out.append((c, edid))
    return out


# Chet, the store's merchant. His placed ref moves into the world persistent cell
# (same formID, so quests and dialogue keep pointing at him) and his two packages,
# which were "in cell GSGeneralStore", become "near editor location".
CHET_REF = 0x00104C79
CHET_PACKAGES = {
    0x00168FAC: 512,   # GSChetStoreSandbox: the store floor around his spot
    0x00168FAD: 1024,  # GSChetSleep22x8: wide enough to reach the bed in the back
}
PLDT_NEAR_EDITOR_LOCATION = 3
# PKDT general flags 0x08..0x100: lock/unlock doors at package start/end/location. Chet's sleep
# package has 0x88 (lock at start, unlock at end); the engine then looks for the location's load
# doors, and with the store door no longer a load door it crashed (0xC0000005 at FalloutNV.exe
# +0x6A55A6) as soon as the package started at 22:00. Without them the door stays unlocked.
PKDT_DOOR_LOCK_FLAGS = 0x08 | 0x10 | 0x20 | 0x40 | 0x80 | 0x100


def make_chet_override(plugin, ext_leaf, int_leaf, delta):
    src = plugin.get_record(CHET_REF)
    assert src is not None and src.sig == "ACHR" and src.flags & FLAG_PERSISTENT
    data = src.get("DATA")
    pos = struct.unpack_from("<3f", data, 0)
    rot = struct.unpack_from("<3f", data, 12)
    new_pos = map_point(pos, ext_leaf, int_leaf, delta)
    new_rz = norm_angle(rot[2] + delta)
    subs = []
    for s in src.subrecords():
        if s.type == "DATA":
            subs.append(("DATA", struct.pack("<6f", *new_pos, rot[0], rot[1], new_rz)))
        else:
            subs.append((s.type, s.data))
    print("Chet %08X -> pos %s rot z %.4f" % (CHET_REF, fmt3(new_pos), new_rz))
    return be.override_record_bytes(src, subs, src.flags)


def make_package_override(plugin, fid, radius):
    src = plugin.get_record(fid)
    assert src is not None and src.sig == "PACK"
    subs = []
    for s in src.subrecords():
        if s.type == "PLDT":
            subs.append(("PLDT", struct.pack("<iIi", PLDT_NEAR_EDITOR_LOCATION, 0, radius) + s.data[12:]))
        elif s.type == "PKDT":
            flags = struct.unpack_from("<I", s.data)[0] & ~PKDT_DOOR_LOCK_FLAGS
            subs.append(("PKDT", struct.pack("<I", flags) + s.data[4:]))
        else:
            subs.append((s.type, s.data))
    return be.override_record_bytes(src, subs, src.flags)


# The store's navmesh, so Chet (and any actor) can stand and walk inside. Without it the
# engine pushes actors to the nearest navmesh, which is outside the building.
# The store straddles the border between cells (-17,0) and (-17,1) (y = 4096), and the
# engine looks for an actor's navmesh in the cell the actor stands in, so the copy is
# split in two navmeshes, one per cell, joined by external connections (NVEX). Together
# they form an island: they are not linked to the exterior navmesh.
INT_NAVMESH = 0x00107719
NAVTRI = struct.Struct("<3H3hHH")   # verts, edges (-1 = none), flags, cover flags
NAVTRI_DOOR = 0x0400                # triangle linked to a door portal (NVDP)
NAVTRI_EXTERNAL_EDGE = (0x1, 0x2, 0x4)  # edge k leads to another navmesh: edge = NVEX index
NAV_CELLS = [  # (cell formID, grid x, grid y, y range)
    (0x000DAEB9, -17, 0, (0.0, 4096.0)),
    (0x000DAEB8, -17, 1, (4096.0, 8192.0)),
]


def navmesh_grid(verts, tris, div):
    """NVGD: divisor, cell size x/y, bounds min/max, then per cell (x fastest) a u16 count and the
    triangles whose xy bounding box touches the cell (a superset of the GECK's exact test)."""
    lo = [min(v[i] for v in verts) for i in range(3)]
    hi = [max(v[i] for v in verts) for i in range(3)]
    dx = (hi[0] - lo[0]) / div
    dy = (hi[1] - lo[1]) / div
    cells = [[] for _ in range(div * div)]
    for ti, t in enumerate(tris):
        P = [verts[i] for i in t[:3]]
        x0, x1 = min(q[0] for q in P), max(q[0] for q in P)
        y0, y1 = min(q[1] for q in P), max(q[1] for q in P)
        for cy in range(div):
            for cx in range(div):
                bx, by = lo[0] + cx * dx, lo[1] + cy * dy
                if x1 >= bx and x0 <= bx + dx and y1 >= by and y0 <= by + dy:
                    cells[cy * div + cx].append(ti)
    out = struct.pack("<Iff3f3f", div, dx, dy, *lo, *hi)
    for c in cells:
        out += struct.pack("<H%dH" % len(c), len(c), *c)
    return out


def make_navmeshes(plugin, fids, ext_leaf, int_leaf, delta):
    """Split the transformed store navmesh by cell. fids: one new formID per NAV_CELLS entry.
    Returns [{"cell", "grid", "fid", "verts", "rec"}] for the non-empty pieces."""
    src = plugin.get_record(INT_NAVMESH)
    vx, tr = src.get("NVVX"), src.get("NVTR")
    verts = [map_point(struct.unpack_from("<3f", vx, i * 12), ext_leaf, int_leaf, delta)
             for i in range(len(vx) // 12)]
    tris = [list(NAVTRI.unpack_from(tr, i * NAVTRI.size)) for i in range(len(tr) // NAVTRI.size)]
    _cell, nverts, ntris, next_conn, ncover, _ndoor = struct.unpack("<6I", src.get("DATA"))
    assert (nverts, ntris, next_conn, ncover) == (len(verts), len(tris), 0, 0)
    div = struct.unpack_from("<I", src.get("NVGD"))[0]

    def piece_of(t):
        cy = sum(verts[i][1] for i in t[:3]) / 3.0
        return next(k for k, c in enumerate(NAV_CELLS) if c[3][0] <= cy < c[3][1])

    owner = [piece_of(t) for t in tris]
    local_tri = {}
    members = [[] for _ in NAV_CELLS]
    for ti, k in enumerate(owner):
        local_tri[ti] = len(members[k])
        members[k].append(ti)

    pieces = []
    for k, (cell, gx, gy, _yr) in enumerate(NAV_CELLS):
        if not members[k]:
            continue
        vmap = {}
        pverts = []
        ptris = []
        nvex = []
        for ti in members[k]:
            t = list(tris[ti])
            t[6] &= ~NAVTRI_DOOR  # the door portal is not copied
            for j in range(3):
                if t[j] not in vmap:
                    vmap[t[j]] = len(pverts)
                    pverts.append(verts[t[j]])
                t[j] = vmap[t[j]]
            for e in range(3):
                n = t[3 + e]
                if n < 0:
                    continue
                if owner[n] == k:
                    t[3 + e] = local_tri[n]
                else:
                    t[6] |= NAVTRI_EXTERNAL_EDGE[e]
                    t[3 + e] = len(nvex)
                    nvex.append(struct.pack("<IIH", 0, fids[owner[n]], local_tri[n]))
            ptris.append(t)
        subs = [
            ("NVER", src.get("NVER")),
            ("DATA", struct.pack("<6I", cell, len(pverts), len(ptris), len(nvex), 0, 0)),
            ("NVVX", b"".join(struct.pack("<3f", *v) for v in pverts)),
            ("NVTR", b"".join(NAVTRI.pack(*t) for t in ptris)),
            ("NVGD", navmesh_grid(pverts, ptris, div)),
        ]
        if nvex:
            subs.append(("NVEX", b"".join(nvex)))
        print("navmesh %08X cell (%d,%d): %d verts, %d tris, %d external links" % (
            fids[k], gx, gy, len(pverts), len(ptris), len(nvex)))
        pieces.append({"cell": cell, "grid": (gx, gy), "fid": fids[k], "verts": pverts,
                       "rec": be.record_bytes("NAVM", fids[k], src.flags, subs,
                                              version=src.form_version, unknown=src.unknown)})
    return pieces


# The engine merges NAVI entries from every plugin (the DLCs override NAVI with only their
# own entries), so this plugin adds just its navmeshes: NVMI places each one in its
# WastelandNV cell; NVCI lists the other piece as the only adjacent navmesh.
NAVI_FORM = 0x00014B92


def make_navi_override(plugin, pieces):
    src = plugin.get_record(NAVI_FORM)
    subs = [("NVER", src.get("NVER"))]
    for p in pieces:
        centre = [sum(v[i] for v in p["verts"]) / len(p["verts"]) for i in range(3)]
        gx, gy = p["grid"]
        subs.append(("NVMI", struct.pack("<III", 0, p["fid"], WORLD) + struct.pack("<hh", gy, gx)
                     + struct.pack("<3f", *centre) + struct.pack("<I", 0)))
    for p in pieces:
        others = [q["fid"] for q in pieces if q is not p]
        subs.append(("NVCI", struct.pack("<II%dI" % len(others), p["fid"], len(others), *others)
                     + struct.pack("<II", 0, 0)))
    return be.override_record_bytes(src, subs, src.flags & ~0x00040000)


def make_stat_copy(src, new_fid, edid, modl=None):
    subs = []
    for s in src.subrecords():
        if s.type == "EDID":
            subs.append(("EDID", be.edid_bytes(edid)))
        elif s.type == "MODL":
            subs.append(("MODL", modl or OPEN_MODL))
        elif s.type == "MODT":
            continue  # texture hash list is dropped (none present on this STAT)
        else:
            subs.append((s.type, s.data))
    return be.record_bytes("STAT", new_fid, src.flags, subs, version=src.form_version,
                           unknown=src.unknown)


def make_building_override(src, stat_fid):
    subs = []
    for s in src.subrecords():
        if s.type == "NAME":
            subs.append(("NAME", struct.pack("<I", stat_fid)))
        else:
            subs.append((s.type, s.data))
    return be.override_record_bytes(src, subs, src.flags)


def make_door_override(src):
    subs = [(s.type, s.data) for s in src.subrecords() if s.type not in DROP]
    return be.override_record_bytes(src, subs, src.flags)


# ----------------------------------------------------------------------------------------
# Fit report and collision
# ----------------------------------------------------------------------------------------

def tri_info(pts):
    a, b, c = pts
    ux, uy, uz = b[0] - a[0], b[1] - a[1], b[2] - a[2]
    vx, vy, vz = c[0] - a[0], c[1] - a[1], c[2] - a[2]
    nx, ny, nz = uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx
    nl = math.sqrt(nx * nx + ny * ny + nz * nz) or 1.0
    return abs(nz) / nl  # 1.0 = horizontal face


def collision_report(nif_info, door_local):
    """Collision triangles of the building (game units) checked against the walk-in requirements."""
    lo, hi = nif_info["vis_min"], nif_info["vis_max"]
    verts = nif_info["coll_verts"]
    inner_lo = tuple(v + INNER_MARGIN for v in lo)
    inner_hi = tuple(v - INNER_MARGIN for v in hi)
    tris = [[verts[i] for i in tri] for tri in nif_info["coll_tris"]]
    inside = []
    horiz = 0
    for pts in tris:
        if not all(inner_lo[k] < p[k] < inner_hi[k] for p in pts for k in range(3)):
            continue
        kind = "horizontal" if tri_info(pts) > 0.7 else "vertical"
        horiz += kind == "horizontal"
        inside.append((kind, pts))
    # door opening: the door leaf volume (door-local bounds) must not be blocked by collision
    dx0, dx1 = door_local[0] - 11.0 + 2.0, door_local[0] + 5.0 - 2.0
    dy0, dy1 = door_local[1] + 2.0, door_local[1] + 128.9 - 2.0
    dz0, dz1 = door_local[2] + 2.0, door_local[2] + 176.4 - 2.0
    door_hits = []
    for pts in tris:
        bx0, bx1 = min(p[0] for p in pts), max(p[0] for p in pts)
        by0, by1 = min(p[1] for p in pts), max(p[1] for p in pts)
        bz0, bz1 = min(p[2] for p in pts), max(p[2] for p in pts)
        if bx1 > dx0 and bx0 < dx1 and by1 > dy0 and by0 < dy1 and bz1 > dz0 and bz0 < dz1:
            door_hits.append(pts)
    # floor under the store (interior floor is at building-local z = door_local z)
    floor_z = door_local[2]
    floor_tris = []
    for pts in tris:
        cx = sum(p[0] for p in pts) / 3.0
        cz = sum(p[2] for p in pts) / 3.0
        if cx > door_local[0] and tri_info(pts) > 0.7 and abs(cz - floor_z) < 40.0:
            floor_tris.append(pts)
    # walking band: triangles inside the store footprint that block a body between floor and head height
    band = [pts for kind, pts in inside
            if min(p[2] for p in pts) < floor_z + 130.0 and max(p[2] for p in pts) > floor_z + 5.0]
    return {"inner_lo": inner_lo, "inner_hi": inner_hi, "inside": inside,
            "horizontal": horiz, "vertical": len(inside) - horiz, "n_tris": len(tris),
            "door_hits": door_hits, "floor_tris": floor_tris, "band": band,
            "band_tris": band,
            "door_box": ((dx0, dx1), (dy0, dy1), (dz0, dz1))}


def plane_groups(tris):
    """Group triangles by the plane they lie on (x=, y=, z= planes or slanted)."""
    c = collections.Counter()
    for pts in tris:
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        zs = [p[2] for p in pts]
        if max(xs) - min(xs) < 1.0:
            c["x = %.0f" % xs[0]] += 1
        elif max(ys) - min(ys) < 1.0:
            c["y = %.0f" % ys[0]] += 1
        elif max(zs) - min(zs) < 1.0:
            c["z = %.0f" % zs[0]] += 1
        else:
            c["slanted"] += 1
    return c


def fmt3(p):
    return "(%.1f, %.1f, %.1f)" % tuple(p)


def write_fit_report(lines):
    os.makedirs(OUT_DIR, exist_ok=True)
    with open(FIT_REPORT, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(lines) + "\n")


# ----------------------------------------------------------------------------------------
# Main build
# ----------------------------------------------------------------------------------------

def analyse(plugin, recs, nif_info):
    ext = recs["ext_door"]
    intd = recs["int_door"]
    ext_pos, ext_rot = ref_transform(ext)
    int_pos, int_rot = ref_transform(intd)
    ext_rz = ext_rot[2]
    int_rz = int_rot[2]
    bld_pos, bld_rot = ref_transform(recs["bld_ref"])
    bld_rz = bld_rot[2]
    to_local = building_frame(bld_pos, bld_rz)

    ext_leaf = leaf_center(ext_pos, ext_rz)
    int_leaf = leaf_center(int_pos, int_rz)
    d1 = ext_rz - int_rz
    d2 = d1 + math.pi
    door_local = to_local(ext_pos)
    ext_leaf_local = to_local(ext_leaf)

    raw_copies, skipped = find_interior_copies(plugin)
    copies = make_copies(plugin, raw_copies)
    cand = {}
    for name, d in (("cand1 (delta = ext_rz - int_rz)", d1), ("cand2 (delta = cand1 + pi)", d2)):
        cand[name] = evaluate_candidate(copies, ext_leaf, int_leaf, d, to_local)
    return {"ext_pos": ext_pos, "ext_rz": ext_rz, "int_pos": int_pos, "int_rz": int_rz,
            "bld_pos": bld_pos, "bld_rz": bld_rz, "to_local": to_local,
            "ext_leaf": ext_leaf, "int_leaf": int_leaf, "d1": d1, "d2": d2,
            "door_local": door_local, "ext_leaf_local": ext_leaf_local,
            "copies": copies, "skipped": skipped, "cand": cand}


def choose_delta(an):
    """Pick the candidate whose interior sits behind the facade with the fewest breaches."""
    best = None
    for name, st in an["cand"].items():
        if best is None or st["n_past_facade"] < best[1]["n_past_facade"]:
            best = (name, st)
    name, st = best
    # the chosen candidate must put (almost) all of the interior behind the facade
    if st["n_past_facade"] > 0.1 * max(1, st["n_bbox"]):
        raise SystemExit("no candidate places the interior behind the facade: %s" % fmt_box(st))
    return name, st["delta"]


def write_report_and_check(an, nif_info, chosen_name, delta):
    ext_leaf, int_leaf = an["ext_leaf"], an["int_leaf"]
    to_local = an["to_local"]
    copies = an["copies"]
    lines = []
    L = lines.append
    L("FIT REPORT: open Goodsprings General Store (interior inside the real building)")
    L("=" * 100)
    L("master: %s" % MASTER_PATH)
    L("building REFR %08X NVGeneralStore (STAT %08X) pos %s rot z %.6f (temporary child of cell %08X)" % (
        BUILDING_REF, BUILDING_STAT, fmt3(an["bld_pos"]), an["bld_rz"], EXT_CELL))
    L("exterior door REFR %08X pos %s rot z %.6f ; door origin in building-local %s" % (
        EXT_DOOR, fmt3(an["ext_pos"]), an["ext_rz"], fmt3(an["door_local"])))
    L("interior door REFR %08X pos %s rot z %.6f ; leaf centres: ext %s  int %s" % (
        GS_INT_DOOR, fmt3(an["int_pos"]), an["int_rz"], fmt3(ext_leaf), fmt3(int_leaf)))
    L("")
    L("ALIGNMENT CANDIDATES (bboxes in building-local coordinates, copies with OBND, FX/marker/light skipped)")
    for name, st in an["cand"].items():
        L("  %s: delta = %.6f rad (%.2f deg)" % (name, st["delta"], math.degrees(st["delta"])))
        L("      bbox %s" % fmt_box(st))
        L("      copies with any box corner in front of facade (x < %.2f): %d of %d" % (
            VIS_FACADE_X, st["n_past_facade"], st["n_bbox"]))
    L("  CHOSEN: %s  delta = %.6f rad (%.2f deg)" % (chosen_name, delta, math.degrees(delta)))
    L("  rule: interior must lie behind the facade (building-local x > %.2f); the candidate with"
      " fewest breaches wins" % VIS_FACADE_X)
    L("")
    chosen = an["cand"][chosen_name]
    L("CHOSEN TRANSFORM: world = ext_leaf + Rz(-delta) (p - int_leaf); rot z += delta (normalised [0, 2pi))")
    L("  copies with non-zero rx/ry: %d (rz is additive, so their orientation is approximate)" % (
        chosen["n_rot_xy"]))
    L("")
    # ---- visual bounds and per-ref overshoot
    vlo, vhi = nif_info["vis_min"], nif_info["vis_max"]
    L("BUILDING VISUAL BOUNDS (root space, from NIF geometry): x [%.2f, %.2f] y [%.2f, %.2f] z [%.2f, %.2f]" % (
        vlo[0], vhi[0], vlo[1], vhi[1], vlo[2], vhi[2]))
    L("")
    L("FIT: copied refs whose OBND box extends outside the visual bounds by more than %.1f units" % OVERSHOOT_TOL)
    out_rows = []
    for c in copies:
        if c.obnd is None:
            continue
        new_pos = map_point(c.pos, ext_leaf, int_leaf, delta)
        new_rz = norm_angle(c.rot[2] + delta)
        c.new_pos, c.new_rz = new_pos, new_rz
        local = [to_local(w) for w in copy_corners_world(c, new_pos, (c.rot[0], c.rot[1], new_rz))]
        bmin = [min(p[i] for p in local) for i in range(3)]
        bmax = [max(p[i] for p in local) for i in range(3)]
        over = []
        for i, ax in enumerate("xyz"):
            lo_out = vlo[i] - bmin[i]
            hi_out = bmax[i] - vhi[i]
            worst = max(lo_out, hi_out)
            if worst > OVERSHOOT_TOL:
                side = "min" if lo_out >= hi_out else "max"
                over.append("%s %s by %.1f" % (ax, side, worst))
        if over:
            out_rows.append((c.src.form_id, c.edid, c.bbox_skip or "", over, bmin, bmax))
    if not out_rows:
        L("  none")
    for fid, edid, skip, over, bmin, bmax in out_rows:
        L("  %08X %-32s %s" % (fid, edid, ", ".join(over)))
        L("      box min %s max %s%s" % (fmt3(bmin), fmt3(bmax), ("  [" + skip + "]") if skip else ""))
    L("  total poking out: %d of %d copies with OBND" % (len(out_rows), sum(1 for c in copies if c.obnd)))
    L("")
    # ---- floor
    L("FLOOR HEIGHT")
    door_floor = an["door_local"][2]
    L("  interior door origin z = %.2f (door mesh bottom z 1 .. top 176.4, so origin = sill/floor)" % an["int_pos"][2])
    L("  maps to building-local z = %.2f (porch slab is local z 11..20)" % door_floor)
    floor_rows = []
    for c in copies:
        if c.obnd is None:
            continue
        if any(k in c.edid for k in ("Floor", "Rug", "Mat", "Carpet", "Tile")):
            local = [to_local(w) for w in copy_corners_world(c, c.new_pos, (c.rot[0], c.rot[1], c.new_rz))]
            floor_rows.append((c.src.form_id, c.edid, min(p[2] for p in local)))
    for fid, edid, zb in floor_rows[:20]:
        L("  floor-like ref %08X %-30s bottom z (building-local) = %.2f" % (fid, edid, zb))
    if floor_rows:
        vals = sorted(z for _, _, z in floor_rows)
        L("  floor-like bottoms: min %.2f median %.2f max %.2f (n=%d)" % (
            vals[0], vals[len(vals) // 2], vals[-1], len(vals)))
    L("")
    # ---- collision
    cr = collision_report(nif_info, an["door_local"])
    L("BUILDING COLLISION (hkPackedNiTriStripsData x7 game units): %d triangles, %d vertices" % (
        cr["n_tris"], nif_info["coll_raw_count"]))
    L("  inside test: all vertices strictly within visual bounds shrunk by %.0f units on every face" % INNER_MARGIN)
    L("  triangles strictly inside: %d (horizontal, floor/ceiling-like: %d ; vertical, walls/partitions: %d)" % (
        len(cr["inside"]), cr["horizontal"], cr["vertical"]))
    L("  inside triangles overlapping the walking band (floor+5 .. floor+130, floor z %.2f): %d" % (
        an["door_local"][2], len(cr["band"])))
    L("  triangles inside the door opening box (door leaf volume, 2-unit margin): %d (0 = opening is clear)" % (
        len(cr["door_hits"])))
    for pts in cr["door_hits"][:20]:
        L("    door-box hit (horizontal at z %.1f = threshold step, not a wall) %s %s %s" % (
            pts[0][2], fmt3(pts[0]), fmt3(pts[1]), fmt3(pts[2])))
    L("  walking-band triangles grouped by plane (building walls, facade, door jamb):")
    for key, n in sorted(plane_groups(cr["band_tris"]).items()):
        L("    %-12s %d" % (key, n))
    L("  collision triangles that are horizontal, centroid in front of the door plane, z within 40 of floor z: %d" % (
        len(cr["floor_tris"])))
    for pts in cr["floor_tris"][:20]:
        L("    floor-level tri %s %s %s" % (fmt3(pts[0]), fmt3(pts[1]), fmt3(pts[2])))
    for kind, pts in cr["inside"]:
        L("    %-10s %s %s %s" % (kind, fmt3(pts[0]), fmt3(pts[1]), fmt3(pts[2])))
    L("")
    # ---- skipped refs
    spill = [c for c in copies if c.new_pos is not None and not inside_cell(c.new_pos)]
    L("CELL MEMBERSHIP: copies outside exterior cell (-17,0) [x -69632..-65536, y 0..4096]: %d of %d" % (
        len(spill), len(copies)))
    if spill:
        L("  they are kept in the temporary group of cell 000DAEB9 as specified; positions spill north into")
        L("  the cell (-17,1) area, y %.1f .. %.1f; x %.1f .. %.1f" % (
            min(c.new_pos[1] for c in spill), max(c.new_pos[1] for c in spill),
            min(c.new_pos[0] for c in spill), max(c.new_pos[0] for c in spill)))
    L("")
    L("SKIPPED INTERIOR REFS (not copied)")
    reason_text = {
        "interior door: replaced by persistent pocket door copy":
            "not copied: the real exterior door %08X is used (no XTEL, no pocket)" % EXT_DOOR,
        "navmesh, pocket gets no navmesh": "not copied: navmesh (per spec)",
        "actor placement (Chet), not copied": "not copied: actor placement (per spec)",
        "interior-only compass marker": "not copied: NorthMarker (per spec)",
    }
    for fid, sig, base, reason in an["skipped"]:
        L("  %08X %-4s %-40s %s" % (fid, sig, base, reason_text.get(reason, reason)))
    write_fit_report(lines)
    return cr, out_rows


def build(args):
    out_path = os.path.abspath(args.out)
    game_data = os.path.normcase(os.path.dirname(MASTER_PATH)) + os.sep
    if os.path.normcase(out_path).startswith(game_data):
        raise SystemExit("refusing to write into the game folder: %s" % out_path)

    plugin, recs, top_order = load_master()
    nif_info = read_building_nif()
    print("building visual bounds: x [%.2f, %.2f] y [%.2f, %.2f] z [%.2f, %.2f]" % (
        nif_info["vis_min"][0], nif_info["vis_max"][0], nif_info["vis_min"][1],
        nif_info["vis_max"][1], nif_info["vis_min"][2], nif_info["vis_max"][2]))

    an = analyse(plugin, recs, nif_info)
    print("door origin (ext) in building-local: %s ; ext leaf in building-local: %s" % (
        fmt3(an["door_local"]), fmt3(an["ext_leaf_local"])))
    for name, st in an["cand"].items():
        print("%s delta=%.4f deg=%.2f" % (name, st["delta"], math.degrees(st["delta"])))
        print("    bbox %s  past facade %d / %d" % (fmt_box(st), st["n_past_facade"], st["n_bbox"]))
    chosen_name, delta = choose_delta(an)
    print("chosen: %s" % chosen_name)

    cr, out_rows = write_report_and_check(an, nif_info, chosen_name, delta)
    ext_leaf, int_leaf = an["ext_leaf"], an["int_leaf"]
    copies = an["copies"]
    print("fit report: %s (%d copies poke out >%.0f units; %d inner collision tris: %d horizontal, %d vertical; band %d; door-box hits %d; floor-level tris %d)" % (
        FIT_REPORT, len(out_rows), OVERSHOOT_TOL, len(cr["inside"]), cr["horizontal"], cr["vertical"],
        len(cr["band"]), len(cr["door_hits"]), len(cr["floor_tris"])))
    if args.report_only:
        plugin.close()
        return

    # --- copy placement + asserts ---------------------------------------------------
    for c in copies:
        c.new_pos = map_point(c.pos, ext_leaf, int_leaf, delta)
        c.new_rz = norm_angle(c.rot[2] + delta)
    spill = [c for c in copies if not inside_cell(c.new_pos)]
    if spill and args.strict_cell:
        raise SystemExit("%d copies land outside cell (-17,0); first %08X %s" % (
            len(spill), spill[0].src.form_id, fmt3(spill[0].new_pos)))
    print("copied refs: %d ; inside cell (-17,0): %d ; outside (kept in DAEB9 temp group): %d" % (
        len(copies), len(copies) - len(spill), len(spill)))
    if spill:
        ys = [c.new_pos[1] for c in spill]
        print("  WARNING: spill into the cell north of (-17,0): y %.1f..%.1f (cell y limit 4096)" % (
            min(ys), max(ys)))

    # --- formIDs --------------------------------------------------------------------
    next_id = NEW_FORM_BASE
    stat_fid = next_id
    next_id += 1
    copy_fids = []
    for _ in copies:
        copy_fids.append(next_id)
        next_id += 1

    # --- records --------------------------------------------------------------------
    stat_rec = make_stat_copy(recs["bld_stat"], stat_fid, "SLGSGeneralStoreOpen")
    bld_override = make_building_override(recs["bld_ref"], stat_fid)
    door_override = make_door_override(recs["ext_door"])
    hide_above_roof_sources(copies, an["ext_pos"][2])
    # door wall STATs are allocated after the copies so the copies keep their formIDs
    wall_stat_recs = []
    wall_stat_of = {}
    for edid in sorted(DOOR_WALL_MODELS):
        new_edid, modl = DOOR_WALL_MODELS[edid]
        wall_stat_of[edid] = next_id
        wall_stat_recs.append(make_stat_copy(plugin.find_by_edid(edid, "STAT"), next_id, new_edid, modl))
        next_id += 1
    navmesh_fids = list(range(next_id, next_id + len(NAV_CELLS)))
    next_id += len(NAV_CELLS)
    navmeshes = make_navmeshes(plugin, navmesh_fids, ext_leaf, int_leaf, delta)
    navmesh_in = {p["cell"]: p["rec"] for p in navmeshes}
    door_walls = door_wall_copies(copies, an["ext_leaf"])
    assert len(door_walls) == 2, door_walls
    for c, edid in door_walls:
        c.name_override = wall_stat_of[edid]
        print("door wall %s %08X -> %08X" % (edid, c.src.form_id, wall_stat_of[edid]))
    copy_recs = [make_refr_copy(c, fid) for c, fid in zip(copies, copy_fids)]
    persistent_recs = [r for c, r in zip(copies, copy_recs) if is_structural(c)]
    # Temporary refs go to the cell they actually stand in (the store straddles y = 4096):
    # the engine resolves things like a furniture target's navmesh through its parent cell.
    temporary_recs = [r for c, r in zip(copies, copy_recs)
                      if not is_structural(c) and c.new_pos[1] < CELL_Y[1]]
    north_recs = [r for c, r in zip(copies, copy_recs)
                  if not is_structural(c) and c.new_pos[1] >= CELL_Y[1]]
    print("temporary refs: %d in (-17,0), %d in (-17,1)" % (len(temporary_recs), len(north_recs)))
    print("structural pieces made persistent: %d (%s)" % (len(persistent_recs), ", ".join(
        sorted({c.edid for c in copies if is_structural(c)}))))

    stat_top = be.group_bytes(b"STAT", 0, stat_rec + b"".join(wall_stat_recs))

    wrld = recs["world"]
    wrld_subs = [(s.type, s.data) for s in wrld.subrecords() if s.type != "OFST"]
    wrld_override = be.override_record_bytes(wrld, wrld_subs, wrld.flags)

    ext_cell = recs["ext_cell"]
    block_label = ext_cell.path[2].label
    sub_label = ext_cell.path[3].label

    chet_override = make_chet_override(plugin, ext_leaf, int_leaf, delta)
    pack_top = be.group_bytes(b"PACK", 0, b"".join(
        make_package_override(plugin, fid, radius) for fid, radius in sorted(CHET_PACKAGES.items())))
    world_persist = be.group_bytes(WORLD_PERSIST, 8, door_override + chet_override
                                   + b"".join(persistent_recs))
    world_cell_children = be.group_bytes(WORLD_PERSIST, 6, world_persist)
    ext_temp = be.group_bytes(EXT_CELL, 9, navmesh_in.get(EXT_CELL, b"") + bld_override
                              + b"".join(temporary_recs))
    ext_cell_children = be.group_bytes(EXT_CELL, 6, ext_temp)
    # cell (-17,1) only carries the back half of the store navmesh; it sits in the same
    # exterior block and sub-block as (-17,0) and comes first there, as in the master.
    north_cell = plugin.get_record(NORTH_CELL)
    assert [g.label for g in north_cell.path] == [g.label for g in ext_cell.path]
    north_children = be.group_bytes(NORTH_CELL, 6, be.group_bytes(
        NORTH_CELL, 9, navmesh_in[NORTH_CELL] + b"".join(north_recs)))
    sub_block = be.group_bytes(sub_label, 5, north_cell.raw() + north_children
                               + ext_cell.raw() + ext_cell_children)
    cell_block = be.group_bytes(block_label, 4, sub_block)
    world_children = be.group_bytes(WORLD, 1, recs["world_cell"].raw() + world_cell_children + cell_block)
    wrld_top = be.group_bytes(b"WRLD", 0, wrld_override + world_children)

    navi_top = be.group_bytes(b"NAVI", 0, make_navi_override(plugin, navmeshes))
    body = stat_top + navi_top + wrld_top + pack_top  # master order: STAT < NAVI < WRLD < PACK
    hedr_version = struct.unpack_from("<f", plugin.header.get("HEDR"), 0)[0]
    num_records = be.count_items(body)
    onam = struct.pack("<I", BUILDING_REF)  # temporary master ref overridden here
    tes4_subs = [
        ("HEDR", struct.pack("<fII", hedr_version, num_records, next_id)),
        ("CNAM", AUTHOR.encode("latin-1") + b"\x00"),
        ("MAST", be.MASTER_NAME_BYTES + b"\x00"),
        ("DATA", b"\x00" * 8),
        ("ONAM", onam),
    ]
    tes4 = be.record_bytes("TES4", 0, 0, tes4_subs, version=plugin.header.form_version,
                           unknown=plugin.header.unknown)
    data = tes4 + body

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    tmp = out_path + ".tmp"
    with open(tmp, "wb") as fh:
        fh.write(data)
    os.replace(tmp, out_path)
    plugin.close()

    print("wrote %s (%d bytes)" % (out_path, len(data)))
    print("STAT %08X SLGSGeneralStoreOpen ; copies %08X..%08X (%d) ; nextObjectID %08X" % (
        stat_fid, copy_fids[0], copy_fids[-1], len(copy_fids), next_id))
    print("overrides: %08X REFR (building, temp) ; %08X REFR (door, persistent) ; WRLD %08X ; CELL %08X (raw) ; CELL %08X (raw)" % (
        BUILDING_REF, EXT_DOOR, WORLD, WORLD_PERSIST, EXT_CELL))
    print("validate with: py -I %s" % VALIDATOR)


# ----------------------------------------------------------------------------------------
# Check: re-read the ESP
# ----------------------------------------------------------------------------------------

def describe_subs(rec):
    out = []
    for s in rec.subrecords():
        if s.type in ("DATA",) and len(s.data) == 24:
            px, py, pz = struct.unpack_from("<3f", s.data)
            rx, ry, rz = struct.unpack_from("<3f", s.data, 12)
            out.append("DATA pos=%s rot=(%.4f, %.4f, %.4f)" % (fmt3((px, py, pz)), rx, ry, rz))
        elif s.type == "NAME":
            out.append("NAME %08X" % struct.unpack("<I", s.data)[0])
        elif s.type in ("EDID", "MODL"):
            out.append("%s %r" % (s.type, s.data.rstrip(b"\x00")))
        else:
            out.append("%s (%d bytes)" % (s.type, len(s.data)))
    return out


def check(out_path):
    plugin = Plugin(out_path)
    plugin.build_index()
    print("re-read %s: %d records indexed" % (out_path, len(plugin._index)))
    onam = plugin.header.get("ONAM")
    fids = [struct.unpack_from("<I", onam, i)[0] for i in range(0, len(onam or b""), 4)]
    print("TES4 ONAM: %s" % ", ".join("%08X" % f for f in fids))

    stat = plugin.find_by_edid("SLGSGeneralStoreOpen", sig="STAT")
    print("STAT %08X EDID=%s" % (stat.form_id, stat.edid()))
    print("  MODL = %r" % stat.get("MODL"))
    print("  subrecords: %s" % [s.type for s in stat.subrecords()])

    bld = plugin.get_record(BUILDING_REF)
    print("building override %08X (%s) in %s" % (bld.form_id, bld.sig,
                                                  bld.path[-1].describe()))
    for line in describe_subs(bld):
        print("  " + line)

    door = plugin.get_record(EXT_DOOR)
    print("door override %08X (%s) flags=%x in %s" % (door.form_id, door.sig, door.flags,
                                                       door.path[-1].describe()))
    for line in describe_subs(door):
        print("  " + line)
    assert b"XTEL" not in [s.type for s in door.subrecords()]

    # transformed doorframe copy
    master = Plugin(MASTER_PATH)
    master.build_index()
    recs = {"ext_door": master.get_record(EXT_DOOR), "int_door": master.get_record(GS_INT_DOOR),
            "bld_ref": master.get_record(BUILDING_REF)}
    ext_pos, ext_rot = ref_transform(recs["ext_door"])
    ext_leaf = leaf_center(ext_pos, ext_rot[2])
    frame_base = master.find_by_edid(DOORFRAME_BASE_EDID, sig="STAT")
    intd_pos, intd_rot = ref_transform(recs["int_door"])
    int_leaf = leaf_center(intd_pos, intd_rot[2])
    delta = None
    for line in open(FIT_REPORT, encoding="utf-8"):
        if line.startswith("  CHOSEN:"):
            delta = float(line.rsplit("delta = ", 1)[1].split(" rad")[0])
    assert delta is not None, "no CHOSEN line in fit report"
    frame_copy = None
    ext_cell_fid = EXT_CELL
    for rec in plugin.records(sig="REFR"):
        if rec.path and rec.path[-1].type_name == "temporary_children":
            nm = rec.get("NAME")
            if nm and struct.unpack("<I", nm)[0] == frame_base.form_id and \
               rec.path[-1].label_text() == "0x%08X" % ext_cell_fid:
                frame_copy = rec
                break
    assert frame_copy is not None, "doorframe copy not found"
    pos, rot = ref_transform(frame_copy)
    dx, dy, dz = (pos[0] - ext_leaf[0], pos[1] - ext_leaf[1], pos[2] - ext_leaf[2])
    print("doorframe copy %08X (base %08X %s) pos=%s rot z=%.4f" % (
        frame_copy.form_id, frame_base.form_id, DOORFRAME_BASE_EDID, fmt3(pos), rot[2]))
    print("  distance from ext leaf centre %s: dxy=%.2f dz=%.2f  |d|=%.2f" % (
        fmt3(ext_leaf), math.hypot(dx, dy), dz, math.sqrt(dx * dx + dy * dy + dz * dz)))
    print("  (the NVCraftsDoorframe origin sits 105 units below the leaf centre in the interior,"
          " so ~105 is expected)")
    copies_n = sum(1 for rec in plugin.records(sig="REFR")
                   if rec.path and rec.path[-1].type_name == "temporary_children"
                   and rec.path[-1].label_text() == "0x%08X" % EXT_CELL
                   and rec.form_id >> 24 == 0x01)
    print("new temporary REFR copies in cell DAEB9: %d" % copies_n)
    master.close()
    plugin.close()


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", default=OUT_PATH)
    ap.add_argument("--report-only", action="store_true")
    ap.add_argument("--check-only", action="store_true")
    ap.add_argument("--validate", action="store_true", help="run validate_esp.py on the output")
    ap.add_argument("--strict-cell", action="store_true",
                    help="fail if any copy lies outside exterior cell (-17,0)")
    args = ap.parse_args()
    if args.check_only:
        check(os.path.abspath(args.out))
        return
    build(args)
    if args.validate and not args.report_only:
        r = subprocess.run([sys.executable, "-I", VALIDATOR, os.path.abspath(args.out)])
        sys.exit(r.returncode)


if __name__ == "__main__":
    main()

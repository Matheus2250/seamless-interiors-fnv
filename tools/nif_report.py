"""Build a text report for NIF files: block table, scene graph, geometry bounds, collision chain,
animation sequences, and a door-opening analysis in building-local coordinates.

Usage (read-only, writes only under tools\\out):
    py -I nif_report.py
"""

import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import niflib as N  # noqa: E402

OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out")
MESH_DIR = os.path.join(OUT_DIR, "meshes")
REPORT = os.path.join(OUT_DIR, "nif_report.txt")

BUILDING = r"architecture\goodsprings\nv_generalstore.nif"
DOOR = r"dungeons\nv_craftsmanhomesinterior\nvcraftsmanrmdooranimated.nif"
DOORFRAME = r"architecture\goodsprings\nv_gs_craftsdoorframe.nif"

# Door placement in world coordinates (from the request).
DOOR_WORLD = (-69255.27, 3594.0, 8379.29)
DOOR_ROT_Z = 4.723
BLDG_WORLD = (-69290.0, 3875.0, 8362.0)
BLDG_ROT_Z = math.radians(270.0)  # 4.712 rad
BLDG_SCALE = 1.0

BOX_DIMS = (60.0, 160.0, 230.0)  # x (thickness), y (width), z (height) around the door

lines = []


def out(s=""):
    lines.append(s)


def fmt(v, nd=2):
    if v is None:
        return "None"
    if isinstance(v, (tuple, list)):
        return "(" + ", ".join(fmt(x, nd) for x in v) + ")"
    return "%.*f" % (nd, v)


def load(rel):
    path = os.path.join(MESH_DIR, rel)
    with open(path, "rb") as f:
        return N.NifFile(f.read(), rel)


def av_xform(info):
    R = info["rotation"]
    return N.Xform(R, info["scale"], info["translation"])


# ---------------------------------------------------------------------------
# Triangle / AABB overlap (separating axis test, Akenine-Moller style)
def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def tri_intersects_box(tri, c, e):
    v = [_sub(p, c) for p in tri]
    # AABB face axes
    for ax in range(3):
        mn = min(p[ax] for p in v)
        mx = max(p[ax] for p in v)
        if mn > e[ax] or mx < -e[ax]:
            return False
    # triangle plane
    n = _cross(_sub(v[1], v[0]), _sub(v[2], v[0]))
    d = _dot(n, v[0])
    r = e[0] * abs(n[0]) + e[1] * abs(n[1]) + e[2] * abs(n[2])
    if abs(d) > r:
        return False
    # 9 edge x axis tests
    unit = [(1, 0, 0), (0, 1, 0), (0, 0, 1)]
    for i in range(3):
        a = v[i]
        b = v[(i + 1) % 3]
        edge = _sub(b, a)
        for u in unit:
            axis = _cross(edge, u)
            if axis == (0, 0, 0):
                continue
            p = [_dot(axis, q) for q in v]
            rad = e[0] * abs(axis[0]) + e[1] * abs(axis[1]) + e[2] * abs(axis[2])
            if min(p) > rad or max(p) < -rad:
                return False
    return True


def point_in_box(p, lo, hi):
    return all(lo[i] <= p[i] <= hi[i] for i in range(3))


# ---------------------------------------------------------------------------
def report_nif(nif, title):
    out("=" * 100)
    out("NIF: %s" % title)
    out("header: %s" % nif.header_string)
    out("version=0x%08X (%d) endian=%d user_version=%d bs_version=%d" %
        (nif.version, nif.version, nif.endian, nif.user_version, nif.bs_version))
    out("author=%r process=%r export=%r" % (nif.author, nif.process_script, nif.export_script))
    out("num_blocks=%d block_types=%d strings=%d groups=%d footer_roots=%s trailing_bytes=%d" %
        (nif.num_blocks, len(nif.block_types), len(nif.strings), len(nif.groups),
         nif.footer_roots, nif.trailing))
    out("string table: %s" % ", ".join(repr(s) for s in nif.strings))
    out("")
    out("-- all blocks (index, type, size) --")
    for b in nif.blocks:
        out("  %4d  %-30s %8d bytes" % (b.index, b.type, b.size))
    out("")
    out("-- block type counts --")
    for t, c in sorted(nif.type_counts().items(), key=lambda kv: -kv[1]):
        out("  %-30s %d" % (t, c))
    out("")


def scene_graph(nif, root_idx, indent="", world=None, results=None, depth=0):
    """Walks NiNode children, returns list of geometry records with root-space transforms."""
    if results is None:
        results = []
    if world is None:
        world = N.Xform()
    b = nif.block(root_idx)
    if b is None:
        return results
    t = b.type
    if t in N.NODE_TYPES:
        info = nif.parse_node(root_idx)
        local = av_xform(info)
        w = world.compose(local)
        out("%s[%d] %s %s  t=%s s=%.3f  world_t=%s  coll=%d" %
            (indent, root_idx, t, info["name"],
             fmt(info["translation"]), info["scale"], fmt(w.t), info["collision"]))
        for c in info["children"]:
            scene_graph(nif, c, indent + "  ", w, results, depth + 1)
    elif t in N.GEOM_TYPES:
        info = nif.parse_geometry(root_idx)
        local = av_xform(info)
        w = world.compose(local)
        data_idx = info["data"]
        dtype = nif.type_of(data_idx)
        if dtype == "NiTriStripsData":
            g = nif.parse_tri_strips_data(data_idx)
            tris = g["triangles"]
        elif dtype == "NiTriShapeData":
            g = nif.parse_tri_shape_data(data_idx)
            tris = g["triangles"]
        else:
            g = {"vertices": [], "triangles": []}
            tris = []
        verts = g["vertices"]
        lb = N.bounds(verts)
        rb = N.bounds([w.apply(p) for p in verts])
        out("%s[%d] %s %r data=[%d]%s verts=%d tris=%d" %
            (indent, root_idx, t, info["name"], data_idx, dtype, len(verts), len(tris)))
        out("%s    local bounds=%s..%s  scale=%.3f  root-space bounds=%s..%s" %
            (indent, fmt(lb[0]) if lb else None, fmt(lb[1]) if lb else None, info["scale"],
             fmt(rb[0]) if rb else None, fmt(rb[1]) if rb else None))
        results.append({"block": root_idx, "name": info["name"], "data": data_idx,
                        "xform": w, "verts_local": verts, "tris": tris,
                        "bounds_root": rb, "type": t})
    return results


def collision_chain(nif, shape_idx, indent="    ", depth=0, out_list=None, xform=None):
    """Prints shape tree; returns list of packed-triangle records for analysis."""
    if out_list is None:
        out_list = []
    t = nif.type_of(shape_idx)
    if t is None:
        out("%s[%s] (no block)" % (indent, shape_idx))
        return out_list
    if t == "bhkMoppBvTreeShape":
        child = nif.mopp_child(shape_idx)
        out("%s[%d] bhkMoppBvTreeShape -> child [%d]" % (indent, shape_idx, child))
        collision_chain(nif, child, indent + "  ", depth + 1, out_list)
    elif t == "bhkPackedNiTriStripsShape":
        data = nif.packed_shape_data_ref(shape_idx)
        scale = nif.packed_shape_scale(shape_idx)
        out("%s[%d] bhkPackedNiTriStripsShape scale=%s data=[%d]" %
            (indent, shape_idx, fmt(scale), data))
        collision_chain(nif, data, indent + "  ", depth + 1, out_list)
    elif t == "hkPackedNiTriStripsData":
        p = nif.packed_tri_data(shape_idx)
        vh = p["vertices"]
        vg = N.scale_pts(vh, N.HAVOK_SCALE)
        b_raw = N.bounds(vh)
        b_g = N.bounds(vg)
        out("%s[%d] hkPackedNiTriStripsData: triangles=%d vertices=%d" %
            (indent, shape_idx, p["num_triangles"], p["num_vertices"]))
        out("%s    vertex bounds (raw Havok) %s..%s" % (indent, fmt(b_raw[0]), fmt(b_raw[1])))
        out("%s    vertex bounds (x7 game units) %s..%s" % (indent, fmt(b_g[0]), fmt(b_g[1])))
        out("%s    undecoded trailing bytes after vertices: %d" % (indent, p["trailing"]))
        out_list.append({"block": shape_idx, "tris": p["triangles"], "verts_game": vg})
    elif t == "bhkConvexVerticesShape":
        c = nif.convex_vertices(shape_idx)
        vh = c["vertices"]
        vg = N.scale_pts(vh, N.HAVOK_SCALE)
        b_raw = N.bounds(vh)
        b_g = N.bounds(vg)
        out("%s[%d] bhkConvexVerticesShape: vertices=%d planes=%d material=%d radius=%.3f" %
            (indent, shape_idx, len(vh), len(c["normals"]), c["material"], c["radius"]))
        out("%s    vertex bounds (raw Havok) %s..%s" % (indent, fmt(b_raw[0]), fmt(b_raw[1])))
        out("%s    vertex bounds (x7 game units) %s..%s" % (indent, fmt(b_g[0]), fmt(b_g[1])))
    elif t == "bhkListShape":
        out("%s[%d] bhkListShape (sub-shape refs not decoded)" % (indent, shape_idx))
    elif t == "bhkBoxShape":
        out("%s[%d] bhkBoxShape %s" % (indent, shape_idx, fmt(nif.box_shape(shape_idx)["raw_floats"][:8])))
    else:
        out("%s[%d] %s (not decoded)" % (indent, shape_idx, t))
    return out_list


def collision_report(nif, title, xf_for_node=None):
    out("-- collision --")
    coll_objs = nif.blocks_of("bhkCollisionObject", "bhkNiCollisionObject", "bhkCollisionObjectNoRef")
    if not coll_objs:
        out("  none")
    records = []
    for cb in coll_objs:
        co = nif.parse_coll_object(cb.index)
        body_t = nif.type_of(co["body"])
        out("  bhkCollisionObject [%d] target=[%d]%s flags=0x%04x body=[%d]%s" %
            (cb.index, co["target"], nif.type_of(co["target"]), co["flags"], co["body"], body_t))
        if body_t and body_t.startswith("bhkRigidBody"):
            shape = nif.body_shape(co["body"])
            out("    body %s shape ref=[%d]" % (body_t, shape))
            records += collision_chain(nif, shape, "    ", 0, [])
    out("")
    return records


def sequence_report(nif):
    out("-- animation --")
    managers = nif.blocks_of("NiControllerManager")
    for m in managers:
        out("  NiControllerManager [%d] size=%d" % (m.index, m.size))
    seqs = nif.blocks_of("NiControllerSequence")
    if not seqs:
        out("  no NiControllerSequence blocks")
    for s in seqs:
        out("  NiControllerSequence [%d] name=%r size=%d" % (s.index, nif.controller_sequence_name(s.index), s.size))
    for k in nif.blocks_of("NiTextKeyExtraData"):
        out("  NiTextKeyExtraData [%d] keys=%s" % (k.index, nif.text_keys(k.index)))
    out("")


def nif_full_report(nif, title):
    report_nif(nif, title)
    out("-- scene graph (root = [%s]); tris = non-degenerate triangles decoded from strips "
        "(the strip header count includes stitch degenerates) --" % ", ".join(str(r) for r in nif.footer_roots))
    geoms = []
    for root in nif.footer_roots:
        geoms += scene_graph(nif, root, "  ")
    out("")
    sequence_report(nif)
    recs = collision_report(nif, title)
    return geoms, recs


def door_analysis(bldg, geoms, coll_recs):
    out("=" * 100)
    out("DOOR OPENING ANALYSIS (building-local coordinates)")
    out("")
    d = (DOOR_WORLD[0] - BLDG_WORLD[0], DOOR_WORLD[1] - BLDG_WORLD[1], DOOR_WORLD[2] - BLDG_WORLD[2])
    out("world delta door - building = %s" % fmt(d))
    out("building rot z = %.6f rad (%.3f deg), door rot z = %.3f rad (%.3f deg)" %
        (BLDG_ROT_Z, math.degrees(BLDG_ROT_Z), DOOR_ROT_Z, math.degrees(DOOR_ROT_Z)))
    out("building scale = %.3f" % BLDG_SCALE)
    out("")
    # Convention A (task text): world = R(-rz) * local  => local = R(+rz) * delta
    # Convention B (opposite sign):                         local = R(-rz) * delta
    cands = {}
    for label, ang in (("A: local = R(+rz) * delta (task convention)", BLDG_ROT_Z),
                       ("B: local = R(-rz) * delta (opposite)", -BLDG_ROT_Z)):
        c, s = math.cos(ang), math.sin(ang)
        x = (c * d[0] - s * d[1]) / BLDG_SCALE
        y = (s * d[0] + c * d[1]) / BLDG_SCALE
        z = d[2] / BLDG_SCALE
        cands[label] = (x, y, z)
        out("  %s -> local door position %s" % (label, fmt((x, y, z))))
    out("")
    # Wall test: which building geometry has a face near each candidate's x, and spans its y/z?
    for label, p in cands.items():
        out("  candidate %s" % label[:1])
        near = []
        for g in geoms:
            rb = g["bounds_root"]
            if rb is None:
                continue
            (x0, y0, z0), (x1, y1, z1) = rb
            if x0 - 20 <= p[0] <= x1 + 20 and y0 <= p[1] <= y1 and z0 <= p[2] <= z1:
                near.append(g)
        out("    geometry blocks whose root-space bbox contains the point (+-20 in x): %s" %
            [(g["block"], g["name"]) for g in near])
    out("")
    # Door's own extents (door NIF local bounds), to show what the box should cover.
    door = load(DOOR)
    dgeoms = scene_graph(door, door.footer_roots[0], "    ")
    door_pts = []
    for g in dgeoms:
        door_pts += [g["xform"].apply(p) for p in g["verts_local"]]
    dbox = N.bounds(door_pts)
    out("")
    out("door NIF visual bounds (door-local, root space) = %s..%s" % (fmt(dbox[0]), fmt(dbox[1])))
    out("")
    # Compare both candidates: how much building geometry / collision sits in the door volume.
    half = (BOX_DIMS[0] / 2.0, BOX_DIMS[1] / 2.0, BOX_DIMS[2] / 2.0)
    door_off = (0.5 * (dbox[0][0] + dbox[1][0]), 0.5 * (dbox[0][1] + dbox[1][1]),
                0.5 * (dbox[0][2] + dbox[1][2]))
    for label, p in cands.items():
        c = (p[0] + door_off[0], p[1] + door_off[1], p[2] + door_off[2])
        ng = 0
        for g in geoms:
            for (a, b, cc) in g["tris"]:
                tri = [g["xform"].apply(g["verts_local"][k]) for k in (a, b, cc)]
                if tri_intersects_box(tri, c, half):
                    ng += 1
        nc = 0
        for rec in coll_recs:
            for (a, b, cc) in rec["tris"]:
                tri = [rec["verts_game"][k] for k in (a, b, cc)]
                if tri_intersects_box(tri, c, half):
                    nc += 1
        out("  candidate %s: box center %s -> visual tris hit=%d, collision tris hit=%d" %
            (label[:1], fmt(c), ng, nc))
    out("")
    # Chosen candidate: A (task convention). It lands on the x ~ -285 front facade, where the
    # visual hit count above is high; candidate B lands inside the building with no geometry.
    chosen_label = [l for l in cands if l.startswith("A")][0]
    px, py, pz = cands[chosen_label]
    # Box: door's visual volume centre in building-local, dims 60 (x) x 160 (y) x 230 (z).
    center = (px + door_off[0], py + door_off[1], pz + door_off[2])
    lo = (center[0] - half[0], center[1] - half[1], center[2] - half[2])
    hi = (center[0] + half[0], center[1] + half[1], center[2] + half[2])
    out("box (candidate %s): center=%s half=%s  min=%s max=%s" %
        (chosen_label[:1], fmt(center), fmt(half), fmt(lo), fmt(hi)))
    out("")
    out("  NOTE: the door's own visual extents are door-local x %s, y %s, z %s"
        % (fmt((dbox[0][0], dbox[1][0])), fmt((dbox[0][1], dbox[1][1])), fmt((dbox[0][2], dbox[1][2]))))
    out("")
    # Geometry triangles in the box
    out("-- geometry triangles intersecting the box --")
    total_g = 0
    for g in geoms:
        hits = []
        for ti, (a, b, c) in enumerate(g["tris"]):
            tri = [g["xform"].apply(g["verts_local"][k]) for k in (a, b, c)]
            if tri_intersects_box(tri, center, half):
                hits.append((ti, tri))
        if hits:
            total_g += len(hits)
            ys = [p[1] for _, t in hits for p in t]
            xs = [p[0] for _, t in hits for p in t]
            zs = [p[2] for _, t in hits for p in t]
            out("  block [%d] %s: %d triangles of %d; x %.1f..%.1f  y %.1f..%.1f  z %.1f..%.1f" %
                (g["block"], g["name"], len(hits), len(g["tris"]), min(xs), max(xs), min(ys), max(ys), min(zs), max(zs)))
    out("  total visual triangles intersecting box: %d" % total_g)
    out("")
    # Collision triangles in the box
    out("-- collision triangles intersecting the box --")
    total_c = 0
    for rec in coll_recs:
        hits = 0
        xs, ys, zs = [], [], []
        for (a, b, c) in rec["tris"]:
            tri = [rec["verts_game"][k] for k in (a, b, c)]
            if tri_intersects_box(tri, center, half):
                hits += 1
                for p in tri:
                    xs.append(p[0]); ys.append(p[1]); zs.append(p[2])
        out("  hkPackedNiTriStripsData [%d]: %d of %d triangles intersect the box" %
            (rec["block"], hits, len(rec["tris"])))
        if hits:
            out("    x %.1f..%.1f  y %.1f..%.1f  z %.1f..%.1f" %
                (min(xs), max(xs), min(ys), max(ys), min(zs), max(zs)))
        total_c += hits
    out("  total collision triangles intersecting box: %d" % total_c)
    out("")
    # Wall plane near the door
    out("-- building wall faces near door (x within 40 of door, y/z spans the door) --")
    for g in geoms:
        if g["bounds_root"] is None:
            continue
        (x0, y0, z0), (x1, y1, z1) = g["bounds_root"]
        if abs(x0 - px) < 40 or abs(x1 - px) < 40:
            out("  block [%d] %s root bounds x %.1f..%.1f  y %.1f..%.1f  z %.1f..%.1f" %
                (g["block"], g["name"], x0, x1, y0, y1, z0, z1))
    out("")
    wall_coverage(geoms, coll_recs, px, (py + dbox[0][1], py + dbox[1][1]),
                  (pz + dbox[0][2], pz + dbox[1][2]))
    return {"center": center, "half": half, "lo": lo, "hi": hi, "candidates": cands, "chosen": chosen_label}


def _in_tri2d(p, a, b, c):
    def sign(p1, p2, p3):
        return (p1[0] - p3[0]) * (p2[1] - p3[1]) - (p2[0] - p3[0]) * (p1[1] - p3[1])
    d1 = sign(p, a, b)
    d2 = sign(p, b, c)
    d3 = sign(p, c, a)
    neg = d1 < 0 or d2 < 0 or d3 < 0
    pos = d1 > 0 or d2 > 0 or d3 > 0
    return not (neg and pos)


def wall_coverage(geoms, coll_recs, x_plane, y_rng, z_rng, slab=8.0, nx=24, nz=12):
    """Flat triangles lying in the slab x in [x_plane - slab, x_plane + slab] cover (y,z) points.
    Reports which points of the door rectangle are covered by visual geometry or collision."""
    out("-- wall coverage of the door rectangle (flat faces in x = %.1f +- %.1f) --" % (x_plane, slab))
    out("   door rectangle y %.1f..%.1f  z %.1f..%.1f" % (y_rng[0], y_rng[1], z_rng[0], z_rng[1]))
    vis_tris, col_tris = [], []
    lo, hi = x_plane - slab, x_plane + slab
    for g in geoms:
        for (a, b, c) in g["tris"]:
            tri = [g["xform"].apply(g["verts_local"][k]) for k in (a, b, c)]
            if all(lo <= p[0] <= hi for p in tri):
                vis_tris.append([(p[1], p[2]) for p in tri])
    for rec in coll_recs:
        for (a, b, c) in rec["tris"]:
            tri = [rec["verts_game"][k] for k in (a, b, c)]
            if all(lo <= p[0] <= hi for p in tri):
                col_tris.append([(p[1], p[2]) for p in tri])
    out("   flat visual triangles in slab: %d ; flat collision triangles in slab: %d" % (len(vis_tris), len(col_tris)))
    rows = []
    n_v = n_c = n_none = n_vc = 0
    for iz in range(nz):
        z = z_rng[1] - (iz + 0.5) * (z_rng[1] - z_rng[0]) / nz  # top to bottom
        row = ""
        for iy in range(nx):
            y = y_rng[0] + (iy + 0.5) * (y_rng[1] - y_rng[0]) / nx
            pv = any(_in_tri2d((y, z), *t) for t in vis_tris)
            pc = any(_in_tri2d((y, z), *t) for t in col_tris)
            if pc:
                n_vc += 1
            if pv and pc:
                row += "#"
                n_v += 1
            elif pv:
                row += "v"
                n_v += 1
            elif pc:
                row += "c"
                n_c += 1
            else:
                row += "."
                n_none += 1
        rows.append("   z=%7.1f |%s|" % (z, row))
    total = nx * nz
    out("   legend: # visual AND collision face, v visual only, c collision only, . open (no face)")
    out("   y from %.1f (left) to %.1f (right)" % (y_rng[0], y_rng[1]))
    for r in rows:
        out(r)
    out("   samples: visual-covered %d/%d, collision-covered (any) %d/%d, collision-only %d/%d, open %d/%d" %
        (n_v, total, n_vc, total, n_c, total, n_none, total))
    out("")


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    bldg = load(BUILDING)
    door = load(DOOR)
    frame = load(DOORFRAME)

    bg, bcoll = nif_full_report(bldg, BUILDING)
    out("")
    # Building summary: all geometry bounds (root space)
    out("-- building geometry summary (root-space vertex bounds per block) --")
    for g in bg:
        rb = g["bounds_root"]
        out("  [%d] %-22s verts=%5d tris=%5d  x %9.1f..%9.1f  y %9.1f..%9.1f  z %9.1f..%9.1f" %
            (g["block"], g["name"], len(g["verts_local"]), len(g["tris"]),
             rb[0][0], rb[1][0], rb[0][1], rb[1][1], rb[0][2], rb[1][2]))
    out("")

    out("=" * 100)
    dg, dcoll = nif_full_report(door, DOOR)
    out("-- door NIF convex collision box: see collision section above --")
    out("")

    out("=" * 100)
    fg, fcoll = nif_full_report(frame, DOORFRAME)
    out("")

    info = door_analysis(bldg, bg, bcoll)

    with open(REPORT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    sys.stdout.write("\n".join(lines) + "\n")
    sys.stdout.write("\nwrote %s\n" % REPORT)


if __name__ == "__main__":
    main()

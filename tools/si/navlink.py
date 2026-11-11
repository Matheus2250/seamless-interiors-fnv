"""Navmesh link between a converted interior and the exterior navmesh (Fase 5.3).

Inside a building there is no loading door any more, so the two navmeshes have to meet: the
triangle of the interior mesh nearest the door and the triangle of the exterior mesh nearest the
door get a reciprocal NVEX edge (the engine's way of joining navmeshes that touch).

This module only measures and picks the pair; the writer patches both records.
"""

import math
import struct

from si.master import fid


def seg_point_dist(a, b, p):
    ax, ay, az = a
    dx, dy, dz = b[0] - ax, b[1] - ay, b[2] - az
    L = dx * dx + dy * dy + dz * dz
    if L <= 1e-9:
        return math.dist(a, p)
    t = max(0.0, min(1.0, ((p[0] - ax) * dx + (p[1] - ay) * dy + (p[2] - az) * dz) / L))
    return math.dist((ax + t * dx, ay + t * dy, az + t * dz), p)


EDGES = ((0, 1), (1, 2), (2, 0))
MAX_REACH = 150.0


def nearest_edge(tris, p, neighbours=None):
    """(distance, triangle index, edge index) of the mesh edge closest to point p.

    With neighbours given, only edges on the outer boundary (neighbour < 0) are considered:
    an inner edge cannot be linked to another navmesh.
    """
    best = (float("inf"), None, None)
    fallback = (float("inf"), None, None)
    for ti, tri in enumerate(tris):
        for e, (i, j) in enumerate(EDGES):
            d = seg_point_dist(tri[i], tri[j], p)
            if d < fallback[0]:
                fallback = (d, ti, e)
            if neighbours is not None and neighbours[ti][e] >= 0:
                continue
            if d < best[0]:
                best = (d, ti, e)
    return best if best[1] is not None else fallback


def navmesh_tris(navm):
    """Triangles of a NAVM record in world space (exterior navmeshes store world coordinates)."""
    vx = navm.get("NVVX")
    V = [struct.unpack_from("<3f", vx, i * 12) for i in range(len(vx) // 12)]
    tr = navm.get("NVTR")
    out = []
    for i in range(len(tr) // 16):
        t = struct.unpack_from("<3H", tr, i * 16)
        out.append([V[j] for j in t])
    return out


def compute(plan, master):
    """Pick the pair of edges to join, or None when the meshes do not meet near the door."""
    door = plan.door
    d_int, tri_int, edge_int = nearest_edge(plan.nav_tris_world, door.pos,
                                            getattr(plan, "nav_tri_edges", None))
    gx, gy = int(door.pos[0] // 4096.0), int(door.pos[1] // 4096.0)
    cell = master.exterior_cell(fid(plan.world), gx, gy)
    if cell is None:
        return None
    best = None
    for navm in [r for r, _g in master.cell_children(cell.form_id) if r.sig == "NAVM"]:
        d, tri, edge = nearest_edge(navmesh_tris(navm), door.pos)
        if best is None or d < best[0]:
            best = (d, navm.form_id, tri, edge)
    if best is None or best[0] > MAX_REACH or d_int > MAX_REACH:
        return None
    return {"tri_world": tri_int, "edge": edge_int, "dist_int": d_int,
            "ext_navm": best[1], "ext_tri": best[2], "ext_edge": best[3], "dist_ext": best[0]}

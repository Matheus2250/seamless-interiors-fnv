"""Triage of one interior: the building that holds its door, the alignment, the fit, the
terrain and the complexity signals that decide whether it can be converted automatically.

Everything is measured, nothing is edited. The result feeds tools/triage.py and its CSV.
"""

import math
import os
import struct

from si import geom, meshes
from si import plan as P
from si.master import fid, u32

BUCKET = 512.0
CANDIDATE_RADIUS = 3000.0
DOOR_INSIDE_MARGIN = 30.0   # the door leaf must sit inside this-much inflated building bounds
WALL_REACH = 90.0          # the door must be this close to the candidate's collision
MIN_BUILDING_XY = 100.0     # anything smaller is a prop, not a building
MIN_BUILDING_Z = 100.0


def _obnd(base):
    data = base.get("OBND") if base is not None else None
    return struct.unpack("<6h", data) if data else None


def _box_corners(b):
    x0, y0, z0, x1, y1, z1 = b
    return [(x, y, z) for x in (x0, x1) for y in (y0, y1) for z in (z0, z1)]


def local_box_bounds(points):
    return ([min(c[i] for c in points) for i in range(3)],
            [max(c[i] for c in points) for i in range(3)])


def point_tri_dist(p, tri):
    """Distance from a point to a triangle (Ericson, Real-Time Collision Detection)."""
    def sub(a, b):
        return (a[0] - b[0], a[1] - b[1], a[2] - b[2])

    def dot(a, b):
        return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]

    ab, ac, ap = sub(tri[1], tri[0]), sub(tri[2], tri[0]), sub(p, tri[0])
    d1, d2 = dot(ab, ap), dot(ac, ap)
    if d1 <= 0 and d2 <= 0:
        return math.sqrt(dot(ap, ap))
    bp = sub(p, tri[1])
    d3, d4 = dot(ab, bp), dot(ac, bp)
    if d3 >= 0 and d4 <= d3:
        return math.sqrt(dot(bp, bp))
    vc = d1 * d4 - d3 * d2
    if vc <= 0 and d1 >= 0 and d3 <= 0:
        v = d1 / (d1 - d3)
        q = (tri[0][0] + v * ab[0], tri[0][1] + v * ab[1], tri[0][2] + v * ab[2])
        return math.sqrt(dot(sub(p, q), sub(p, q)))
    cp = sub(p, tri[2])
    d5, d6 = dot(ab, cp), dot(ac, cp)
    if d6 >= 0 and d5 <= d6:
        return math.sqrt(dot(cp, cp))
    vb = d5 * d2 - d1 * d6
    if vb <= 0 and d2 >= 0 and d6 <= 0:
        w = d2 / (d2 - d6)
        q = (tri[0][0] + w * ac[0], tri[0][1] + w * ac[1], tri[0][2] + w * ac[2])
        return math.sqrt(dot(sub(p, q), sub(p, q)))
    va = d3 * d6 - d5 * d4
    if va <= 0 and (d4 - d3) >= 0 and (d5 - d6) >= 0:
        w = (d4 - d3) / ((d4 - d3) + (d5 - d6))
        q = (tri[1][0] + w * (tri[2][0] - tri[1][0]), tri[1][1] + w * (tri[2][1] - tri[1][1]),
             tri[1][2] + w * (tri[2][2] - tri[1][2]))
        return math.sqrt(dot(sub(p, q), sub(p, q)))
    denom = 1.0 / (va + vb + vc)
    v, w = vb * denom, vc * denom
    q = (tri[0][0] + ab[0] * v + ac[0] * w, tri[0][1] + ab[1] * v + ac[1] * w,
         tri[0][2] + ab[2] * v + ac[2] * w)
    return math.sqrt(dot(sub(p, q), sub(p, q)))


class CollisionIndex:
    """Collision triangles of a placed ref, bucketed for a quick 'how close is this point'."""

    CELL = 128.0

    def __init__(self, tris):
        self.cells = {}
        self.lo = [1e18] * 3
        self.hi = [-1e18] * 3
        for tri in tris:
            for p in tri:
                for i in range(3):
                    self.lo[i] = min(self.lo[i], p[i])
                    self.hi[i] = max(self.hi[i], p[i])
            x0 = int(math.floor(min(p[0] for p in tri) / self.CELL))
            x1 = int(math.floor(max(p[0] for p in tri) / self.CELL))
            y0 = int(math.floor(min(p[1] for p in tri) / self.CELL))
            y1 = int(math.floor(max(p[1] for p in tri) / self.CELL))
            for cx in range(x0, x1 + 1):
                for cy in range(y0, y1 + 1):
                    self.cells.setdefault((cx, cy), []).append(tri)

    def min_dist(self, p, limit):
        """Distance from p to the nearest triangle, or None when farther than limit."""
        cx, cy = int(math.floor(p[0] / self.CELL)), int(math.floor(p[1] / self.CELL))
        rings = int(math.ceil(limit / self.CELL)) + 1
        best = None
        for r in range(rings + 1):
            for dx in range(-r, r + 1):
                for dy in range(-r, r + 1):
                    if max(abs(dx), abs(dy)) != r:
                        continue
                    for tri in self.cells.get((cx + dx, cy + dy), ()):
                        d = point_tri_dist(p, tri)
                        if best is None or d < best:
                            best = d
            if best is not None and best <= r * self.CELL:
                break
        return best if (best is not None and best <= limit) else None


def _model_of(base):
    modl = base.get("MODL") if base is not None else None
    return modl.rstrip(b"\x00").decode("latin-1").lower() if modl else ""


def _wrap(pos, rz, local, scale):
    return geom.to_world(pos, rz, (local[0] * scale, local[1] * scale, local[2] * scale))


def convex_hull(points):
    """Monotone chain on 2D points (used for the interior footprint)."""
    pts = sorted(set(points))
    if len(pts) <= 2:
        return pts

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower = []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    upper = []
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return lower[:-1] + upper[:-1]


class Buckets:
    """Spatial hash of outdoor structure refs."""

    def __init__(self, refs):
        self.b = {}
        for r in refs:
            x, y = r["pos"][0], r["pos"][1]
            self.b.setdefault((int(x // BUCKET), int(y // BUCKET)), []).append(r)

    def near(self, x, y, radius=CANDIDATE_RADIUS):
        n = int(radius // BUCKET) + 1
        cx, cy = int(x // BUCKET), int(y // BUCKET)
        out = []
        for by in range(cy - n, cy + n + 1):
            for bx in range(cx - n, cx + n + 1):
                for r in self.b.get((bx, by), ()):
                    dx, dy = r["pos"][0] - x, r["pos"][1] - y
                    if dx * dx + dy * dy <= radius * radius:
                        out.append(r)
        return out


class BuildingFinder:
    def __init__(self, master, struct_refs):
        self.m = master
        self.buckets = Buckets(struct_refs)
        self._base_cache = {}
        self._bounds_cache = {}
        self._collision_cache = {}
        self._wall_cache = {}

    def has_collision(self, base_fid):
        """True for pieces that solidly define space (walls, floors, rocks, rubble): their model
        has collision geometry. Light beams, decals, markers and triggers do not."""
        if base_fid in self._collision_cache:
            return self._collision_cache[base_fid]
        base = self.base(base_fid)
        out = False
        if base is not None:
            if base.sig == "SCOL":
                out = True
            else:
                model = meshes.model_path(base)
                if model:
                    try:
                        out = bool(meshes.collision_tris_local(meshes.read_model(model)))
                    except Exception:
                        out = False
        self._collision_cache[base_fid] = out
        return out

    def base(self, fid_):
        if fid_ not in self._base_cache:
            self._base_cache[fid_] = self.m.get(fid_)
        return self._base_cache[fid_]

    def local_bounds(self, base_fid, precise=False):
        """Mesh-local (lo, hi) bounds of a base. OBND is instant (it is in the record); visual
        bounds need the model from the BSA, so they are only used when precise is asked or when
        the record has no OBND. Third item says which one it is."""
        key = (base_fid, bool(precise))
        if key in self._bounds_cache:
            return self._bounds_cache[key]
        base = self.base(base_fid)
        obnd = _obnd(base)
        bounds, kind = None, None
        if precise or obnd is None:
            model = meshes.model_path(base) if base is not None else None
            data = meshes.read_model(model) if model else None
            if data:
                try:
                    vb = meshes.visual_bounds(data)
                except Exception:
                    vb = None
                    if os.environ.get("SI_DEBUG_BOUNDS"):
                        print("bounds fallback:", model)
                if vb:
                    bounds, kind = vb, "visual"
        if bounds is None:
            if obnd is None:
                self._bounds_cache[key] = None
                return None
            bounds, kind = (list(obnd[:3]), list(obnd[3:])), "obnd"
        self._bounds_cache[key] = (bounds[0], bounds[1], kind)
        return self._bounds_cache[key]

    def ref_bounds_local(self, ref, precise=False):
        """The ref's own (lo, hi) box in its base's space, scaled."""
        lb = self.local_bounds(ref["base"], precise)
        if lb is None:
            return None
        s = ref.get("scale", 1.0) or 1.0
        return ([v * s for v in lb[0]], [v * s for v in lb[1]], lb[2])

    def world_bounds(self, ref, precise=False):
        """World (lo, hi) AABB of a placed ref, from its base's bounds."""
        b = self.ref_bounds_local(ref, precise)
        if b is None:
            return None
        lo, hi = b[0], b[1]
        pos, rz = ref["pos"], ref["rot"][2]
        corners = [geom.to_world(pos, rz, c) for c in _box_corners((lo[0], lo[1], lo[2], hi[0], hi[1], hi[2]))]
        return local_box_bounds(corners)

    def collision_index(self, ref):
        """Collision triangles of a placed ref in world space, bucketed (cached per ref)."""
        key = ref["fid"]
        if key in self._collision_cache:
            return self._collision_cache[key]
        base = self.base(ref["base"])
        model = meshes.model_path(base) if base is not None else None
        data = meshes.read_model(model) if model else None
        idx = None
        if data:
            try:
                tris = [t for _n, t in meshes.ref_collision_world(
                    ref["pos"], (0, 0, ref["rot"][2]), ref.get("scale", 1.0) or 1.0, data)]
            except Exception:
                tris = []
            if tris:
                idx = CollisionIndex(tris)
        self._collision_cache[key] = idx
        return idx

    def candidates(self, leaf_centre, radius=CANDIDATE_RADIUS):
        """Structure refs whose world AABB (precise bounds) contains the door leaf centre, that are
        big enough to be a building and whose **collision actually reaches the door**: a utility
        pole can contain the door in its bounding box while its geometry is far away."""
        out, loose = [], []
        for r in self.buckets.near(leaf_centre[0], leaf_centre[1], radius):
            wb = self.world_bounds(r, precise=True)
            if wb is None:
                continue
            lo, hi = wb
            if not all(lo[i] - DOOR_INSIDE_MARGIN <= leaf_centre[i] <= hi[i] + DOOR_INSIDE_MARGIN
                       for i in range(3)):
                continue
            sx, sy, sz = hi[0] - lo[0], hi[1] - lo[1], hi[2] - lo[2]
            if sx < MIN_BUILDING_XY or sy < MIN_BUILDING_XY or sz < MIN_BUILDING_Z:
                continue
            entry = (sx * sy * sz, r, wb)
            idx = self.collision_index(r)
            if idx is None:
                loose.append(entry)      # no collision to test: keep it as a fallback
                continue
            d = idx.min_dist(leaf_centre, WALL_REACH)
            if d is not None:
                out.append(entry)
        out.sort(key=lambda t: t[0])
        loose.sort(key=lambda t: t[0])
        return out or loose


def door_leaf_centre(master, door_base):
    """Centre of the door leaf in the door mesh's space (visual bounds first, OBND as fallback)."""
    model = meshes.model_path(door_base)
    if model:
        data = meshes.read_model(model)
        if data:
            try:
                b = meshes.visual_bounds(data)
            except Exception:
                b = None
            if b:
                return tuple((b[0][i] + b[1][i]) / 2.0 for i in range(3))
    return geom.door_leaf_centre(door_base)


class InteriorTriage:
    def __init__(self, master, inv, finder, cell_fid, door_fid, leaf_c):
        self.m = master
        self.inv = inv
        self.finder = finder
        self.cell_fid = cell_fid
        self.door_fid = door_fid          # exterior door ref
        self.leaf_c = leaf_c
        self.cell = inv["interiors"][cell_fid]
        self.ext = inv["doors"][door_fid]
        self.notes = []
        self.scored = []
        self._resolve()

    def _resolve(self):
        self.ext_pos = tuple(self.ext["pos"])
        self.ext_rz = self.ext["rot"][2]
        self.int = self.inv["doors"][self.ext["xtel"]]
        self.int_pos = tuple(self.int["pos"])
        self.int_rz = self.int["rot"][2]
        self.world = self.ext["world"]
        self.int_cell = self.int["cell"]
        self.leaf = geom.to_world(self.ext_pos, self.ext_rz, self.leaf_c)

    # ---- footprint and building choice --------------------------------------------------
    def footprint(self):
        """Interior plan as the convex hull of every kept ref's box, in interior space, plus a
        cloud of ref positions for the alignment vote."""
        pts = []
        cloud = []
        for i, r in enumerate(self.cell["refs"]):
            base = self.finder.base(r["base"])
            if P.skip_reason(base) is not None:
                continue
            if i % 8 == 0:
                cloud.append(tuple(r["pos"]))
            if not self.finder.has_collision(r["base"]):
                continue
            b = self.finder.ref_bounds_local(r)
            if b is None:
                continue
            (x0, y0, _z0), (x1, y1, _z1) = (b[0], b[1])
            pos, rz = tuple(r["pos"]), r["rot"][2]
            for cx in (x0, x1):
                for cy in (y0, y1):
                    w = geom.to_world(pos, rz, (cx, cy, 0.0))
                    pts.append((round(w[0], 2), round(w[1], 2)))
        self.cloud = cloud or [(0.0, 0.0, 0.0)]
        self.hull = convex_hull(pts)
        return self.hull

    def _plan_overflow(self, al, bpos, brz, blo, bhi):
        worst = 0.0
        for p in self.hull:
            w = al.point((p[0], p[1], 0.0))
            l = geom.to_local(bpos, brz, w)
            worst = max(worst, blo[0] - l[0], l[0] - bhi[0], blo[1] - l[1], l[1] - bhi[1])
        return max(worst, 0.0)

    def choose_building(self):
        if not hasattr(self, "hull"):
            self.footprint()
        cands = self.finder.candidates(self.leaf)
        self.candidates = [(v, r["fid"], r["base"]) for v, r, _w in cands[:8]]
        scored = []
        for volume, ref, _wb in cands:
            b = self.finder.ref_bounds_local(ref, precise=True)
            if b is None:
                continue
            blo, bhi = b[0], b[1]
            pos, rz = tuple(ref["pos"]), ref["rot"][2]
            al = geom.choose_alignment(self.ext_pos, self.ext_rz, self.int_pos, self.int_rz,
                                       self.leaf_c, self.cloud, pos, rz)
            ov = self._plan_overflow(al, pos, rz, blo, bhi)
            scored.append({"overflow": ov, "volume": volume, "ref": ref, "al": al,
                           "bounds": (blo, bhi, b[2])})
        # the building that holds the most of the interior wins; ties by size. Architecture
        # meshes beat rocks/cliffs, which can also enclose the door.
        def architecture(s):
            d = _model_of(self.finder.base(s["ref"]["base"]))
            return 0 if d.startswith(("architecture\\", "dungeons\\", "scol\\",
                                      "dlc01\\", "dlc02\\", "dlc03\\", "dlc04\\")) else 1

        # fit first, architecture only as tie-break: a dock/pod under architecture\ wins today
        # just by living in that folder, even with an absurd overflow (Fase 9, passo 1)
        scored.sort(key=lambda s: (round(s["overflow"], 3), architecture(s), s["volume"]))
        self.scored = scored
        if not scored:
            self.building = None
            return None
        best = scored[0]
        self.building = best["ref"]
        self.al = best["al"]
        self.bld_pos = tuple(best["ref"]["pos"])
        self.bld_rz = best["ref"]["rot"][2]
        self.building_local = best["bounds"]
        self.prelim_overflow = best["overflow"]
        return best["ref"]

    # ---- precise fit --------------------------------------------------------------------
    def fit(self):
        """Interior extent in building-local space vs the building's own bounds.

        Interior refs use OBND (a rounding error of a unit does not matter at this scale); the
        building uses its model's real bounds. The plan (x/y) decides whether it fits;
        vertically, pieces below the building's floor (column bases, rugs) are harmless and
        talk activators above the roof are already moved under the floor by the pipeline."""
        lb = self.finder.local_bounds(self.building["base"], precise=True)
        if lb is None:
            return None
        s_bld = self.building.get("scale") or 1.0
        blo = [lb[0][i] * s_bld for i in range(3)]
        bhi = [lb[1][i] * s_bld for i in range(3)]
        self.building_local = (blo, bhi, lb[2])

        lo = [1e9] * 3
        hi = [-1e9] * 3
        worst_xy, sum_xy, out_xy = 0.0, 0.0, 0
        worst_other, out_other = 0.0, 0
        z_above, z_below, hidden = 0.0, 0.0, 0
        offenders = []
        kept = 0
        fx_planes = 0
        for r in self.cell["refs"]:
            base = self.finder.base(r["base"])
            if P.skip_reason(base) is not None:
                continue
            b = self.finder.ref_bounds_local(r)
            if b is None:
                continue
            if not self.finder.has_collision(r["base"]):
                ext = [b[1][0] - b[0][0], b[1][1] - b[0][1]]
                if max(ext) > 600:
                    fx_planes += 1
                    continue
            kept += 1
            pos, rz = tuple(r["pos"]), r["rot"][2]
            wp = self.al.point(pos)
            wrz = self.al.rot(rz)
            corners = [geom.to_local(self.bld_pos, self.bld_rz, geom.to_world(wp, wrz, c))
                       for c in _box_corners((b[0][0], b[0][1], b[0][2], b[1][0], b[1][1], b[1][2]))]
            rlo = [min(c[i] for c in corners) for i in range(3)]
            rhi = [max(c[i] for c in corners) for i in range(3)]
            for i in range(3):
                lo[i] = min(lo[i], rlo[i])
                hi[i] = max(hi[i], rhi[i])
            ov_xy = max(blo[0] - rlo[0], rhi[0] - bhi[0], blo[1] - rlo[1], rhi[1] - bhi[1], 0.0)
            if self.finder.has_collision(r["base"]):
                # the shell decides whether the room fits; decals and props hanging outside
                # are cosmetics, not a fit problem
                if ov_xy > 0.01:
                    out_xy += 1
                    offenders.append((ov_xy, r["fid"], (base.edid() or "") if base else ""))
                worst_xy = max(worst_xy, ov_xy)
                sum_xy += ov_xy
            else:
                if ov_xy > 0.01:
                    out_other += 1
                worst_other = max(worst_other, ov_xy)
            z_below = max(z_below, blo[2] - rlo[2])
            over_top = rhi[2] - bhi[2]
            if over_top > 0.01:
                if base is not None and base.sig in ("TACT", "ACTI") and rlo[2] > bhi[2]:
                    hidden += 1        # pipeline moves these under the floor
                else:
                    z_above = max(z_above, over_top)
        self.interior_bounds = (lo, hi)
        self.fit_xy = worst_xy
        self.fit_other = worst_other
        self.fit_refs_out = out_xy
        self.fit_refs_out_other = out_other
        self.fit_mean_overflow = sum_xy / max(1, kept)
        self.z_above = z_above
        self.z_below = z_below
        self.hidden = hidden
        self.kept_refs = kept
        self.fx_planes = fx_planes
        offenders.sort(reverse=True)
        self.fit_offenders = offenders[:6]
        return worst_xy

    # ---- terrain -----------------------------------------------------------------------
    def terrain_check(self, terrain):
        """Max terrain height above the interior floor, sampled over the interior footprint."""
        if terrain is None or not hasattr(self, "interior_bounds"):
            return None
        floor_z = self.ext_pos[2]
        lo, hi = self.interior_bounds
        world = fid(self.world)
        heights = []
        for i in range(7):
            for j in range(7):
                lx = lo[0] + (hi[0] - lo[0]) * i / 6.0
                ly = lo[1] + (hi[1] - lo[1]) * j / 6.0
                w = geom.to_world(self.bld_pos, self.bld_rz, (lx, ly, 0.0))
                h = terrain.height(world, w[0], w[1])
                if h is not None:
                    heights.append(h)
        if not heights:
            return None
        self.terrain_max_above = max(heights) - floor_z
        self.terrain_min_above = min(heights) - floor_z
        return self.terrain_max_above

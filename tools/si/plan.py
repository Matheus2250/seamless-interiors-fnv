"""Plan for one interior: everything needed to place it inside its building, derived from the
master plus a small JSON config (tools/buildings/<id>.json).

Config keys:
  id               short name, used for formID keys and mesh folders (required)
  exterior_door    exterior load door REFR formID (required)
  building_ref     building REFR formID (required for now; Fase 1 will detect it)
  package_radius   {package formID: radius} overrides for "near editor location" (optional)
"""

import math
import struct

from si import geom, meshes
from si.master import FLAG_PERSISTENT, fid, grid_of, ref_pos_rot, ref_scale, u32

# Refs never copied (CLAUDE.md sections 6 and 13).
SKIP_BASE_EDIDS = {"NorthMarker"}
# Things that can sit across a doorway but must not have a hole cut in them: traps and tripwires.
WALL_SKIP_PREFIXES = ("Trap", "Tripwire")
SKIP_BASE_PREFIXES = ("IndFXLightRays", "FXLightBeam")   # fake window sunlight planes
PLACEABLE_BASE_SIGS = {"STAT", "SCOL", "MSTT", "FURN", "CONT", "LIGH", "ACTI", "TACT", "DOOR",
                       "MISC", "ALCH", "ARMO", "WEAP", "AMMO", "BOOK", "KEYM", "NOTE", "IDLM",
                       "TERM", "SOUN", "TREE", "GRAS", "ADDN", "IMOD", "CCRD", "CHIP", "CMNY",
                       "LVLI"}
# Copies keep their own owner and lock; these pointed at interior refs or teleports.
COPY_DROP = {"XTEL", "XNDP", "XESP", "XLKR", "XEZN", "XRGD", "XPRM"}
PRE_XOWN = {"EDID", "NAME", "XEZN", "XRGD", "XRGB", "XPRM", "XTRI", "XMBO", "XTEL", "MMRK",
            "FULL", "CNAM", "BNAM", "MNAM", "NNAM", "XSRF", "XSRD", "XTRG", "XLCM", "XRDO"}
# A structural (room-kit) piece: a STAT with collision whose model is an architecture/dungeon
# kit piece (walls, floors, columns). They are made persistent so floors exist when persistent
# actors load (CLAUDE.md section 10).
KIT_MODEL_DIRS = ("meshes\\dungeons\\", "meshes\\architecture\\")
HIDDEN_DEPTH = 200.0          # talking activators above the roof go this far under the floor
VISUAL_BLOCK_REACH = 400.0    # how far from the door a piece can sit and still draw across it
VISUAL_BLOCK_COVERAGE = 0.5   # share of the opening its faces must cover to be cut
DOOR_CORRIDOR_DEPTH = 130.0   # how far in front/behind the door leaf a wall still blocks it
DOOR_AGREEMENT = 30.0        # units a second door may miss its interior door (rigid body test)
DOOR_TURN_AGREEMENT = math.radians(5.0)
WALK_BAND = (10.0, 130.0)     # body height above the navmesh used for the collision test
PLDT_NEAR_REF = 0
PLDT_NEAR_EDITOR_LOCATION = 3
MARKER_BASE = 0x0000003B          # STAT XMarker (MarkerX.nif)
PLDT_IN_CELL = 1
PKDT_DOOR_LOCK_FLAGS = 0x08 | 0x10 | 0x20 | 0x40 | 0x80 | 0x100
DEFAULT_PACKAGE_RADIUS = 512


def skip_reason(base):
    """Why a ref of an interior is not copied (None when it is). Shared with the triage so
    both use the same rules."""
    if base is None:
        return "base missing"
    edid = base.edid() or ""
    if edid in SKIP_BASE_EDIDS:
        return "interior-only marker"
    if edid.startswith(SKIP_BASE_PREFIXES):
        return "window sunlight effect"
    if base.sig not in PLACEABLE_BASE_SIGS:
        return "base type %s not placeable" % base.sig
    return None


def door_box_of(base):
    """The door leaf box in the door model space: OBND when usable, else the model bounds
    (some door statics ship OBND = 0,0,0,0,0,0)."""
    b = geom.obnd(base)
    if b is not None and min(b[3] - b[0], b[4] - b[1], b[5] - b[2]) >= 1.0:
        return b
    model = meshes.model_path(base)
    data = meshes.read_model(model) if model else None
    if data:
        try:
            vb = meshes.visual_bounds(data)
        except Exception:
            vb = None
        if vb:
            return (vb[0][0], vb[0][1], vb[0][2], vb[1][0], vb[1][1], vb[1][2])
    return b


def door_frame_of(box, rz):
    """(normal, side) unit vectors of the opening in world space, from the leaf box (thin
    axis = wall normal) and the ref rotation."""
    spans = [box[3] - box[0], box[4] - box[1]]
    thin = 0 if spans[0] < spans[1] else 1
    nx, ny = (1.0, 0.0) if thin == 0 else (0.0, 1.0)
    sx, sy = (0.0, 1.0) if thin == 0 else (1.0, 0.0)
    n = geom.rot2(-rz, nx, ny)
    s = geom.rot2(-rz, sx, sy)
    return (n[0], n[1], 0.0), (s[0], s[1], 0.0)


class Door:
    """One loading door pair being converted: the exterior REFR and its interior counterpart.

    A cell can have several doors out (62 of the 283 do); each pair is converted, the first one
    drives the alignment and the others are checked against it."""

    def __init__(self, master, ext_ref):
        self.ext = ext_ref
        self.base = master.base_of(ext_ref)
        xtel = ext_ref.get("XTEL")
        assert xtel, "exterior door %08X has no XTEL" % ext_ref.form_id
        self.int_ = master.require(u32(xtel), "REFR")
        self.pos, self.rot = ref_pos_rot(ext_ref)
        self.int_pos, self.int_rot = ref_pos_rot(self.int_)
        self.box = door_box_of(self.base)
        self.leaf_world = [geom.to_world(self.pos, self.rot[2], c)
                           for c in geom.box_corners(self.box)]
        self.deviation = None      # filled after the alignment
        self.turn = None
        self.normal, self.side = door_frame_of(self.box, self.rot[2])
        self.scale = 1.0

    def set_scale(self, int_scale):
        """The exterior door takes the interior door's size times the interior scale (writer:
        XSCL on the door override), whatever XSCL the two door refs had in the master."""
        self.int_door_scale = ref_scale(self.int_)
        self.scale = scale = self.int_door_scale * int_scale
        self.leaf_world = [geom.to_world(self.pos, self.rot[2], c)
                           for c in geom.box_corners(self.box, scale)]


class Copy:
    """A ref of the interior placed in the exterior."""

    def __init__(self, src, base, pos, rz):
        self.src = src
        self.base = base
        self.edid = base.edid() or ""
        self.pos = pos
        self.rz = rz
        self.structural = False
        self.model_override = None   # mesh path (bytes, NUL-terminated) for a door-wall piece
        self.stat_key = None
        # effective scale in the exterior: the ref's own XSCL times the interior scale (the
        # writer stores exactly this in XSCL); set by BuildingPlan
        self.scale = ref_scale(src)


class SharedBuilding:
    """One building mesh shared by several interiors (a motel whose rooms are separate cells).

    The pipeline groups the plans by building ref; each plan only computes its own facade cuts,
    and this class cuts every opening into one mesh and clears the collision inside the walkable
    volume of *all* the interiors it hosts."""

    def __init__(self, master, bld_ref):
        self.m = master
        self.ref = bld_ref
        self.base = master.base_of(bld_ref)
        self.pos, self.rot = ref_pos_rot(bld_ref)
        self.scale = ref_scale(bld_ref)
        self.model = meshes.model_path(self.base)
        self.plans = []
        self.mesh = None
        self.path = None
        self.coverage = None
        self.note = ""

    def add(self, plan):
        self.plans.append(plan)

    def build(self):
        src = meshes.read_model(self.model)
        assert src, "building model not found: %s" % self.model
        out = src
        n_cut = 0
        for plan in self.plans:
            for cut in plan.facade_cuts:
                out, k = meshes.cut_visual(out, cut)
                n_cut += k
        bpos, brz, s = self.pos, self.rot[2], self.scale

        def inner_wall(_n, tri_local):
            pts = [geom.to_world(bpos, brz, (p[0] * s, p[1] * s, p[2] * s))
                   for p in _samples(tri_local)]
            return any(plan.over_walkable(p) for plan in self.plans for p in pts)

        out, removed = meshes.degenerate_collision(out, inner_wall)
        leader = self.plans[0].id
        self.mesh = out
        self.path = "SeamlessInteriors\\%s\\%s" % (leader, self.model.split("\\")[-1])
        cuts = [cut for plan in self.plans for cut in plan.facade_cuts]
        self.coverage = max(meshes.visual_coverage(out, cut) for cut in cuts)
        self.note = ("shared building %s: %d facade faces cut for %d interiors, %d inner "
                     "collision triangles removed %s"
                     % (self.base.edid() or "%08X" % self.ref.form_id, n_cut, len(self.plans),
                        len(removed), removed))
        return out


class BuildingPlan:
    def __init__(self, master, cfg, building=None):
        self.m = master
        self.cfg = cfg
        self.id = cfg["id"]
        self.notes = []
        self.building_group = building
        # a cell with several doors is only converted when every pair lines up (one rigid body);
        # set strict_doors=false in the config to build anyway and inspect the numbers
        self.strict_doors = cfg.get("strict_doors", True)
        self._resolve_doors()
        self._resolve_building()
        self._collect_interior()
        self._align()
        self._place_copies()
        self._find_structural()
        self._hide_roof_sources()
        self._plan_navmesh()
        self._plan_building_mesh()
        try:
            self._plan_door_walls()
            self._plan_actors()
        except Exception:
            # a half-planned interior must not leave its openings cut in a shared building
            if self.building_group is not None and self in self.building_group.plans:
                self.building_group.plans.remove(self)
            raise

    # ---- doors and cells ----------------------------------------------------------------
    def _resolve_doors(self):
        m = self.m
        fids = self.cfg.get("exterior_doors") or [self.cfg["exterior_door"]]
        doors = [Door(m, m.require(f, "REFR")) for f in fids]
        d = doors[0]
        self.world = m.world_of(d.ext)
        assert self.world is not None and m.world_of(d.int_) is None
        self.int_cell = m.require(m.parent_cell_of(d.int_), "CELL")
        # every extra door must belong to the same interior cell and world
        kept = [d]
        for x in doors[1:]:
            if m.parent_cell_of(x.int_) != self.int_cell.form_id:
                self.notes.append("door %08X leads to another cell (%08X), left as a load door"
                                  % (x.ext.form_id, m.parent_cell_of(x.int_) or 0))
                continue
            if m.world_of(x.ext) != self.world:
                self.notes.append("door %08X is in another worldspace, left as a load door"
                                  % x.ext.form_id)
                continue
            kept.append(x)
        self.doors = kept
        self.door = d
        self.ext_door, self.int_door = d.ext, d.int_
        self.door_base = d.base
        self.ext_pos, self.ext_rot = d.pos, d.rot
        self.int_pos, self.int_rot = d.int_pos, d.int_rot
        self.world_cell = m.world_persistent_cell(self.world)

    def _resolve_building(self):
        m = self.m
        self.bld_ref = m.require(self.cfg["building_ref"], "REFR")
        self.bld_base = m.base_of(self.bld_ref)
        self.bld_pos, self.bld_rot = ref_pos_rot(self.bld_ref)
        self.bld_model = meshes.model_path(self.bld_base)
        self.bld_cell = m.parent_cell_of(self.bld_ref)
        self.bld_persistent = bool(self.bld_ref.flags & FLAG_PERSISTENT)
        self.bld_scale = ref_scale(self.bld_ref)   # refs can be scaled (0.9, 1.1 in the master)
        # optional shrink of the interior so it fits inside a smaller building (Fase 9)
        self.int_scale = float(self.cfg.get("scale", 1.0) or 1.0)
        for d in self.doors:
            d.set_scale(self.int_scale)

    def _collect_interior(self):
        self.refs, self.actors, self.navmeshes = [], [], []
        self.skipped = []
        for rec, _gtype in self.m.cell_children(self.int_cell.form_id):
            if rec.sig == "NAVM":
                if not self.cfg.get("no_navmesh"):     # experiment switch (crash bisection)
                    self.navmeshes.append(rec)
            elif rec.sig in ("ACHR", "ACRE"):
                self.actors.append(rec)
            elif rec.sig == "REFR":
                base = self.m.base_of(rec)
                why = self._skip_reason(rec, base)
                if why:
                    self.skipped.append((rec.form_id, base.edid() if base else "?", why))
                else:
                    self.refs.append((rec, base))
            else:
                self.skipped.append((rec.form_id, rec.sig, "record type not copied"))
        owner = self.int_cell.get("XOWN")
        rank = self.int_cell.get("XRNK")
        self.cell_owner = owner
        self.cell_rank = rank

    def _skip_reason(self, rec, base):
        if rec.get("XTEL"):
            return "load door"
        return skip_reason(base)

    # ---- alignment ----------------------------------------------------------------------
    def _align(self):
        # leaf centre from the door model's real vertex bounds (OBND is rounded to integers, and
        # the 180 degree flip doubles any error in it)
        bounds = meshes.visual_bounds(meshes.read_model(meshes.model_path(self.door_base)) or b"")            if meshes.model_path(self.door_base) else None
        leaf_c = (tuple((bounds[0][i] + bounds[1][i]) / 2.0 for i in range(3)) if bounds
                  else geom.door_leaf_centre(self.door_base))
        self.leaf_c = leaf_c
        pts = [ref_pos_rot(r)[0] for r, _b in self.refs]
        self.al = geom.choose_alignment(self.ext_pos, self.ext_rot[2], self.int_pos, self.int_rot[2],
                                        leaf_c, pts, self.bld_pos, self.bld_rot[2],
                                        scale=self.int_scale,
                                        int_door_scale=self.door.int_door_scale)
        self.floor_z = self.ext_pos[2]
        # Every extra door must land where its interior counterpart lands: the interior is one
        # rigid body, so one transform has to satisfy all pairs. Vanilla interiors often cheat
        # (the load door teleports and turns the player), and then no rigid placement works; such
        # a cell cannot be converted without a decision, so refuse instead of building it broken.
        agreement = float(self.cfg.get("door_agreement", DOOR_AGREEMENT))
        turn_tol = math.radians(float(self.cfg.get("door_turn", math.degrees(DOOR_TURN_AGREEMENT))))
        for d in self.doors[1:]:
            expected = self.al.point(d.int_pos)
            d.deviation = math.dist(expected, d.pos)
            d.turn = abs(((self.al.rot(d.int_rot[2]) - d.rot[2] + math.pi) % math.tau) - math.pi)
            if d.deviation > agreement or d.turn > turn_tol:
                self.notes.append(
                    "door %08X lands %.0f units / %.0f deg from its interior door"
                    % (d.ext.form_id, d.deviation, math.degrees(d.turn)))
                if not self.strict_doors:
                    continue
                raise ValueError(
                    "door %08X lands %.0f units / %.0f deg away from its interior door; the "
                    "vanilla pair is not rigid, this cell needs a manual decision"
                    % (d.ext.form_id, d.deviation, math.degrees(d.turn)))

    def _place_copies(self):
        self.copies = []
        for rec, base in self.refs:
            pos, rot = ref_pos_rot(rec)
            copy = Copy(rec, base, self.al.point(pos), self.al.rot(rot[2]))
            copy.scale = ref_scale(rec) * self.int_scale
            self.copies.append(copy)

    def door_box(self, door=None):
        """The door leaf's box in the door mesh's space."""
        return (door or self.door).box

    def door_leaf_world(self, door=None):
        """Corners of the exterior door leaf in world space."""
        return (door or self.door).leaf_world

    def door_frame_world(self, door=None):
        """(normal, side) unit vectors of the door opening in world space (horizontal)."""
        d = door or self.door
        return d.normal, d.side

    @staticmethod
    def _local_dir(rz, d):
        """A world direction in the local space of a ref rotated by rz (world = P + Rz(-rz) local)."""
        x, y = geom.rot2(rz, d[0], d[1])
        return (x, y, 0.0)

    @staticmethod
    def _off_axis(rotated_side):
        """How far the piece's frame is from being axis aligned with the door (radians)."""
        ang = math.atan2(rotated_side[1], rotated_side[0])
        return abs(((ang + math.pi / 4) % (math.pi / 2)) - math.pi / 4)

    ORIENTED_THRESHOLD = math.radians(1.5)

    # ---- structural pieces --------------------------------------------------------------
    def _find_structural(self):
        for c in self.copies:
            if c.base.sig != "STAT":
                continue
            tris = meshes.collision_tris_local(meshes.read_model(meshes.model_path(c.base)))
            if not tris:
                continue
            if meshes.model_path(c.base).startswith(KIT_MODEL_DIRS):
                c.structural = True

    def _hide_roof_sources(self):
        b = geom.obnd(self.bld_base)
        roof = self.bld_pos[2] + (b[5] if b else 600)
        for c in self.copies:
            if c.base.sig in ("TACT", "ACTI") and c.pos[2] > roof:
                c.pos = (c.pos[0], c.pos[1], self.floor_z - HIDDEN_DEPTH)
                self.notes.append("hid %s %08X under the floor (was above the roof)" % (c.edid, c.src.form_id))

    # ---- navmesh ------------------------------------------------------------------------
    def _plan_navmesh(self):
        self.nav_tris_world = []
        self.nav_tri_edges = []
        for nav in self.navmeshes:
            vx, tr = nav.get("NVVX"), nav.get("NVTR")
            V = [self.al.point(struct.unpack_from("<3f", vx, i * 12)) for i in range(len(vx) // 12)]
            for i in range(len(tr) // 16):
                raw = struct.unpack_from("<3H3hHH", tr, i * 16)
                self.nav_tris_world.append([V[j] for j in raw[:3]])
                self.nav_tri_edges.append(list(raw[3:6]))
        # joining the interior navmesh to the exterior one is optional (plan says separate plugin)
        self.nav_link = None
        if self.cfg.get("navlink"):
            from si import navlink
            self.nav_link = navlink.compute(self, self.m)
            if self.nav_link is not None:
                self.notes.append("navmesh link: exterior %08X tri %d edge %d at %.0f units"
                                  % (self.nav_link["ext_navm"], self.nav_link["ext_tri"],
                                     self.nav_link["ext_edge"], self.nav_link["dist_ext"]))

    def nav_z(self, p):
        """Height of the interior navmesh under p (world x/y), or None when p is off the mesh."""
        for tri in self.nav_tris_world:
            if _in_tri_xy(p, tri):
                return _z_on_tri(p, tri)
        return None

    def over_walkable(self, p):
        """True if world point p is inside the walkable volume above the interior navmesh."""
        for tri in self.nav_tris_world:
            if _in_tri_xy(p, tri):
                z = _z_on_tri(p, tri)
                return z + WALK_BAND[0] <= p[2] <= z + WALK_BAND[1]
        return False

    # ---- building mesh ------------------------------------------------------------------
    def _plan_facade_cuts(self):
        """The rectangle to cut in the building mesh for every door of this interior."""
        bpos, brz = self.bld_pos, self.bld_rot[2]
        s = self.bld_scale
        self.facade_cuts = []
        for door in self.doors:
            # the mesh is in model space; the ref's XSCL scales it into the world
            leaf_local = [tuple(q / s for q in geom.to_local(bpos, brz, p))
                          for p in door.leaf_world]
            n_local = self._local_dir(brz, door.normal)
            s_local = self._local_dir(brz, door.side)
            if self._off_axis(s_local) > self.ORIENTED_THRESHOLD:
                self.facade_cuts.append(meshes.doorway_cut(leaf_local, normal=n_local, side=s_local))
            else:
                self.facade_cuts.append(meshes.doorway_cut(leaf_local))
        self.facade_cut = self.facade_cuts[0]

    def _plan_building_mesh(self):
        self._plan_facade_cuts()
        if self.building_group is not None:
            # the mesh is cut once for every interior of the building (SharedBuilding.build)
            self.building_group.add(self)
            self.building_mesh = None
            self.building_mesh_path = None
            self.building_cut_coverage = None
            return
        src = meshes.read_model(self.bld_model)
        assert src, "building model not found: %s" % self.bld_model
        bpos, brz = self.bld_pos, self.bld_rot[2]
        s = self.bld_scale
        out = src
        n_cut = 0
        for cut in self.facade_cuts:
            out, k = meshes.cut_visual(out, cut)
            n_cut += k

        def inner_wall(_n, tri_local):
            return any(self.over_walkable(geom.to_world(bpos, brz, (p[0] * s, p[1] * s, p[2] * s)))
                       for p in _samples(tri_local))

        out, removed = meshes.degenerate_collision(out, inner_wall)
        self.building_mesh = out
        self.building_mesh_path = "SeamlessInteriors\\%s\\%s" % (self.id, self.bld_model.split("\\")[-1])
        self.notes.append("building: %d facade faces cut (%d door(s)), %d inner collision "
                          "triangles removed %s" % (n_cut, len(self.doors), len(removed), removed))
        self.building_cut_coverage = max(meshes.visual_coverage(out, c) for c in self.facade_cuts)

    # ---- walls behind the old load door -------------------------------------------------
    def _plan_door_walls(self):
        """Cut the wall behind every old loading door; a piece hit by two doors gets both cuts."""
        self.door_wall_meshes = []
        per_copy = {}
        for door in self.doors:
            self._collect_door_wall_cuts(door, per_copy)
            self._collect_door_backings(door)
        for key in sorted(per_copy, key=lambda k: per_copy[k][0].src.form_id):
            c, cuts = per_copy[key]
            nif = meshes.read_model(meshes.model_path(c.base))
            n_cut, removed = 0, []
            for cut in cuts:
                nif, k = meshes.cut_visual(nif, cut)
                n_cut += k
                nif, gone = meshes.degenerate_collision(nif, doorway_filter(cut))
                removed += gone
            name = meshes.model_path(c.base).split("\\")[-1].replace(".nif", "")
            path = "SeamlessInteriors\\%s\\%s_%08X.nif" % (self.id, name, c.src.form_id)
            c.model_override = path
            c.stat_key = "%s:wallstat:%08X" % (self.id, c.src.form_id)
            self.door_wall_meshes.append((c, path, nif, cuts))
            self.notes.append("door wall %s %08X: %d face(s) cut over %d door(s), "
                              "%d collision triangles removed, coverage left %.2f"
                              % (c.edid, c.src.form_id, n_cut, len(cuts), len(removed),
                                 max(meshes.visual_coverage(nif, cut) for cut in cuts)))

    def _collect_door_backings(self, door):
        """Exterior statics that draw a sheet right behind the door (the vanilla saloon has a
        decal standing there: invisible while the door is a load door, a bright plane across the
        opening once it swings). They are switched off (writer: initially disabled)."""
        if not hasattr(self, "door_backings"):
            self.door_backings = []
        import nif_strips as ns
        leaf = door.leaf_world
        centre = [sum(q[i] for q in leaf) / float(len(leaf)) for i in range(3)]
        n, s = door.normal, door.side
        us = [q[0] * s[0] + q[1] * s[1] for q in leaf]
        zs = [q[2] for q in leaf]
        pts = [(min(us) + (max(us) - min(us)) * (i + 0.5) / 8.0, min(zs) + (max(zs) - min(zs)) * (j + 0.5) / 8.0)
               for i in range(8) for j in range(8)]
        depth0 = centre[0] * n[0] + centre[1] * n[1]
        skip = {self.bld_ref.form_id} | {d.ext.form_id for d in self.doors}
        cells = {grid_of(q[0], q[1]) for q in leaf}
        for g in cells:
            cell = self.m.exterior_cell(self.world, *g)
            if cell is None:
                continue
            for rec, _gtype in self.m.cell_children(cell.form_id):
                if rec.sig != "REFR" or rec.form_id in skip or rec.flags & 0x800:
                    continue
                pos, rot = ref_pos_rot(rec)
                if math.dist(pos[:2], centre[:2]) > 400.0:
                    continue
                base = self.m.base_of(rec)
                if base is None or base.sig != "STAT" or not base.get("MODL"):
                    continue
                try:
                    f = meshes._parse(meshes.read_model(meshes.model_path(base)))
                except Exception:
                    continue
                if f is None:
                    continue
                sc = ref_scale(rec)
                tris = []
                for b in f.blocks:
                    if b.type != "NiTriStripsData":
                        continue
                    try:
                        d = ns.parse_tri_strips_data(b.raw)
                    except Exception:
                        continue
                    world = [geom.to_world_euler(pos, rot, (v[0] * sc, v[1] * sc, v[2] * sc))
                             for v in d["vertices"]]
                    for t in ns.decode_strips(d["strips"]):
                        if len(set(t)) != 3:
                            continue
                        P = [world[i] for i in t]
                        dep = [q[0] * n[0] + q[1] * n[1] - depth0 for q in P]
                        if max(abs(x) for x in dep) <= 40.0:
                            tris.append([(q[0] * s[0] + q[1] * s[1], q[2]) for q in P])
                if not tris:
                    continue
                covered = sum(1 for pt in pts if any(meshes._inside2(pt, t) for t in tris))
                if covered >= len(pts) * VISUAL_BLOCK_COVERAGE:
                    self.door_backings.append(rec)
                    self.notes.append("exterior static %08X (%s) stands across the door: disabled"
                                      % (rec.form_id, (base.get("EDID") or b"").rstrip(b"\0").decode()))

    def _collect_door_wall_cuts(self, door, per_copy):
        leaf = door.leaf_world
        paths = self.door_paths(door)
        normal_w, side_w = door.normal, door.side
        leaf_centre = [sum(q[i] for q in leaf) / float(len(leaf)) for i in range(3)]
        for c in self.copies:
            # walls can be kit pieces or DLC/other statics: what matters is solid collision in
            # the corridor, not which folder the model lives in
            if c.base.sig not in ("STAT", "SCOL"):
                continue
            nif = meshes.read_model(meshes.model_path(c.base))
            scale = meshes_scale(c)
            local = meshes.collision_tris_local(nif)
            world = meshes.ref_collision_world(c.pos, (0, 0, c.rz), scale, nif)
            # only pieces whose collision crosses the walking path through the doorway (a column
            # touching the edge of the opening is left alone)
            hit_pairs = [(lt, t) for (n, lt), (_n2, t) in zip(local, world)
                         if any(_segment_hits(a, b, t) for a, b in paths)]
            hit_tris = [lt for lt, _t in hit_pairs]
            # a wall is tall; a tripwire, a sill or a decal across the doorway is not something
            # to cut a hole in
            if (c.edid or "").startswith(WALL_SKIP_PREFIXES):
                continue
            if hit_tris and not is_wall_height(corridor_tris([t for _n, t in world], paths),
                                               self.int_scale):
                continue
            if not hit_tris and (not nif or math.dist(c.pos[:2], leaf_centre[:2]) > VISUAL_BLOCK_REACH):
                continue
            leaf_local = [tuple(q / scale for q in geom.to_local(c.pos, c.rz, p)) for p in leaf]
            # a piece rotated relative to the door needs the door's own frame, or the rectangle
            # does not line up with the opening and pieces of wall survive beside it
            n_local = self._local_dir(c.rz, normal_w)
            s_local = self._local_dir(c.rz, side_w)
            if self._off_axis(s_local) > self.ORIENTED_THRESHOLD:
                cut = meshes.doorway_cut(leaf_local, normal=n_local, side=s_local)
            else:
                cut = meshes.doorway_cut(leaf_local)
            if not hit_tris:
                # No collision in the way, but the piece may still draw something across the
                # opening: kit walls with a doorway carry a one-sided glow plane in it (seen from
                # outside as a bright sheet where the open door should show the room).
                try:
                    covered = meshes.visual_coverage(nif, cut)
                except Exception:
                    covered = 0.0
                if covered >= VISUAL_BLOCK_COVERAGE:
                    per_copy.setdefault(id(c), (c, []))[1].append(cut)
                continue
            # the blocking wall may sit some distance behind the leaf (recess, alcove): cut every
            # face of this piece that lies in the corridor's thickness, not just at the leaf plane
            lo = min(min(meshes.cut_coords(cut, p)[0] for p in t) for t in hit_tris)
            hi = max(max(meshes.cut_coords(cut, p)[0] for p in t) for t in hit_tris)
            cut["band"] = (min(cut["band"][0], lo - 10.0), max(cut["band"][1], hi + 10.0))
            per_copy.setdefault(id(c), (c, []))[1].append(cut)

    def door_paths(self, door=None):
        """Rays through the door opening in the door's own frame (what must be free)."""
        d = door or self.door
        return door_opening_paths(d.leaf_world, d.normal, d.side, DOOR_CORRIDOR_DEPTH)

    # ---- actors -------------------------------------------------------------------------
    def _nav_centre(self):
        """A point inside the room: area-weighted centroid of the navmesh, slightly above it."""
        tris = self.nav_tris_world
        if not tris:
            return (self.ext_pos[0], self.ext_pos[1], self.floor_z + 30.0)
        total, acc = 0.0, [0.0, 0.0, 0.0]
        for (x1, y1, z1), (x2, y2, z2), (x3, y3, z3) in tris:
            a = abs((x2 - x1) * (y3 - y1) - (x3 - x1) * (y2 - y1)) * 0.5
            acc[0] += (x1 + x2 + x3) / 3.0 * a
            acc[1] += (y1 + y2 + y3) / 3.0 * a
            acc[2] += (z1 + z2 + z3) / 3.0 * a
            total += a
        if total <= 0.0:
            return (self.ext_pos[0], self.ext_pos[1], self.floor_z + 30.0)
        return (acc[0] / total, acc[1] / total, acc[2] / total + 20.0)

    def _plan_actors(self):
        radius_cfg = {fid(k): v for k, v in self.cfg.get("package_radius", {}).items()}
        self.actor_moves = []
        self.package_overrides = {}
        for a in self.actors:
            pos, rot = ref_pos_rot(a)
            self.actor_moves.append((a, self.al.point(pos), self.al.rot(rot[2])))
            npc = self.m.base_of(a)
            for s in npc.subrecords():
                if s.type != "PKID":
                    continue
                pack = self.m.get(u32(s.data))
                pldt = pack.get("PLDT") if pack else None
                if pldt and struct.unpack_from("<iI", pldt) == (PLDT_IN_CELL, self.int_cell.form_id):
                    self.package_overrides[pack.form_id] = radius_cfg.get(pack.form_id,
                                                                          DEFAULT_PACKAGE_RADIUS)
        # Packages of *other* NPCs that send them into this cell: "in cell" no longer exists in
        # the world, so they are pointed at a persistent marker at the room's centre instead.
        self.marker_pos = self._nav_centre()
        self.visitor_packages = {}
        for pack in self.m.packages_in_cell(self.int_cell.form_id):
            if pack.form_id in self.package_overrides:
                continue
            self.visitor_packages[pack.form_id] = radius_cfg.get(pack.form_id,
                                                                 DEFAULT_PACKAGE_RADIUS * 2)
        if self.visitor_packages:
            self.notes.append("%d visitor package(s) now target a marker at the room centre"
                              % len(self.visitor_packages))

    # ---- cells --------------------------------------------------------------------------
    def grid(self, pos):
        return grid_of(pos[0], pos[1])


# Rays through the doorway are the player's body, not the full door height: a beam or column
# 130 units above the threshold (3+ metres) does not stop anyone from walking in.
DOOR_BODY_HEIGHTS = (18.0, 34.0, 50.0, 66.0)
DOOR_BODY_HALF_WIDTH = 0.35      # fraction of the leaf width on each side


def door_opening_paths(leaf, normal, side, depth=130.0):
    """Rays straight through the door opening, in the door's own frame: 5 lateral positions across
    +-35% of the leaf width, at four heights of the player's body, +-depth along the normal.
    Used both to pick the pieces to cut and to verify the opening is clear afterwards."""
    centre = [sum(p[i] for p in leaf) / float(len(leaf)) for i in range(3)]
    proj = [p[0] * side[0] + p[1] * side[1] for p in leaf]
    width = max(proj) - min(proj)
    z_lo = min(p[2] for p in leaf)
    out = []
    for iu in (-2, -1, 0, 1, 2):
        for h in DOOR_BODY_HEIGHTS:
            u = DOOR_BODY_HALF_WIDTH * width * iu / 2.0
            if h > (max(p[2] for p in leaf) - z_lo) - 5.0:
                continue
            a = [centre[0] + u * side[0] - depth * normal[0],
                 centre[1] + u * side[1] - depth * normal[1], z_lo + h]
            b = [centre[0] + u * side[0] + depth * normal[0],
                 centre[1] + u * side[1] + depth * normal[1], z_lo + h]
            out.append((tuple(a), tuple(b)))
    return out


def doorway_filter(cut):
    """Collision-triangle predicate: inside this cut's slab and overlapping its rectangle."""
    def test(_n, tri):
        b0, b1 = cut["band"]
        u0, u1, v0, v1 = cut["rect"]
        c3 = [meshes.cut_coords(cut, p) for p in tri]
        return (all(b0 - 5 <= w <= b1 + 5 for w, _u, _v in c3)
                and max(u for _w, u, _v in c3) > u0
                and min(u for _w, u, _v in c3) < u1
                and max(v for _w, _u, v in c3) > v0
                and min(v for _w, _u, v in c3) < v1)
    return test


def door_corridor_paths(leaf, depth, normal=None, side=None):
    """Segments through the doorway: centre and +-22% of the leaf width (so a narrow door does
    not hit its own jambs), at 25% and 60% of the leaf height, "depth" to each side.

    With normal/side the segments follow the door's own frame; an angled door (the shacks sit at
    30-40 degrees) otherwise gets a corridor aligned to the world axes, which is rotated relative
    to the opening and selects the wrong pieces."""
    centre = [sum(p[i] for p in leaf) / float(len(leaf)) for i in range(3)]
    z_lo = min(p[2] for p in leaf)
    height = max(p[2] for p in leaf) - z_lo
    out = []
    if normal is not None:
        proj = [p[0] * side[0] + p[1] * side[1] for p in leaf]
        width = max(proj) - min(proj)
        for off in (-0.22 * width, 0.0, 0.22 * width):
            for height_f in (0.25, 0.6):
                a = [centre[0] + off * side[0], centre[1] + off * side[1], z_lo + height_f * height]
                b = [a[0] - depth * normal[0], a[1] - depth * normal[1], a[2]]
                c = [a[0] + depth * normal[0], a[1] + depth * normal[1], a[2]]
                out.append((tuple(b), tuple(c)))
        return out
    lo = [min(p[i] for p in leaf) for i in range(3)]
    hi = [max(p[i] for p in leaf) for i in range(3)]
    normal_axis = 0 if hi[0] - lo[0] < hi[1] - lo[1] else 1
    side_axis = 1 - normal_axis
    width = hi[side_axis] - lo[side_axis]
    for off in (-0.22 * width, 0.0, 0.22 * width):
        for height_f in (0.25, 0.6):
            a = list(centre)
            a[side_axis] += off
            a[2] = z_lo + height_f * height
            b = list(a)
            a[normal_axis] -= depth
            b[normal_axis] += depth
            out.append((tuple(a), tuple(b)))
    return out


WALL_MIN_HEIGHT = 100.0   # vanilla units: anything lower across a doorway is walked over


def corridor_tris(world_tris, paths):
    """Triangles of a piece whose xy box overlaps the xy box of the doorway paths, at any
    height. A wall made of many small triangles fills this region top to bottom; the tall part
    of a compound piece standing elsewhere stays out of it."""
    xs = [q[0] for seg in paths for q in seg]
    ys = [q[1] for seg in paths for q in seg]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    return [t for t in world_tris
            if max(p[0] for p in t) >= x0 and min(p[0] for p in t) <= x1
            and max(p[1] for p in t) >= y0 and min(p[1] for p in t) <= y1]


def is_wall_height(tris, int_scale=1.0):
    """A wall, not a sill/tripwire: height of the piece inside the doorway corridor (see
    corridor_tris), in the interior's original size (a scaled-down wall is still a wall)."""
    zs = [p[2] for t in tris for p in t]
    return bool(zs) and (max(zs) - min(zs)) / (int_scale or 1.0) >= WALL_MIN_HEIGHT


def meshes_scale(c):
    """Scale of a copy as placed in the exterior (ref XSCL x interior scale)."""
    return c.scale


def _segment_hits(a, b, tri, eps=1e-9):
    """Moller-Trumbore segment/triangle intersection."""
    d = [b[i] - a[i] for i in range(3)]
    e1 = [tri[1][i] - tri[0][i] for i in range(3)]
    e2 = [tri[2][i] - tri[0][i] for i in range(3)]
    h = [d[1] * e2[2] - d[2] * e2[1], d[2] * e2[0] - d[0] * e2[2], d[0] * e2[1] - d[1] * e2[0]]
    det = sum(e1[i] * h[i] for i in range(3))
    if abs(det) < eps:
        return False
    inv = 1.0 / det
    s = [a[i] - tri[0][i] for i in range(3)]
    u = inv * sum(s[i] * h[i] for i in range(3))
    if u < 0 or u > 1:
        return False
    q = [s[1] * e1[2] - s[2] * e1[1], s[2] * e1[0] - s[0] * e1[2], s[0] * e1[1] - s[1] * e1[0]]
    v = inv * sum(d[i] * q[i] for i in range(3))
    if v < 0 or u + v > 1:
        return False
    t = inv * sum(e2[i] * q[i] for i in range(3))
    return 0.0 <= t <= 1.0


def _samples(tri, n=4):
    out = []
    for i in range(n + 1):
        for j in range(n + 1 - i):
            a, b = i / n, j / n
            cw = 1.0 - a - b
            out.append(tuple(a * tri[0][k] + b * tri[1][k] + cw * tri[2][k] for k in range(3)))
    return out


def _in_tri_xy(p, tri):
    (x1, y1, _), (x2, y2, _), (x3, y3, _) = tri
    d = (y2 - y3) * (x1 - x3) + (x3 - x2) * (y1 - y3)
    if abs(d) < 1e-9:
        return False
    a = ((y2 - y3) * (p[0] - x3) + (x3 - x2) * (p[1] - y3)) / d
    b = ((y3 - y1) * (p[0] - x3) + (x1 - x3) * (p[1] - y3)) / d
    return a >= 0 and b >= 0 and a + b <= 1


def _z_on_tri(p, tri):
    (x1, y1, z1), (x2, y2, z2), (x3, y3, z3) = tri
    d = (y2 - y3) * (x1 - x3) + (x3 - x2) * (y1 - y3)
    a = ((y2 - y3) * (p[0] - x3) + (x3 - x2) * (p[1] - y3)) / d
    b = ((y3 - y1) * (p[0] - x3) + (x1 - x3) * (p[1] - y3)) / d
    return a * z1 + b * z2 + (1 - a - b) * z3


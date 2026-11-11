"""Writes SeamlessInteriors.esp from any number of BuildingPlans.

Rules (CLAUDE.md): full WRLD > world children > block > sub-block > CELL hierarchy; WRLD
override without OFST; persistent refs in the world persistent cell; every temporary ref in
the cell that contains its position; master temporary refs that are overridden listed in ONAM;
NAVI override carries only our NVMI/NVCI entries; top groups in the master's order.
"""

import os
import struct
import sys
from collections import defaultdict

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
import build_esp as be  # noqa: E402
import esplib  # noqa: E402

from si import plan as P  # noqa: E402
from si.master import FLAG_PERSISTENT, grid_of, u32  # noqa: E402

AUTHOR = "Seamless Interiors"
NAVI_FORM = 0x00014B92
NAVTRI = struct.Struct("<3H3hHH")   # verts, edges (-1 = none), flags, cover flags
NAVTRI_DOOR = 0x0400
NVMI_ISLAND = 0x20
NAVTRI_EDGE_FLAGS = 0x0007
NAVTRI_EXTERNAL_EDGE = (0x1, 0x2, 0x4)
FLAG_COMPRESSED = 0x00040000


class Esp:
    def __init__(self, master, registry):
        self.m = master
        self.reg = registry
        self.esm_flag = False                  # TES4 master flag (see build)
        self.tops = defaultdict(list)          # top group label -> [record bytes]
        self.world_persist = defaultdict(list)  # world fid -> [record bytes]
        self.cell_refs = defaultdict(lambda: defaultdict(list))  # cell fid -> group type -> [bytes]
        self.onam = set()
        self._disabled = set()
        self.navi = []                          # navmesh pieces (dicts)
        self.mesh_files = {}                    # relative mesh path -> bytes
        self.nav_links = []                     # (interior id, ext navm, ext edge idx, ...)
        self.ext_links = defaultdict(list)      # ext navm -> [(tri, edge, own navm, own tri, id)]
        self.ext_unports = defaultdict(set)     # ext navm -> {(our door fid, door tri)}
        self.ext_merge = {}                     # ext navm -> appended verts/tris/nvex (merge mode)
        self.merge_navmesh = False              # append interiors to the cells' own navmeshes
        self._main_navm = None
        self.nav_notes = []                     # links skipped (edge already used)
        self._claimed_edges = {}                # (navm, tri, edge) -> interior id
        self.worlds = set()
        self._used_buildings = {}               # building REFR formID -> interior id
        self._building_paths = {}               # building REFR formID -> cut mesh path

    # ---- helpers ------------------------------------------------------------------------
    def place(self, src_rec, rec_bytes):
        """Put an override of a master ref back where the master has it."""
        gtype = src_rec.path[-1].group_type
        cell = self.m.parent_cell_of(src_rec)
        world = self.m.world_of(src_rec)
        self.worlds.add(world)
        if cell == self.m.world_persistent_cell(world).form_id:
            self.world_persist[world].append(rec_bytes)
        else:
            self.cell_refs[cell][gtype].append(rec_bytes)
            if gtype == esplib.GROUP_TEMPORARY_CHILDREN:
                self.onam.add(src_rec.form_id)

    def place_new(self, world, pos, persistent, rec_bytes):
        self.worlds.add(world)
        if persistent:
            self.world_persist[world].append(rec_bytes)
            return
        gx, gy = grid_of(pos[0], pos[1])
        cell = self.m.exterior_cell(world, gx, gy)
        assert cell is not None, "no exterior cell (%d,%d)" % (gx, gy)
        self.cell_refs[cell.form_id][esplib.GROUP_TEMPORARY_CHILDREN].append(rec_bytes)

    def stat_copy(self, src, new_fid, edid, modl):
        subs = []
        for s in src.subrecords():
            if s.type == "EDID":
                subs.append(("EDID", be.edid_bytes(edid)))
            elif s.type == "MODL":
                subs.append(("MODL", modl.encode("latin-1") + b"\x00"))
            elif s.type in ("MODT", "MODS"):
                continue
            else:
                subs.append((s.type, s.data))
        self.tops["STAT"].append(be.record_bytes("STAT", new_fid, src.flags, subs,
                                                 version=src.form_version, unknown=src.unknown))

    # ---- one building -------------------------------------------------------------------
    def add(self, pl):
        reg = self.reg
        key = pl.bld_ref.form_id
        if key in self._used_buildings:
            # several interiors of the same building share one cut mesh: whoever comes second
            # must be looking at exactly the same file
            if self._building_paths.get(key) != pl.building_mesh_path:
                raise ValueError("building %08X: %s and %s do not share one cut mesh"
                                 % (key, self._used_buildings[key], pl.id))
        else:
            self._used_buildings[key] = pl.id
            self._building_paths[key] = pl.building_mesh_path
            # building: new STAT with the cut mesh, building ref override
            bstat = reg.get("%s:stat:building" % pl.id)
            self.stat_copy(pl.bld_base, bstat, "SI%sBuilding" % pl.id, pl.building_mesh_path)
            self.mesh_files[pl.building_mesh_path] = pl.building_mesh
            subs = [("NAME", struct.pack("<I", bstat)) if s.type == "NAME" else (s.type, s.data)
                    for s in pl.bld_ref.subrecords()]
            self.place(pl.bld_ref, be.override_record_bytes(pl.bld_ref, subs, pl.bld_ref.flags))

        # exterior sheets standing behind a door: initially disabled
        for rec in getattr(pl, "door_backings", []):
            if rec.form_id not in self._disabled:
                self._disabled.add(rec.form_id)
                subs = [(s.type, s.data) for s in rec.subrecords()]
                self.place(rec, be.override_record_bytes(rec, subs, rec.flags | 0x800))

        # exterior doors: plain (animated) doors, no teleport
        for door in pl.doors:
            subs = [(s.type, s.data) for s in door.ext.subrecords()
                    if s.type not in ("XTEL", "XNDP")]
            cur = struct.unpack("<f", door.ext.get("XSCL"))[0] if door.ext.get("XSCL") else 1.0
            if abs(door.scale - cur) > 1e-4:      # the door takes the size of the opening behind it
                subs = [(t, d) for t, d in subs if t != "XSCL"]
                at = next(i for i, (t, _d) in enumerate(subs) if t == "DATA")
                if abs(door.scale - 1.0) > 1e-4:
                    subs.insert(at, ("XSCL", struct.pack("<f", door.scale)))
            self.place(door.ext, be.override_record_bytes(door.ext, subs, door.ext.flags))
            # the exterior navmesh still lists this door as a portal (NVDP + door flag on the
            # triangle): a path through it asks the door for its teleport and the game crashes
            # at 0x6A55A6 (Victor's patrol ends on the GSShackVictorInt porch). Drop the portal.
            xndp = door.ext.get("XNDP")
            if xndp:
                navm, tri = struct.unpack_from("<IH", xndp)
                self.ext_unports[navm].add((door.ext.form_id, tri))

        # door-wall pieces: one STAT per cut mesh
        wall_stat = {}
        for c, path, data, _cut in pl.door_wall_meshes:
            sf = reg.get(c.stat_key)
            self.stat_copy(c.base, sf, "SI%sWall%08X" % (pl.id, c.src.form_id), path)
            self.mesh_files[path] = data
            wall_stat[c.src.form_id] = sf

        # copies
        for c in pl.copies:
            f = reg.get("%s:copy:%08X" % (pl.id, c.src.form_id))
            self.place_new(pl.world, c.pos, c.structural,
                           self.copy_record(pl, c, f, wall_stat.get(c.src.form_id)))

        # actors and their packages
        for a, pos, rz in pl.actor_moves:
            data = a.get("DATA")
            rx, ry = struct.unpack_from("<2f", data, 12)
            subs = [("DATA", struct.pack("<6f", *pos, rx, ry, rz)) if s.type == "DATA" else (s.type, s.data)
                    for s in a.subrecords()]
            rec = be.override_record_bytes(a, subs, a.flags)
            self.place_new(pl.world, pos, bool(a.flags & FLAG_PERSISTENT), rec)
            if not a.flags & FLAG_PERSISTENT:
                self.onam.add(a.form_id)           # overridden temporary actor (as the DLCs do)
        # visitors: a persistent XMarker at the room centre and packages aimed at it
        marker = None
        if getattr(pl, "visitor_packages", None):
            marker = reg.get("%s:visitor_marker" % pl.id)
            self.place_new(pl.world, pl.marker_pos, True,
                           self.marker_record(marker, pl.marker_pos))
        for pack_fid, radius in sorted(pl.package_overrides.items()):
            self.tops["PACK"].append(self.package_record(self.m.get(pack_fid), radius,
                                                         P.PLDT_NEAR_EDITOR_LOCATION, 0))
        for pack_fid, radius in sorted(getattr(pl, "visitor_packages", {}).items()):
            self.tops["PACK"].append(self.package_record(self.m.get(pack_fid), radius,
                                                         P.PLDT_NEAR_REF, marker))

        # navmesh
        for nav in pl.navmeshes:
            self.add_navmesh(pl, nav)

    def copy_record(self, pl, c, new_fid, name_override):
        has_owner = c.src.get("XOWN") is not None
        rx, ry = struct.unpack_from("<2f", c.src.get("DATA"), 12)
        subs = []
        for s in c.src.subrecords():
            if s.type in P.COPY_DROP:
                continue
            if s.type == "DATA":
                subs.append(("DATA", struct.pack("<6f", *c.pos, rx, ry, c.rz)))
            elif s.type == "NAME" and name_override:
                subs.append(("NAME", struct.pack("<I", name_override)))
            elif s.type == "XSCL" and pl.int_scale != 1.0:
                # the interior was shrunk: the refs keep their relative size (Fase 9)
                subs.append(("XSCL", struct.pack("<f", struct.unpack("<f", s.data)[0] * pl.int_scale)))
            else:
                subs.append((s.type, s.data))
        if pl.int_scale != 1.0 and not any(t == "XSCL" for t, _d in subs):
            at = max([i for i, (t, _d) in enumerate(subs) if t in ("DATA",)] or [len(subs)]) 
            subs.insert(at, ("XSCL", struct.pack("<f", pl.int_scale)))
        if not has_owner and pl.cell_owner:
            # refs without an owner inherited the interior cell's owner; the exterior has none
            at = max([i for i, (t, _d) in enumerate(subs) if t in P.PRE_XOWN] or [0]) + 1
            extra = [("XOWN", pl.cell_owner)] + ([("XRNK", pl.cell_rank)] if pl.cell_rank else [])
            subs[at:at] = extra
        flags = c.src.flags & ~FLAG_COMPRESSED
        flags = flags | FLAG_PERSISTENT if c.structural else flags & ~FLAG_PERSISTENT
        return be.record_bytes("REFR", new_fid, flags, subs)

    def marker_record(self, fid, pos):
        """A persistent XMarker the visitors can be pointed at."""
        subs = [("NAME", struct.pack("<I", P.MARKER_BASE)),
                ("DATA", struct.pack("<6f", pos[0], pos[1], pos[2], 0.0, 0.0, 0.0))]
        return be.record_bytes("REFR", fid, FLAG_PERSISTENT, subs)

    def package_record(self, src, radius, mode, target):
        """Rewrite the location of a package that used to be 'in cell': mode 3 = near editor
        location (the cell's own actors), mode 0 = near ref (visitors, target = marker)."""
        subs = []
        for s in src.subrecords():
            if s.type in ("PLDT", "PTDT"):
                typ = struct.unpack_from("<i", s.data)[0]
                if typ == P.PLDT_IN_CELL:
                    subs.append((s.type, struct.pack("<iIi", mode, target, radius) + s.data[12:]))
                else:
                    subs.append((s.type, s.data))
            elif s.type == "PKDT":
                flags = u32(s.data) & ~P.PKDT_DOOR_LOCK_FLAGS
                subs.append(("PKDT", struct.pack("<I", flags) + s.data[4:]))
            else:
                subs.append((s.type, s.data))
        return be.override_record_bytes(src, subs, src.flags)

    # ---- navmesh ------------------------------------------------------------------------
    def add_navmesh(self, pl, src):
        vx, tr = src.get("NVVX"), src.get("NVTR")
        verts = [pl.al.point(struct.unpack_from("<3f", vx, i * 12)) for i in range(len(vx) // 12)]
        tris = [list(NAVTRI.unpack_from(tr, i * NAVTRI.size)) for i in range(len(tr) // NAVTRI.size)]
        _c, nverts, ntris, next_conn, _ncover, _ndoor = struct.unpack("<6I", src.get("DATA"))
        assert (nverts, ntris, next_conn) == (len(verts), len(tris), 0)
        div = struct.unpack_from("<I", src.get("NVGD"))[0]

        def grid_of_tri(t):
            return grid_of(sum(verts[i][0] for i in t[:3]) / 3.0, sum(verts[i][1] for i in t[:3]) / 3.0)

        owner = [grid_of_tri(t) for t in tris]
        if pl.cfg.get("drop_border_tris"):           # experiment switch (crash bisection)
            owner = [g if all(grid_of(verts[i][0], verts[i][1]) == g for i in t[:3]) else None
                     for g, t in zip(owner, tris)]
        grids = sorted({g for g in owner if g is not None})
        fids = {g: self.reg.get("%s:navm:%08X:%d,%d" % (pl.id, src.form_id, g[0], g[1])) for g in grids}
        members = defaultdict(list)
        local_tri = {}
        for ti, g in enumerate(owner):
            if g is None:
                continue
            local_tri[ti] = len(members[g])
            members[g].append(ti)
        link = getattr(pl, "nav_link", None)
        if self.merge_navmesh:
            if all(self.main_navmesh(pl.world, g) is not None for g in grids):
                self._merge_navmesh(pl, grids, members, owner, local_tri, verts, tris)
                return
            self.nav_notes.append("%s: a cell has no vanilla main navmesh, kept as new NAVMs" % pl.id)
        for g in grids:
            cell = self.m.exterior_cell(pl.world, *g)
            vmap, pverts, ptris, nvex = {}, [], [], []
            own_link_tri = None
            for ti in members[g]:
                t = list(tris[ti])
                # the source's external edges point into ITS NVEX list; ours is rebuilt, so the
                # flags and values have to be dropped and recomputed (door portals included)
                t[6] &= ~(NAVTRI_DOOR | NAVTRI_EDGE_FLAGS)
                for j in range(3):
                    if t[j] not in vmap:
                        vmap[t[j]] = len(pverts)
                        pverts.append(verts[t[j]])
                    t[j] = vmap[t[j]]
                for e in range(3):
                    n = t[3 + e]
                    if n < 0 or owner[n] is None:
                        t[3 + e] = -1
                        continue
                    if owner[n] == g:
                        t[3 + e] = local_tri[n]
                    elif pl.cfg.get("no_nvex"):          # experiment switch (crash bisection)
                        t[3 + e] = -1
                    else:
                        t[6] |= NAVTRI_EXTERNAL_EDGE[e]
                        t[3 + e] = len(nvex)
                        nvex.append(struct.pack("<IIH", 0, fids[owner[n]], local_tri[n]))
                if link is not None and ti == link["tri_world"] and t[3 + link["edge"]] >= 0:
                    self.nav_notes.append(
                        "%s: nearest interior edge is not on the navmesh boundary" % pl.id)
                if link is not None and ti == link["tri_world"] and t[3 + link["edge"]] < 0:
                    e = link["edge"]
                    key = (link["ext_navm"], link["ext_tri"], e)
                    if key in self._claimed_edges:
                        self.nav_notes.append("%s: exterior edge %08X tri %d already used by %s"
                                              % (pl.id, key[0], link["ext_tri"],
                                                 self._claimed_edges[key]))
                    else:
                        # the edge nearest the outside door joins the exterior navmesh
                        self._claimed_edges[key] = pl.id
                        t[6] |= NAVTRI_EXTERNAL_EDGE[e]
                        t[3 + e] = len(nvex)
                        nvex.append(struct.pack("<IIH", 0, link["ext_navm"], link["ext_tri"]))
                        own_link_tri = (fids[g], len(ptris))
                ptris.append(t)
            if own_link_tri is not None:
                self.ext_links.setdefault(link["ext_navm"], []).append(
                    (link["ext_tri"], link["ext_edge"], own_link_tri[0], own_link_tri[1], pl.id))
            subs = [("NVER", src.get("NVER")),
                    ("DATA", struct.pack("<6I", cell.form_id, len(pverts), len(ptris), len(nvex), 0, 0)),
                    ("NVVX", b"".join(struct.pack("<3f", *v) for v in pverts)),
                    ("NVTR", b"".join(NAVTRI.pack(*t) for t in ptris)),
                    ("NVGD", navmesh_grid(pverts, ptris, div))]
            if nvex:
                subs.append(("NVEX", b"".join(nvex)))
            rec = be.record_bytes("NAVM", fids[g], src.flags, subs, version=src.form_version,
                                  unknown=src.unknown)
            self.cell_refs[cell.form_id][esplib.GROUP_TEMPORARY_CHILDREN].insert(0, rec)
            if not pl.cfg.get("no_navi"):           # experiment switch (crash bisection)
                self.navi.append({"fid": fids[g], "world": pl.world, "grid": g, "verts": pverts,
                                  "adjacent": [fids[o] for o in grids if o != g]})

    def main_navmesh(self, world, g):
        """The vanilla main navmesh of an exterior cell (NAVI NVMI with flag 0), or None."""
        if self._main_navm is None:
            self._main_navm = {}
            for d in self.m.get(NAVI_FORM).get_all("NVMI"):
                flags, navm, loc = struct.unpack_from("<III", d)
                if not flags & NVMI_ISLAND:
                    gy, gx = struct.unpack_from("<hh", d, 12)
                    self._main_navm.setdefault((loc, gx, gy), navm)
        return self._main_navm.get((world, g[0], g[1]))

    def _merge_navmesh(self, pl, grids, members, owner, local_tri, verts, tris):
        """Plan B for the 0x6A55A6 crash: instead of new NAVMs in cells that already have one
        (no official plugin ever does that), append the interior's triangles to the cell's own
        main navmesh, as an override (what the DLCs and the GECK do). The piece stays an island
        inside that navmesh; pieces in two cells are joined by NVEX between the two mains."""
        # vanilla main navmeshes are always one connected region (0 of 600 in WastelandNV have
        # a loose part; the GECK makes those separate island NAVMs), so the merged piece is
        # joined to the outside at the door through an ordinary internal edge
        link = getattr(pl, "nav_link", None)
        main, base = {}, {}
        for g in grids:
            fid = self.main_navmesh(pl.world, g)
            if fid is None:
                raise ValueError("%s: no vanilla navmesh in cell %r to merge into" % (pl.id, g))
            src = self.m.get(fid)
            acc = self.ext_merge.setdefault(fid, {"verts": [], "tris": [], "nvex": []})
            n_v = len(src.get("NVVX")) // 12 + len(acc["verts"])
            n_t = len(src.get("NVTR")) // NAVTRI.size + len(acc["tris"])
            n_x = len(src.get("NVEX") or b"") // 10 + len(acc["nvex"])
            main[g], base[g] = fid, (n_v, n_t, n_x)
        # reserve every piece's triangle range before writing links that point across pieces
        for g in grids:
            acc = self.ext_merge[main[g]]
            vbase, tbase, _xbase = base[g]
            # NVEX index = vanilla links + every merged link so far (acc grows across interiors)
            xvan = len(self.m.get(main[g]).get("NVEX") or b"") // 10
            vmap = {}
            for ti in members[g]:
                t = list(tris[ti])
                t[6] &= ~(NAVTRI_DOOR | NAVTRI_EDGE_FLAGS)
                for j in range(3):
                    if t[j] not in vmap:
                        vmap[t[j]] = vbase + len(vmap)
                        acc["verts"].append(verts[t[j]])
                    t[j] = vmap[t[j]]
                for e in range(3):
                    n = t[3 + e]
                    if n < 0 or owner[n] is None:
                        t[3 + e] = -1
                    elif owner[n] == g:
                        t[3 + e] = tbase + local_tri[n]
                    else:
                        o = owner[n]
                        t[6] |= NAVTRI_EXTERNAL_EDGE[e]
                        t[3 + e] = xvan + len(acc["nvex"])
                        acc["nvex"].append(struct.pack("<IIH", 0, main[o], base[o][1] + local_tri[n]))
                if link is not None and ti == link["tri_world"]:
                    e = link["edge"]
                    own = tbase + local_tri[ti]
                    if t[3 + e] != -1:
                        self.nav_notes.append("%s: door edge is not on the navmesh boundary" % pl.id)
                    elif link["ext_navm"] == main[g]:
                        t[3 + e] = link["ext_tri"]
                        acc.setdefault("joins", []).append((link["ext_tri"], link["ext_edge"], own))
                    else:
                        # the door's outside triangle belongs to another cell's navmesh: NVEX
                        t[6] |= NAVTRI_EXTERNAL_EDGE[e]
                        t[3 + e] = xvan + len(acc["nvex"])
                        acc["nvex"].append(struct.pack("<IIH", 0, link["ext_navm"], link["ext_tri"]))
                        self.ext_links.setdefault(link["ext_navm"], []).append(
                            (link["ext_tri"], link["ext_edge"], main[g], own, pl.id))
                acc["tris"].append(t)

    def flush_navlinks(self):
        """One override per exterior navmesh, with every interior link patched in at once (two
        rooms of the same building share the same exterior navmesh)."""
        for ext_navm in sorted(set(self.ext_links) | set(self.ext_unports) | set(self.ext_merge)):
            src = self.m.get(ext_navm)
            if src is None:
                continue
            links = self.ext_links.get(ext_navm, [])
            unports = self.ext_unports.get(ext_navm, set())
            gone = {d for d, _t in unports}
            cell = self.m.parent_cell_of(src)
            nvex = src.get("NVEX") or b""
            nvdp = src.get("NVDP") or b""
            kept_dp = [nvdp[i:i + 8] for i in range(0, len(nvdp), 8)
                       if struct.unpack_from("<I", nvdp, i)[0] not in gone]
            # a triangle stays a door triangle only if another door still uses it
            still_door = {struct.unpack_from("<IH", e)[1] for e in kept_dp}
            undoor = {t for _d, t in unports if t not in still_door}
            merged = self.ext_merge.get(ext_navm, {"verts": [], "tris": [], "nvex": []})
            nvex += b"".join(merged["nvex"])       # merged pieces' links come first
            base = len(nvex) // 10
            patches = {}
            extra = b""
            for i, (tri, edge, own_navm, own_tri, ident) in enumerate(links):
                patches[(tri, edge)] = base + i
                extra += struct.pack("<IIH", 0, own_navm, own_tri)
                self.nav_links.append((ident, ext_navm, base + i, own_navm, own_tri))
            subs = []
            for s in src.subrecords():
                if s.type == "NVTR":
                    raw = bytearray(s.data)
                    for (tri, edge), idx in patches.items():
                        off = tri * 16
                        struct.pack_into("<h", raw, off + 6 + 2 * edge, idx)
                        flags = struct.unpack_from("<H", raw, off + 12)[0]
                        struct.pack_into("<H", raw, off + 12, flags | (1 << edge))
                    for tri in undoor:
                        off = tri * 16
                        flags = struct.unpack_from("<H", raw, off + 12)[0]
                        struct.pack_into("<H", raw, off + 12, flags & ~NAVTRI_DOOR)
                    for tri, edge, own in merged.get("joins", []):
                        # reciprocal internal edge: outside triangle -> merged door triangle
                        struct.pack_into("<h", raw, tri * 16 + 6 + 2 * edge, own)
                    raw += b"".join(NAVTRI.pack(*t) for t in merged["tris"])
                    subs.append(("NVTR", bytes(raw)))
                elif s.type == "NVVX":
                    subs.append(("NVVX", s.data + b"".join(struct.pack("<3f", *v)
                                                           for v in merged["verts"])))
                elif s.type == "NVGD" and merged["tris"]:
                    vx = src.get("NVVX") + b"".join(struct.pack("<3f", *v) for v in merged["verts"])
                    allv = [struct.unpack_from("<3f", vx, i * 12) for i in range(len(vx) // 12)]
                    tr = src.get("NVTR")
                    allt = [NAVTRI.unpack_from(tr, i * NAVTRI.size)
                            for i in range(len(tr) // NAVTRI.size)] + merged["tris"]
                    div, _dx, _dy, *bounds = struct.unpack_from("<Iff3f3f", s.data)
                    subs.append(("NVGD", navmesh_grid(allv, allt, div, bounds)))
                elif s.type == "NVEX":
                    subs.append(("NVEX", nvex + extra))
                elif s.type == "NVDP":
                    if kept_dp:
                        subs.append(("NVDP", b"".join(kept_dp)))
                elif s.type == "DATA":
                    vals = list(struct.unpack_from("<6I", s.data))
                    vals[1] += len(merged["verts"])
                    vals[2] += len(merged["tris"])
                    vals[3] += len(merged["nvex"]) + len(links)
                    vals[5] = len(kept_dp) if nvdp else vals[5]
                    subs.append(("DATA", struct.pack("<6I", *vals)))
                else:
                    subs.append((s.type, s.data))
            if not src.get("NVEX") and nvex + extra:
                subs.append(("NVEX", nvex + extra))
            rec = be.record_bytes("NAVM", src.form_id, src.flags & ~FLAG_COMPRESSED, subs,
                                  version=src.form_version, unknown=src.unknown)
            self.cell_refs[cell][esplib.GROUP_TEMPORARY_CHILDREN].insert(0, rec)
            # an overridden temporary record of the master must be listed in ONAM (like refs)
            self.onam.add(src.form_id)

    def navi_record(self):
        src = self.m.get(NAVI_FORM)
        subs = [("NVER", src.get("NVER"))]
        for p in self.navi:
            centre = [sum(v[i] for v in p["verts"]) / len(p["verts"]) for i in range(3)]
            gx, gy = p["grid"]
            # our pieces are islands (not joined to the cell's main navmesh). Vanilla marks such
            # navmeshes with flag 0x20 + island data (bounds, u16 nverts, u16 ntris, verts, tris);
            # 363 vanilla islands carry empty geometry, as here. (Tried against the 0x6A55A6
            # crash: it did not fix it, see CLAUDE.md section 19; kept because it matches vanilla.)
            lo = [min(v[i] for v in p["verts"]) for i in range(3)]
            hi = [max(v[i] for v in p["verts"]) for i in range(3)]
            subs.append(("NVMI", struct.pack("<III", NVMI_ISLAND, p["fid"], p["world"])
                         + struct.pack("<hh", gy, gx) + struct.pack("<3f", *centre)
                         + struct.pack("<6f", *(lo + hi)) + struct.pack("<HHI", 0, 0, 0)))
        for p in self.navi:
            adj = p["adjacent"]
            subs.append(("NVCI", struct.pack("<II%dI" % len(adj), p["fid"], len(adj), *adj)
                         + struct.pack("<II", 0, 0)))
        # exterior navmeshes whose door portal we dropped: NAVI still lists the door as a way
        # into the interior (NVCI third list). Re-emit their entries without our doors.
        mi = {struct.unpack_from("<I", d, 4)[0]: d for d in src.get_all("NVMI")}
        for d in src.get_all("NVCI"):
            navm = struct.unpack_from("<I", d)[0]
            if navm not in self.ext_unports or navm not in mi:
                continue
            gone = {door for door, _t in self.ext_unports[navm]}
            off, lists = 4, []
            for _ in range(3):
                n = struct.unpack_from("<I", d, off)[0]
                lists.append(list(struct.unpack_from("<%dI" % n, d, off + 4)))
                off += 4 + 4 * n
            lists[2] = [door for door in lists[2] if door not in gone]
            subs.insert(1, ("NVMI", mi[navm]))
            subs.append(("NVCI", struct.pack("<I", navm) + b"".join(
                struct.pack("<I%dI" % len(lst), len(lst), *lst) for lst in lists) + d[off:]))
        return be.override_record_bytes(src, subs, src.flags & ~FLAG_COMPRESSED)

    # ---- output -------------------------------------------------------------------------
    def world_group(self, world):
        wrld = self.m.get(world)
        subs = [(s.type, s.data) for s in wrld.subrecords() if s.type != "OFST"]
        body = be.override_record_bytes(wrld, subs, wrld.flags & ~FLAG_COMPRESSED)
        wcell = self.m.world_persistent_cell(world)
        children = b""
        if self.world_persist[world]:
            children += wcell.raw() + be.group_bytes(wcell.form_id, 6, be.group_bytes(
                wcell.form_id, 8, b"".join(self.world_persist[world])))
        # exterior cells grouped by block / sub-block, in master file order
        cells = sorted((self.m.get(c) for c in self.cell_refs if self.m.world_of(self.m.get(c)) == world),
                       key=lambda r: r.offset)
        blocks = defaultdict(lambda: defaultdict(list))
        block_order, sub_order = [], defaultdict(list)
        for cell in cells:
            bl, sb = cell.path[2].label, cell.path[3].label
            if bl not in block_order:
                block_order.append(bl)
            if sb not in sub_order[bl]:
                sub_order[bl].append(sb)
            blocks[bl][sb].append(cell)
        for bl in block_order:
            sub_bytes = b""
            for sb in sub_order[bl]:
                content = b""
                for cell in blocks[bl][sb]:
                    groups = b""
                    for gtype in (esplib.GROUP_PERSISTENT_CHILDREN, esplib.GROUP_TEMPORARY_CHILDREN):
                        recs = self.cell_refs[cell.form_id].get(gtype)
                        if recs:
                            groups += be.group_bytes(cell.form_id, gtype, b"".join(recs))
                    content += cell.raw() + be.group_bytes(cell.form_id, 6, groups)
                sub_bytes += be.group_bytes(sb, 5, content)
            children += be.group_bytes(bl, 4, sub_bytes)
        return body + be.group_bytes(world, 1, children)

    def build(self):
        self.flush_navlinks()
        if self.navi or self.ext_unports:
            self.tops["NAVI"].append(self.navi_record())
        for world in sorted(self.worlds):
            self.tops.setdefault("WRLD", []).append(self.world_group(world))
        order = [g.label.decode("latin-1") for g in self.m.plugin.top_groups()]
        body = b"".join(be.group_bytes(label.encode("latin-1"), 0, b"".join(self.tops[label]))
                        for label in order if self.tops.get(label))
        hedr_version = struct.unpack_from("<f", self.m.plugin.header.get("HEDR"), 0)[0]
        subs = [("HEDR", struct.pack("<fII", hedr_version, be.count_items(body), self.reg.next_object_id())),
                ("CNAM", AUTHOR.encode("latin-1") + b"\x00"),
                ("MAST", b"FalloutNV.esm\x00"),
                ("DATA", b"\x00" * 8)]
        if self.onam:
            subs.append(("ONAM", b"".join(struct.pack("<I", f) for f in sorted(self.onam))))
        # "ESM" flag (0x1) on the .esp: FO3/FNV mishandle navmesh coming from plain ESPs (the
        # community "navmesh bug"); navmesh mods ship ESM-flagged
        tes4 = be.record_bytes("TES4", 0, 0x1 if self.esm_flag else 0, subs,
                               version=self.m.plugin.header.form_version,
                               unknown=self.m.plugin.header.unknown)
        return tes4 + body


def grid_divisor(ntris):
    """The NVGD divisor the GECK uses: 1 up to 16 triangles, then one more per 50, capped at 12.
    Holds for all 6,138 NAVMs of FalloutNV.esm and the DLCs. The engine does not trust the stored
    value: a divisor copied from the source navmesh (2 on a 12-triangle piece) crashed the game
    with heap corruption (FalloutNV.exe+0x6A55A6, inside the allocator)."""
    return 1 if ntris <= 16 else min(12, ntris // 50 + 2)


def navmesh_grid(verts, tris, div=None, bounds=None):
    """NVGD: divisor, cell size x/y, bounds, then per cell (x fastest) the triangles whose xy
    bounding box touches it (a superset of the GECK's exact test). `bounds` (min xyz, max xyz)
    keeps a vanilla grid's extent (an exterior main navmesh covers its whole cell). The divisor
    always follows `grid_divisor`; the argument is ignored."""
    div = grid_divisor(len(tris))
    lo = [min(v[i] for v in verts) for i in range(3)]
    hi = [max(v[i] for v in verts) for i in range(3)]
    if bounds is not None:
        lo = [min(a, b) for a, b in zip(lo, bounds[:3])]
        hi = [max(a, b) for a, b in zip(hi, bounds[3:])]
    dx = (hi[0] - lo[0]) / div
    dy = (hi[1] - lo[1]) / div
    cells = [[] for _ in range(div * div)]
    for ti, t in enumerate(tris):
        pts = [verts[i] for i in t[:3]]
        x0, x1 = min(q[0] for q in pts), max(q[0] for q in pts)
        y0, y1 = min(q[1] for q in pts), max(q[1] for q in pts)
        for cy in range(div):
            for cx in range(div):
                bx, by = lo[0] + cx * dx, lo[1] + cy * dy
                if x1 >= bx and x0 <= bx + dx and y1 >= by and y0 <= by + dy:
                    cells[cy * div + cx].append(ti)
    out = struct.pack("<Iff3f3f", div, dx, dy, *lo, *hi)
    for c in cells:
        out += struct.pack("<H%dH" % len(c), len(c), *c)
    return out

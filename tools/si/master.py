"""Read-only access to FalloutNV.esm with the lookups the pipeline needs."""

import os
import struct
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
import esplib  # noqa: E402

DATA_DIR = r"C:\Program Files (x86)\Steam\steamapps\common\Fallout New Vegas\Data"
MASTER_PATH = os.path.join(DATA_DIR, "FalloutNV.esm")
MESHES_BSA = os.path.join(DATA_DIR, "Fallout - Meshes.bsa")
CELL_SIZE = 4096.0
FLAG_PERSISTENT = 0x00000400


def fid(value):
    """formID from an int or a hex string like "00106373"."""
    return value if isinstance(value, int) else int(value, 16)


def u32(data, offset=0):
    return struct.unpack_from("<I", data, offset)[0]


def ref_pos_rot(rec):
    data = rec.get("DATA")
    return struct.unpack_from("<3f", data, 0), struct.unpack_from("<3f", data, 12)


def ref_scale(rec):
    data = rec.get("XSCL")
    return struct.unpack("<f", data)[0] if data else 1.0


def grid_of(x, y):
    return (int(x // CELL_SIZE), int(y // CELL_SIZE))


class Master:
    def __init__(self, path=MASTER_PATH):
        self.plugin = esplib.Plugin(path)
        self.plugin.build_index()
        self._ext_cells = None
        self._world_cells = None
        self._cell_children = {}
        self._packages = None

    def get(self, form_id):
        return self.plugin.get_record(fid(form_id))

    def require(self, form_id, sig):
        rec = self.get(form_id)
        if rec is None or rec.sig != sig:
            raise ValueError("%08X is not a %s in the master" % (fid(form_id), sig))
        return rec

    def base_of(self, ref):
        return self.get(u32(ref.get("NAME")))

    def find_by_edid(self, edid, sig):
        return self.plugin.find_by_edid(edid, sig)

    # ---- cells -------------------------------------------------------------------------
    def _scan_cells(self):
        self._ext_cells = {}
        self._world_cells = {}
        for rec in self.plugin.records("CELL", top="WRLD"):
            world = [g for g in rec.path if g.group_type == esplib.GROUP_WORLD_CHILDREN]
            if not world:
                continue
            world_fid = world[0].label_value()
            if any(g.group_type == esplib.GROUP_EXTERIOR_CELL_BLOCK for g in rec.path):
                xclc = rec.get("XCLC")
                gx, gy = struct.unpack_from("<ii", xclc)
                self._ext_cells[(world_fid, gx, gy)] = rec
            else:
                self._world_cells[world_fid] = rec  # the world's persistent cell

    def exterior_cell(self, world_fid, gx, gy):
        if self._ext_cells is None:
            self._scan_cells()
        return self._ext_cells.get((world_fid, gx, gy))

    def world_persistent_cell(self, world_fid):
        if self._world_cells is None:
            self._scan_cells()
        return self._world_cells[world_fid]

    def world_of(self, rec):
        """WRLD formID a record lives in (via its world-children group), or None (interior)."""
        for g in rec.path:
            if g.group_type == esplib.GROUP_WORLD_CHILDREN:
                return g.label_value()
        return None

    def parent_cell_of(self, ref):
        """CELL formID of the cell-children group a ref lives in."""
        for g in reversed(ref.path):
            if g.group_type == esplib.GROUP_CELL_CHILDREN:
                return g.label_value()
        return None

    def packages_in_cell(self, cell_fid):
        """PACK records whose location (PLDT or PTDT) is 'in cell' this cell."""
        cell_fid = fid(cell_fid)
        if self._packages is None:
            self._packages = {}
            for r in self.plugin.walk_top("PACK"):
                if r.kind != "record" or r.sig != "PACK":
                    continue
                for sig in ("PLDT", "PTDT"):
                    data = r.get(sig)
                    if data and len(data) >= 8 and struct.unpack_from("<i", data)[0] == 1:
                        cell = struct.unpack_from("<I", data, 4)[0]
                        self._packages.setdefault(cell, []).append(r)
        return self._packages.get(cell_fid, [])

    def cell_children(self, cell_fid):
        """[(record, child group type)] for every ref/navmesh in a cell."""
        cell_fid = fid(cell_fid)
        if cell_fid not in self._cell_children:
            cell = self.get(cell_fid)
            out = []
            for g in self.plugin.walk():
                if g.kind == "group" and g.group_type == esplib.GROUP_CELL_CHILDREN \
                        and g.label_value() == cell_fid:
                    for sub in g.children():
                        if sub.kind == "group":
                            for item in sub.children():
                                if item.kind == "record":
                                    out.append((item, sub.group_type))
                    break
            assert cell is not None
            self._cell_children[cell_fid] = out
        return self._cell_children[cell_fid]

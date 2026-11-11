"""Terrain heights from LAND/VHGT (verified against placed refs in Goodsprings).

VHGT = float offset + 33*33 signed byte deltas. Row 0 accumulates along x; each later row's
first byte is the delta from the previous row's first byte, the rest accumulate along x.
Row 0 is the y-min side of the cell. Height = (offset + accumulated) * 8 game units; bilinear
interpolation matches ground-placed refs to under a unit (median error 0.25 at Goodsprings,
and the store area decodes to exactly 8352, CLAUDE.md section 6).
"""

import struct

GRID = 33
SPACING = 4096.0 / (GRID - 1)
STEP_SCALE = 8.0


def decode(vhgt):
    offset = struct.unpack_from("<f", vhgt, 0)[0]
    steps = struct.unpack_from("<%db" % (GRID * GRID), vhgt, 4)
    vals = [[0.0] * GRID for _ in range(GRID)]
    for x in range(1, GRID):
        vals[0][x] = vals[0][x - 1] + steps[x]
    for y in range(1, GRID):
        row = y * GRID
        vals[y][0] = vals[y - 1][0] + steps[row]
        for x in range(1, GRID):
            vals[y][x] = vals[y][x - 1] + steps[row + x]
    return offset, vals


class Terrain:
    """Terrain height sampler, one VHGT grid per exterior cell (decoded lazily)."""

    def __init__(self, master):
        self.m = master
        self._grids = {}

    def grid(self, world_fid, gx, gy):
        key = (world_fid, gx, gy)
        if key in self._grids:
            return self._grids[key]
        cell = self.m.exterior_cell(world_fid, gx, gy)
        out = None
        if cell is not None:
            for rec, _g in self.m.cell_children(cell.form_id):
                if rec.sig == "LAND":
                    vhgt = rec.get("VHGT")
                    if vhgt:
                        out = decode(vhgt)
                    break
        self._grids[key] = out
        return out

    def height(self, world_fid, x, y):
        gx, gy = int(x // 4096.0), int(y // 4096.0)
        grid = self.grid(world_fid, gx, gy)
        if grid is None:
            return None
        offset, vals = grid
        u = (x - gx * 4096.0) / 4096.0 * (GRID - 1)
        v = (y - gy * 4096.0) / 4096.0 * (GRID - 1)
        u = min(max(u, 0.0), GRID - 1)
        v = min(max(v, 0.0), GRID - 1)
        x0, y0 = int(u), int(v)
        x1, y1 = min(GRID - 1, x0 + 1), min(GRID - 1, y0 + 1)
        fx, fy = u - x0, v - y0
        h = (vals[y0][x0] * (1 - fx) * (1 - fy) + vals[y0][x1] * fx * (1 - fy)
             + vals[y1][x0] * (1 - fx) * fy + vals[y1][x1] * fx * fy)
        return (offset + h) * STEP_SCALE

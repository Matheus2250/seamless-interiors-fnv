"""Geometry: Gamebryo rotation convention and the interior -> building alignment.

Convention (verified, see CLAUDE.md section 7): world = P + Rz(-rz) . local, with
Rz(a) = [[cos a, -sin a], [sin a, cos a]].
"""

import math
import struct

TWO_PI = 2.0 * math.pi
# Door leaf centre in the door mesh's local space (NVCraftsmanRmDoorAnimated: x -11..5,
# y 0..128.9, z 1..176.4). Doors with another mesh use their own OBND centre (door_leaf_centre).
DEFAULT_LEAF_C = (-3.0, 64.45, 88.7)


def rot2(a, x, y):
    c, s = math.cos(a), math.sin(a)
    return x * c - y * s, x * s + y * c


def norm_angle(a):
    a = math.fmod(a, TWO_PI)
    return a + TWO_PI if a < 0 else a


def to_world(pos, rz, local):
    x, y = rot2(-rz, local[0], local[1])
    return (pos[0] + x, pos[1] + y, pos[2] + local[2])


def to_world_euler(pos, rot, local):
    """to_world with all three ref rotations (x, then y, then z, each with the engine's sign).
    Exact for a single-axis rotation; the composition order for mixed x/y is not verified."""
    x, y, z = local
    rx, ry, rz = rot
    y, z = y * math.cos(rx) + z * math.sin(rx), -y * math.sin(rx) + z * math.cos(rx)
    x, z = x * math.cos(ry) - z * math.sin(ry), x * math.sin(ry) + z * math.cos(ry)
    wx, wy = rot2(-rz, x, y)
    return (pos[0] + wx, pos[1] + wy, pos[2] + z)


def to_local(pos, rz, world):
    x, y = rot2(rz, world[0] - pos[0], world[1] - pos[1])
    return (x, y, world[2] - pos[2])


def obnd(base):
    data = base.get("OBND") if base is not None else None
    return struct.unpack("<6h", data) if data else None


def door_leaf_centre(door_base):
    b = obnd(door_base)
    if b is None:
        return DEFAULT_LEAF_C
    return ((b[0] + b[3]) / 2.0, (b[1] + b[4]) / 2.0, (b[2] + b[5]) / 2.0)


def box_corners(bounds, scale=1.0):
    x0, y0, z0, x1, y1, z1 = bounds
    return [(x * scale, y * scale, z * scale) for x in (x0, x1) for y in (y0, y1) for z in (z0, z1)]


class Alignment:
    """Maps interior-cell coordinates onto the exterior so the two door leaves coincide."""

    def __init__(self, ext_pos, ext_rz, int_pos, int_rz, leaf_c, flip, scale=1.0, int_door_scale=1.0):
        self.scale = scale
        self.delta = norm_angle(ext_rz - int_rz + (math.pi if flip else 0.0))
        # Door refs can be scaled (XSCL, about the door's origin): the leaf centre moves with it.
        # The exterior door ends up at interior door scale x interior scale (the writer sets its
        # XSCL), so floor and jambs of the two coincide.
        self.ext_leaf = to_world(ext_pos, ext_rz, tuple(c * scale * int_door_scale for c in leaf_c))
        self.int_leaf = to_world(int_pos, int_rz, tuple(c * int_door_scale for c in leaf_c))

    def point(self, p):
        x, y = rot2(-self.delta, p[0] - self.int_leaf[0], p[1] - self.int_leaf[1])
        s = self.scale
        return (self.ext_leaf[0] + s * x, self.ext_leaf[1] + s * y,
                self.ext_leaf[2] + s * (p[2] - self.int_leaf[2]))

    def rot(self, rz):
        return norm_angle(rz + self.delta)


def choose_alignment(ext_pos, ext_rz, int_pos, int_rz, leaf_c, interior_points, bld_pos, bld_rz,
                     scale=1.0, int_door_scale=1.0):
    """Both candidates (with/without the 180 degree flip); keep the one that puts the interior on
    the building side of the door, i.e. whose mapped interior centre is nearest the building."""
    best = None
    for flip in (True, False):
        al = Alignment(ext_pos, ext_rz, int_pos, int_rz, leaf_c, flip, scale, int_door_scale)
        mapped = [al.point(p) for p in interior_points]
        cx = sum(p[0] for p in mapped) / len(mapped)
        cy = sum(p[1] for p in mapped) / len(mapped)
        d = math.hypot(cx - bld_pos[0], cy - bld_pos[1])
        if best is None or d < best[0]:
            best = (d, al)
    return best[1]

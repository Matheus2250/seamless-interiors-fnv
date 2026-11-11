"""Test kit: console .bat files for checking a converted interior in game (Fase 7).

For every config it writes three position files (outside the door, inside looking at the door,
two side views) using the coordinates measured by the pipeline, plus a README with the checklist.

Angle convention, taken from the bats that were actually tested in v1 (CLAUDE.md section 12):
the player's facing is (sin z, cos z), so z = atan2(dx, dy) in degrees; z = 0 faces +y.

Usage: py -I testkit.py [--only ID ...] [--limit N]
"""

import argparse
import glob
import json
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from si.master import Master  # noqa: E402
from si.plan import BuildingPlan  # noqa: E402

BUILDINGS = os.path.join(HERE, "buildings")
OUT = os.path.join(HERE, "out", "testkit")


def facing(dx, dy):
    return (math.degrees(math.atan2(dx, dy)) + 360.0) % 360.0


def bat(pos, deg):
    lines = ["player.setpos x %.0f" % pos[0], "player.setpos y %.0f" % pos[1],
             "player.setpos z %.0f" % pos[2], "player.setangle x 0",
             "player.setangle z %.0f" % deg]
    return "\r\n".join(lines) + "\r\n"


def write(name, text):
    with open(os.path.join(OUT, name), "w", newline="", encoding="ascii") as fh:
        fh.write(text)


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", nargs="*", default=None)
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args(argv)
    master = Master()
    configs = []
    for path in sorted(glob.glob(os.path.join(BUILDINGS, "*.json"))):
        cfg = json.load(open(path, encoding="utf-8"))
        if args.only and cfg["id"] not in args.only:
            continue
        configs.append(cfg)
    os.makedirs(OUT, exist_ok=True)
    rows, skipped = [], []
    for cfg in configs[:args.limit or None]:
        try:
            plan = BuildingPlan(master, cfg, building=None)
        except Exception as exc:
            skipped.append((cfg["id"], str(exc)[:120]))
            continue
        door = plan.door
        n, side = door.normal, door.side
        # the door frame normal can point either way (GSShackVictorInt: away from the building);
        # turn it toward the copied interior so "outside" really is outside
        cx = sum(c.pos[0] for c in plan.copies) / len(plan.copies)
        cy = sum(c.pos[1] for c in plan.copies) / len(plan.copies)
        if (cx - door.pos[0]) * n[0] + (cy - door.pos[1]) * n[1] < 0:
            n = (-n[0], -n[1], n[2])
        floor = door.pos[2]
        outside = (door.pos[0] - n[0] * 260.0, door.pos[1] - n[1] * 260.0, floor + 5.0)
        deg_out = facing(n[0], n[1])
        marker = getattr(plan, "marker_pos", None)
        if marker is None:
            z_nav = plan.nav_z((door.pos[0] + n[0] * 120.0, door.pos[1] + n[1] * 120.0, floor))
            marker = (door.pos[0] + n[0] * 120.0, door.pos[1] + n[1] * 120.0,
                      (z_nav if z_nav is not None else floor) + 20.0)
        z_in = plan.nav_z(marker)
        inside = (marker[0], marker[1], (z_in if z_in is not None else marker[2] - 20.0) + 5.0)
        deg_in = facing(door.pos[0] - inside[0], door.pos[1] - inside[1])
        side_a = (door.pos[0] + side[0] * 500.0, door.pos[1] + side[1] * 500.0, floor + 60.0)
        deg_a = facing(door.pos[0] - side_a[0], door.pos[1] - side_a[1])
        side_b = (door.pos[0] - side[0] * 500.0, door.pos[1] - side[1] * 500.0, floor + 60.0)
        deg_b = facing(door.pos[0] - side_b[0], door.pos[1] - side_b[1])
        ident = cfg["id"]
        write("%s_outside.txt" % ident, bat(outside, deg_out))
        write("%s_inside.txt" % ident, bat(inside, deg_in))
        write("%s_side1.txt" % ident, bat(side_a, deg_a))
        write("%s_side2.txt" % ident, bat(side_b, deg_b))
        rows.append({"id": ident, "door": door.ext.form_id, "outside": outside,
                     "inside": inside, "deg_in": deg_in,
                     "actors": [("%08X" % a.form_id) for a, _p, _r in plan.actor_moves],
                     "doors": len(plan.doors)})
        print("%-28s outside %8.0f %8.0f %6.0f   inside %8.0f %8.0f %6.0f" % (
            ident, outside[0], outside[1], outside[2], inside[0], inside[1], inside[2]))
    lines = ["# Test kit - Seamless Interiors", "",
             "Copie os .txt desta pasta para a raiz do jogo (a pasta que tem FalloutNV.exe)",
             "e rode no console: bat NOME (sem .txt).",
             "Cada interior tem quatro comandos: _outside (na frente da porta), _inside",
             "(dentro, olhando para a porta), _side1 e _side2 (laterais do predio).", "",
             "## Checklist por interior", "",
             "| interior | porta | atores | portas |", "|---|---|---|---|"]
    for r in rows:
        lines.append("| %s | %08X | %s | %d |" % (r["id"], r["door"],
                                                   ", ".join(r["actors"]) or "-", r["doors"]))
    lines += ["", "## O que conferir", "",
              "1. bat ID_outside: a porta esta na fachada, abre com E e nao ha tela de loading.",
              "2. Andar para dentro: o interior tem que aparecer, piso firme, sem teleporte.",
              "3. bat ID_inside: olhando para a porta, ver o mundo la fora pelo vao.",
              "4. bat ID_side1 / _side2: nenhuma parede do interior atravessando o predio.",
              "5. NPCs do interior: nascem dentro, conversam; a noite vao dormir.",
              "6. Sair para longe (bat g no save de teste) e voltar: tudo recarrega.", "",
              "## Configs que nao geraram kit (plano recusado)", ""]
    for ident, why in skipped:
        lines.append("- %s: %s" % (ident, why))
    write("LEIA-ME.md", "\n".join(lines) + "\n")
    print("wrote %s (%d interiors, %d skipped)" % (OUT, len(rows), len(skipped)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

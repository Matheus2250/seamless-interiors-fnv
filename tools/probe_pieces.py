import json, os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from si import triage as T
from si.master import Master
from si.plan import BuildingPlan

ident = sys.argv[1]
master = Master()
from si import inventory
inv = inventory.load(master)
if os.path.exists(os.path.join(HERE, 'buildings', ident + '.json')):
    cfg = json.load(open(os.path.join(HERE, 'buildings', ident + '.json'), encoding='utf-8'))
    pl = BuildingPlan(master, cfg, building=None)
    leaf = pl.door.pos
else:
    pos = None
    doors = inv['doors']
    for fid_, d in doors.items():
        tgt = doors.get(d.get('xtel'))
        if tgt and tgt.get('cell') == ident.upper() and d.get('world') is not None:
            pos = d['pos']
            break
    if pos is None:
        print('cell', ident, 'has no exterior door'); raise SystemExit(1)
    leaf = pos
f = T.BuildingFinder(master, inv['struct_refs'])
rows = []
for r in f.buckets.near(leaf[0], leaf[1], 700):
    base = f.base(r['base'])
    if base is None or base.sig not in ('STAT', 'SCOL'):
        continue
    idx = f.collision_index(r)
    if idx is None:
        continue
    d = idx.min_dist(leaf, 300.0)
    if d is None:
        continue
    rows.append((round(d, 1), int(r['fid'], 16) if isinstance(r['fid'], str) else r['fid'], base.edid() or '', f.world_bounds(r, precise=True)))
rows.sort()
print(ident, 'door', tuple(round(v) for v in leaf))
for d, rid, name, wb in rows[:8]:
    size = tuple(round(wb[1][i] - wb[0][i]) for i in range(3)) if wb else ()
    print('  %7.1f  %08X  %-26s size %s' % (d, rid, name, size))
print('  pieces within 90:', sum(1 for d, *_ in rows if d <= 90))
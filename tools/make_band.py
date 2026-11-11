import csv, json, os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
lo, hi = float(sys.argv[1]), float(sys.argv[2])
rows = list(csv.DictReader(open(os.path.join(HERE, 'out', 'triage.csv'), encoding='utf-8')))
made = []
for r in rows:
    fit = float(r['fit_xy'] or 0)
    size = r['building_size'] or ''
    if fit <= 0 or 'x' not in size:
        continue
    try:
        dims = [float(v) for v in size.split('x')[:3]]
    except ValueError:
        continue
    if min(dims) <= 0:
        continue
    s = round(min(d / (d + fit) for d in dims), 3)
    if not (lo <= s < hi):
        continue
    if not r['ext_door'] or not r['building_ref']:
        continue
    ident = r['edid'] or r['id']
    path = os.path.join(HERE, 'buildings', ident + '.json')
    if os.path.exists(path):
        continue
    cfg = {'enabled': False, 'id': ident, 'exterior_doors': [r['ext_door']],
           'building_ref': r['building_ref'], 'cell': r['id'], 'scale': s}
    json.dump(cfg, open(path, 'w', encoding='utf-8'), indent=1)
    made.append((ident, s))
print('criados:', len(made))
for m in made:
    print('  ', m)

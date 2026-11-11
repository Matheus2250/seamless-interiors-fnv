import csv, json, os
HERE = os.path.dirname(os.path.abspath(__file__))
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
    if not (0.8 <= s < 1.0):
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
print('configs com escala criados:', len(made))
for m in made:
    print('  ', m)
print('total configs:', len([p for p in os.listdir(os.path.join(HERE, 'buildings')) if p.endswith('.json')]))

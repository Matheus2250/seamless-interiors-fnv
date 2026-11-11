import json, os, re, subprocess, sys
HERE = os.path.dirname(os.path.abspath(__file__))

def run(args):
    return subprocess.run([sys.executable, '-I'] + args, cwd=HERE,
                          capture_output=True, text=True).stdout

out = run(['verify_build.py'])
ids = []
cur = None
for line in out.splitlines():
    m = re.match(r'== (\S+): FAIL', line)
    if m:
        cur = m.group(1)
    if 'sticks' in line and cur:
        ids.append(cur)
        cur = None
print('reprovados:', len(ids))
ok, stuck = [], []
for ident in ids:
    path = os.path.join(HERE, 'buildings', ident + '.json')
    if not os.path.exists(path):
        continue
    cfg = json.load(open(path, encoding='utf-8'))
    s = float(cfg.get('scale', 1.0))
    for step in range(8):
        cfg['scale'] = round(s, 3)
        json.dump(cfg, open(path, 'w', encoding='utf-8'), indent=1)
        o = run(['verify_build.py', '--only', ident])
        if 'RESULT: PASS' in o:
            ok.append((ident, round(s, 3)))
            break
        if 'sticks' not in o:
            stuck.append((ident, 'erro'))
            break
        s *= 0.95
    else:
        stuck.append((ident, round(s, 3)))
print('ok:', len(ok))
for x in ok:
    print('  ', x)
print('ainda presos:', len(stuck))
for x in stuck:
    print('  ', x)

import json, os, re, subprocess, sys
HERE = os.path.dirname(os.path.abspath(__file__))
ids = sys.argv[1:]
for ident in ids:
    path = os.path.join(HERE, 'buildings', ident + '.json')
    cfg = json.load(open(path, encoding='utf-8'))
    s = float(cfg.get('scale', 1.0))
    for step in range(6):
        cfg['scale'] = round(s, 3)
        json.dump(cfg, open(path, 'w', encoding='utf-8'), indent=1)
        out = subprocess.run([sys.executable, '-I', 'verify_build.py', '--only', ident],
                             cwd=HERE, capture_output=True, text=True).stdout
        m = re.search(r'sticks (\d+) out', out)
        if 'RESULT: PASS' in out and m is None:
            print(ident, 'PASS com s =', round(s, 3))
            break
        if m:
            s *= 0.95
        else:
            print(ident, 'sem medida:', out.strip().splitlines()[-1][:80])
            break
    else:
        print(ident, 'NAO convergiu (s =', round(s, 3), ')')

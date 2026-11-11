"""Summarise the address space of a running process: allocations grouped by size. py -I memmap_now.py [name]"""
import collections, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import crashcatch as cc, crashwatch as cw
pid = cc.find_pid(sys.argv[1] if len(sys.argv) > 1 else "FalloutNV.exe")
h = cw.k32.OpenProcess(0x0410, False, pid)
regs = [r for r in cw.memmap(h) if r[0] < 0x80000000]
print("commit %.0f reserved %.0f free %.0f largest %.1f (below 2 GB)" % cw.memsum(regs))
allocs = collections.defaultdict(lambda: [0, 0, 0])      # allocation base -> size, committed, type
for b, s, st, pr, ty, ab in regs:
    if st == 0x10000: continue
    a = allocs[ab]; a[0] += s; a[2] = ty
    if st == 0x1000: a[1] += s
by = collections.Counter(); com = collections.Counter()
for ab, (s, c, ty) in allocs.items():
    k = (round(s / 1048576.0, 2), {0x20000: "private", 0x40000: "mapped", 0x1000000: "image"}.get(ty, ty))
    by[k] += 1; com[k] += c
for k, n in sorted(by.items(), key=lambda kv: -kv[0][0] * kv[1])[:18]:
    print("%8.2f MB x %4d %-8s total %7.1f MB, committed %7.1f MB" % (k[0], n, k[1], k[0] * n, com[k] / 1048576.0))

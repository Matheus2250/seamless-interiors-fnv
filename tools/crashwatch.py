"""Capture a crashing 32-bit process without a debugger (SteamStub hides threads from one).

Waits for WerFault.exe to show up while the target is alive (the target is frozen in its
unhandled-exception filter at that point), suspends the target, saves every thread's context and
stack, finds the exception CONTEXT on the stacks, and dumps the decrypted image.

Usage: py -I crashwatch.py [--name FalloutNV.exe] [--out out/crash] [--now]
  --now  capture immediately (plumbing test), without waiting for WerFault
"""

import ctypes
import ctypes.wintypes as wt
import json
import os
import struct
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import crashcatch as cc  # noqa: E402

k32 = cc.k32
ntdll = ctypes.WinDLL("ntdll")
k32.OpenProcess.restype = wt.HANDLE
k32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
k32.CreateToolhelp32Snapshot.restype = wt.HANDLE
k32.CreateToolhelp32Snapshot.argtypes = [wt.DWORD, wt.DWORD]
ntdll.NtSuspendProcess.argtypes = [wt.HANDLE]
ntdll.NtResumeProcess.argtypes = [wt.HANDLE]
k32.K32EnumProcessModulesEx.argtypes = [wt.HANDLE, ctypes.c_void_p, wt.DWORD,
                                        ctypes.POINTER(wt.DWORD), wt.DWORD]


class TE32(ctypes.Structure):
    _fields_ = [("dwSize", wt.DWORD), ("cntUsage", wt.DWORD), ("tid", wt.DWORD),
                ("pid", wt.DWORD), ("pri", wt.LONG), ("delta", wt.LONG), ("flags", wt.DWORD)]


def threads_of(pid):
    snap = k32.CreateToolhelp32Snapshot(4, 0)
    e = TE32()
    e.dwSize = ctypes.sizeof(TE32)
    out = []
    ok = k32.Thread32First(wt.HANDLE(snap), ctypes.byref(e))
    while ok:
        if e.pid == pid:
            out.append(e.tid)
        ok = k32.Thread32Next(wt.HANDLE(snap), ctypes.byref(e))
    k32.CloseHandle(snap)
    return out


def image_base(hproc):
    mods = (ctypes.c_void_p * 4)()
    need = wt.DWORD(0)
    if k32.K32EnumProcessModulesEx(hproc, mods, ctypes.sizeof(mods), ctypes.byref(need), 1):
        return mods[0] or 0x400000
    return 0x400000


def find_contexts(stack, base, size):
    """CONTEXT records on a stack: ContextFlags 0x0001xxxx and Eip inside the image."""
    out = []
    for off in range(0, len(stack) - 0xCC, 4):
        flags, = struct.unpack_from("<I", stack, off)
        if flags & 0xFFFF0000 != 0x00010000 or not flags & 0x7:
            continue
        regs = struct.unpack_from("<16I", stack, off + cc.CTX_OFF)
        ctx = dict(zip(cc.CTX_NAMES, regs))
        if base <= ctx["eip"] < base + size and ctx["cs"] == 0x23 and ctx["ss"] == 0x2B:
            out.append([off, ctx])
    return out


def find_records(stack):
    """EXCEPTION_RECORD32 for an access violation: code, flags, 0, address, nparams 2."""
    out = []
    for off in range(0, len(stack) - 28, 4):
        code, flags, rec, addr, n, a0, a1 = struct.unpack_from("<7I", stack, off)
        if code == 0xC0000005 and n == 2 and a0 in (0, 1, 8):
            out.append({"off": off, "address": addr, "access": a0, "target": a1, "flags": flags})
    return out


class MBI(ctypes.Structure):
    _fields_ = [("base", ctypes.c_ulonglong), ("abase", ctypes.c_ulonglong), ("aprot", wt.DWORD),
                ("part", wt.WORD), ("pad", wt.WORD), ("size", ctypes.c_ulonglong),
                ("state", wt.DWORD), ("protect", wt.DWORD), ("type", wt.DWORD), ("pad2", wt.DWORD)]


k32.VirtualQueryEx.restype = ctypes.c_size_t
k32.VirtualQueryEx.argtypes = [wt.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t]


def memmap(hproc):
    """All regions below 4 GB: [base, size, state, protect, type, allocation base]."""
    out, addr, m = [], 0x10000, MBI()
    while addr < 0xFFFF0000:
        if not k32.VirtualQueryEx(hproc, ctypes.c_void_p(addr), ctypes.byref(m), ctypes.sizeof(m)):
            break
        out.append([m.base, m.size, m.state, m.protect, m.type, m.abase])
        addr = m.base + m.size
    return out


def memsum(regions):
    """(committed, reserved-only, free, largest free block) in MB."""
    mb = 1048576.0
    com = sum(r[1] for r in regions if r[2] == 0x1000)
    res = sum(r[1] for r in regions if r[2] == 0x2000)
    free = [r[1] for r in regions if r[2] == 0x10000]
    return com / mb, res / mb, sum(free) / mb, max(free or [0]) / mb


def capture(pid, out):
    os.makedirs(out, exist_ok=True)
    hproc = k32.OpenProcess(0x1F0FFF, False, pid)
    if not hproc:
        print("OpenProcess failed", ctypes.get_last_error())
        return 2
    ntdll.NtSuspendProcess(hproc)
    try:
        base = image_base(hproc)
        regions = memmap(hproc)
        json.dump(regions, open(os.path.join(out, "memmap.json"), "w"))
        print("memory at crash: commit %.0f MB, reserved %.0f MB, free %.0f MB, largest free %.1f MB"
              % memsum(regions), flush=True)
        size = cc.dump_image(hproc, base, os.path.join(out, "image_at_crash.bin"))
        rep = {"time": time.strftime("%H:%M:%S"), "image_base": base, "image_size": size,
               "threads": []}
        for tid in threads_of(pid):
            ctx = cc.context(tid)
            if ctx is None:
                continue
            stack = cc.read(hproc, ctx["esp"], 0x40000)
            open(os.path.join(out, "stack_%d.bin" % tid), "wb").write(stack)
            th = {"tid": tid, "context": ctx, "stack_len": len(stack),
                  "records": find_records(stack), "contexts": find_contexts(stack, base, size)}
            for _off, c in th["contexts"]:
                c["deref"] = {r: cc.read(hproc, c[r], 0x200).hex()
                              for r in ("eax", "ebx", "ecx", "edx", "esi", "edi", "ebp")}
                c["code"] = cc.read(hproc, c["eip"] - 0x100, 0x200).hex()
            if th["records"]:
                # objects the crashing frames point to (actor, list...): first 0x280 bytes each
                heap = {}
                for off, c in th["contexts"]:
                    so = max(0, c["esp"] - ctx["esp"])
                    for (w,) in struct.iter_unpack("<I", stack[so:so + 0x800]):
                        if w < 0x10000 or base <= w < base + size or w in heap or len(heap) > 400:
                            continue
                        if ctx["esp"] - 0x100000 <= w <= ctx["esp"] + 0x100000:
                            continue
                        data = cc.read(hproc, w, 0x280)
                        if data:
                            heap[w] = data.hex()
                th["heap"] = heap
            rep["threads"].append(th)
            if th["records"]:
                print("tid %d: %s" % (tid, ["%08X %s %08X" % (r["address"], "rwx"[min(r["access"], 2)],
                                                              r["target"]) for r in th["records"]]))
        json.dump(rep, open(os.path.join(out, "crash.json"), "w"))
        print("captured %d threads -> %s" % (len(rep["threads"]), out), flush=True)
    finally:
        ntdll.NtResumeProcess(hproc)
        k32.CloseHandle(hproc)
    return 0


def main(argv):
    name, out, now = "FalloutNV.exe", os.path.join(HERE, "out", "crash"), False
    i = 0
    while i < len(argv):
        if argv[i] == "--name":
            name = argv[i + 1]; i += 1
        elif argv[i] == "--out":
            out = argv[i + 1]; i += 1
        elif argv[i] == "--now":
            now = True
        i += 1
    pid = cc.find_pid(name)
    if pid is None:
        print("process not found:", name)
        return 2
    print("watching %s pid %d" % (name, pid), flush=True)
    hq = k32.OpenProcess(0x0410, False, pid)
    tick = 0
    while not now:
        tick += 1
        if tick % 100 == 0:             # every ~3 s: address-space use, to see what grows
            print("%s commit %.0f reserved %.0f free %.0f largest %.1f threads %d"
                  % ((time.strftime("%H:%M:%S"),) + memsum(memmap(hq)) + (len(threads_of(pid)),)),
                  flush=True)
        if cc.find_pid(name) != pid:
            print("process gone before WerFault was seen")
            return 1
        if cc.find_pid("WerFault.exe") is not None:
            break
        time.sleep(0.03)
    return capture(pid, out)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

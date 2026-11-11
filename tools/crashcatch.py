"""Attach to a running 32-bit process as a debugger and record access violations.

The Steam FalloutNV.exe is encrypted on disk (SteamStub), so a crash address alone says little.
This captures, at the moment of the fault: registers, the code around EIP, the stack, what the
registers point to, and the loaded image (decrypted in memory) for static analysis.

Usage: py -I crashcatch.py [--name FalloutNV.exe] [--out out/crash] [--snapshot]
  --snapshot  capture the main thread right after attaching and detach (plumbing test)
Stdlib only (ctypes). The target keeps running if this script is stopped (no kill on exit).
"""

import ctypes
import ctypes.wintypes as wt
import json
import os
import struct
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
k32 = ctypes.WinDLL("kernel32", use_last_error=True)

EXCEPTION_DEBUG_EVENT, CREATE_THREAD_DEBUG_EVENT, CREATE_PROCESS_DEBUG_EVENT = 1, 2, 3
EXIT_PROCESS_DEBUG_EVENT, LOAD_DLL_DEBUG_EVENT = 5, 6
DBG_CONTINUE, DBG_EXCEPTION_NOT_HANDLED = 0x00010002, 0x80010001
ACCESS_VIOLATION = 0xC0000005
BREAKPOINTS = (0x80000003, 0x4000001F, 0x80000004, 0x4000001E)
WOW64_CONTEXT_FULL = 0x00010007
THREAD_ALL = 0x1FFFFF

k32.OpenThread.restype = wt.HANDLE
k32.OpenThread.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
k32.ReadProcessMemory.argtypes = [wt.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t,
                                  ctypes.POINTER(ctypes.c_size_t)]
k32.Wow64GetThreadContext.argtypes = [wt.HANDLE, ctypes.c_void_p]
k32.CloseHandle.argtypes = [wt.HANDLE]
k32.WaitForDebugEvent.argtypes = [ctypes.c_void_p, wt.DWORD]
k32.ContinueDebugEvent.argtypes = [wt.DWORD, wt.DWORD, wt.DWORD]

CTX_NAMES = ("gs", "fs", "es", "ds", "edi", "esi", "ebx", "edx", "ecx", "eax",
             "ebp", "eip", "cs", "eflags", "esp", "ss")
CTX_OFF = 4 + 6 * 4 + 112       # ContextFlags, Dr0-3/6/7, FLOATING_SAVE_AREA
CTX_SIZE = CTX_OFF + 16 * 4 + 512
IMAGE_SIZE = [0xC00000]


def find_pid(name):
    class PE32(ctypes.Structure):
        _fields_ = [("dwSize", wt.DWORD), ("cntUsage", wt.DWORD), ("pid", wt.DWORD),
                    ("heap", ctypes.c_void_p), ("module", wt.DWORD), ("threads", wt.DWORD),
                    ("ppid", wt.DWORD), ("pri", wt.LONG), ("flags", wt.DWORD),
                    ("exe", ctypes.c_wchar * 260)]
    k32.CreateToolhelp32Snapshot.restype = wt.HANDLE
    snap = k32.CreateToolhelp32Snapshot(2, 0)
    e = PE32()
    e.dwSize = ctypes.sizeof(PE32)
    pid = None
    ok = k32.Process32FirstW(wt.HANDLE(snap), ctypes.byref(e))
    while ok:
        if e.exe.lower() == name.lower():
            pid = e.pid
        ok = k32.Process32NextW(wt.HANDLE(snap), ctypes.byref(e))
    k32.CloseHandle(snap)
    return pid


def read(hproc, addr, size):
    """Read up to `size` bytes; returns what is readable from `addr` (page by page)."""
    out = b""
    while size > 0:
        n = min(size, 0x1000 - (addr & 0xFFF))
        buf = ctypes.create_string_buffer(n)
        got = ctypes.c_size_t(0)
        if not k32.ReadProcessMemory(hproc, ctypes.c_void_p(addr), buf, n, ctypes.byref(got)):
            break
        out += buf.raw[:got.value]
        addr += n
        size -= n
    return out


def context(tid):
    h = k32.OpenThread(THREAD_ALL, False, tid)
    if not h:
        return None
    buf = ctypes.create_string_buffer(CTX_SIZE)
    struct.pack_into("<I", buf, 0, WOW64_CONTEXT_FULL)
    ok = k32.Wow64GetThreadContext(h, buf)
    k32.CloseHandle(h)
    if not ok:
        return None
    return dict(zip(CTX_NAMES, struct.unpack_from("<16I", buf, CTX_OFF)))


def dump_image(hproc, base, path):
    hdr = read(hproc, base, 0x1000)
    pe = struct.unpack_from("<I", hdr, 0x3C)[0]
    size = struct.unpack_from("<I", hdr, pe + 0x50)[0]        # SizeOfImage
    with open(path, "wb") as f:
        for off in range(0, size, 0x1000):
            page = read(hproc, base + off, 0x1000)
            f.write(page.ljust(0x1000, b"\0"))
    IMAGE_SIZE[0] = size
    return size


def capture(hproc, tid, base, extra):
    ctx = context(tid)
    rep = dict(extra, time=time.strftime("%H:%M:%S"), tid=tid, image_base=base, context=ctx)
    if ctx is None:
        return rep
    eip, esp = ctx["eip"], ctx["esp"]
    rep["code_start"] = eip - 0x400
    rep["code"] = read(hproc, eip - 0x400, 0x600).hex()
    stack = read(hproc, esp, 0x3000)
    rep["stack"] = stack.hex()
    # stack words that point into the image: candidate return addresses
    words = struct.unpack_from("<%dI" % (len(stack) // 4), stack)
    rep["stack_code_ptrs"] = [[i * 4, w] for i, w in enumerate(words)
                              if base + 0x1000 <= w < base + IMAGE_SIZE[0]][:300]
    rep["deref"] = {r: read(hproc, ctx[r], 0x100).hex()
                    for r in ("eax", "ebx", "ecx", "edx", "esi", "edi", "ebp")}
    return rep


def main(argv):
    name, out, snapshot = "FalloutNV.exe", os.path.join(HERE, "out", "crash"), False
    i = 0
    while i < len(argv):
        if argv[i] == "--name":
            name = argv[i + 1]; i += 1
        elif argv[i] == "--out":
            out = argv[i + 1]; i += 1
        elif argv[i] == "--snapshot":
            snapshot = True
        i += 1
    os.makedirs(out, exist_ok=True)
    pid = find_pid(name)
    if pid is None:
        print("process not found:", name)
        return 2
    if not k32.DebugActiveProcess(pid):
        print("DebugActiveProcess failed:", ctypes.get_last_error())
        return 2
    k32.DebugSetProcessKillOnExit(False)
    print("attached to %s pid %d" % (name, pid), flush=True)
    ev = ctypes.create_string_buffer(512)
    hproc, base, main_tid, count = None, 0, None, 0
    while True:
        if not k32.WaitForDebugEvent(ev, 1000):
            if snapshot and hproc:
                rep = capture(hproc, main_tid, base, {"kind": "snapshot"})
                json.dump(rep, open(os.path.join(out, "snapshot.json"), "w"))
                print("snapshot written; eip %08X" % rep["context"]["eip"])
                break
            continue
        code, epid, tid = struct.unpack_from("<III", ev, 0)
        status = DBG_CONTINUE
        if code == CREATE_PROCESS_DEBUG_EVENT:
            hfile, hproc, _hthread, base = struct.unpack_from("<4Q", ev, 16)
            main_tid = tid
            if hfile:
                k32.CloseHandle(hfile)
            size = dump_image(hproc, base, os.path.join(out, "image.bin"))
            print("image base %08X size %X dumped" % (base, size), flush=True)
        elif code == LOAD_DLL_DEBUG_EVENT:
            hfile = struct.unpack_from("<Q", ev, 16)[0]
            if hfile:
                k32.CloseHandle(hfile)
        elif code == EXCEPTION_DEBUG_EVENT:
            exc, _flags, _rec, addr, nparams = struct.unpack_from("<IIQQI", ev, 16)
            info = struct.unpack_from("<4Q", ev, 48)
            first = struct.unpack_from("<I", ev, 168)[0]
            if exc not in BREAKPOINTS:
                status = DBG_EXCEPTION_NOT_HANDLED
            if exc == ACCESS_VIOLATION:
                count += 1
                rep = capture(hproc, tid, base, {
                    "kind": "access_violation", "first_chance": bool(first), "address": addr,
                    "access": info[0], "target": info[1]})
                path = os.path.join(out, "av_%02d_%s.json" % (count, "first" if first else "second"))
                json.dump(rep, open(path, "w"))
                print("%s AV at %08X (%s %08X) %s chance -> %s"
                      % (rep["time"], addr, ("read", "write", "?", "?", "?", "?", "?", "?", "exec")[min(info[0], 8)],
                         info[1], "first" if first else "second", os.path.basename(path)), flush=True)
                if not first or count >= 20:
                    # image again: the heap state is gone but code/data of the module are useful
                    dump_image(hproc, base, os.path.join(out, "image_at_crash.bin"))
                    k32.ContinueDebugEvent(epid, tid, status)
                    break
        elif code == EXIT_PROCESS_DEBUG_EVENT:
            print("process exited", flush=True)
            k32.ContinueDebugEvent(epid, tid, status)
            return 0
        k32.ContinueDebugEvent(epid, tid, status)
    k32.DebugActiveProcessStop(pid)
    print("detached", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

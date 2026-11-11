"""Extract the ObScript command table from FalloutNV.exe (v1.4.0.525) to tools/out/commands.json.

Read-only: the executable is only opened for reading.

CommandInfo (40 bytes, little-endian, verified against the binary):
    +0  const char* longName
    +4  const char* shortName      (0 when absent)
    +8  uint32      opcode
    +12 const char* helpText
    +16 uint16      needsParent
    +18 uint16      numParams
    +20 ParamInfo*  params         (array of numParams entries, 12 bytes each)
    +24 void*       execute
    +28 void*       parse
    +32 void*       eval
    +36 uint32      flags
ParamInfo (12 bytes): const char* typeStr; uint32 typeID; uint32 isOptional.

The table starts at the entry for "GameMode" (block type 0x00) and ends at the
sentinel "ADD NEW FUNCTIONS BEFORE THIS ONE!!!" (opcode 0x1280).

Usage:  py -I extract_commands.py
"""
import json
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
EXE = r"C:\Program Files (x86)\Steam\steamapps\common\Fallout New Vegas\FalloutNV.exe"
OUT = os.path.join(HERE, "out", "commands.json")
IMAGE_BASE = 0x400000
TABLE_START_VA = 0x5C0000  # placeholder, replaced by search below
SENTINEL = "ADD NEW FUNCTIONS BEFORE THIS ONE!!!"
ENTRY = struct.Struct("<IIIIHHIIIII")
PARAM = struct.Struct("<III")


class Pe:
    def __init__(self, data):
        self.d = data
        pe = struct.unpack_from("<I", data, 0x3C)[0]
        nsec = struct.unpack_from("<H", data, pe + 6)[0]
        optsz = struct.unpack_from("<H", data, pe + 20)[0]
        so = pe + 24 + optsz
        self.secs = []
        for i in range(nsec):
            name, vsz, va, rsz, rptr = struct.unpack_from("<8sIIII", data, so + 40 * i)
            self.secs.append((va, vsz, rptr, rsz))

    def off(self, va):
        rva = va - IMAGE_BASE
        if rva < 0:
            return None
        for v, vs, rp, rs in self.secs:
            n = min(vs, rs)
            if v <= rva < v + n:
                return rp + (rva - v)
        return None

    def cstr(self, va):
        if va == 0:
            return ""
        o = self.off(va)
        if o is None:
            return None
        e = self.d.find(b"\0", o, o + 512)
        if e < 0:
            return None
        s = self.d[o:e]
        if not all(32 <= c < 127 for c in s):
            return None
        return s.decode("latin-1")


def main():
    data = open(EXE, "rb").read()
    pe = Pe(data)
    # Locate the table by its first entry: GameMode (opcode 0) whose params are 0 and whose
    # shortName is empty, found through the GameMode string pointer.
    gm = data.find(b"GameMode\0")
    gm_va = IMAGE_BASE + pe.secs[0][0] + 0  # fallback unused
    # Find the VA of the "GameMode" string, then the entry pointing at it.
    gm_off = gm
    gm_va = None
    for v, vs, rp, rs in pe.secs:
        if rp <= gm_off < rp + rs:
            gm_va = IMAGE_BASE + v + (gm_off - rp)
            break
    ptr = struct.pack("<I", gm_va)
    hits = []
    i = data.find(ptr)
    while i != -1:
        hits.append(i)
        i = data.find(ptr, i + 1)
    start = None
    for h in hits:
        if ENTRY.unpack_from(data, h)[2] == 0 and ENTRY.unpack_from(data, h)[0] == gm_va:
            start = h
            break
    if start is None:
        raise SystemExit("could not locate the GameMode command entry")

    entries = []
    off = start
    while True:
        ln, sn, opc, helpt, needp, npar, params, ex, parse, ev, flags = ENTRY.unpack_from(data, off)
        name = pe.cstr(ln)
        if not name:
            raise SystemExit("bad longName at %#x" % off)
        if name == SENTINEL:
            break
        short = pe.cstr(sn) if sn else ""
        if short is None:
            raise SystemExit("bad shortName at %#x" % off)
        plist = []
        if npar:
            po = pe.off(params)
            for k in range(npar):
                t, tid, opt = PARAM.unpack_from(data, po + 12 * k)
                tname = pe.cstr(t)
                plist.append({"type": tname, "typeId": tid, "optional": bool(opt)})
        entries.append({
            "name": name,
            "shortName": short or None,
            "opcode": opc,
            "needsParent": bool(needp),
            "numParams": npar,
            "params": plist,
            "flags": flags,
            "executeVA": ex,
            "file_offset": off,
        })
        off += ENTRY.size
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump({"exe": "FalloutNV.exe v1.4.0.525", "imageBase": IMAGE_BASE,
                   "tableStartFileOffset": start, "count": len(entries),
                   "commands": entries}, f, indent=1)
    print("wrote %d commands to %s" % (len(entries), OUT))


if __name__ == "__main__":
    main()

"""Read-only reader for Bethesda BSA archives (version 104: Fallout 3 / Fallout New Vegas).

Usage:
    py -I bsalib.py <archive.bsa> --count
    py -I bsalib.py <archive.bsa> --list [prefix]
    py -I bsalib.py <archive.bsa> --extract <path-in-archive> <out-file>

Library:
    from bsalib import BSA
    with BSA(path) as a:
        a.list_files()        -> list of lowercased backslash paths
        a.read("meshes\\foo.nif") -> bytes (decompressed)
"""

import os
import struct
import sys
import zlib

BSA_MAGIC = b"BSA\x00"

FLAG_DIR_NAMES = 0x001
FLAG_FILE_NAMES = 0x002
FLAG_COMPRESSED = 0x004
FLAG_EMBED_NAMES = 0x100

SIZE_COMPRESS_TOGGLE = 0x40000000
SIZE_MASK = 0x3FFFFFFF


class BSAError(Exception):
    pass


def _norm(path):
    return path.replace("/", "\\").strip("\\").lower()


class _FileEntry:
    __slots__ = ("path", "folder", "name", "size_raw", "offset", "compressed")

    def __init__(self, path, folder, name, size_raw, offset, compressed):
        self.path = path
        self.folder = folder
        self.name = name
        self.size_raw = size_raw
        self.offset = offset
        self.compressed = compressed


class BSA:
    def __init__(self, path):
        self.path = path
        self._f = open(path, "rb")
        self._parse()

    def close(self):
        self._f.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # ------------------------------------------------------------------
    def _parse(self):
        f = self._f
        hdr = f.read(36)
        if len(hdr) != 36 or hdr[:4] != BSA_MAGIC:
            raise BSAError("not a BSA file: %s" % self.path)
        (magic, version, dir_offset, arc_flags, folder_count, file_count,
         total_folder_names, total_file_names, file_flags) = struct.unpack("<4s8I", hdr)
        if version != 104:
            raise BSAError("unsupported BSA version %d (only 104 supported)" % version)
        self.version = version
        self.arc_flags = arc_flags
        self.folder_count = folder_count
        self.file_count = file_count
        self.total_folder_names = total_folder_names
        self.total_file_names = total_file_names
        self.file_flags = file_flags
        self.default_compressed = bool(arc_flags & FLAG_COMPRESSED)
        self.embed_names = bool(arc_flags & FLAG_EMBED_NAMES)
        self.has_dir_names = bool(arc_flags & FLAG_DIR_NAMES)
        self.has_file_names = bool(arc_flags & FLAG_FILE_NAMES)

        # Folder records: hash u64, count u32, offset u32 (16 bytes each).
        f.seek(dir_offset)
        raw_folders = f.read(16 * folder_count)
        folders = []
        for i in range(folder_count):
            h, count, off = struct.unpack_from("<QII", raw_folders, i * 16)
            # Offset stored as absolute offset + totalFileNameLength.
            folders.append((h, count, off - total_file_names))

        # Per folder: optional bzstring name, then `count` file records.
        self._folder_info = []  # (folder_name, [(hash, size, offset), ...])
        rec_ends = []
        for h, count, off in folders:
            f.seek(off)
            folder_name = ""
            pos = off
            if self.has_dir_names:
                ln = struct.unpack("<B", f.read(1))[0]
                raw = f.read(ln)
                folder_name = raw.rstrip(b"\x00").decode("latin-1")
                pos = off + 1 + ln
            f.seek(pos)
            recs_raw = f.read(16 * count)
            recs = []
            for j in range(count):
                fh, fsize, foff = struct.unpack_from("<QII", recs_raw, j * 16)
                recs.append((fh, fsize, foff))
            self._folder_info.append((folder_name, recs))
            rec_ends.append(pos + 16 * count)

        # File names block follows the last record block.
        names_start = max(rec_ends) if rec_ends else dir_offset + 16 * folder_count
        names = []
        if self.has_file_names:
            f.seek(names_start)
            blob = f.read(total_file_names)
            names = blob.split(b"\x00")
        self.entries = {}
        self._order = []
        name_i = 0
        for folder_name, recs in self._folder_info:
            for fh, fsize, foff in recs:
                if self.has_file_names:
                    fname = names[name_i].decode("latin-1")
                    name_i += 1
                else:
                    fname = "%016x" % fh
                full = _norm((folder_name + "\\" + fname) if folder_name else fname)
                compressed = self.default_compressed
                if fsize & SIZE_COMPRESS_TOGGLE:
                    compressed = not compressed
                size_raw = fsize & SIZE_MASK
                e = _FileEntry(full, folder_name, fname, size_raw, foff, compressed)
                self.entries[full] = e
                self._order.append(full)

    # ------------------------------------------------------------------
    def list_files(self):
        return list(self._order)

    def __contains__(self, path):
        return _norm(path) in self.entries

    def read(self, path):
        e = self.entries.get(_norm(path))
        if e is None:
            raise KeyError(path)
        f = self._f
        f.seek(e.offset)
        data = f.read(e.size_raw)
        if self.embed_names:
            ln = data[0]
            data = data[1 + ln:]
        if e.compressed:
            orig = struct.unpack_from("<I", data, 0)[0]
            out = zlib.decompress(data[4:])
            if len(out) != orig:
                raise BSAError("size mismatch for %s: %d != %d" % (path, len(out), orig))
            return out
        return data


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 1
    archive = argv[0]
    cmd = argv[1]
    with BSA(archive) as a:
        if cmd == "--count":
            print("archive: %s" % archive)
            print("version=%d flags=0x%x folders=%d files=%d" %
                  (a.version, a.arc_flags, a.folder_count, a.file_count))
            print("files indexed: %d" % len(a._order))
            return 0
        if cmd == "--list":
            prefix = _norm(argv[2]) if len(argv) > 2 else ""
            for p in a.list_files():
                if p.startswith(prefix):
                    print(p)
            return 0
        if cmd == "--extract":
            data = a.read(argv[2])
            with open(argv[3], "wb") as out:
                out.write(data)
            print("wrote %d bytes to %s" % (len(data), argv[3]))
            return 0
    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

"""esplib: lazy, streaming reader for TES4-format plugins (Fallout 3 / Fallout: New Vegas).

Standard library only. The plugin file is memory-mapped read-only. Nothing is
decompressed or copied until a caller asks for it, and whole groups can be
skipped without touching their contents (walk(descend=...)).

Every Record and Group keeps its file offset, so raw() returns the exact bytes
as stored (still compressed if the record is compressed) for verbatim copying.

Format assumptions (verified against FalloutNV.esm by dump_store.py):
  record header : 24 bytes  <4sIIIIHH  type, dataSize, flags, formID, vcInfo, formVersion, unknown
  group header  : 24 bytes  <4sI4siII  'GRUP', groupSize (includes header), label, groupType, stamp, unknown
  subrecord     : 6-byte header <4sH   type, size, data.  'XXXX' (size 4) gives the size of the NEXT subrecord
  compressed    : flag 0x00040000; payload = uint32 decompressed size + zlib stream
"""

import mmap
import struct
import zlib
from collections import namedtuple

RECORD_HEADER = struct.Struct("<4sIIIIHH")
GROUP_HEADER = struct.Struct("<4sI4siII")
SUB_HEADER = struct.Struct("<4sH")
COMPRESSED = 0x00040000
INTERIOR_CELL_FLAG = 0x0001

GROUP_TOP = 0
GROUP_WORLD_CHILDREN = 1
GROUP_INTERIOR_CELL_BLOCK = 2
GROUP_INTERIOR_SUB_BLOCK = 3
GROUP_EXTERIOR_CELL_BLOCK = 4
GROUP_EXTERIOR_SUB_BLOCK = 5
GROUP_CELL_CHILDREN = 6
GROUP_TOPIC_CHILDREN = 7
GROUP_PERSISTENT_CHILDREN = 8
GROUP_TEMPORARY_CHILDREN = 9
GROUP_VISIBLE_DISTANT_CHILDREN = 10

GROUP_TYPE_NAMES = {
    0: "top",
    1: "world_children",
    2: "interior_cell_block",
    3: "interior_sub_block",
    4: "exterior_cell_block",
    5: "exterior_sub_block",
    6: "cell_children",
    7: "topic_children",
    8: "persistent_children",
    9: "temporary_children",
    10: "visible_distant_children",
}

Subrecord = namedtuple("Subrecord", "type data offset")


class FormatError(Exception):
    pass


def iter_subrecords(payload):
    """Yield Subrecord(type:str, data:bytes, offset:int) from a decompressed record payload."""
    pos = 0
    n = len(payload)
    override = None
    while pos + 6 <= n:
        sig, size = SUB_HEADER.unpack_from(payload, pos)
        start = pos
        pos += 6
        if sig == b"XXXX" and size == 4:
            override = struct.unpack_from("<I", payload, pos)[0]
            pos += 4
            continue
        if override is not None:
            size = override
            override = None
        if pos + size > n:
            raise FormatError("subrecord %r at %d overruns record payload" % (sig, start))
        yield Subrecord(sig.decode("latin-1"), payload[pos:pos + size], start)
        pos += size


class Record:
    """A record header plus its location. Payload is read lazily."""

    __slots__ = ("plugin", "offset", "sig", "data_size", "flags", "form_id",
                 "vc_info", "form_version", "unknown", "path")
    kind = "record"

    def __init__(self, plugin, offset, sig, data_size, flags, form_id, vc_info,
                 form_version, unknown, path):
        self.plugin = plugin
        self.offset = offset
        self.sig = sig
        self.data_size = data_size
        self.flags = flags
        self.form_id = form_id
        self.vc_info = vc_info
        self.form_version = form_version
        self.unknown = unknown
        self.path = path  # tuple of enclosing Group objects, outermost first

    def __repr__(self):
        return "<Record %s %08X @%d>" % (self.sig, self.form_id, self.offset)

    @property
    def total_size(self):
        return RECORD_HEADER.size + self.data_size

    @property
    def is_compressed(self):
        return bool(self.flags & COMPRESSED)

    def raw(self):
        """Header + payload exactly as stored in the file (compressed stays compressed)."""
        return self.plugin.raw(self.offset, self.total_size)

    def payload(self):
        """Decompressed payload (the bytes after the 24-byte header)."""
        raw = self.plugin.raw(self.offset + RECORD_HEADER.size, self.data_size)
        if self.flags & COMPRESSED:
            (size,) = struct.unpack_from("<I", raw, 0)
            body = raw[4:]
            try:
                out = zlib.decompress(body)
            except zlib.error:
                # FalloutNV.esm has records (e.g. LAND 00150FC0) whose zlib Adler-32 trailer
                # is wrong while the deflate data is intact. Fall back to raw deflate
                # after the 2-byte zlib header; the declared size still has to match.
                out = zlib.decompressobj(-15).decompress(body[2:])
            if len(out) != size:
                raise FormatError("%r: inflated %d bytes, header says %d" % (self, len(out), size))
            return out
        return raw

    def subrecords(self):
        return list(iter_subrecords(self.payload()))

    def get(self, sig):
        """First subrecord data with this type, or None."""
        for sub in iter_subrecords(self.payload()):
            if sub.type == sig:
                return sub.data
        return None

    def get_all(self, sig):
        return [sub.data for sub in iter_subrecords(self.payload()) if sub.type == sig]

    def edid(self):
        data = self.get("EDID")
        if data is None:
            return None
        return data.rstrip(b"\x00").decode("latin-1")

    def path_description(self):
        return [g.describe() for g in self.path]


class Group:
    """A GRUP header plus its location. Children are read lazily via children()."""

    __slots__ = ("plugin", "offset", "size", "label", "group_type", "stamp", "unknown", "path")
    kind = "group"

    def __init__(self, plugin, offset, size, label, group_type, stamp, unknown, path):
        self.plugin = plugin
        self.offset = offset
        self.size = size
        self.label = label          # raw 4 bytes
        self.group_type = group_type
        self.stamp = stamp
        self.unknown = unknown
        self.path = path            # enclosing groups (not including self)

    def __repr__(self):
        return "<Group type=%d label=%s @%d size=%d>" % (
            self.group_type, self.label_text(), self.offset, self.size)

    @property
    def type_name(self):
        return GROUP_TYPE_NAMES.get(self.group_type, "type%d" % self.group_type)

    def label_value(self):
        """Decoded label: str for top groups, int for block/sub-block, (y, x) for exterior
        blocks, formID (int) for child groups."""
        t = self.group_type
        if t == GROUP_TOP:
            return self.label.decode("latin-1")
        if t in (GROUP_INTERIOR_CELL_BLOCK, GROUP_INTERIOR_SUB_BLOCK):
            return struct.unpack("<i", self.label)[0]
        if t in (GROUP_EXTERIOR_CELL_BLOCK, GROUP_EXTERIOR_SUB_BLOCK):
            y, x = struct.unpack("<hh", self.label)
            return (y, x)
        return struct.unpack("<I", self.label)[0]

    def label_text(self):
        t = self.group_type
        v = self.label_value()
        if t == GROUP_TOP:
            return v
        if t in (GROUP_EXTERIOR_CELL_BLOCK, GROUP_EXTERIOR_SUB_BLOCK):
            return "y=%d,x=%d" % v
        if t in (GROUP_INTERIOR_CELL_BLOCK, GROUP_INTERIOR_SUB_BLOCK):
            return str(v)
        return "0x%08X" % v

    def describe(self):
        return {"group_type": self.group_type,
                "type_name": self.type_name,
                "label": self.label_text()}

    def raw(self):
        return self.plugin.raw(self.offset, self.size)

    def children(self, descend=None):
        """Immediate-and-nested children (depth-first), like walk() but rooted here."""
        return self.plugin._walk(self.offset + GROUP_HEADER.size, self.offset + self.size,
                                 self.path + (self,), descend)


class Plugin:
    """Read-only view of a plugin file."""

    def __init__(self, path):
        self.path = path
        self._file = open(path, "rb")
        try:
            self._mm = mmap.mmap(self._file.fileno(), 0, access=mmap.ACCESS_READ)
        except Exception:
            self._file.close()
            raise
        self.size = len(self._mm)
        self._index = None
        self.duplicate_form_ids = 0
        header = self._item_at(0, ())
        if not isinstance(header, Record) or header.sig != "TES4":
            raise FormatError("%s does not start with a TES4 record" % path)
        self.header = header
        self.first_offset = header.total_size

    def close(self):
        try:
            self._mm.close()
        finally:
            self._file.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def raw(self, offset, length):
        """Copy of bytes [offset, offset+length) of the file."""
        return self._mm[offset:offset + length]

    def _item_at(self, off, path):
        mm = self._mm
        if off + 24 > self.size:
            raise FormatError("truncated header at %d" % off)
        if mm[off:off + 4] == b"GRUP":
            _, size, label, gtype, stamp, unknown = GROUP_HEADER.unpack_from(mm, off)
            if size < GROUP_HEADER.size or off + size > self.size:
                raise FormatError("bad GRUP size %d at %d" % (size, off))
            return Group(self, off, size, label, gtype, stamp, unknown, path)
        sig, dsize, flags, fid, vc, fv, unk = RECORD_HEADER.unpack_from(mm, off)
        if off + 24 + dsize > self.size:
            raise FormatError("record %r at %d overruns file" % (sig, off))
        return Record(self, off, sig.decode("latin-1"), dsize, flags, fid, vc, fv, unk, path)

    def _walk(self, start, end, path, descend):
        off = start
        while off < end:
            item = self._item_at(off, path)
            yield item
            if item.kind == "group":
                if descend is None or descend(item):
                    yield from self._walk(off + GROUP_HEADER.size, off + item.size,
                                          path + (item,), descend)
                off += item.size
            else:
                off += item.total_size

    def top_groups(self):
        """Yield the top-level GRUP headers (contents are skipped)."""
        off = self.first_offset
        while off < self.size:
            item = self._item_at(off, ())
            if item.kind != "group":
                raise FormatError("top-level record at %d (expected GRUP)" % off)
            yield item
            off += item.size

    def walk(self, descend=None):
        """Yield every Group and Record in file order. descend(group) -> False skips a group's contents."""
        return self._walk(self.first_offset, self.size, (), descend)

    def records(self, sig=None, top=None, descend=None):
        """Yield Records, optionally only of type `sig` and/or inside top group `top` (e.g. 'CELL')."""
        if top is None:
            source = self.walk(descend)
        else:
            source = self.walk_top(top, descend)
        for item in source:
            if item.kind == "record" and (sig is None or item.sig == sig):
                yield item

    def walk_top(self, top, descend=None):
        """Like walk() but only inside the top GRUP whose label is `top` (e.g. 'CELL', 'WRLD')."""
        for g in self.top_groups():
            if g.group_type == GROUP_TOP and g.label == top.encode("latin-1"):
                yield g
                yield from self._walk(g.offset + GROUP_HEADER.size, g.offset + g.size, (g,), descend)

    def build_index(self):
        """Scan the whole file once and map formID -> Record (first occurrence wins)."""
        index = {}
        dups = 0
        for item in self.walk():
            if item.kind == "record":
                if item.form_id in index:
                    dups += 1
                else:
                    index[item.form_id] = item
        self._index = index
        self.duplicate_form_ids = dups
        return index

    def get_record(self, form_id):
        """Record for a formID (builds the index on first use), or None."""
        if self._index is None:
            self.build_index()
        return self._index.get(form_id)

    def find_by_edid(self, edid, sig=None, top=None, ignore_case=False):
        """First record whose EDID equals `edid`, optionally restricted by type and/or top group."""
        want = edid.lower() if ignore_case else edid
        for rec in self.records(sig=sig, top=top):
            name = rec.edid()
            if name is None:
                continue
            if (name.lower() if ignore_case else name) == want:
                return rec
        return None

    def search_edid(self, substring, sig=None, top=None, ignore_case=True):
        """All records whose EDID contains `substring`."""
        needle = substring.lower() if ignore_case else substring
        out = []
        for rec in self.records(sig=sig, top=top):
            name = rec.edid()
            if name is None:
                continue
            hay = name.lower() if ignore_case else name
            if needle in hay:
                out.append(rec)
        return out


def decode_formid(data, offset=0):
    return struct.unpack_from("<I", data, offset)[0]


if __name__ == "__main__":
    import sys
    import time

    if len(sys.argv) != 2:
        print("usage: py esplib.py <plugin.esm>")
        sys.exit(2)
    t0 = time.time()
    with Plugin(sys.argv[1]) as plugin:
        print("TES4 header: %d bytes of payload" % plugin.header.data_size)
        for g in plugin.top_groups():
            print("top group %s size=%d" % (g.label_text(), g.size))
        counts = {}
        groups = 0
        for item in plugin.walk():
            if item.kind == "group":
                groups += 1
            else:
                counts[item.sig] = counts.get(item.sig, 0) + 1
        print("groups: %d" % groups)
        print("records: %d" % sum(counts.values()))
        for sig in sorted(counts):
            print("  %s %d" % (sig, counts[sig]))
    print("scan time: %.2fs" % (time.time() - t0))

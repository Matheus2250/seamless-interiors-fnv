"""scriptc: compiler for a subset of the Fallout: New Vegas ObScript language to SCDA bytecode.

Standard library only. Run with `py -I`.

Byte layout (learned from FalloutNV.esm SCPT records; see test_scriptc.py for the checks):

  SCDA = 1D 00 00 00                              script header (always first)
         { Begin block } ...                      one per Begin ... End
  statement record = opcode(u16) length(u16) data(length bytes)
    0x0010 Begin    length 6 (block declares no parameter, e.g. GameMode) or 6 + count(u16) [+ 'r' refIdx(u16)]
                    for blocks that declare a parameter (count 0 when none given, 1 when given).
                    data = blockType(u16) blkLen(u32) then the parameter part.
                    blkLen = bytes from the end of this Begin record to the next Begin (or end of SCDA).
                    A Begin parameter is registered in SCRO after its block body is compiled.
    0x0011 End      no data
    0x0015 set      target, exprLen(u16), expr
                    target = 'f'/'s' var(u16)   or   'r' refIdx(u16) + 's'/'f' var(u16)  (other script's var)
    0x0016 if       JmpOps(u16) exprLen(u16) expr
    0x0018 elseif   same as if
    0x0017 else     JmpOps(u16)
    0x0019 endif    no data
    0x001C ref prefix  refIdx(u16)  (4 bytes total; used only before a call) -- in this compiler the
                    prefix is folded into the call record it precedes, so it is emitted as part of the
                    call's bytes.
    0x001E return   no data
    other 0x1000+  function call: funcCode(u16) paramBytes(u16) [count(u16) params...]
                    count is present iff the command declares parameters (paramBytes > 0).

  JmpOps = number of statements in the branch body (nested if/elseif/else/endif markers count,
           a reference-prefixed call counts once).

  Ref indices: locals first, then SCRO.  A local ref variable has refIdx = its position among the
  ref variables (SCRV order); an SCRO entry k (1-based) has refIdx = nLocalRefs + k.

  Expressions are postfix (RPN). Every item begins with ' ' (0x20) except var/ref-member continuations:
    ' ' 'f'/'s' var(u16)      variable read (f = float or ref local, s = short/int/long local)
    ' ' 'Z' refIdx(u16)       object reference value (e.g. Player, a placed ref)
    ' ' 'r' refIdx(u16) 'X' funcCode(u16) paramBytes(u16) [count(u16) params]    ref.function
    ' ' 'X' funcCode paramBytes [count params]                                    function call
    ' ' text                  numeric literal (verbatim text) or operator (==, !=, <, >, <=, >=, &&, ||, +, -, *, /)
    ' ' '~'                   unary minus
  Parameters: axis 'X'/'Y'/'Z' (1 byte); float literal 'z'+double; float var 'f'+u16; int literal
  'n'+int32; int var 's'+u16; reference 'r'+u16; string u16(len)+text; u16 for enum-like types.
  Global variables in expressions: 'G' + 1-based SCRO index.
  ShowMessage and ShowWarning carry trailing padding bytes after their parameters (+6 / +2).

Known limits (see test_scriptc.py output): the GECK keeps reference tables from earlier compiles,
so SCRO order and the local-vs-SCRO numbering of references are partly history-dependent. The
compiler reproduces them for fresh first-use order; `scro_seed` supplies a stored SCRO order.
Operators: && and || share one precedence level (left to right); comparisons bind tighter.

Public API:
  compile_script(source, resolve, script_type=0, var_indices=None, var_resolver=None) -> dict
  build_subrecords(result) -> list of (type:str, data:bytes)
  annotate(scda, scro, scrv, vars_) -> list[str]   human-readable listing
"""

import json
import os
import re
import struct

HERE = os.path.dirname(os.path.abspath(__file__))
COMMANDS_PATH = os.path.join(HERE, "out", "commands.json")

PLAYER_FORMID = 0x14          # what the GECK stores in SCRO for both `player` and `PlayerRef`
USE_UNUSED_REFS = True        # see _Compiler: ref locals never referenced in code
BLOCK_MAX_OPCODE = 0x25       # block-type opcodes are 0x00..0x25 in the command table
FUNC_MIN_OPCODE = 0x1000      # script functions start here

OP_BEGIN = 0x0010
OP_END = 0x0011
OP_SET = 0x0015
OP_ELSE = 0x0017
OP_IF = 0x0016
OP_ELSEIF = 0x0018
OP_ENDIF = 0x0019
OP_REFCALL = 0x001C
OP_RETURN = 0x001E
OP_SCN = 0x001D

# Command parameter typeIds (from the GECK table)
T_STRING = 0
T_INTEGER = 1
T_FLOAT = 2
T_ACTOR_VALUE = 5
T_AXIS = 8
T_ANIM_GROUP = 10
T_SEX = 18
T_STAGE = 23

BINARY_OPS = {
    # && and || share one precedence level and associate left to right (checked against the oracle).
    "||": (1, b"||"),
    "&&": (1, b"&&"),
    "==": (3, b"=="), "!=": (3, b"!="), "<": (3, b"<"), ">": (3, b">"),
    "<=": (3, b"<="), ">=": (3, b">="),
    "+": (4, b"+"), "-": (4, b"-"),
    "*": (5, b"*"), "/": (5, b"/"),
}


class UnsupportedError(Exception):
    """Source uses a construct outside the supported subset."""


class CompileError(Exception):
    """Source is in the subset but is malformed or refers to something unknown."""


# ---------------------------------------------------------------------------
# Command table
# ---------------------------------------------------------------------------

def _load_commands():
    with open(COMMANDS_PATH, encoding="utf-8") as f:
        cmds = json.load(f)["commands"]
    funcs_by_name = {}
    for c in cmds:
        if c["opcode"] < FUNC_MIN_OPCODE:
            continue
        for key in (c["name"], c["shortName"]):
            if key:
                funcs_by_name.setdefault(key.lower(), c)
    blocks = {}
    for c in cmds:
        if c["opcode"] <= BLOCK_MAX_OPCODE and c["name"] not in ("Unused", "Unused2"):
            blocks.setdefault(c["name"].lower(), c["opcode"])
            if c["shortName"]:
                blocks.setdefault(c["shortName"].lower(), c["opcode"])
    by_opcode = {c["opcode"]: c for c in cmds}
    return funcs_by_name, blocks, by_opcode


FUNCS, BLOCKS, BY_OPCODE = _load_commands()
BLOCK_NAME = {v: k for k, v in BLOCKS.items()}


# ---------------------------------------------------------------------------
# Lexing
# ---------------------------------------------------------------------------

TOKEN_RE = re.compile(
    r"""\s*(?:
        (?P<num>\d+\.\d+|\d+)
       |(?P<str>"[^"]*")
       |(?P<id>[A-Za-z_][A-Za-z0-9_]*)
       |(?P<op>==|!=|<=|>=|&&|\|\||[<>+\-*/().~])
    )""",
    re.X,
)


def _strip_comment(line):
    out = []
    in_str = False
    for ch in line:
        if ch == '"':
            in_str = not in_str
        if ch == ";" and not in_str:
            break
        out.append(ch)
    return "".join(out)


def _tokenize(line):
    toks = []
    pos = 0
    line = line.rstrip()
    while pos < len(line):
        if line[pos].isspace():
            pos += 1
            continue
        m = TOKEN_RE.match(line, pos)
        if not m or m.end() == pos:
            raise CompileError("cannot tokenize %r" % line[pos:])
        pos = m.end()
        if m.group("num") is not None:
            toks.append(("num", m.group("num")))
        elif m.group("str") is not None:
            toks.append(("str", m.group("str")[1:-1]))
        elif m.group("id") is not None:
            toks.append(("id", m.group("id")))
        else:
            toks.append(("op", m.group("op")))
    return toks


def _lines(source):
    out = []
    for lineno, raw in enumerate(source.splitlines(), 1):
        toks = _tokenize(_strip_comment(raw))
        if toks:
            out.append((lineno, toks))
    return out


# ---------------------------------------------------------------------------
# Compiler
# ---------------------------------------------------------------------------

class _IfNode:
    def __init__(self, cond):
        self.branches = [("if", cond, [])]      # (kind, cond_bytes_or_None, body)


class _Block:
    def __init__(self, opcode, has_param, param_name):
        self.opcode = opcode
        self.has_param = has_param      # block type declares a parameter: count(u16) [+ ref]
        self.param_name = param_name    # identifier given after the block name, if any
        self.param_ref = None           # combined ref index, filled when the block ends
        self.body = []


def _count(items):
    """JmpOps-style count of a statement list (see module docstring)."""
    n = 0
    for it in items:
        if isinstance(it, bytes):
            n += 1
        else:  # _IfNode
            n += 1  # the if record
            for k, (kind, cond, body) in enumerate(it.branches):
                n += _count(body)
                if k > 0:
                    n += 1  # elseif / else marker
            n += 1  # endif
    return n


def _ser(items):
    out = bytearray()
    for it in items:
        if isinstance(it, bytes):
            out += it
            continue
        for k, (kind, cond, body) in enumerate(it.branches):
            jmp = _count(body)
            if kind == "if" or kind == "elseif":
                op = OP_IF if kind == "if" else OP_ELSEIF
                data = struct.pack("<HH", jmp, len(cond)) + cond
                out += struct.pack("<HH", op, len(data)) + data
            else:
                out += struct.pack("<HHH", OP_ELSE, 2, jmp)
            out += _ser(body)
        out += struct.pack("<HH", OP_ENDIF, 0)
    return bytes(out)


class _Compiler:
    def __init__(self, source, resolve, var_indices, var_resolver, script_type, scro_seed=None,
                 resolve_kind=None):
        self.source = source
        self.resolve = resolve
        self.resolve_kind = resolve_kind
        self.var_resolver = var_resolver
        self.script_type = script_type
        self.lines = _lines(source)
        self.scro = []                 # formIDs; first-use order unless seeded
        self.scro_index = {}           # formID -> 1-based index
        for formid in (scro_seed or []):
            if formid not in self.scro_index:
                self.scro.append(formid)
                self.scro_index[formid] = len(self.scro)
        self.vars = []                 # dicts: name, index, type ('float'|'ref'|'int')
        self.var_by_name = {}
        self._collect_decls(var_indices)
        self.ref_locals_all = sorted(
            [v for v in self.vars if v["kind"] == "ref"], key=lambda v: v["index"])
        # Ref locals that the code never mentions are not counted in the ref index space
        # (they still appear in SCRV). Set USE_UNUSED_REFS to False to count every ref local.
        used = self._names_used_in_code()
        if USE_UNUSED_REFS:
            self.local_refs = self.ref_locals_all
        else:
            self.local_refs = [v for v in self.ref_locals_all if v["name"].lower() in used]
        self.ref_pos = {v["name"].lower(): i + 1 for i, v in enumerate(self.local_refs)}
        self.scrv_all = [v["index"] for v in self.ref_locals_all]

    def _names_used_in_code(self):
        names = set()
        for _, toks in self.lines:
            if toks[0][0] == "id" and toks[0][1].lower() in ("short", "int", "long", "float", "ref", "scn", "scriptname"):
                continue
            for kind, val in toks:
                if kind == "id":
                    names.add(val.lower())
        return names

    # --- declarations ----------------------------------------------------
    def _collect_decls(self, var_indices):
        types = {"short": "int", "int": "int", "long": "int", "float": "float", "ref": "ref"}
        next_index = 1
        for lineno, toks in self.lines:
            if toks[0][0] == "id" and toks[0][1].lower() in ("scn", "scriptname"):
                continue
            if toks[0][0] == "id" and toks[0][1].lower() in types:
                if len(toks) != 2 or toks[1][0] != "id":
                    raise CompileError("line %d: bad declaration" % lineno)
                name = toks[1][1]
                key = name.lower()
                if key in self.var_by_name:
                    raise CompileError("line %d: %s declared twice" % (lineno, name))
                if var_indices is not None:
                    if key not in var_indices:
                        raise CompileError("var_indices has no entry for %s" % name)
                    idx = var_indices[key]
                else:
                    idx = next_index
                next_index = max(next_index, idx) + 1
                v = {"name": name, "index": idx, "kind": types[toks[0][1].lower()]}
                self.vars.append(v)
                self.var_by_name[key] = v

    def _ref_index_for_formid(self, formid):
        if formid not in self.scro_index:
            self.scro.append(formid)
            self.scro_index[formid] = len(self.scro)
        return len(self.local_refs) + self.scro_index[formid]

    def _edid_formid(self, name):
        low = name.lower()
        if low in ("player", "playerref"):
            return PLAYER_FORMID
        formid = self.resolve(name)
        if formid is None:
            raise CompileError("unresolved form %r" % name)
        return formid

    def _ref_of(self, name):
        """Return the combined refIdx for an identifier used as an object reference."""
        low = name.lower()
        if low in self.ref_pos:
            return self.ref_pos[low]
        if low in ("player", "playerref"):
            return self._ref_index_for_formid(PLAYER_FORMID)
        return self._ref_index_for_formid(self._edid_formid(name))

    def _var(self, name):
        v = self.var_by_name.get(name.lower())
        if v is None:
            return None
        return v

    # --- primitive encoders ----------------------------------------------
    @staticmethod
    def _u16(v):
        return struct.pack("<H", v)

    def _literal_int(self, text):
        if "." in text:
            raise UnsupportedError("integer parameter given float literal %r" % text)
        return int(text)

    # --- expressions (RPN) -----------------------------------------------
    def _expr(self, toks, pos, min_prec=0):
        lhs, pos = self._unary(toks, pos)
        while pos < len(toks) and toks[pos][0] == "op" and toks[pos][1] in BINARY_OPS:
            prec, optext = BINARY_OPS[toks[pos][1]]
            if prec < min_prec:
                break
            pos += 1
            rhs, pos = self._expr(toks, pos, prec + 1)
            lhs = lhs + rhs + b" " + optext
        return lhs, pos

    def _unary(self, toks, pos):
        if pos < len(toks) and toks[pos] == ("op", "-"):
            inner, pos = self._unary(toks, pos + 1)
            return inner + b" ~", pos
        return self._primary(toks, pos)

    def _primary(self, toks, pos):
        if pos >= len(toks):
            raise CompileError("unexpected end of expression")
        kind, val = toks[pos]
        if kind == "op" and val == "(":
            inner, pos = self._expr(toks, pos + 1, 0)
            if pos >= len(toks) or toks[pos] != ("op", ")"):
                raise CompileError("missing ')'")
            return inner, pos + 1
        if kind == "num":
            return b" " + val.encode("ascii"), pos + 1
        if kind != "id":
            raise CompileError("unexpected token %r" % (val,))
        name = val
        low = name.lower()
        nxt = toks[pos + 1] if pos + 1 < len(toks) else None
        is_member = nxt == ("op", ".")
        # object reference (local ref var, player, or form)
        if is_member:
            ref_idx = self._ref_of(name)
            cmd_tok = toks[pos + 2] if pos + 2 < len(toks) else None
            if cmd_tok is None or cmd_tok[0] != "id":
                raise CompileError("expected member name after '.'")
            cmd = FUNCS.get(cmd_tok[1].lower())
            if cmd is None:
                raise UnsupportedError("member %r is not a known function" % cmd_tok[1])
            body, pos = self._call_body(cmd, toks, pos + 3)
            return b" r" + self._u16(ref_idx) + body, pos
        v = self._var(name)
        if v is not None:
            if v["kind"] == "ref":
                return b" f" + self._u16(v["index"]), pos + 1
            letter = b" s" if v["kind"] == "int" else b" f"
            return letter + self._u16(v["index"]), pos + 1
        if low in ("player", "playerref"):
            return b" Z" + self._u16(self._ref_of(name)), pos + 1
        cmd = FUNCS.get(low)
        if cmd is not None:
            body, pos = self._call_body(cmd, toks, pos + 1)
            return b" " + body, pos
        if low in self.ref_pos:
            return b" Z" + self._u16(self._ref_of(name)), pos + 1
        formid = self._edid_formid(name)
        if self.resolve_kind is not None and self.resolve_kind(name) == "GLOB":
            # global variable: 'G' + 1-based SCRO index (no local-ref offset)
            self._ref_index_for_formid(formid)
            return b" G" + self._u16(self.scro_index[formid]), pos + 1
        return b" Z" + self._u16(self._ref_index_for_formid(formid)), pos + 1

    def _call_body(self, cmd, toks, pos):
        """Encode a function call starting after its name (no leading space)."""
        params = cmd["params"]
        args = bytearray()
        count = 0
        while count < len(params):
            if pos >= len(toks):
                break
            kind, val = toks[pos]
            if kind == "op" and val not in ("-",):
                break
            if kind == "op" and val == "-" and (pos + 1 >= len(toks) or toks[pos + 1][0] != "num"):
                break
            enc, pos = self._arg(params[count], toks, pos)
            args += enc
            count += 1
        if not params:
            if count:
                raise CompileError("%s takes no arguments" % cmd["name"])
            return b"X" + struct.pack("<HH", cmd["opcode"], 0), pos
        # The GECK writes a few commands with trailing bytes beyond their declared parameters
        # (observed in the oracle: ShowMessage +6, ShowWarning +2).
        if cmd["name"] == "ShowMessage":
            args += b"\0" * 6
        elif cmd["name"] == "ShowWarning":
            args += b"\0" * 2
        body = struct.pack("<H", count) + bytes(args)
        return b"X" + struct.pack("<HH", cmd["opcode"], len(body)) + body, pos

    def _arg(self, param, toks, pos):
        tid = param["typeId"]
        kind, val = toks[pos]
        neg = False
        if kind == "op" and val == "-":
            neg = True
            pos += 1
            kind, val = toks[pos]
        if tid == T_AXIS:
            if kind != "id" or val.lower() not in ("x", "y", "z"):
                raise CompileError("expected axis X/Y/Z, got %r" % val)
            return val.upper().encode("ascii"), pos + 1
        if tid == T_FLOAT:
            if kind == "num":
                return b"z" + struct.pack("<d", -float(val) if neg else float(val)), pos + 1
            if kind == "id" and self._var(val) is not None:
                v = self._var(val)
                if neg:
                    raise UnsupportedError("negated variable argument")
                return (b"f" if v["kind"] != "int" else b"s") + self._u16(v["index"]), pos + 1
            raise UnsupportedError("float argument %r" % val)
        if tid in (T_INTEGER, T_STAGE):
            if kind == "num":
                n = self._literal_int(val)
                return b"n" + struct.pack("<i", -n if neg else n), pos + 1
            if kind == "id" and self._var(val) is not None and not neg:
                v = self._var(val)
                if v["kind"] == "int":
                    return b"s" + self._u16(v["index"]), pos + 1
            raise UnsupportedError("integer argument %r" % val)
        if tid == T_STRING:
            if kind != "str":
                raise UnsupportedError("string argument expected")
            text = val.encode("cp1252")
            return struct.pack("<H", len(text)) + text, pos + 1
        if tid in (T_ACTOR_VALUE, T_ANIM_GROUP, T_SEX):
            if kind == "num" and not neg:
                return struct.pack("<H", int(val)), pos + 1
            raise UnsupportedError("%s argument %r needs an enum table" % (param["type"], val))
        # everything else is a form or object reference
        if kind == "id":
            v = self._var(val)
            if v is not None and v["kind"] != "ref":
                raise UnsupportedError("numeric variable passed to form parameter %r" % val)
            return b"r" + self._u16(self._ref_of(val)), pos + 1
        raise UnsupportedError("argument %r for %s" % (val, param["type"]))

    # --- statements -------------------------------------------------------
    def _set_stmt(self, toks):
        # set <target> to <expr>
        to_pos = None
        for i, t in enumerate(toks):
            if t[0] == "id" and t[1].lower() == "to":
                to_pos = i
                break
        if to_pos is None:
            raise CompileError("set without 'to'")
        tgt = toks[1:to_pos]
        if len(tgt) != 1 or tgt[0][0] != "id":
            if len(tgt) == 3 and tgt[1] == ("op", "."):
                raise UnsupportedError("assignment to another script's variable")
            raise CompileError("bad set target")
        v = self._var(tgt[0][1])
        if v is None:
            raise CompileError("unknown variable %r" % tgt[0][1])
        target = (b"s" if v["kind"] == "int" else b"f") + self._u16(v["index"])
        expr, pos = self._expr(toks, to_pos + 1, 0)
        if pos != len(toks):
            raise CompileError("trailing tokens in set")
        data = target + self._u16(len(expr)) + expr
        return struct.pack("<HH", OP_SET, len(data)) + data

    def _cond(self, toks):
        expr, pos = self._expr(toks, 0, 0)
        if pos != len(toks):
            raise CompileError("trailing tokens in condition")
        return expr

    def _call_stmt(self, toks):
        ref = None
        pos = 0
        if len(toks) >= 3 and toks[0][0] == "id" and toks[1] == ("op", "."):
            ref = toks[0][1]
            pos = 2
        if toks[pos][0] != "id":
            raise CompileError("expected a command")
        cmd = FUNCS.get(toks[pos][1].lower())
        if cmd is None:
            raise UnsupportedError("unknown or unsupported statement %r" % toks[pos][1])
        body, end = self._call_body(cmd, toks, pos + 1)
        if end != len(toks):
            raise CompileError("too many arguments to %s" % cmd["name"])
        # body is 'X' + funcCode + paramBytes + data; a statement record is funcCode + paramBytes + data
        prefix = b""
        if ref is not None:
            prefix = b"\x1c\x00" + self._u16(self._ref_of(ref))
        return prefix + body[1:]

    def compile(self):
        top = []
        stack = []      # entries: ("block", _Block) or ("if", _IfNode, body_list_ref)
        blocks = []
        cur = None      # current statement list

        def body_target():
            if not stack:
                raise CompileError("statement outside Begin/End")
            top = stack[-1]
            return top[1].body if top[0] == "block" else top[1]

        for lineno, toks in self.lines:
            try:
                head = toks[0][1].lower() if toks[0][0] == "id" else None
                if head in ("scn", "scriptname"):
                    continue
                if head in ("short", "int", "long", "float", "ref"):
                    continue
                if head == "begin":
                    if stack:
                        raise CompileError("nested Begin")
                    if len(toks) < 2 or toks[1][0] != "id":
                        raise CompileError("Begin without block name")
                    bname = toks[1][1].lower()
                    if bname not in BLOCKS:
                        raise UnsupportedError("unknown block %r" % toks[1][1])
                    opcode = BLOCKS[bname]
                    cmd = BY_OPCODE[opcode]
                    has_param = cmd["numParams"] > 0
                    if len(toks) > 2:
                        if len(toks) != 3 or toks[2][0] != "id" or not has_param:
                            raise UnsupportedError("Begin parameter")
                    blk = _Block(opcode, has_param, toks[2][1] if len(toks) > 2 else None)
                    blocks.append(blk)
                    stack.append(("block", blk))
                    continue
                if head == "end":
                    if not stack or stack[-1][0] != "block":
                        raise CompileError("End without Begin (line %d)" % lineno)
                    blk = stack.pop()[1]
                    # The GECK registers a Begin parameter after the block body has been compiled.
                    if blk.param_name is not None:
                        blk.param_ref = self._ref_of(blk.param_name)
                    continue
                if head == "if":
                    node = _IfNode(self._cond(toks[1:]))
                    body_target().append(node)
                    stack.append(("if", node.branches[0][2], node))
                    continue
                if head in ("elseif", "else"):
                    if not stack or stack[-1][0] != "if":
                        raise CompileError("%s without if (line %d)" % (head, lineno))
                    node = stack[-1][2]
                    if head == "elseif":
                        body = []
                        node.branches.append(("elseif", self._cond(toks[1:]), body))
                    else:
                        if len(toks) != 1:
                            raise CompileError("junk after else")
                        body = []
                        node.branches.append(("else", None, body))
                    stack[-1] = ("if", body, node)
                    continue
                if head == "endif":
                    if not stack or stack[-1][0] != "if":
                        raise CompileError("endif without if (line %d)" % lineno)
                    stack.pop()
                    continue
                if head == "return":
                    if len(toks) != 1:
                        raise CompileError("junk after return")
                    body_target().append(struct.pack("<HH", OP_RETURN, 0))
                    continue
                if head == "set":
                    body_target().append(self._set_stmt(toks))
                    continue
                body_target().append(self._call_stmt(toks))
            except UnsupportedError:
                raise
            except CompileError as e:
                raise CompileError("line %d: %s" % (lineno, e))
            except (IndexError, struct.error) as e:
                raise CompileError("line %d: malformed statement (%s)" % (lineno, e))
        if stack:
            raise CompileError("unterminated block or if")
        # Assemble
        out = bytearray(b"\x1d\x00\x00\x00")
        for blk in blocks:
            body = _ser(blk.body) + struct.pack("<HH", OP_END, 0)
            if blk.has_param:
                if blk.param_ref is None:
                    tail = struct.pack("<H", 0)                      # count = 0
                else:
                    tail = struct.pack("<H", 1) + b"r" + self._u16(blk.param_ref)
                data_len = 6 + len(tail)
                head = struct.pack("<HHHI", OP_BEGIN, data_len, blk.opcode, len(body)) + tail
            else:
                head = struct.pack("<HHHI", OP_BEGIN, 6, blk.opcode, len(body))
            out += head + body
        return bytes(out)


def compile_script(source, resolve, script_type=0, var_indices=None, var_resolver=None, scro_seed=None,
                   resolve_kind=None):
    """Compile ObScript source text.

    resolve(edid) -> formID or None   (called for form / object references by editor ID)
    var_indices   optional {lowercased var name: SLSD index}; default is declaration order 1..n
    var_resolver  unused by this subset (other-script variables are unsupported)
    scro_seed     optional list of formIDs placed first in SCRO, in order. The GECK keeps refs from
                  earlier compiles, so the stored SCRO order is not always first-use order; this
                  lets a caller reproduce a stored list. Unused seeded entries are kept.
    """
    c = _Compiler(source, resolve, var_indices, var_resolver, script_type, scro_seed, resolve_kind)
    scda = c.compile()
    name = None
    for _, toks in c.lines:
        if toks[0][0] == "id" and toks[0][1].lower() in ("scn", "scriptname") and len(toks) >= 2:
            name = toks[1][1]
            break
    vars_out = [(v["index"], v["name"], 1 if v["kind"] == "int" else 0) for v in c.vars]
    max_index = max([v["index"] for v in c.vars], default=0)
    scrv = list(c.scrv_all)
    return {
        "edid": name,
        "schr": struct.pack("<IIIIHH", 0, len(c.scro) + len(scrv), len(scda), max_index,
                            script_type, 1),
        "scda": scda,
        "sctx": source.encode("cp1252", errors="replace"),
        "vars": vars_out,
        "scro": list(c.scro),
        "scrv": scrv,
        "script_type": script_type,
    }


def build_subrecords(result):
    """(type, data) pairs in the order GECK SCPT records use."""
    out = [("EDID", (result["edid"] or "").encode("cp1252") + b"\0"),
           ("SCHR", result["schr"]),
           ("SCDA", result["scda"]),
           ("SCTX", result["sctx"])]
    for index, name, typ in result["vars"]:
        slsd = bytearray(24)
        struct.pack_into("<I", slsd, 0, index)
        slsd[16] = typ  # type flag; bytes 4..15 and 17..23 are uninitialised GECK memory (zeroed here)
        out.append(("SLSD", bytes(slsd)))
        out.append(("SCVR", name.encode("cp1252") + b"\0"))
    for idx in result["scrv"]:
        out.append(("SCRV", struct.pack("<I", idx)))
    for formid in result["scro"]:
        out.append(("SCRO", struct.pack("<I", formid)))
    return out


# ---------------------------------------------------------------------------
# Annotation (decoder used for reviewing output)
# ---------------------------------------------------------------------------

def _describe_ref(idx, nl, scro):
    if idx <= nl:
        return "local ref #%d" % idx
    k = idx - nl
    if k <= len(scro):
        return "SCRO[%d]=%08X" % (k, scro[k - 1])
    return "ref#%d(?)" % idx


def annotate(scda, scro=(), scrv=(), vars_=()):
    nl = len(scrv)
    out = []
    pos = 0
    b = scda
    names = {v[0]: v[1] for v in vars_}
    def vname(i):
        return "%s(%d)" % (names.get(i, "?"), i)

    def cmd_text(fc, pb, body):
        cmd = BY_OPCODE.get(fc)
        nm = cmd["name"] if cmd else "?"
        parts = []
        if pb:
            cnt = struct.unpack_from("<H", body, 0)[0]
            parts.append("count=%d" % cnt)
            p = 2
            params = cmd["params"] if cmd else []
            for k in range(cnt):
                ptype = params[k]["type"] if k < len(params) else "?"
                c = body[p]
                if c == 0x72:
                    parts.append("%s=%s" % (ptype, _describe_ref(struct.unpack_from("<H", body, p + 1)[0], nl, scro)))
                    p += 3
                elif c == 0x66:
                    parts.append("%s=float %s" % (ptype, vname(struct.unpack_from("<H", body, p + 1)[0])))
                    p += 3
                elif c == 0x73:
                    parts.append("%s=short %s" % (ptype, vname(struct.unpack_from("<H", body, p + 1)[0])))
                    p += 3
                elif c == 0x6E:
                    parts.append("%s=%d" % (ptype, struct.unpack_from("<i", body, p + 1)[0]))
                    p += 5
                elif c == 0x7A:
                    parts.append("%s=%g" % (ptype, struct.unpack_from("<d", body, p + 1)[0]))
                    p += 9
                elif c in (0x58, 0x59, 0x5A):
                    parts.append("%s=%s" % (ptype, chr(c)))
                    p += 1
                else:
                    parts.append("%s=raw %s" % (ptype, body[p:p + 2].hex()))
                    p += 2
        return "%s(%04X) %s" % (nm, fc, " ".join(parts))

    while pos + 4 <= len(b):
        op = struct.unpack_from("<H", b, pos)[0]
        if op == OP_REFCALL:
            idx = struct.unpack_from("<H", b, pos + 2)[0]
            out.append("%04X: ref prefix -> %s" % (pos, _describe_ref(idx, nl, scro)))
            pos += 4
            continue
        ln = struct.unpack_from("<H", b, pos + 2)[0]
        data = b[pos + 4:pos + 4 + ln]
        if op == OP_SCN:
            out.append("%04X: scn header" % pos)
        elif op == OP_BEGIN:
            bt = struct.unpack_from("<H", data, 0)[0]
            blk = struct.unpack_from("<I", data, 2)[0]
            bcmd = BY_OPCODE.get(bt)
            extra = ""
            if ln >= 8:
                extra = " param=%s" % _annot_param(data[6:], nl, scro)
            out.append("%04X: Begin %s (type %02X) blkLen=%d%s" % (pos, bcmd["name"] if bcmd else "?", bt, blk, extra))
        elif op == OP_END:
            out.append("%04X: End" % pos)
        elif op == OP_ENDIF:
            out.append("%04X: endif" % pos)
        elif op == OP_RETURN:
            out.append("%04X: return" % pos)
        elif op == OP_ELSE:
            out.append("%04X: else (JmpOps=%d)" % (pos, struct.unpack_from("<H", data, 0)[0]))
        elif op in (OP_IF, OP_ELSEIF):
            jmp, el = struct.unpack_from("<HH", data, 0)
            out.append("%04X: %s (JmpOps=%d) expr[%s]" % (pos, "if" if op == OP_IF else "elseif", jmp,
                                                       _annot_expr(data[4:4 + el], nl, scro, names, cmd_text)))
        elif op == OP_SET:
            t = 0
            if data[0] == 0x72:
                tgt = "%s.%s" % (_describe_ref(struct.unpack_from("<H", data, 1)[0], nl, scro),
                                 vname(struct.unpack_from("<H", data, 4)[0]))
                t = 6
            else:
                tgt = vname(struct.unpack_from("<H", data, 1)[0])
                t = 3
            el = struct.unpack_from("<H", data, t)[0]
            out.append("%04X: set %s := expr[%s]" % (pos, tgt,
                                                     _annot_expr(data[t + 2:t + 2 + el], nl, scro, names, cmd_text)))
        elif op >= FUNC_MIN_OPCODE:
            out.append("%04X: %s" % (pos, cmd_text(op, ln, data)))
        else:
            out.append("%04X: opcode %04X len=%d" % (pos, op, ln))
        pos += 4 + ln
    return out


def _annot_expr(e, nl, scro, names, cmd_text):
    toks = []
    i = 0
    while i < len(e):
        c = e[i]
        if c == 0x20:
            d = e[i + 1]
            if d == 0x58:
                fc, pb = struct.unpack_from("<HH", e, i + 2)
                toks.append(cmd_text(fc, pb, e[i + 6:i + 6 + pb]))
                i += 6 + pb
            elif d == 0x72:
                idx = struct.unpack_from("<H", e, i + 2)[0]
                i += 4
                if i < len(e) and e[i] == 0x58:
                    fc, pb = struct.unpack_from("<HH", e, i + 1)
                    toks.append("[%s].%s" % (_describe_ref(idx, nl, scro), cmd_text(fc, pb, e[i + 5:i + 5 + pb])))
                    i += 5 + pb
                elif i < len(e) and e[i] in (0x66, 0x73):
                    v = struct.unpack_from("<H", e, i + 1)[0]
                    toks.append("[%s].var%d" % (_describe_ref(idx, nl, scro), v))
                    i += 3
                else:
                    toks.append("ref(%s)" % _describe_ref(idx, nl, scro))
            elif d == 0x5A:
                toks.append("ref(%s)" % _describe_ref(struct.unpack_from("<H", e, i + 2)[0], nl, scro))
                i += 4
            elif d in (0x66, 0x73):
                toks.append("var %s" % vname_of(names, struct.unpack_from("<H", e, i + 2)[0]))
                i += 4
            else:
                j = i + 1
                while j < len(e) and e[j] != 0x20:
                    j += 1
                toks.append(e[i + 1:j].decode("latin-1"))
                i = j
        elif c in (0x66, 0x73):
            toks.append("var %s" % vname_of(names, struct.unpack_from("<H", e, i + 1)[0]))
            i += 3
        else:
            toks.append("?%02X" % c)
            i += 1
    return " ".join(toks)


def vname_of(names, i):
    return "%s(%d)" % (names.get(i, "?"), i)


def _annot_param(b, nl, scro):
    """Begin parameter block: count(u16) then optional 'r' + refIdx(u16)."""
    if len(b) < 2:
        return "?"
    cnt = struct.unpack_from("<H", b, 0)[0]
    if cnt == 0:
        return "none"
    if len(b) >= 5 and b[2] == 0x72:
        return _describe_ref(struct.unpack_from("<H", b, 3)[0], nl, scro)
    return "raw %s" % b[2:].hex(" ")

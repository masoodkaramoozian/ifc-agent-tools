"""A small, dependency-free parser for IFC-SPF (ISO 10303-21) files.

It turns ``#12=IFCWALL('2O2Fr$t4X7Zf8NOew3FLOH',$,'Wall',...);`` into
``Entity(id=12, type='IFCWALL', args=[...])`` where arguments are plain Python
values:

=================  ==============================
STEP token         Python value
=================  ==============================
``$``              ``None``
``*``              ``DERIVED``
``#12``            ``Ref(12)``
``.T.`` / ``.F.``  ``True`` / ``False``
``.ENUM.``         ``Enum('ENUM')``
``'text'``         ``str`` (with \\X2\\ decoding)
``1.5`` / ``3``    ``float`` / ``int``
``(a,b)``          ``list``
``IFCLABEL('x')``  ``Typed('IFCLABEL', 'x')``
=================  ==============================
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Ref:
    id: int

    def __repr__(self) -> str:
        return f"#{self.id}"


@dataclass(frozen=True)
class Enum:
    value: str

    def __repr__(self) -> str:
        return f".{self.value}."


@dataclass(frozen=True)
class Typed:
    type: str
    value: object


class _Derived:
    def __repr__(self) -> str:
        return "*"


DERIVED = _Derived()


@dataclass
class Entity:
    id: int
    type: str
    args: list = field(default_factory=list)

    def __getitem__(self, i):
        return self.args[i] if i < len(self.args) else None


class StepSyntaxError(ValueError):
    pass


_TOKEN = re.compile(
    r"""\s*(?:
        (?P<ref>\#\d+)
      | (?P<str>'(?:[^']|'')*')
      | (?P<enum>\.[A-Za-z0-9_]+\.)
      | (?P<num>[-+]?\d+(?:\.\d*)?(?:[eE][-+]?\d+)?)
      | (?P<null>\$)
      | (?P<der>\*)
      | (?P<kw>[A-Za-z_][A-Za-z0-9_]*)
      | (?P<punct>[(),=;])
      | (?P<bin>"[0-9A-Fa-f]*")
    )""",
    re.VERBOSE,
)
_COMMENT = re.compile(r"/\*.*?\*/", re.S)


def decode_string(raw: str) -> str:
    """Decode a STEP string literal body (without the outer quotes)."""
    s = raw.replace("''", "'")
    if "\\" not in s:
        return s
    out, i = [], 0
    while i < len(s):
        if s.startswith("\\X2\\", i):
            end = s.index("\\X0\\", i)
            hexs = s[i + 4:end]
            out.append("".join(chr(int(hexs[j:j + 4], 16)) for j in range(0, len(hexs), 4)))
            i = end + 4
        elif s.startswith("\\X4\\", i):
            end = s.index("\\X0\\", i)
            hexs = s[i + 4:end]
            out.append("".join(chr(int(hexs[j:j + 8], 16)) for j in range(0, len(hexs), 8)))
            i = end + 4
        elif s.startswith("\\X\\", i):
            out.append(bytes([int(s[i + 3:i + 5], 16)]).decode("latin-1"))
            i += 5
        elif s.startswith("\\S\\", i):
            out.append(chr(ord(s[i + 3]) + 128))
            i += 4
        elif s.startswith("\\P", i) and i + 3 < len(s) and s[i + 3] == "\\":
            i += 4  # code-page switch; ignored
        elif s.startswith("\\\\", i):
            out.append("\\")
            i += 2
        else:
            out.append(s[i])
            i += 1
    return "".join(out)


def _tokens(text: str):
    pos, n = 0, len(text)
    while pos < n:
        m = _TOKEN.match(text, pos)
        if not m:
            if text[pos:].strip() == "":
                return
            raise StepSyntaxError(f"unexpected input near: {text[pos:pos + 60]!r}")
        pos = m.end()
        kind = m.lastgroup
        yield kind, m.group(kind)


def parse_data(text: str) -> dict[int, Entity]:
    """Parse the DATA section of an SPF file into ``{id: Entity}``."""
    try:
        return _parse_data(text)
    except StopIteration:
        raise StepSyntaxError("unexpected end of file (truncated IFC?)") from None


def _parse_data(text: str) -> dict[int, Entity]:
    text = _COMMENT.sub("", text)
    start = text.find("DATA;")
    end = text.rfind("ENDSEC;")
    if start < 0:
        raise StepSyntaxError("no DATA section found")
    body = text[start + 5:end if end > start else len(text)]

    entities: dict[int, Entity] = {}
    it = _tokens(body)
    for kind, val in it:
        if kind != "ref":
            raise StepSyntaxError(f"expected entity id, got {val!r}")
        eid = int(val[1:])
        k, v = next(it)
        if v != "=":
            raise StepSyntaxError(f"expected '=' after #{eid}")
        k, name = next(it)
        if k == "punct" and name == "(":  # complex (multi-leaf) entity instance
            etype, args = _parse_complex(it)
        else:
            k, v = next(it)
            if v != "(":
                raise StepSyntaxError(f"expected '(' after {name}")
            etype, args = name.upper(), _parse_list(it)
        k, v = next(it)
        if v != ";":
            raise StepSyntaxError(f"expected ';' after #{eid}")
        entities[eid] = Entity(eid, etype, args)
    return entities


def _parse_complex(it):
    """``#1=(IFCA(...)IFCB(...));`` -> first leaf type, concatenated args."""
    leaves, args = [], []
    for kind, val in it:
        if kind == "punct" and val == ")":
            return (leaves[0] if leaves else "COMPLEX"), args
        if kind == "kw":
            leaves.append(val.upper())
            next(it)  # '('
            args.extend(_parse_list(it))
    raise StepSyntaxError("unterminated complex entity")


def _parse_list(it) -> list:
    """Parse values until the matching ')' (the opening '(' is consumed)."""
    out: list = []
    for kind, val in it:
        if kind == "punct":
            if val == ")":
                return out
            if val == ",":
                continue
            if val == "(":
                out.append(_parse_list(it))
                continue
            raise StepSyntaxError(f"unexpected {val!r}")
        if kind == "ref":
            out.append(Ref(int(val[1:])))
        elif kind == "str":
            out.append(decode_string(val[1:-1]))
        elif kind == "num":
            out.append(float(val) if any(c in val for c in ".eE") else int(val))
        elif kind == "enum":
            e = val[1:-1].upper()
            out.append(True if e == "T" else False if e == "F" else None if e == "U" else Enum(e))
        elif kind == "null":
            out.append(None)
        elif kind == "der":
            out.append(DERIVED)
        elif kind == "kw":  # typed value, e.g. IFCLABEL('x')
            next(it)  # '('
            inner = _parse_list(it)
            out.append(Typed(val.upper(), inner[0] if len(inner) == 1 else inner))
        elif kind == "bin":
            out.append(val.strip('"'))
    raise StepSyntaxError("unterminated list")


def parse_header_schema(text: str) -> str:
    m = re.search(r"FILE_SCHEMA\s*\(\s*\(\s*'([^']+)'", text[:20000], re.I)
    return m.group(1).upper() if m else "UNKNOWN"


def load(path: str) -> tuple[str, dict[int, Entity]]:
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        text = f.read()
    return parse_header_schema(text), parse_data(text)

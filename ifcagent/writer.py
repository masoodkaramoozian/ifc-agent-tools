"""Minimal IFC-SPF (ISO 10303-21) writer.

Only what is needed to author small, valid IFC4 test models without any
third-party dependency. Used by ``examples/generate_sample.py``.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

_B64 = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz_$"


def compress_guid(u: uuid.UUID) -> str:
    """Convert a UUID to the 22-character IFC GlobalId encoding."""
    n = u.int
    chars = []
    for _ in range(22):
        chars.append(_B64[n % 64])
        n //= 64
    return "".join(reversed(chars))


def stable_guid(*parts: str) -> str:
    """Deterministic GlobalId, so regenerated files are byte-identical."""
    return compress_guid(uuid.uuid5(uuid.NAMESPACE_URL, "ifcagent/" + "/".join(parts)))


@dataclass(frozen=True)
class Ref:
    id: int

    def __str__(self) -> str:
        return f"#{self.id}"


@dataclass(frozen=True)
class Enum:
    value: str

    def __str__(self) -> str:
        return f".{self.value}."


@dataclass(frozen=True)
class Typed:
    type: str
    value: object


class Derived:
    """The STEP '*' token (attribute derived in a subtype)."""

    def __str__(self) -> str:
        return "*"


DERIVED = Derived()


def _fmt(v) -> str:
    if v is None:
        return "$"
    if isinstance(v, bool):
        return ".T." if v else ".F."
    if isinstance(v, (Ref, Enum, Derived)):
        return str(v)
    if isinstance(v, Typed):
        return f"{v.type.upper()}({_fmt(v.value)})"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        s = repr(float(v))
        if "e" in s or "E" in s:
            mant, exp = s.lower().split("e")
            if "." not in mant:
                mant += "."
            return f"{mant}E{int(exp)}"
        return s if "." in s else s + "."
    if isinstance(v, str):
        out = []
        for ch in v.replace("'", "''"):
            if ord(ch) < 128:
                out.append(ch.replace("\\", "\\\\"))
            else:
                out.append("\\X2\\%04X\\X0\\" % ord(ch))
        return "'" + "".join(out) + "'"
    if isinstance(v, (list, tuple)):
        return "(" + ",".join(_fmt(x) for x in v) + ")"
    raise TypeError(f"cannot serialise {v!r}")


class IfcWriter:
    def __init__(self, schema: str = "IFC4"):
        self.schema = schema
        self.lines: list[str] = []
        self._next = 1

    def add(self, ifc_type: str, *args) -> Ref:
        ref = Ref(self._next)
        self._next += 1
        self.lines.append(f"{ref}={ifc_type.upper()}({','.join(_fmt(a) for a in args)});")
        return ref

    def write(self, path: str, name: str = "model.ifc", author: str = "", org: str = "") -> None:
        ts = datetime(2026, 1, 1, tzinfo=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
        header = [
            "ISO-10303-21;",
            "HEADER;",
            "FILE_DESCRIPTION(('ViewDefinition [ReferenceView_V1.2]'),'2;1');",
            f"FILE_NAME({_fmt(name)},{_fmt(ts)},({_fmt(author)}),({_fmt(org)}),'ifcagent','ifcagent writer','');",
            f"FILE_SCHEMA(('{self.schema}'));",
            "ENDSEC;",
            "DATA;",
        ]
        footer = ["ENDSEC;", "END-ISO-10303-21;", ""]
        with open(path, "w", encoding="ascii", newline="\n") as f:
            f.write("\n".join(header + self.lines + footer))

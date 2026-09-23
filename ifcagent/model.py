"""IfcModel: entity graph, spatial structure, properties, quantities, geometry."""
from __future__ import annotations

import os
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from functools import cached_property

from .geometry import GeometryEngine, Shape
from .step import Entity, Enum, Ref, Typed, load

NON_PHYSICAL = {
    "IFCPROJECT", "IFCSITE", "IFCBUILDING", "IFCBUILDINGSTOREY", "IFCSPACE", "IFCSPATIALZONE",
    "IFCEXTERNALSPATIALELEMENT", "IFCOPENINGELEMENT", "IFCOPENINGSTANDARDCASE", "IFCVOIDINGFEATURE",
    "IFCPROJECTIONELEMENT", "IFCANNOTATION", "IFCGRID", "IFCVIRTUALELEMENT", "IFCFACILITY",
    "IFCFACILITYPART", "IFCBRIDGE", "IFCROAD", "IFCRAILWAY", "IFCMARINEFACILITY", "IFCALIGNMENT",
    "IFCDISTRIBUTIONPORT", "IFCGEOGRAPHICELEMENT",
}
SPATIAL = {"IFCSITE", "IFCBUILDING", "IFCBUILDINGSTOREY", "IFCSPACE", "IFCFACILITY", "IFCFACILITYPART"}
_PREFIX = {"EXA": 1e18, "PETA": 1e15, "TERA": 1e12, "GIGA": 1e9, "MEGA": 1e6, "KILO": 1e3, "HECTO": 1e2,
           "DECA": 1e1, "DECI": 1e-1, "CENTI": 1e-2, "MILLI": 1e-3, "MICRO": 1e-6, "NANO": 1e-9}


def plain(v):
    """Turn parsed STEP values into JSON-friendly Python values."""
    if isinstance(v, Typed):
        return plain(v.value)
    if isinstance(v, Enum):
        return v.value
    if isinstance(v, Ref):
        return f"#{v.id}"
    if isinstance(v, list):
        return [plain(x) for x in v]
    return v


@dataclass
class Element:
    id: int
    guid: str
    type: str
    name: str | None
    tag: str | None
    storey: str | None
    predefined: str | None = None
    type_name: str | None = None
    psets: dict = field(default_factory=dict)
    qtos: dict = field(default_factory=dict)

    @property
    def label(self) -> str:
        return f"{self.type[3:].title() if self.type.startswith('IFC') else self.type} '{self.name or self.guid}'"

    def as_dict(self) -> dict:
        return {
            "id": self.id, "guid": self.guid, "type": self.type, "name": self.name, "tag": self.tag,
            "storey": self.storey, "predefined_type": self.predefined, "type_name": self.type_name,
        }


class IfcModel:
    def __init__(self, path: str):
        self.path = os.path.abspath(path)
        self.schema, self.entities = load(path)
        self.by_type: dict[str, list[Entity]] = defaultdict(list)
        for ent in self.entities.values():
            self.by_type[ent.type].append(ent)
        self.length_scale, self.area_scale, self.volume_scale, self.unit_names = self._units()
        self.geom = GeometryEngine(self.entities, self.length_scale)
        self._shapes: dict[int, Shape] = {}
        self._index_relations()

    # ------------------------------------------------------------------ basics
    def get(self, ref) -> Entity | None:
        if isinstance(ref, Ref):
            return self.entities.get(ref.id)
        if isinstance(ref, int):
            return self.entities.get(ref)
        return None

    def _units(self):
        scales = {"LENGTHUNIT": 1.0, "AREAUNIT": 1.0, "VOLUMEUNIT": 1.0}
        names = {"LENGTHUNIT": "METRE", "AREAUNIT": "SQUARE_METRE", "VOLUMEUNIT": "CUBIC_METRE"}
        for ua in self.by_type.get("IFCUNITASSIGNMENT", [])[:1]:
            for uref in ua[0]:
                u = self.get(uref)
                if u is None or not isinstance(u[1], Enum):
                    continue
                kind = u[1].value
                if kind not in scales:
                    continue
                if u.type == "IFCSIUNIT":
                    f = _PREFIX.get(u[2].value, 1.0) if isinstance(u[2], Enum) else 1.0
                    power = {"LENGTHUNIT": 1, "AREAUNIT": 2, "VOLUMEUNIT": 3}[kind]
                    scales[kind] = f ** power
                    names[kind] = ((u[2].value + "_") if isinstance(u[2], Enum) else "") + u[3].value
                elif u.type == "IFCCONVERSIONBASEDUNIT":
                    mwu = self.get(u[3])
                    val = mwu[0].value if isinstance(mwu[0], Typed) else mwu[0]
                    base = self.get(mwu[1])
                    f = _PREFIX.get(base[2].value, 1.0) if base is not None and isinstance(base[2], Enum) else 1.0
                    power = {"LENGTHUNIT": 1, "AREAUNIT": 2, "VOLUMEUNIT": 3}[kind]
                    scales[kind] = float(val) * f ** power
                    names[kind] = u[2]
        return scales["LENGTHUNIT"], scales["AREAUNIT"], scales["VOLUMEUNIT"], names

    # ------------------------------------------------------------------ relations
    def _index_relations(self):
        self.container: dict[int, int] = {}          # element -> spatial structure
        self.parent: dict[int, int] = {}             # aggregated part -> whole
        self.children: dict[int, list[int]] = defaultdict(list)
        self.psets_of: dict[int, list[int]] = defaultdict(list)
        self.type_of: dict[int, int] = {}
        self.opening_host: dict[int, int] = {}       # opening -> host element
        self.filling_opening: dict[int, int] = {}    # filler (window/door) -> opening
        self.space_boundaries: dict[int, set[int]] = defaultdict(set)
        for r in self.by_type.get("IFCRELCONTAINEDINSPATIALSTRUCTURE", []):
            for e in r[4] or []:
                self.container[e.id] = r[5].id
        for r in self.by_type.get("IFCRELAGGREGATES", []):
            for e in r[5] or []:
                self.parent[e.id] = r[4].id
                self.children[r[4].id].append(e.id)
        for r in self.by_type.get("IFCRELDEFINESBYPROPERTIES", []):
            objs = r[4] if isinstance(r[4], list) else [r[4]]
            for e in objs:
                self.psets_of[e.id].append(r[5].id)
        for r in self.by_type.get("IFCRELDEFINESBYTYPE", []):
            for e in r[4] or []:
                self.type_of[e.id] = r[5].id
        for r in self.by_type.get("IFCRELVOIDSELEMENT", []):
            self.opening_host[r[5].id] = r[4].id
        for r in self.by_type.get("IFCRELFILLSELEMENT", []):
            self.filling_opening[r[5].id] = r[4].id
        for t in ("IFCRELSPACEBOUNDARY", "IFCRELSPACEBOUNDARY1STLEVEL", "IFCRELSPACEBOUNDARY2NDLEVEL"):
            for r in self.by_type.get(t, []):
                if isinstance(r[4], Ref) and isinstance(r[5], Ref):
                    self.space_boundaries[r[4].id].add(r[5].id)

    def host_of(self, eid: int) -> int | None:
        """Wall (or slab...) hosting a window/door through an opening."""
        op = self.filling_opening.get(eid)
        return self.opening_host.get(op) if op else None

    def storey_of(self, eid: int) -> Entity | None:
        seen = set()
        cur = self.container.get(eid) or self.parent.get(eid)
        while cur is not None and cur not in seen:
            seen.add(cur)
            ent = self.entities.get(cur)
            if ent is None:
                return None
            if ent.type == "IFCBUILDINGSTOREY":
                return ent
            cur = self.container.get(cur) or self.parent.get(cur)
        return None

    # ------------------------------------------------------------------ properties
    def _props(self, pdef: Entity) -> dict:
        out = {}
        for pref in pdef[4] or []:
            p = self.get(pref)
            if p is None:
                continue
            if p.type == "IFCPROPERTYSINGLEVALUE":
                out[p[0]] = plain(p[2])
            elif p.type == "IFCPROPERTYENUMERATEDVALUE":
                out[p[0]] = plain(p[2])
            elif p.type == "IFCPROPERTYLISTVALUE":
                out[p[0]] = plain(p[2])
            elif p.type == "IFCPROPERTYBOUNDEDVALUE":
                out[p[0]] = {"upper": plain(p[2]), "lower": plain(p[3])}
        return out

    def _quantities(self, q: Entity) -> dict:
        out = {}
        for qref in q[5] or []:
            qq = self.get(qref)
            if qq is None or not qq.type.startswith("IFCQUANTITY"):
                continue
            v = qq[3]
            if not isinstance(v, (int, float)):
                continue
            kind = qq.type[len("IFCQUANTITY"):]
            scale = {"LENGTH": self.length_scale, "AREA": self.area_scale, "VOLUME": self.volume_scale}.get(kind, 1.0)
            out[qq[0]] = {"kind": kind.lower(), "value": float(v) * scale}
        return out

    def property_sets(self, eid: int) -> tuple[dict, dict]:
        psets, qtos = {}, {}
        for pid in self.psets_of.get(eid, []):
            p = self.entities.get(pid)
            if p is None:
                continue
            if p.type == "IFCPROPERTYSET":
                psets[p[2] or f"#{pid}"] = self._props(p)
            elif p.type == "IFCELEMENTQUANTITY":
                qtos[p[2] or f"#{pid}"] = self._quantities(p)
        tid = self.type_of.get(eid)
        if tid:
            t = self.entities[tid]
            for pref in t[5] or []:
                p = self.get(pref)
                if p is not None and p.type == "IFCPROPERTYSET":
                    psets.setdefault(p[2], {})
                    for k, v in self._props(p).items():
                        psets[p[2]].setdefault(k, v)  # occurrence overrides type
        return psets, qtos

    def type_panels(self, eid: int) -> list[str]:
        """IfcWindowPanelProperties.OperationType values of the element's type."""
        tid = self.type_of.get(eid)
        if not tid:
            return []
        out = []
        for pref in self.entities[tid][5] or []:
            p = self.get(pref)
            if p is not None and p.type == "IFCWINDOWPANELPROPERTIES" and isinstance(p[4], Enum):
                out.append(p[4].value)
        return out

    # ------------------------------------------------------------------ elements
    def _is_product(self, ent: Entity) -> bool:
        a = ent.args
        return (
            len(a) >= 7 and isinstance(a[0], str) and len(a[0]) == 22
            and isinstance(a[5], Ref) and isinstance(a[6], Ref)
            and self.entities.get(a[6].id) is not None
            and self.entities[a[6].id].type == "IFCPRODUCTDEFINITIONSHAPE"
        )

    @cached_property
    def elements(self) -> list[Element]:
        out = []
        for ent in self.entities.values():
            if ent.type in NON_PHYSICAL or ent.type.endswith("TYPE") or not self._is_product(ent):
                continue
            out.append(self._element(ent))
        out.sort(key=lambda e: (e.storey or "", e.type, e.name or ""))
        return out

    @cached_property
    def spaces(self) -> list[Element]:
        return [self._element(e) for e in self.by_type.get("IFCSPACE", [])]

    @cached_property
    def storeys(self) -> list[dict]:
        out = []
        for s in self.by_type.get("IFCBUILDINGSTOREY", []):
            elev = s[9] if len(s.args) > 9 and isinstance(s[9], (int, float)) else None
            out.append({"name": s[2], "guid": s[0], "elevation": elev * self.length_scale if elev is not None else None})
        return sorted(out, key=lambda d: (d["elevation"] is None, d["elevation"] or 0))

    def _element(self, ent: Entity) -> Element:
        st = self.storey_of(ent.id)
        psets, qtos = self.property_sets(ent.id)
        tid = self.type_of.get(ent.id)
        predefined = None
        for v in ent.args[8:]:
            if isinstance(v, Enum) and not (ent.type in SPATIAL and v.value in ("ELEMENT", "COMPLEX", "PARTIAL")):
                predefined = v.value
                break
        return Element(
            id=ent.id, guid=ent[0], type=ent.type, name=ent[2], tag=ent[7] if len(ent.args) > 7 and isinstance(ent[7], str) else None,
            storey=st[2] if st else None, predefined=predefined,
            type_name=self.entities[tid][2] if tid else None, psets=psets, qtos=qtos,
        )

    def element(self, key) -> Element | None:
        """Look up by GlobalId, step id (int or '#12') or exact name."""
        if isinstance(key, str) and key.startswith("#") and key[1:].isdigit():
            key = int(key[1:])
        for e in self.elements + self.spaces:
            if e.guid == key or e.id == key or e.name == key:
                return e
        return None

    def shape(self, eid: int) -> Shape:
        if eid not in self._shapes:
            self._shapes[eid] = self.geom.product_shape(self.entities[eid])
        return self._shapes[eid]

    # ------------------------------------------------------------------ summary
    def summary(self) -> dict:
        counts = Counter(e.type for e in self.elements)
        notes = Counter()
        empty = 0
        for e in self.elements:
            sh = self.shape(e.id)
            empty += sh.empty
            for n in sh.notes:
                notes[n.split(" (#")[0].split(" #")[0]] += 1
        proj = (self.by_type.get("IFCPROJECT") or [None])[0]
        return {
            "file": os.path.basename(self.path),
            "schema": self.schema,
            "project": proj[2] if proj else None,
            "units": self.unit_names,
            "length_unit_to_metre": self.length_scale,
            "entity_count": len(self.entities),
            "storeys": self.storeys,
            "spaces": len(self.spaces),
            "elements_by_type": dict(sorted(counts.items(), key=lambda kv: -kv[1])),
            "elements_without_geometry": empty,
            "geometry_notes": dict(notes),
        }

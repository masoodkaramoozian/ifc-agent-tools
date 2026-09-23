"""Quantity take-off (QTO) computed from geometry, and validation of authored Qto sets."""
from __future__ import annotations

from collections import defaultdict

import numpy as np

from .model import Element, IfcModel

WALLS = {"IFCWALL", "IFCWALLSTANDARDCASE", "IFCWALLELEMENTEDCASE", "IFCCURTAINWALL"}
SLABS = {"IFCSLAB", "IFCSLABSTANDARDCASE", "IFCSLABELEMENTEDCASE", "IFCROOF", "IFCCOVERING", "IFCPLATE"}
LINEAR = {"IFCBEAM", "IFCBEAMSTANDARDCASE", "IFCCOLUMN", "IFCCOLUMNSTANDARDCASE", "IFCMEMBER",
          "IFCMEMBERSTANDARDCASE", "IFCPILE", "IFCRAILING"}
FLOW = {"IFCDUCTSEGMENT", "IFCPIPESEGMENT", "IFCCABLECARRIERSEGMENT", "IFCCABLESEGMENT", "IFCFLOWSEGMENT"}
FILLINGS = {"IFCWINDOW", "IFCWINDOWSTANDARDCASE", "IFCDOOR", "IFCDOORSTANDARDCASE"}

# Standard IFC base-quantity names -> keys of our computed quantities
QTO_MAP = {
    "Length": "length", "Width": "width", "Height": "height", "Depth": "thickness",
    "GrossVolume": "gross_volume", "NetVolume": "net_volume",
    "GrossSideArea": "gross_side_area", "NetSideArea": "net_side_area",
    "GrossArea": "area", "NetArea": "area", "Area": "area",
    "CrossSectionArea": "cross_section_area",
    "NetFloorArea": "floor_area", "GrossFloorArea": "floor_area",
}


def _local_extents(model: IfcModel, eid: int, frame: np.ndarray):
    """Min/max of an element's vertices expressed in ``frame`` (4x4, metres)."""
    v = model.shape(eid).vertices()
    if not len(v):
        return None
    inv = np.linalg.inv(frame)
    loc = v @ inv[:3, :3].T + inv[:3, 3]
    return loc.min(0), loc.max(0)


def _frame(model: IfcModel, eid: int) -> np.ndarray:
    m = model.geom.placement(model.entities[eid][5]).copy()
    m[:3, 3] *= model.length_scale
    return m


def element_quantities(model: IfcModel, el: Element) -> dict:
    sh = model.shape(el.id)
    q: dict = {"count": 1, "gross_volume": round(sh.volume, 6)}
    if sh.empty:
        q["note"] = "no geometry"
        return q
    vertical = sh.sweep_dir is not None and abs(sh.sweep_dir[2]) > 0.99
    t = el.type

    if t in WALLS:
        frame = _frame(model, el.id)
        lo, hi = _local_extents(model, el.id, frame)
        ext = hi - lo
        along_x = ext[0] >= ext[1]
        length, width, height = (ext[0], ext[1], ext[2]) if along_x else (ext[1], ext[0], ext[2])
        a_idx, t_idx = (0, 1) if along_x else (1, 0)
        opening_area = opening_vol = 0.0
        n_open = 0
        for op, host in model.opening_host.items():
            if host != el.id:
                continue
            olo, ohi = _local_extents(model, op, frame) or (None, None)
            if olo is None:
                continue
            clip = lambda i: max(0.0, min(ohi[i], hi[i]) - max(olo[i], lo[i]))  # noqa: E731
            face = clip(a_idx) * clip(2)
            opening_area += face
            opening_vol += face * clip(t_idx)
            n_open += 1
        q.update(
            length=length, width=width, height=height,
            gross_side_area=length * height, net_side_area=length * height - opening_area,
            net_volume=sh.volume - opening_vol, openings=n_open,
        )
    elif t in SLABS:
        if vertical:
            q.update(area=sh.profile_area, thickness=sh.sweep_length)
        else:
            lo, hi = sh.aabb()
            q.update(area=float((hi - lo)[0] * (hi - lo)[1]), thickness=float((hi - lo)[2]))
        q["net_volume"] = sh.volume
    elif t in LINEAR or t in FLOW:
        # longest local extent = member axis, whether the tool extruded the
        # profile along the axis (usual) or extruded the elevation vertically
        lo, hi = _local_extents(model, el.id, _frame(model, el.id))
        length = float(max(hi - lo))
        q.update(length=length, cross_section_area=sh.volume / length if length else None)
    elif t in FILLINGS:
        ent = model.entities[el.id]
        h, w = ent[8], ent[9]
        if isinstance(h, (int, float)) and isinstance(w, (int, float)):
            h, w = h * model.length_scale, w * model.length_scale
        else:
            lo, hi = _local_extents(model, el.id, _frame(model, el.id))
            dims = sorted(hi - lo)
            w, h = dims[1], dims[2]
        q.update(width=w, height=h, area=w * h)
    elif t == "IFCSPACE":
        lo, hi = sh.aabb()
        if vertical:
            q.update(floor_area=sh.profile_area, height=sh.sweep_length)
        else:
            q.update(floor_area=sh.volume / max(hi[2] - lo[2], 1e-9), height=float(hi[2] - lo[2]))
    return {k: (round(float(v), 4) if isinstance(v, (float, np.floating)) else v) for k, v in q.items()}


def takeoff(model: IfcModel, group_by: str = "type", types: list[str] | None = None) -> dict:
    """Aggregate quantities. ``group_by`` in {'type', 'storey', 'type_storey', 'element'}."""
    wanted = {_norm(t) for t in types} if types else None
    rows = []
    for el in model.elements:
        if wanted and el.type not in wanted:
            continue
        q = element_quantities(model, el)
        rows.append({**el.as_dict(), **q})
    if group_by == "element":
        return {"group_by": "element", "rows": rows}
    groups: dict = defaultdict(lambda: defaultdict(float))
    keys = ["count", "gross_volume", "net_volume", "length", "area", "net_side_area", "gross_side_area"]
    for r in rows:
        if group_by == "storey":
            g = r["storey"] or "(unassigned)"
        elif group_by == "type_storey":
            g = f"{r['type']} @ {r['storey'] or '(unassigned)'}"
        else:
            g = r["type"]
        for k in keys:
            v = r.get(k)
            if isinstance(v, (int, float)):
                groups[g][k] += v
    out = {g: {k: round(v, 3) for k, v in d.items()} for g, d in sorted(groups.items())}
    return {"group_by": group_by, "units": {"length": "m", "area": "m2", "volume": "m3"}, "groups": out}


def space_quantities(model: IfcModel) -> list[dict]:
    out = []
    for sp in model.spaces:
        out.append({**sp.as_dict(), **element_quantities(model, sp)})
    return out


def validate_qto(model: IfcModel, rel_tol: float = 0.05) -> dict:
    """Compare authored IfcElementQuantity values with geometry-derived values."""
    checked, issues = 0, []
    for el in model.elements + model.spaces:
        if not el.qtos:
            continue
        computed = element_quantities(model, el)
        for qset, vals in el.qtos.items():
            for name, item in vals.items():
                key = QTO_MAP.get(name)
                if key is None or computed.get(key) in (None, 0):
                    continue
                checked += 1
                auth, comp = item["value"], float(computed[key])
                diff = (auth - comp) / comp
                if abs(diff) > rel_tol:
                    issues.append({
                        "element": el.as_dict(), "qto_set": qset, "quantity": name,
                        "authored": round(auth, 4), "computed": round(comp, 4),
                        "difference_pct": round(100 * diff, 1),
                    })
    return {"checked": checked, "tolerance_pct": 100 * rel_tol, "discrepancies": issues}


def _norm(t: str) -> str:
    t = t.strip().upper()
    return t if t.startswith("IFC") else "IFC" + t

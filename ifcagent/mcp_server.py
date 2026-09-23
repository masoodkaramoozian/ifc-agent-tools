"""MCP server exposing the IFC tools to AI agents (Claude Code, Codex, Cursor, ...).

Run over stdio:   python -m ifcagent.mcp_server
Claude Code picks it up automatically from the repo's ``.mcp.json``.

Every tool takes the path of an .ifc file; parsed models are cached by
(path, modification time) so an agent can call many tools cheaply.
"""
from __future__ import annotations

import os

from mcp.server.fastmcp import FastMCP

from .clash import detect_clashes as _clashes
from .model import IfcModel
from .quantities import element_quantities, space_quantities, takeoff, validate_qto
from .report import build_report
from .ventilation import check_ventilation as _vent

mcp = FastMCP(
    "ifc-agent",
    instructions=(
        "Tools for reading and checking IFC (BIM) models: model summary, element lookup, "
        "quantity take-off, validation of authored quantities, clash detection and natural "
        "ventilation screening. Lengths are metres, areas m2, volumes m3. Start with "
        "model_summary. Results that depend on assumptions say so - surface those to the user."
    ),
)
_cache: dict[str, tuple[float, IfcModel]] = {}


def _model(path: str) -> IfcModel:
    path = os.path.abspath(os.path.expanduser(path))
    if not os.path.isfile(path):
        raise FileNotFoundError(f"IFC file not found: {path}")
    mtime = os.path.getmtime(path)
    hit = _cache.get(path)
    if hit and hit[0] == mtime:
        return hit[1]
    m = IfcModel(path)
    _cache[path] = (mtime, m)
    return m


@mcp.tool()
def model_summary(ifc_path: str) -> dict:
    """Schema, project name, units, storeys (with elevations), number of spaces, element
    counts per IFC class and any geometry the parser could only approximate."""
    return _model(ifc_path).summary()


@mcp.tool()
def list_elements(ifc_path: str, ifc_type: str | None = None, storey: str | None = None,
                  name_contains: str | None = None, limit: int = 100) -> dict:
    """List physical elements, optionally filtered by IFC class (e.g. 'IfcWall' or 'wall'),
    storey name, and a case-insensitive substring of the element name."""
    m = _model(ifc_path)
    t = None
    if ifc_type:
        t = ifc_type.upper()
        t = t if t.startswith("IFC") else "IFC" + t
    rows = [
        e.as_dict() for e in m.elements
        if (t is None or e.type == t)
        and (storey is None or (e.storey or "").lower() == storey.lower())
        and (name_contains is None or name_contains.lower() in (e.name or "").lower())
    ]
    return {"total": len(rows), "elements": rows[:limit], "truncated": len(rows) > limit}


@mcp.tool()
def get_element(ifc_path: str, element: str) -> dict:
    """Full detail for one element or space, looked up by GlobalId, '#<step id>' or exact name:
    attributes, storey, type, property sets, authored quantity sets and geometry-derived quantities."""
    m = _model(ifc_path)
    el = m.element(element)
    if el is None:
        return {"error": f"no element matching {element!r}"}
    sh = m.shape(el.id)
    lo, hi = sh.aabb()
    host = m.host_of(el.id)
    return {
        **el.as_dict(),
        "property_sets": el.psets,
        "authored_quantities": el.qtos,
        "computed_quantities": element_quantities(m, el),
        "bounding_box": {"min": lo.round(4).tolist(), "max": hi.round(4).tolist()} if lo is not None else None,
        "host_element": m.entities[host][2] if host else None,
        "window_panel_operations": m.type_panels(el.id) or None,
        "geometry_notes": sh.notes or None,
    }


@mcp.tool()
def quantity_takeoff(ifc_path: str, group_by: str = "type", ifc_types: list[str] | None = None) -> dict:
    """Quantity take-off computed from geometry (not from authored Qto sets).
    group_by: 'type' | 'storey' | 'type_storey' | 'element'. Walls report length, height,
    width, gross/net side area and net volume (openings deducted); slabs area and thickness;
    beams/columns/ducts/pipes length; windows/doors area."""
    return takeoff(_model(ifc_path), group_by, ifc_types)


@mcp.tool()
def space_schedule(ifc_path: str) -> list[dict]:
    """Room schedule: every IfcSpace with storey, floor area, height and volume."""
    return space_quantities(_model(ifc_path))


@mcp.tool()
def validate_quantities(ifc_path: str, tolerance_pct: float = 5.0) -> dict:
    """Compare quantities authored in the model (IfcElementQuantity, e.g. Qto_WallBaseQuantities)
    against values recomputed from geometry and list those that differ by more than tolerance_pct.
    Useful before trusting a model's quantities for cost estimates."""
    return validate_qto(_model(ifc_path), tolerance_pct / 100)


@mcp.tool()
def detect_clashes(ifc_path: str, group_a: list[str] | None = None, group_b: list[str] | None = None,
                   tolerance_mm: float = 10.0, clearance_mm: float = 0.0, max_results: int = 100) -> dict:
    """Clash detection with exact convex-solid intersection tests.
    group_a / group_b: IFC classes to test against each other, e.g. ['IfcDuctSegment','IfcPipeSegment']
    vs ['IfcBeam','IfcWall']; omit both for all-vs-all. Overlaps up to tolerance_mm are treated as
    intended contact. clearance_mm > 0 also reports elements closer than that distance.
    Windows/doors vs their host wall and assembly/part pairs are skipped. Returns duplicate,
    hard and clearance clashes with penetration depth / gap and an (x, y, z) location in metres."""
    return _clashes(_model(ifc_path), group_a, group_b, tolerance_mm / 1000, clearance_mm / 1000, max_results)


@mcp.tool()
def check_natural_ventilation(ifc_path: str, preset: str = "hk_bpr", default_openable_ratio: float = 0.5,
                              min_glazing_ratio: float | None = None, min_openable_ratio: float | None = None,
                              wind_speed_ms: float = 3.0, delta_t_k: float = 3.0) -> dict:
    """Screen every IfcSpace for natural ventilation: finds the exterior windows serving it,
    glazed and openable area (openable fraction from window-type panel operation or a property;
    otherwise default_openable_ratio, flagged as an assumption), checks them against a rule set
    (preset 'hk_bpr' = HK B(P)R 30/31: glazing >= 1/10 and openable >= 1/16 of floor area;
    'uk_adf2010' = openable >= 1/20; or 'custom' with explicit ratios), applies CIBSE room-depth
    rules (single-sided <= 2.5H, cross <= 5H) and gives an indicative BS 5925 air-change rate."""
    return _vent(_model(ifc_path), preset, min_glazing_ratio, min_openable_ratio, default_openable_ratio,
                 wind_speed=wind_speed_ms, delta_t=delta_t_k)


@mcp.tool()
def write_qa_report(ifc_path: str, out_dir: str | None = None, clearance_mm: float = 50.0, preset: str = "hk_bpr") -> dict:
    """Run every check and write report.md, report.json and one annotated plan PNG per storey
    into out_dir (default: '<model name>_report' next to the IFC file). Returns the file paths."""
    m = _model(ifc_path)
    out_dir = out_dir or os.path.splitext(m.path)[0] + "_report"
    return build_report(m, out_dir, clearance_mm / 1000, preset)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()

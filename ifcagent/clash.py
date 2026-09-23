"""Clash detection: hard clashes, clearance (soft) clashes and duplicates.

Narrow phase is an exact Separating-Axis Test between convex prisms (see
:mod:`ifcagent.geometry`), so the reported penetration depth is the minimum
translation needed to separate the two elements.

Pairs that are *meant* to touch or overlap are skipped:

* a window/door and the wall that hosts it (IfcRelFillsElement + IfcRelVoidsElement)
* an assembly and its parts (IfcRelAggregates)
* contacts whose |overlap| is within ``tolerance`` (walls standing on slabs,
  walls butting at corners, ...)
"""
from __future__ import annotations

import numpy as np

from .geometry import shape_overlap
from .model import Element, IfcModel
from .quantities import _norm


def _pair_ok(a: Element, b: Element, A: set | None, B: set | None) -> bool:
    if A is None and B is None:
        return True
    A = A or B
    B = B or A
    return (a.type in A and b.type in B) or (a.type in B and b.type in A)


def detect_clashes(
    model: IfcModel,
    group_a: list[str] | None = None,
    group_b: list[str] | None = None,
    tolerance: float = 0.01,
    clearance: float = 0.0,
    max_results: int = 200,
) -> dict:
    """Run clash detection.

    group_a / group_b : IFC type names (``IfcBeam`` or ``beam``). Only pairs with
        one element in each group are tested.  Omit both for all-vs-all.
    tolerance : overlaps up to this depth (m) are treated as contact, not clashes.
    clearance : report elements closer than this (m) as clearance clashes.
    """
    A = {_norm(t) for t in group_a} if group_a else None
    B = {_norm(t) for t in group_b} if group_b else None
    els = [e for e in model.elements if not model.shape(e.id).empty]
    boxes = {e.id: model.shape(e.id).aabb() for e in els}
    margin = max(clearance, tolerance)
    order = sorted(els, key=lambda e: boxes[e.id][0][0])

    hosts = {}
    for e in els:
        h = model.host_of(e.id)
        if h:
            hosts[e.id] = h

    def related(a: Element, b: Element) -> bool:
        if hosts.get(a.id) == b.id or hosts.get(b.id) == a.id:
            return True
        return model.parent.get(a.id) == b.id or model.parent.get(b.id) == a.id

    clashes, tested = [], 0
    for i, a in enumerate(order):
        lo_a, hi_a = boxes[a.id]
        for b in order[i + 1:]:
            lo_b, hi_b = boxes[b.id]
            if lo_b[0] > hi_a[0] + margin:
                break
            if np.any(lo_b > hi_a + margin) or np.any(lo_a > hi_b + margin):
                continue
            if not _pair_ok(a, b, A, B) or related(a, b):
                continue
            tested += 1
            ov = shape_overlap(model.shape(a.id), model.shape(b.id))
            lo, hi = np.maximum(lo_a, lo_b), np.minimum(hi_a, hi_b)
            where = ((lo + hi) / 2).round(3).tolist()
            dup = (
                a.type == b.type
                and np.allclose(lo_a, lo_b, atol=1e-3) and np.allclose(hi_a, hi_b, atol=1e-3)
                and abs(model.shape(a.id).volume - model.shape(b.id).volume) <= 0.01 * max(model.shape(a.id).volume, 1e-9)
            )
            if dup:
                kind, value = "duplicate", ov
            elif ov > tolerance:
                kind, value = "hard", ov
            elif clearance > 0 and -ov > tolerance and -ov < clearance:
                kind, value = "clearance", -ov
            else:
                continue
            approx = model.shape(a.id).approx or model.shape(b.id).approx
            clashes.append({
                "kind": kind,
                "a": a.as_dict(), "b": b.as_dict(),
                "penetration_m" if kind != "clearance" else "gap_m": round(value, 4),
                "location": where,
                "approximate_geometry": approx,
            })

    rank = {"duplicate": 0, "hard": 1, "clearance": 2}
    clashes.sort(key=lambda c: (rank[c["kind"]], -(c.get("penetration_m") or 0), c.get("gap_m") or 0))
    summary = {k: sum(c["kind"] == k for c in clashes) for k in rank}
    return {
        "settings": {"group_a": sorted(A) if A else "ALL", "group_b": sorted(B) if B else "ALL",
                     "tolerance_m": tolerance, "clearance_m": clearance},
        "pairs_tested": tested,
        "summary": summary,
        "clashes": clashes[:max_results],
        "truncated": len(clashes) > max_results,
    }

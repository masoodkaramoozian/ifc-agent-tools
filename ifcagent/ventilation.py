"""Natural-ventilation screening of IfcSpaces from the window data in an IFC model.

For every space the checker finds the exterior windows that serve it, works
out glazed and *openable* area, and tests them against a prescriptive rule
set.  It also applies the classic room-depth rules of thumb and gives an
indicative single-sided airflow estimate.

Rule sets (``preset``)
----------------------
hk_bpr      Hong Kong Building (Planning) Regulations 30/31 (prescriptive
            route): glazing >= 1/10 of floor area, openable >= 1/16.
uk_adf2010  England Approved Document F (2010) purge ventilation for
            habitable rooms: openable >= 1/20 of floor area.
custom      pass ``min_glazing_ratio`` / ``min_openable_ratio`` yourself.

Depth rules of thumb (CIBSE AM10): single-sided ventilation is effective to
~2.5 x floor-to-ceiling height; cross ventilation to ~5 x height.

Airflow (BS 5925 single-sided formulas, per window, indicative only)
    wind      Q = 0.025 * A * U
    buoyancy  Q = Cd * A/3 * sqrt(dT * g * h / T)      (Cd = 0.61)
    ACH = 3600 * sum(max(Q_wind, Q_stack)) / room volume

Openable fraction of each window, in order of preference
    1. a property named ``OpenableAreaRatio`` / ``OpenableRatio`` in any pset
    2. the IfcWindowPanelProperties.OperationType of the window type
       (FIXEDCASEMENT = 0, SLIDING* = 0.5, hinged/pivot/tilt = 1, averaged over panels)
    3. ``default_openable_ratio`` - reported as an *assumption*
"""
from __future__ import annotations

import math

import numpy as np

from .model import IfcModel
from .quantities import element_quantities

PRESETS = {
    "hk_bpr": {"min_glazing_ratio": 1 / 10, "min_openable_ratio": 1 / 16,
               "source": "HK Building (Planning) Regulations 30/31 prescriptive route"},
    "uk_adf2010": {"min_glazing_ratio": None, "min_openable_ratio": 1 / 20,
                   "source": "England Approved Document F (2010), purge ventilation"},
}
PANEL_FACTOR = {"FIXEDCASEMENT": 0.0, "SLIDINGHORIZONTAL": 0.5, "SLIDINGVERTICAL": 0.5,
                "NOTDEFINED": None, "OTHEROPERATION": None}
WINDOWS = {"IFCWINDOW", "IFCWINDOWSTANDARDCASE"}


def _openable(model: IfcModel, el) -> tuple[float | None, str]:
    for pset, props in el.psets.items():
        for key in ("OpenableAreaRatio", "OpenableRatio"):
            if isinstance(props.get(key), (int, float)):
                return float(props[key]), f"property {pset}.{key}"
    panels = model.type_panels(el.id)
    if panels:
        factors = [PANEL_FACTOR.get(p, 1.0) for p in panels]
        if all(f is not None for f in factors):
            return sum(factors) / len(factors), "window type panels: " + "+".join(p.lower() for p in panels)
    return None, "assumed"


def _true_north(model: IfcModel) -> float:
    """Angle (rad) of project north measured from +Y, counter-clockwise."""
    for ctx in model.by_type.get("IFCGEOMETRICREPRESENTATIONCONTEXT", []):
        tn = model.get(ctx[5]) if len(ctx.args) > 5 else None
        if tn is not None:
            x, y = float(tn[0][0]), float(tn[0][1])
            return math.atan2(-x, y)
    return 0.0


def _compass(n: np.ndarray, north: float) -> str:
    # rotate so that true north is +Y
    c, s = math.cos(-north), math.sin(-north)
    x, y = c * n[0] - s * n[1], s * n[0] + c * n[1]
    ang = (math.degrees(math.atan2(x, y)) + 360) % 360  # 0 = N, 90 = E
    return ["N", "NE", "E", "SE", "S", "SW", "W", "NW"][int((ang + 22.5) // 45) % 8]


def _dist_to_polys(p: np.ndarray, polys: list[np.ndarray]) -> float:
    best = math.inf
    for poly in polys:
        inside, n = True, len(poly)
        area = 0.5 * sum(poly[i, 0] * poly[(i + 1) % n, 1] - poly[(i + 1) % n, 0] * poly[i, 1] for i in range(n))
        sgn = 1 if area >= 0 else -1
        d = math.inf
        for i in range(n):
            a, b = poly[i], poly[(i + 1) % n]
            ab, ap = b - a, p - a
            t = max(0.0, min(1.0, float(np.dot(ap, ab) / max(np.dot(ab, ab), 1e-12))))
            d = min(d, float(np.linalg.norm(a + t * ab - p)))
            if sgn * (ab[0] * ap[1] - ab[1] * ap[0]) < 0:
                inside = False
        best = min(best, 0.0 if inside else d)
    return best


def check_ventilation(
    model: IfcModel,
    preset: str = "hk_bpr",
    min_glazing_ratio: float | None = None,
    min_openable_ratio: float | None = None,
    default_openable_ratio: float = 0.5,
    max_window_distance: float = 0.5,
    wind_speed: float = 3.0,
    delta_t: float = 3.0,
    mean_temp_k: float = 298.0,
) -> dict:
    rules = dict(PRESETS.get(preset, {"min_glazing_ratio": None, "min_openable_ratio": None, "source": "custom"}))
    if min_glazing_ratio is not None:
        rules["min_glazing_ratio"] = min_glazing_ratio
    if min_openable_ratio is not None:
        rules["min_openable_ratio"] = min_openable_ratio
    north = _true_north(model)

    # candidate windows with their geometry
    wins = []
    for el in model.elements:
        if el.type not in WINDOWS:
            continue
        sh = model.shape(el.id)
        if sh.empty:
            continue
        v = sh.vertices()
        lo, hi = v.min(0), v.max(0)
        xy = v[:, :2] - v[:, :2].mean(0)
        _, vecs = np.linalg.eigh(xy.T @ xy)
        normal = vecs[:, 0]  # minor horizontal axis = through-wall direction
        q = element_quantities(model, el)
        ratio, source = _openable(model, el)
        wins.append({"el": el, "c": (lo + hi) / 2, "z": (lo[2], hi[2]), "n": normal, "area": q.get("area", 0.0),
                     "height": q.get("height", hi[2] - lo[2]), "ratio": ratio, "ratio_source": source})

    spaces = []
    for sp in model.spaces:
        sh = model.shape(sp.id)
        if sh.empty:
            continue
        lo, hi = sh.aabb()
        polys = [p.base[:, :2] for p in sh.pieces]
        sq = element_quantities(model, sp)
        spaces.append({"el": sp, "lo": lo, "hi": hi, "polys": polys, "q": sq, "vol": sh.volume,
                       "centroid": np.vstack(polys).mean(0), "declared": model.space_boundaries.get(sp.id)})

    # assign windows to spaces
    serving: dict[int, list] = {s["el"].id: [] for s in spaces}
    for w in wins:
        hits = []
        for s in spaces:
            if s["declared"]:
                if w["el"].id in s["declared"]:
                    hits.append(s)
                continue
            z_ov = min(w["z"][1], s["hi"][2]) - max(w["z"][0], s["lo"][2])
            if z_ov > 0.1 and _dist_to_polys(w["c"][:2], s["polys"]) <= max_window_distance:
                hits.append(s)
        w["internal"] = len(hits) > 1
        for s in hits:
            serving[s["el"].id].append(w)

    results = []
    for s in spaces:
        sp, q = s["el"], s["q"]
        floor, height = q.get("floor_area", 0.0), q.get("height", 0.0)
        ext = [w for w in serving[sp.id] if not w["internal"]]
        facades, glazing, openable, flows, assumed = {}, 0.0, 0.0, 0.0, False
        wrows = []
        for w in ext:
            n = w["n"] if np.dot(w["n"], w["c"][:2] - s["centroid"]) >= 0 else -w["n"]
            ratio = w["ratio"]
            if ratio is None:
                ratio, assumed = default_openable_ratio, True
            a_open = w["area"] * ratio
            glazing += w["area"]
            openable += a_open
            q_wind = 0.025 * a_open * wind_speed
            q_stack = 0.61 * a_open / 3 * math.sqrt(max(delta_t, 0) * 9.81 * w["height"] / mean_temp_k)
            flows += max(q_wind, q_stack)
            face = _compass(n, north)
            facades.setdefault(face, n)
            wrows.append({"name": w["el"].name, "guid": w["el"].guid, "facade": face, "area_m2": round(w["area"], 3),
                          "openable_ratio": round(ratio, 3), "openable_source": w["ratio_source"] if w["ratio"] is not None else "assumed",
                          "openable_m2": round(a_open, 3)})

        normals = list(facades.values())
        max_angle = max((math.degrees(math.acos(max(-1, min(1, float(np.dot(a, b)))))) for a in normals for b in normals), default=0)
        mode = "none" if not normals else "single-sided" if max_angle < 45 else "cross" if max_angle > 135 else "corner"

        def depth_along(n):
            pts = np.vstack(s["polys"])
            proj = pts @ n
            return float(proj.max() - proj.min())

        if mode == "cross":
            depth, limit = max(depth_along(n) for n in normals), 5 * height
        elif mode in ("single-sided", "corner"):
            depth, limit = min(depth_along(n) for n in normals), 2.5 * height
        else:
            depth, limit = None, None

        checks = {}
        if rules.get("min_glazing_ratio"):
            req = floor * rules["min_glazing_ratio"]
            checks["glazing_area"] = {"provided_m2": round(glazing, 3), "required_m2": round(req, 3), "pass": glazing >= req - 1e-9}
        if rules.get("min_openable_ratio"):
            req = floor * rules["min_openable_ratio"]
            checks["openable_area"] = {"provided_m2": round(openable, 3), "required_m2": round(req, 3), "pass": openable >= req - 1e-9}
        checks["room_depth"] = {"mode": mode, "depth_m": round(depth, 2) if depth is not None else None,
                                "limit_m": round(limit, 2) if limit is not None else None,
                                "pass": depth is not None and depth <= limit + 1e-9}
        ach = 3600 * flows / s["vol"] if s["vol"] > 0 else 0.0
        results.append({
            "space": sp.as_dict(), "long_name": sp.name, "floor_area_m2": floor, "height_m": height,
            "windows": wrows, "facades": sorted(facades), "checks": checks,
            "indicative_ach": round(ach, 2),
            "pass": all(c["pass"] for c in checks.values()),
            "uses_assumptions": assumed,
        })

    internal = sorted({w["el"].name for w in wins if w.get("internal")})
    unassigned = sorted(w["el"].name for w in wins if not any(w in v for v in serving.values()))
    return {
        "rules": {**rules, "preset": preset, "default_openable_ratio": default_openable_ratio,
                  "depth_rule": "CIBSE AM10: single-sided <= 2.5H, cross <= 5H",
                  "airflow": f"BS 5925 single-sided, U={wind_speed} m/s, dT={delta_t} K"},
        "summary": {"spaces": len(results), "pass": sum(r["pass"] for r in results),
                    "fail": sum(not r["pass"] for r in results)},
        "spaces": results,
        "internal_windows": internal,
        "unassigned_windows": unassigned,
        "disclaimer": "Screening aid for design review, not a code-compliance certificate.",
    }

"""Markdown/JSON QA report and annotated floor plans."""
from __future__ import annotations

import json
import os

import numpy as np

from .clash import detect_clashes
from .model import IfcModel
from .quantities import takeoff, validate_qto
from .ventilation import check_ventilation

# reference palette (dataviz skill): 3 categorical slots + status colours
SURFACE, INK, INK2, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#d9d8d3"
CAT = {"openings": "#2a78d6", "structure": "#eb6834", "mep": "#1baf7a"}
GOOD, CRITICAL = "#0ca30c", "#d03b3b"
STRUCTURE = {"IFCBEAM", "IFCCOLUMN", "IFCMEMBER", "IFCBEAMSTANDARDCASE", "IFCCOLUMNSTANDARDCASE"}
MEP = {"IFCDUCTSEGMENT", "IFCPIPESEGMENT", "IFCCABLECARRIERSEGMENT", "IFCFLOWSEGMENT", "IFCDUCTFITTING", "IFCPIPEFITTING"}


def _hull(pts: np.ndarray) -> np.ndarray:
    pts = sorted(set(map(tuple, np.round(pts, 6))))
    if len(pts) < 3:
        return np.array(pts)

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower, upper = [], []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return np.array(lower[:-1] + upper[:-1])


def plot_plans(model: IfcModel, clashes: dict, vent: dict, out_dir: str) -> list[str]:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.lines import Line2D
    from matplotlib.patches import Patch, Polygon

    os.makedirs(out_dir, exist_ok=True)
    status = {r["space"]["guid"]: r["pass"] for r in vent["spaces"]}
    numbered = list(enumerate(clashes["clashes"], 1))
    paths = []
    for st in model.storeys:
        name = st["name"]
        fig, ax = plt.subplots(figsize=(8, 6.4), dpi=150)
        fig.patch.set_facecolor(SURFACE)
        ax.set_facecolor(SURFACE)
        for sp in model.spaces:
            if sp.storey != name:
                continue
            ok = status.get(sp.guid)
            for p in model.shape(sp.id).pieces:
                col = GOOD if ok else CRITICAL
                ax.add_patch(Polygon(p.base[:, :2], closed=True, facecolor=col, alpha=0.10, edgecolor="none"))
            pts = np.vstack([p.base[:, :2] for p in model.shape(sp.id).pieces])
            c = np.array([pts[:, 0].min() + 0.62 * np.ptp(pts[:, 0]), pts[:, 1].min() + 0.72 * np.ptp(pts[:, 1])])  # clear of mid-room beams
            label = "ventilation PASS" if ok else "ventilation FAIL"
            ax.text(c[0], c[1] + 0.25, sp.name, ha="center", va="bottom", fontsize=10, color=INK, weight="bold")
            ax.text(c[0], c[1] - 0.05, ("✓ " if ok else "✗ ") + label, ha="center", va="top", fontsize=8,
                    color=GOOD if ok else CRITICAL)
        for el in model.elements:
            if el.storey != name or el.type.startswith("IFCSLAB") or el.type == "IFCROOF":
                continue
            sh = model.shape(el.id)
            if sh.empty:
                continue
            hull = _hull(sh.vertices()[:, :2])
            if len(hull) < 3:
                continue
            if el.type in ("IFCWINDOW", "IFCDOOR"):
                fc, ec, lw, z = CAT["openings"], CAT["openings"], 0.5, 4
            elif el.type in STRUCTURE:
                fc, ec, lw, z = CAT["structure"], SURFACE, 1.0, 3
            elif el.type in MEP:
                fc, ec, lw, z = CAT["mep"], SURFACE, 1.0, 5
            else:
                fc, ec, lw, z = INK2, SURFACE, 1.0, 2
            ax.add_patch(Polygon(hull, closed=True, facecolor=fc, edgecolor=ec, linewidth=lw, zorder=z,
                                 alpha=0.85 if el.type in STRUCTURE else 1.0))
        z0 = st["elevation"] or 0.0
        nxt = [s["elevation"] for s in model.storeys if (s["elevation"] or 0) > z0]
        z1 = min(nxt) if nxt else z0 + 100
        for i, c in numbered:
            if not (z0 - 0.3 <= c["location"][2] < z1 - 0.3):
                continue
            x, y = c["location"][:2]
            ax.plot(x, y, marker="o", markersize=15, color=CRITICAL, markeredgecolor=SURFACE, markeredgewidth=2, zorder=10)
            ax.text(x, y, str(i), ha="center", va="center", color="white", fontsize=8, weight="bold", zorder=11)
        ax.set_aspect("equal")
        ax.autoscale_view()
        x0, x1 = ax.get_xlim()
        y0, y1 = ax.get_ylim()
        ax.set_xlim(x0 - 0.8, x1 + 1.2)
        ax.set_ylim(y0 - 0.8, y1 + 0.8)
        for s in ax.spines.values():
            s.set_visible(False)
        ax.tick_params(colors=INK2, labelsize=8, length=0)
        ax.grid(color=GRID, linewidth=0.5, zorder=0)
        ax.set_axisbelow(True)
        ax.set_xlabel("x (m)", color=INK2, fontsize=8)
        ax.set_ylabel("y (m)", color=INK2, fontsize=8)
        ax.set_title(f"{model.summary()['project']} — {name}", loc="left", color=INK, fontsize=12, weight="bold")
        handles = [
            Patch(color=INK2, label="walls"), Patch(color=CAT["openings"], label="windows / doors"),
            Patch(color=CAT["structure"], label="structure"), Patch(color=CAT["mep"], label="MEP"),
            Line2D([], [], marker="o", color=CRITICAL, linestyle="none", markersize=9, label="clash (numbered)"),
        ]
        ax.legend(handles=handles, loc="upper center", bbox_to_anchor=(0.5, -0.1), ncol=5, frameon=False,
                  fontsize=8, labelcolor=INK2)
        fig.tight_layout()
        p = os.path.join(out_dir, f"plan_{name.lower().replace(' ', '_')}.png")
        fig.savefig(p, facecolor=SURFACE, bbox_inches="tight", pad_inches=0.2)
        plt.close(fig)
        paths.append(p)
    return paths


def build_report(model: IfcModel, out_dir: str, clearance: float = 0.05, preset: str = "hk_bpr",
                 plots: bool = True) -> dict:
    os.makedirs(out_dir, exist_ok=True)
    summ = model.summary()
    qto = takeoff(model, "type")
    val = validate_qto(model)
    cl = detect_clashes(model, clearance=clearance)
    vent = check_ventilation(model, preset=preset)
    images = plot_plans(model, cl, vent, out_dir) if plots else []

    L = [f"# IFC QA report — {summ['file']}", ""]
    L += [f"Schema **{summ['schema']}** · {sum(summ['elements_by_type'].values())} elements · "
          f"{len(summ['storeys'])} storeys · {summ['spaces']} spaces · length unit {summ['units']['LENGTHUNIT']}", ""]
    s = cl["summary"]
    L += ["## At a glance", "",
          f"- Clashes: **{s['hard']} hard**, **{s['duplicate']} duplicate**, **{s['clearance']} clearance** (< {clearance*1000:.0f} mm)",
          f"- Authored quantities that disagree with geometry: **{len(val['discrepancies'])}** of {val['checked']} checked",
          f"- Natural ventilation ({vent['rules']['source']}): **{vent['summary']['pass']} pass / {vent['summary']['fail']} fail**",
          ""]
    L += ["## Clashes", "", "| # | kind | element A | element B | depth / gap (mm) | location (x, y, z) |", "|---|---|---|---|---|---|"]
    for i, c in enumerate(cl["clashes"], 1):
        v = c.get("penetration_m", c.get("gap_m"))
        L.append(f"| {i} | {c['kind']} | {c['a']['name']} ({c['a']['type'][3:]}) | {c['b']['name']} ({c['b']['type'][3:]}) "
                 f"| {v*1000:.0f} | {', '.join(f'{x:.2f}' for x in c['location'])} |")
    L += ["", "## Natural ventilation", "",
          "| space | floor m² | facades | glazing m² (req.) | openable m² (req.) | depth m (limit) | ACH* | result |",
          "|---|---|---|---|---|---|---|---|"]
    for r in vent["spaces"]:
        ch = r["checks"]
        g = ch.get("glazing_area", {})
        o = ch.get("openable_area", {})
        d = ch["room_depth"]
        L.append(f"| {r['long_name']} | {r['floor_area_m2']:.2f} | {'+'.join(r['facades']) or '-'} ({d['mode']}) "
                 f"| {g.get('provided_m2', '-')} ({g.get('required_m2', '-')}) | {o.get('provided_m2', '-')} ({o.get('required_m2', '-')}) "
                 f"| {d['depth_m']} ({d['limit_m']}) | {r['indicative_ach']} | {'PASS' if r['pass'] else 'FAIL'} |")
    L += ["", f"*Indicative air changes per hour, {vent['rules']['airflow']}. {vent['disclaimer']}", ""]
    L += ["## Quantity take-off (by IFC class)", "", "| class | count | length m | area m\u00b2 (walls: net side) | gross vol m\u00b3 | net vol m\u00b3 |",
          "|---|---|---|---|---|---|"]
    for g, d in qto["groups"].items():
        L.append(f"| {g[3:]} | {d.get('count', 0):.0f} | {d.get('length', '')} | {d.get('area', d.get('net_side_area', ''))} "
                 f"| {d.get('gross_volume', '')} | {d.get('net_volume', '')} |")
    if val["discrepancies"]:
        L += ["", "## Authored quantities vs geometry", "", "| element | quantity | authored | computed | diff % |", "|---|---|---|---|---|"]
        for d in val["discrepancies"]:
            L.append(f"| {d['element']['name']} | {d['qto_set']}.{d['quantity']} | {d['authored']} | {d['computed']} | {d['difference_pct']:+.1f} |")
    if images:
        L += ["", "## Plans", ""] + [f"![{os.path.basename(p)}]({os.path.basename(p)})" for p in images]
    notes = summ["geometry_notes"]
    if notes:
        L += ["", "## Geometry notes", ""] + [f"- {k}: {v} element(s)" for k, v in notes.items()]
    md = "\n".join(L) + "\n"
    md_path = os.path.join(out_dir, "report.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(md)
    data = {"summary": summ, "quantities": qto, "qto_validation": val, "clashes": cl, "ventilation": vent}
    json_path = os.path.join(out_dir, "report.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, default=float)
    return {"markdown": md_path, "json": json_path, "images": images}

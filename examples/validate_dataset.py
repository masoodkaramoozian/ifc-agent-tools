"""Batch-validate ifcagent on a folder of IFC files (e.g. the IFCNet dataset).

For every .ifc file under FOLDER (recursively) it records whether parsing
succeeded, how many elements got geometry, whether that geometry is exact or
approximated, the computed quantities and the run time. With ``--crosscheck``
it also computes each element's volume with IfcOpenShell (an independent,
OpenCASCADE-based geometry kernel) and reports the agreement.

    python examples/validate_dataset.py path/to/IFCNetCore --limit 200
    pip install ifcopenshell
    python examples/validate_dataset.py path/to/IFCNetCore --limit 200 --crosscheck

Writes  <out>/elements.csv, <out>/files.csv and <out>/summary.md
"""
from __future__ import annotations

import argparse
import csv
import glob
import os
import statistics
import sys
import time
import traceback
from collections import Counter

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from ifcagent.model import IfcModel  # noqa: E402
from ifcagent.quantities import element_quantities  # noqa: E402


def ifcopenshell_volumes(path: str) -> dict[str, float]:
    """GlobalId -> volume (m3) from IfcOpenShell. Needs `pip install ifcopenshell`."""
    import ifcopenshell
    import ifcopenshell.geom
    import ifcopenshell.util.shape
    import ifcopenshell.util.unit

    f = ifcopenshell.open(path)
    scale = ifcopenshell.util.unit.calculate_unit_scale(f)
    settings = ifcopenshell.geom.settings()
    out = {}
    for el in f.by_type("IfcElement"):
        if el.is_a("IfcOpeningElement") or not el.Representation:
            continue
        try:
            shape = ifcopenshell.geom.create_shape(settings, el)
            vol = ifcopenshell.util.shape.get_volume(shape.geometry)
            # the geometry iterator already converts to metres in recent versions;
            # guard against files where it did not
            out[el.GlobalId] = vol if scale == 1 or vol < 1e6 else vol * scale ** 3
        except Exception:  # noqa: BLE001 - some elements have no solid
            continue
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("folder")
    ap.add_argument("--limit", type=int, default=0, help="max files (0 = all)")
    ap.add_argument("--out", default="validation_results")
    ap.add_argument("--crosscheck", action="store_true", help="compare volumes with IfcOpenShell")
    args = ap.parse_args(argv)

    files = sorted(glob.glob(os.path.join(args.folder, "**", "*.ifc"), recursive=True))
    if args.limit:
        files = files[: args.limit]
    if not files:
        print(f"no .ifc files under {args.folder}")
        return 1
    os.makedirs(args.out, exist_ok=True)

    file_rows, el_rows, errors = [], [], Counter()
    for i, path in enumerate(files, 1):
        rel = os.path.relpath(path, args.folder)
        t0 = time.perf_counter()
        try:
            m = IfcModel(path)
            summ = m.summary()
            ref = {}
            if args.crosscheck:
                try:
                    ref = ifcopenshell_volumes(path)
                except ImportError:
                    print("ifcopenshell not installed - run: pip install ifcopenshell")
                    return 1
                except Exception as exc:  # noqa: BLE001
                    errors[f"ifcopenshell: {type(exc).__name__}"] += 1
            for el in m.elements:
                sh = m.shape(el.id)
                q = element_quantities(m, el)
                r = ref.get(el.guid)
                diff = (sh.volume - r) / r * 100 if r else None
                el_rows.append({
                    "file": rel, "guid": el.guid, "type": el.type, "has_geometry": not sh.empty,
                    "approximate": sh.approx, "notes": "; ".join(sh.notes),
                    "volume_m3": round(sh.volume, 6), "length_m": q.get("length"),
                    "area_m2": q.get("area", q.get("net_side_area")),
                    "ifcopenshell_volume_m3": round(r, 6) if r else None,
                    "volume_diff_pct": round(diff, 3) if diff is not None else None,
                })
            file_rows.append({"file": rel, "ok": True, "schema": summ["schema"], "elements": len(m.elements),
                              "without_geometry": summ["elements_without_geometry"],
                              "seconds": round(time.perf_counter() - t0, 3), "error": ""})
        except Exception as exc:  # noqa: BLE001
            errors[type(exc).__name__] += 1
            file_rows.append({"file": rel, "ok": False, "schema": "", "elements": 0, "without_geometry": 0,
                              "seconds": round(time.perf_counter() - t0, 3),
                              "error": f"{type(exc).__name__}: {exc}"[:200]})
            if os.environ.get("IFCAGENT_DEBUG"):
                traceback.print_exc()
        if i % 25 == 0 or i == len(files):
            print(f"{i}/{len(files)} files")

    for name, rows in (("files.csv", file_rows), ("elements.csv", el_rows)):
        if rows:
            with open(os.path.join(args.out, name), "w", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=list(rows[0]))
                w.writeheader()
                w.writerows(rows)

    ok = [r for r in file_rows if r["ok"]]
    geo = [r for r in el_rows if r["has_geometry"]]
    exact = [r for r in geo if not r["approximate"]]
    notes = Counter(n.split(":")[0] for r in el_rows for n in r["notes"].split("; ") if n)
    L = ["# ifcagent dataset validation", "",
         f"- Folder: `{os.path.basename(os.path.abspath(args.folder))}` - {len(files)} IFC files",
         f"- Parsed without error: **{len(ok)} / {len(files)}** ({100 * len(ok) / len(files):.1f} %)",
         f"- Elements: {len(el_rows)}; with geometry: **{len(geo)}** ({100 * len(geo) / max(len(el_rows), 1):.1f} %); "
         f"exact geometry: {len(exact)} ({100 * len(exact) / max(len(geo), 1):.1f} % of those with geometry)",
         f"- Median parse + analysis time per file: {statistics.median(r['seconds'] for r in file_rows):.3f} s", ""]
    if errors:
        L += ["## Failures by type", ""] + [f"- {k}: {v}" for k, v in errors.most_common()] + [""]
    if notes:
        L += ["## Geometry notes (approximations / unsupported items)", ""] + [f"- {k}: {v}" for k, v in notes.most_common(15)] + [""]
    diffs = [abs(r["volume_diff_pct"]) for r in el_rows if r["volume_diff_pct"] is not None]
    if diffs:
        diffs.sort()
        within = lambda t: 100 * sum(d <= t for d in diffs) / len(diffs)  # noqa: E731
        L += ["## Volume agreement with IfcOpenShell", "",
              f"- Elements compared: {len(diffs)}",
              f"- |difference| <= 1 %: **{within(1):.1f} %**, <= 5 %: {within(5):.1f} %",
              f"- Median |difference|: {statistics.median(diffs):.3f} %, 90th percentile: {diffs[int(0.9 * (len(diffs) - 1))]:.3f} %",
              ""]
    by_class = Counter(r["type"] for r in el_rows)
    L += ["## Elements by class", "", "| class | elements |", "|---|---|"] + [f"| {k} | {v} |" for k, v in by_class.most_common()]
    with open(os.path.join(args.out, "summary.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")
    print("\n".join(L[:8]))
    print(f"\nwrote {args.out}/summary.md, files.csv, elements.csv")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Command line interface:  ``python -m ifcagent <command> model.ifc``"""
from __future__ import annotations

import argparse
import json
import sys

from .clash import detect_clashes
from .model import IfcModel
from .quantities import takeoff, validate_qto
from .report import build_report
from .ventilation import check_ventilation


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="ifcagent", description="IFC quantity take-off, clash detection and ventilation checks")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def add(name, help_):
        p = sub.add_parser(name, help=help_)
        p.add_argument("ifc")
        return p

    add("summary", "schema, units, storeys, element counts")
    p = add("qto", "quantity take-off")
    p.add_argument("--group-by", default="type", choices=["type", "storey", "type_storey", "element"])
    p.add_argument("--types", nargs="*", help="IFC classes, e.g. IfcWall IfcSlab")
    p = add("validate", "compare authored Qto sets against geometry")
    p.add_argument("--tol", type=float, default=0.05)
    p = add("clash", "clash detection")
    p.add_argument("-a", "--group-a", nargs="*")
    p.add_argument("-b", "--group-b", nargs="*")
    p.add_argument("--tolerance", type=float, default=0.01, help="contact tolerance in m (default 0.01)")
    p.add_argument("--clearance", type=float, default=0.0, help="clearance distance in m")
    p = add("ventilation", "natural ventilation screening of spaces")
    p.add_argument("--preset", default="hk_bpr", choices=["hk_bpr", "uk_adf2010", "custom"])
    p.add_argument("--default-openable", type=float, default=0.5)
    p = add("report", "full QA report (markdown + json + plan images)")
    p.add_argument("-o", "--out", default="ifc_report")
    p.add_argument("--clearance", type=float, default=0.05)
    p.add_argument("--preset", default="hk_bpr")
    args = ap.parse_args(argv)

    m = IfcModel(args.ifc)
    if args.cmd == "summary":
        out = m.summary()
    elif args.cmd == "qto":
        out = takeoff(m, args.group_by, args.types)
    elif args.cmd == "validate":
        out = validate_qto(m, args.tol)
    elif args.cmd == "clash":
        out = detect_clashes(m, args.group_a, args.group_b, args.tolerance, args.clearance)
    elif args.cmd == "ventilation":
        out = check_ventilation(m, args.preset, default_openable_ratio=args.default_openable)
    else:
        out = build_report(m, args.out, args.clearance, args.preset)
    json.dump(out, sys.stdout, indent=2, default=float)
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

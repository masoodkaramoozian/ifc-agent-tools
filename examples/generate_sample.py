"""Generate ``sample_house.ifc`` - a small two-storey IFC4 house used for demos and tests.

The model is intentionally *imperfect*, the way real federated models are:

* a concrete beam runs straight through a partition wall            -> hard clash
* a supply-air duct penetrates the east facade with no opening       -> hard clash
* a partition wall on the first floor was duplicated                -> duplicate / hard clash
* a water pipe runs 30 mm below the beam                             -> clearance clash (at 50 mm)
* one wall carries a stale authored NetVolume in Qto_WallBaseQuantities -> QTO discrepancy
* the kitchen and bedroom 1 are under-ventilated                     -> ventilation failures

Everything else (walls sitting on slabs, windows inside their host walls,
walls butting at corners) is *correct* and must NOT be reported.

Run:  python examples/generate_sample.py  [output.ifc]
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from ifcagent.writer import DERIVED, Enum, IfcWriter, Typed, stable_guid  # noqa: E402

W = IfcWriter("IFC4")
_counter = {"n": 0}


def gid(name: str) -> str:
    _counter["n"] += 1
    return stable_guid("sample_house", name, str(_counter["n"]))


# ---------------------------------------------------------------- geometry helpers
def pt(*c):
    return W.add("IFCCARTESIANPOINT", tuple(float(x) for x in c))


def dr(*c):
    return W.add("IFCDIRECTION", tuple(float(x) for x in c))


def a2p3(loc=(0, 0, 0), axis=None, ref=None):
    return W.add("IFCAXIS2PLACEMENT3D", pt(*loc), dr(*axis) if axis else None, dr(*ref) if ref else None)


def a2p2(loc=(0, 0)):
    return W.add("IFCAXIS2PLACEMENT2D", pt(*loc), None)


def placement(rel_to, loc=(0, 0, 0), axis=None, ref=None):
    return W.add("IFCLOCALPLACEMENT", rel_to, a2p3(loc, axis, ref))


def shape(item, rep_type="SweptSolid"):
    rep = W.add("IFCSHAPEREPRESENTATION", BODY, "Body", rep_type, (item,))
    return W.add("IFCPRODUCTDEFINITIONSHAPE", None, None, (rep,))


def rect_prism(xdim, ydim, depth, centre=None, position=None, direction=(0, 0, 1)):
    centre = centre if centre is not None else (xdim / 2, ydim / 2)
    prof = W.add("IFCRECTANGLEPROFILEDEF", Enum("AREA"), None, a2p2(centre), float(xdim), float(ydim))
    return W.add("IFCEXTRUDEDAREASOLID", prof, position or a2p3(), dr(*direction), float(depth))


def box(storey_pl, x0, y0, z0, x1, y1, z1, along="x"):
    """Placement + body for an axis-aligned box given in storey coordinates.

    ``along='y'`` rotates the local X axis onto world +Y (as authoring tools do
    for walls drawn in the Y direction) so the parser's rotation handling is
    exercised.
    """
    if along == "x":
        pl = placement(storey_pl, (x0, y0, z0))
        body = rect_prism(x1 - x0, y1 - y0, z1 - z0)
    else:  # local X -> world +Y, local Y -> world -X
        pl = placement(storey_pl, (x1, y0, z0), axis=(0, 0, 1), ref=(0, 1, 0))
        body = rect_prism(y1 - y0, x1 - x0, z1 - z0)
    return pl, shape(body)


# ---------------------------------------------------------------- project setup
units = W.add(
    "IFCUNITASSIGNMENT",
    (
        W.add("IFCSIUNIT", DERIVED, Enum("LENGTHUNIT"), None, Enum("METRE")),
        W.add("IFCSIUNIT", DERIVED, Enum("AREAUNIT"), None, Enum("SQUARE_METRE")),
        W.add("IFCSIUNIT", DERIVED, Enum("VOLUMEUNIT"), None, Enum("CUBIC_METRE")),
        W.add("IFCSIUNIT", DERIVED, Enum("PLANEANGLEUNIT"), None, Enum("RADIAN")),
    ),
)
CTX = W.add("IFCGEOMETRICREPRESENTATIONCONTEXT", None, "Model", 3, 1.0e-5, a2p3(), None)
BODY = W.add("IFCGEOMETRICREPRESENTATIONSUBCONTEXT", "Body", "Model", DERIVED, DERIVED, DERIVED, DERIVED, CTX, None, Enum("MODEL_VIEW"), None)
project = W.add("IFCPROJECT", gid("project"), None, "Sample House", "ifc-agent-tools demo model", None, None, None, (CTX,), units)

site_pl = placement(None)
site = W.add("IFCSITE", gid("site"), None, "Site", None, None, site_pl, None, None, Enum("ELEMENT"), None, None, 0.0, None, None)
bldg_pl = placement(site_pl)
building = W.add("IFCBUILDING", gid("building"), None, "Two-storey house", None, None, bldg_pl, None, None, Enum("ELEMENT"), None, None, None)

STOREY_H = 3.2
storeys, storey_pls, contents = [], [], []
for i, name in enumerate(["Ground Floor", "First Floor"]):
    pl = placement(bldg_pl, (0, 0, i * STOREY_H))
    st = W.add("IFCBUILDINGSTOREY", gid(name), None, name, None, None, pl, None, None, Enum("ELEMENT"), i * STOREY_H)
    storeys.append(st)
    storey_pls.append(pl)
    contents.append([])

W.add("IFCRELAGGREGATES", gid("agg1"), None, None, None, project, (site,))
W.add("IFCRELAGGREGATES", gid("agg2"), None, None, None, site, (building,))
W.add("IFCRELAGGREGATES", gid("agg3"), None, None, None, building, tuple(storeys))


def pset(obj, name, props: dict):
    vals = []
    for k, v in props.items():
        if isinstance(v, bool):
            nv = Typed("IFCBOOLEAN", v)
        elif isinstance(v, float):
            nv = Typed("IFCREAL", v)
        else:
            nv = Typed("IFCLABEL", str(v))
        vals.append(W.add("IFCPROPERTYSINGLEVALUE", k, None, nv, None))
    ps = W.add("IFCPROPERTYSET", gid(name), None, name, None, tuple(vals))
    W.add("IFCRELDEFINESBYPROPERTIES", gid("rdp"), None, None, None, (obj,), ps)


def qto(obj, name, qs: dict):
    items = []
    for k, (kind, v) in qs.items():
        items.append(W.add(f"IFCQUANTITY{kind}", k, None, None, float(v), None))
    q = W.add("IFCELEMENTQUANTITY", gid(name), None, name, None, None, tuple(items))
    W.add("IFCRELDEFINESBYPROPERTIES", gid("rdq"), None, None, None, (obj,), q)


def element(ifc_type, storey, name, geom, *tail, props=None):
    pl, body = geom
    e = W.add(ifc_type, gid(name), None, name, None, None, pl, body, name.split(" ")[1] if " " in name else name, *tail)
    contents[storey].append(e)
    if props:
        pset(e, *props)
    return e


# ---------------------------------------------------------------- slabs
L, D, T_EXT, T_INT, H = 10.0, 8.0, 0.2, 0.1, 3.0
for s in range(2):
    element("IFCSLAB", s, f"Slab {['GF', '1F'][s]}", box(storey_pls[s], 0, 0, -0.2, L, D, 0.0), Enum("FLOOR"),
            props=("Pset_SlabCommon", {"IsExternal": False, "LoadBearing": True}))
element("IFCSLAB", 1, "Slab Roof", box(storey_pls[1], 0, 0, H, L, D, H + 0.2), Enum("ROOF"),
        props=("Pset_SlabCommon", {"IsExternal": True, "LoadBearing": True}))

# ---------------------------------------------------------------- walls
walls = {}


def wall(s, key, x0, y0, x1, y1, along, external=True):
    w = element("IFCWALL", s, f"Wall {key}", box(storey_pls[s], x0, y0, 0.0, x1, y1, H, along), Enum("STANDARD"),
                props=("Pset_WallCommon", {"IsExternal": external, "LoadBearing": external}))
    walls[(s, key)] = (w, x0, y0, x1, y1, along)
    return w


INT_X = {0: 6.0, 1: 5.0}  # partition position per storey
for s in range(2):
    tag = ["GF", "1F"][s]
    wall(s, f"{tag}-S", 0, 0, L, T_EXT, "x")
    wall(s, f"{tag}-N", 0, D - T_EXT, L, D, "x")
    wall(s, f"{tag}-W", 0, T_EXT, T_EXT, D - T_EXT, "y")
    wall(s, f"{tag}-E", L - T_EXT, T_EXT, L, D - T_EXT, "y")
    wall(s, f"{tag}-INT", INT_X[s], T_EXT, INT_X[s] + T_INT, D - T_EXT, "y", external=False)

# Error 1: duplicated partition on the first floor (copy/paste in the authoring tool)
dup = element("IFCWALL", 1, "Wall 1F-INT (copy)", box(storey_pls[1], 5.0, T_EXT, 0, 5.1, D - T_EXT, H, "y"), Enum("STANDARD"),
              props=("Pset_WallCommon", {"IsExternal": False, "LoadBearing": False}))

# Authored base quantities for the ground-floor facade walls.  Wall GF-N carries a
# stale NetVolume (exported before its window was added / changed).
OPENINGS_M2 = {"GF-S": 1.8 * 1.5 * 2 + 1.0 * 2.1, "GF-N": 1.2 * 1.2}
for key, stale in [("GF-S", False), ("GF-N", True)]:
    w, x0, y0, x1, y1, along = walls[(0, key)]
    length = max(x1 - x0, y1 - y0)
    gross = length * T_EXT * H
    net = gross - OPENINGS_M2[key] * T_EXT
    qto(w, "Qto_WallBaseQuantities", {
        "Length": ("LENGTH", length), "Width": ("LENGTH", T_EXT), "Height": ("LENGTH", H),
        "GrossSideArea": ("AREA", length * H), "NetSideArea": ("AREA", length * H - OPENINGS_M2[key]),
        "GrossVolume": ("VOLUME", gross),
        # stale value: exported when the wall was still 220 mm thick
        "NetVolume": ("VOLUME", gross * 1.1 if stale else net),
    })

# ---------------------------------------------------------------- window types
PANEL = {
    "W-A casement+fixed": ["SIDEHUNGLEFTHAND", "FIXEDCASEMENT"],
    "W-B sliding": ["SLIDINGHORIZONTAL", "SLIDINGHORIZONTAL"],
    "W-C fixed picture": ["FIXEDCASEMENT"],
    "W-D top-hung": ["TOPHUNG"],
}
wtypes = {}
for tname, ops in PANEL.items():
    panels = tuple(
        W.add("IFCWINDOWPANELPROPERTIES", gid(tname + op + str(i)), None, None, None, Enum(op), Enum("NOTDEFINED"), 0.06, 0.05, None)
        for i, op in enumerate(ops)
    )
    part = "SINGLE_PANEL" if len(ops) == 1 else "DOUBLE_PANEL_VERTICAL"
    wtypes[tname] = (W.add("IFCWINDOWTYPE", gid(tname), None, tname, None, None, panels, None, None, None,
                           Enum("WINDOW"), Enum(part), False, None), [])

# ---------------------------------------------------------------- windows & doors
def opening_in(kind, s, wall_key, offset, sill, width, height, name, wtype=None):
    """Create an opening in a wall and fill it with a window or door."""
    w, x0, y0, x1, y1, along = walls[(s, wall_key)]
    thick = (y1 - y0) if along == "x" else (x1 - x0)
    if along == "x":
        o = (x0 + offset, y0, sill, x0 + offset + width, y1, sill + height)
        f = (x0 + offset, y0 + thick / 2 - 0.05, sill, x0 + offset + width, y0 + thick / 2 + 0.05, sill + height)
    else:
        o = (x0, y0 + offset, sill, x1, y0 + offset + width, sill + height)
        f = (x0 + thick / 2 - 0.05, y0 + offset, sill, x0 + thick / 2 + 0.05, y0 + offset + width, sill + height)
    opl, obody = box(storey_pls[s], *o, along=along)
    op = W.add("IFCOPENINGELEMENT", gid("open " + name), None, "Opening " + name, None, None, opl, obody, None, Enum("OPENING"))
    W.add("IFCRELVOIDSELEMENT", gid("void " + name), None, None, None, w, op)
    geom = box(storey_pls[s], *f, along=along)
    if kind == "window":
        el = element("IFCWINDOW", s, name, geom, float(height), float(width), Enum("WINDOW"), None, None)
        wtypes[wtype][1].append(el)
    else:
        el = element("IFCDOOR", s, name, geom, float(height), float(width), Enum("DOOR"), Enum("SINGLE_SWING_LEFT"), None)
    W.add("IFCRELFILLSELEMENT", gid("fill " + name), None, None, None, op, el)
    return el


# Ground floor: living room (west) and kitchen (east of x=6.1)
opening_in("window", 0, "GF-S", 0.8, 0.9, 1.8, 1.5, "Win L1", "W-A casement+fixed")
opening_in("window", 0, "GF-S", 3.4, 0.9, 1.8, 1.5, "Win L2", "W-B sliding")
opening_in("window", 0, "GF-W", 3.0, 0.9, 1.2, 1.5, "Win L3", "W-A casement+fixed")
opening_in("window", 0, "GF-N", 7.3, 1.2, 1.2, 1.2, "Win K1", "W-A casement+fixed")
opening_in("door", 0, "GF-S", 7.5, 0.0, 1.0, 2.1, "Door Entrance")
opening_in("door", 0, "GF-INT", 5.0, 0.0, 0.9, 2.1, "Door GF-INT")
# First floor: bedroom 1 (west) and bedroom 2 (east of x=5.1)
opening_in("window", 1, "1F-S", 1.2, 0.9, 2.4, 1.6, "Win B1a", "W-C fixed picture")
opening_in("window", 1, "1F-W", 5.0, 1.6, 0.6, 0.6, "Win B1b", "W-D top-hung")
opening_in("window", 1, "1F-E", 2.0, 0.9, 1.5, 1.5, "Win B2a", "W-B sliding")
opening_in("window", 1, "1F-S", 7.0, 0.9, 1.5, 1.5, "Win B2b", "W-B sliding")
opening_in("door", 1, "1F-INT", 1.0, 0.0, 0.9, 2.1, "Door 1F-INT")

for tname, (t, occ) in wtypes.items():
    if occ:
        W.add("IFCRELDEFINESBYTYPE", gid("rdt " + tname), None, None, None, tuple(occ), t)

# ---------------------------------------------------------------- structure & MEP
# Error 2: beam passes through the ground-floor partition (x = 6.0 .. 6.1)
element("IFCBEAM", 0, "Beam B1 300x500", box(storey_pls[0], T_EXT, 3.85, 2.5, L - T_EXT, 4.15, 3.0), Enum("BEAM"),
        props=("Pset_BeamCommon", {"LoadBearing": True}))

# Error 3: supply duct penetrates the east facade without a builder's-work opening
element("IFCDUCTSEGMENT", 0, "Duct SA-01 400x300", box(storey_pls[0], 6.1, 1.8, 2.6, 11.0, 2.2, 2.9), Enum("RIGIDSEGMENT"))

# Error 4 (soft): DN100 pipe 30 mm under the beam soffit, extruded along +Y
r = 0.05
pipe_pl = placement(storey_pls[0], (3.0, 0.3, 2.5 - 0.03 - r))
circ = W.add("IFCCIRCLEPROFILEDEF", Enum("AREA"), None, a2p2((0, 0)), r)
pipe_body = W.add("IFCEXTRUDEDAREASOLID", circ, a2p3((0, 0, 0), axis=(0, 1, 0), ref=(1, 0, 0)), dr(0, 0, 1), 7.4)
element("IFCPIPESEGMENT", 0, "Pipe CW-01 DN100", (pipe_pl, shape(pipe_body)), Enum("RIGIDSEGMENT"))

# ---------------------------------------------------------------- spaces
spaces = [
    (0, "Living", 0.2, 0.2, 6.0, 7.8),
    (0, "Kitchen", 6.1, 0.2, 9.8, 7.8),
    (1, "Bedroom 1", 0.2, 0.2, 5.0, 7.8),
    (1, "Bedroom 2", 5.1, 0.2, 9.8, 7.8),
]
by_storey = {0: [], 1: []}
for s, name, x0, y0, x1, y1 in spaces:
    pl, body = box(storey_pls[s], x0, y0, 0.0, x1, y1, H)
    sp = W.add("IFCSPACE", gid(name), None, name, None, None, pl, body, name, Enum("ELEMENT"), Enum("INTERNAL"), None)
    pset(sp, "Pset_SpaceCommon", {"Reference": name, "IsExternal": False, "NaturalVentilation": True})
    by_storey[s].append(sp)
for s in range(2):
    W.add("IFCRELAGGREGATES", gid(f"aggsp{s}"), None, None, None, storeys[s], tuple(by_storey[s]))
    W.add("IFCRELCONTAINEDINSPATIALSTRUCTURE", gid(f"cont{s}"), None, None, None, tuple(contents[s]), storeys[s])

if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(__file__), "sample_house.ifc")
    W.write(out, name=os.path.basename(out), author="ifc-agent-tools", org="")
    print(f"wrote {out} ({len(W.lines)} entities)")

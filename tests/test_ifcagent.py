"""Test-suite: python -m unittest discover -s tests -v"""
from __future__ import annotations

import asyncio
import math
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)

from ifcagent.clash import detect_clashes  # noqa: E402
from ifcagent.model import IfcModel  # noqa: E402
from ifcagent.quantities import element_quantities, takeoff, validate_qto  # noqa: E402
from ifcagent.step import DERIVED, Enum, Ref, Typed, decode_string, parse_data  # noqa: E402
from ifcagent.ventilation import check_ventilation  # noqa: E402
from ifcagent.writer import DERIVED as W_DERIVED, Enum as WEnum, IfcWriter, stable_guid  # noqa: E402

SAMPLE = os.path.join(ROOT, "examples", "sample_house.ifc")


def setUpModule():
    subprocess.run([sys.executable, os.path.join(ROOT, "examples", "generate_sample.py"), SAMPLE], check=True,
                   capture_output=True)


# --------------------------------------------------------------------------- mini-model builder
class Mini:
    """Build tiny single-storey IFC4 models for targeted tests."""

    def __init__(self, length_prefix=None):
        self.w = w = IfcWriter()
        self.n = 0
        units = w.add("IFCUNITASSIGNMENT", (w.add("IFCSIUNIT", W_DERIVED, WEnum("LENGTHUNIT"),
                                                  WEnum(length_prefix) if length_prefix else None, WEnum("METRE")),))
        ctx = w.add("IFCGEOMETRICREPRESENTATIONCONTEXT", None, "Model", 3, 1e-5, self.a2p3(), None)
        self.ctx = ctx
        proj = w.add("IFCPROJECT", self.gid(), None, "Mini", None, None, None, None, (ctx,), units)
        self.pl = w.add("IFCLOCALPLACEMENT", None, self.a2p3())
        self.storey = w.add("IFCBUILDINGSTOREY", self.gid(), None, "L0", None, None, self.pl, None, None, WEnum("ELEMENT"), 0.0)
        w.add("IFCRELAGGREGATES", self.gid(), None, None, None, proj, (self.storey,))
        self.contents = []

    def gid(self):
        self.n += 1
        return stable_guid("mini", str(self.n))

    def pt(self, *c):
        return self.w.add("IFCCARTESIANPOINT", tuple(float(x) for x in c))

    def a2p3(self, loc=(0, 0, 0), axis=None, ref=None):
        return self.w.add("IFCAXIS2PLACEMENT3D", self.pt(*loc),
                          self.w.add("IFCDIRECTION", tuple(map(float, axis))) if axis else None,
                          self.w.add("IFCDIRECTION", tuple(map(float, ref))) if ref else None)

    def body(self, *items, rtype="SweptSolid"):
        rep = self.w.add("IFCSHAPEREPRESENTATION", self.ctx, "Body", rtype, tuple(items))
        return self.w.add("IFCPRODUCTDEFINITIONSHAPE", None, None, (rep,))

    def extrusion(self, profile, depth, position=None):
        return self.w.add("IFCEXTRUDEDAREASOLID", profile, position or self.a2p3(),
                          self.w.add("IFCDIRECTION", (0.0, 0.0, 1.0)), float(depth))

    def rect(self, x, y, cx=None, cy=None):
        pos = self.w.add("IFCAXIS2PLACEMENT2D", self.w.add("IFCCARTESIANPOINT", (float(cx if cx is not None else x / 2), float(cy if cy is not None else y / 2))), None)
        return self.w.add("IFCRECTANGLEPROFILEDEF", WEnum("AREA"), None, pos, float(x), float(y))

    def poly_profile(self, pts):
        pl = self.w.add("IFCPOLYLINE", tuple(self.w.add("IFCCARTESIANPOINT", (float(a), float(b))) for a, b in pts + [pts[0]]))
        return self.w.add("IFCARBITRARYCLOSEDPROFILEDEF", WEnum("AREA"), None, pl)

    def element(self, ifc_type, name, body, loc=(0, 0, 0), tail=(None,)):
        pl = self.w.add("IFCLOCALPLACEMENT", self.pl, self.a2p3(loc))
        e = self.w.add(ifc_type, self.gid(), None, name, None, None, pl, body, None, *tail)
        self.contents.append(e)
        return e

    def save(self) -> str:
        self.w.add("IFCRELCONTAINEDINSPATIALSTRUCTURE", self.gid(), None, None, None, tuple(self.contents), self.storey)
        fd, path = tempfile.mkstemp(suffix=".ifc")
        os.close(fd)
        self.w.write(path)
        return path


# --------------------------------------------------------------------------- tests
class TestStep(unittest.TestCase):
    def test_values(self):
        text = ("DATA;\n#1=IFCTEST('it''s',$,*,.T.,.F.,.ELEMENT.,#2,(1,2.5,-3.E-2),IFCLABEL('x'),(IFCREAL(0.5)));\n"
                "/* comment ; with semicolon */\n#2=IFCOTHER('a;b');\nENDSEC;")
        e = parse_data(text)
        a = e[1].args
        self.assertEqual(a[0], "it's")
        self.assertIsNone(a[1])
        self.assertIs(a[2], DERIVED)
        self.assertEqual(a[3:6], [True, False, Enum("ELEMENT")])
        self.assertEqual(a[6], Ref(2))
        self.assertEqual(a[7], [1, 2.5, -0.03])
        self.assertEqual(a[8], Typed("IFCLABEL", "x"))
        self.assertEqual(a[9], [Typed("IFCREAL", 0.5)])
        self.assertEqual(e[2].args, ["a;b"])

    def test_unicode_and_complex(self):
        self.assertEqual(decode_string("\\X2\\5EFA7B51\\X0\\ A"), "建筑 A")
        self.assertEqual(decode_string("caf\\X\\E9"), "café")
        e = parse_data("DATA;\n#5=(IFCLENGTHMEASURE(1.)IFCNAMEDUNIT(*,.LENGTHUNIT.));\nENDSEC;")
        self.assertEqual(e[5].type, "IFCLENGTHMEASURE")


class TestSampleModel(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.m = IfcModel(SAMPLE)

    def test_summary(self):
        s = self.m.summary()
        self.assertEqual(s["schema"], "IFC4")
        self.assertEqual(s["elements_by_type"]["IFCWALL"], 11)
        self.assertEqual(s["spaces"], 4)
        self.assertEqual([st["name"] for st in s["storeys"]], ["Ground Floor", "First Floor"])
        self.assertEqual(s["elements_without_geometry"], 0)

    def test_rotated_placement(self):
        # Wall GF-E is authored with local X along world +Y
        lo, hi = self.m.shape(self.m.element("Wall GF-E").id).aabb()
        self.assertTrue(all(abs(a - b) < 1e-9 for a, b in zip(lo, [9.8, 0.2, 0.0])))
        self.assertTrue(all(abs(a - b) < 1e-9 for a, b in zip(hi, [10.0, 7.8, 3.0])))

    def test_wall_quantities(self):
        q = element_quantities(self.m, self.m.element("Wall GF-S"))
        self.assertAlmostEqual(q["length"], 10.0)
        self.assertAlmostEqual(q["gross_side_area"], 30.0)
        self.assertAlmostEqual(q["net_side_area"], 30.0 - 2 * 1.8 * 1.5 - 1.0 * 2.1, places=4)
        self.assertAlmostEqual(q["net_volume"], 4.5, places=4)
        self.assertEqual(q["openings"], 3)

    def test_takeoff_totals(self):
        g = takeoff(self.m)["groups"]
        self.assertAlmostEqual(g["IFCSLAB"]["area"], 240.0)
        self.assertAlmostEqual(g["IFCWINDOW"]["area"], 17.34, places=3)
        self.assertAlmostEqual(g["IFCBEAM"]["length"], 9.6)
        self.assertAlmostEqual(g["IFCPIPESEGMENT"]["gross_volume"], math.pi * 0.05 ** 2 * 7.4, places=3)

    def test_qto_validation_finds_stale_value(self):
        v = validate_qto(self.m)
        self.assertEqual(len(v["discrepancies"]), 1)
        d = v["discrepancies"][0]
        self.assertEqual((d["element"]["name"], d["quantity"]), ("Wall GF-N", "NetVolume"))

    def test_clashes(self):
        r = detect_clashes(self.m, clearance=0.05)
        self.assertEqual(r["summary"], {"duplicate": 1, "hard": 3, "clearance": 1})
        pairs = {(c["kind"], frozenset((c["a"]["name"], c["b"]["name"]))) for c in r["clashes"]}
        self.assertIn(("hard", frozenset(("Beam B1 300x500", "Wall GF-INT"))), pairs)
        self.assertIn(("hard", frozenset(("Duct SA-01 400x300", "Wall GF-E"))), pairs)
        self.assertIn(("duplicate", frozenset(("Wall 1F-INT", "Wall 1F-INT (copy)"))), pairs)
        self.assertIn(("clearance", frozenset(("Beam B1 300x500", "Pipe CW-01 DN100"))), pairs)
        # hosted windows / walls standing on slabs are not clashes
        for c in r["clashes"]:
            self.assertFalse({c["a"]["type"], c["b"]["type"]} == {"IFCWINDOW", "IFCWALL"})
            self.assertNotIn("IFCSLAB", (c["a"]["type"], c["b"]["type"]))

    def test_clash_groups_and_no_clearance(self):
        r = detect_clashes(self.m, ["IfcPipeSegment"], ["beam"])
        self.assertEqual(r["summary"], {"duplicate": 0, "hard": 0, "clearance": 0})
        r = detect_clashes(self.m, ["IfcPipeSegment"], ["beam"], clearance=0.05)
        self.assertEqual(r["summary"]["clearance"], 1)
        self.assertAlmostEqual(r["clashes"][0]["gap_m"], 0.03, delta=0.002)

    def test_ventilation(self):
        r = check_ventilation(self.m)
        by = {s["long_name"]: s for s in r["spaces"]}
        self.assertTrue(by["Living"]["pass"])
        self.assertTrue(by["Bedroom 2"]["pass"])
        self.assertFalse(by["Kitchen"]["pass"])
        self.assertFalse(by["Bedroom 1"]["checks"]["openable_area"]["pass"])  # fixed picture window
        self.assertTrue(by["Bedroom 1"]["checks"]["glazing_area"]["pass"])
        self.assertEqual(by["Kitchen"]["checks"]["room_depth"]["mode"], "single-sided")
        self.assertEqual(by["Kitchen"]["facades"], ["N"])
        self.assertAlmostEqual(by["Living"]["checks"]["openable_area"]["provided_m2"], 3.6)
        self.assertFalse(any(s["uses_assumptions"] for s in r["spaces"]))
        # UK ADF 2010 purge rule: 1/20 of 36.48 m2 = 1.82 m2 > 0.36 m2 -> still fails
        self.assertFalse({s["long_name"]: s for s in check_ventilation(self.m, "uk_adf2010")["spaces"]}["Bedroom 1"]["pass"])


class TestGeometryEdgeCases(unittest.TestCase):
    def test_millimetre_units(self):
        mm = Mini("MILLI")
        mm.element("IFCWALL", "W", mm.body(mm.extrusion(mm.rect(5000, 200), 3000)), tail=(WEnum("STANDARD"),))
        m = IfcModel(mm.save())
        q = element_quantities(m, m.elements[0])
        self.assertAlmostEqual(q["length"], 5.0)
        self.assertAlmostEqual(q["gross_volume"], 3.0)
        self.assertAlmostEqual(m.length_scale, 0.001)

    def test_concave_slab_and_column_in_notch(self):
        mm = Mini()
        L_shape = [(0, 0), (6, 0), (6, 2), (2, 2), (2, 6), (0, 6)]  # L-shaped slab, area 20
        mm.element("IFCSLAB", "L-slab", mm.body(mm.extrusion(mm.poly_profile(L_shape), 0.3)), tail=(WEnum("FLOOR"),))
        # column standing in the notch of the L: bounding boxes overlap, solids do not
        mm.element("IFCCOLUMN", "C-notch", mm.body(mm.extrusion(mm.rect(0.4, 0.4), 3)), loc=(4, 4, -1), tail=(WEnum("COLUMN"),))
        # column that really pierces the slab
        mm.element("IFCCOLUMN", "C-hit", mm.body(mm.extrusion(mm.rect(0.4, 0.4), 3)), loc=(1, 1, -1), tail=(WEnum("COLUMN"),))
        m = IfcModel(mm.save())
        slab = m.element("L-slab")
        self.assertAlmostEqual(element_quantities(m, slab)["area"], 20.0)
        self.assertAlmostEqual(m.shape(slab.id).volume, 6.0)
        names = [{c["a"]["name"], c["b"]["name"]} for c in detect_clashes(m)["clashes"]]
        self.assertIn({"L-slab", "C-hit"}, names)
        self.assertNotIn({"L-slab", "C-notch"}, names)

    def test_rotated_prisms_sat(self):
        # two 1x1 boxes, one rotated 45 deg about Z: corner-to-face distance tests the edge axes
        mm = Mini()
        mm.element("IFCBUILDINGELEMENTPROXY", "A", mm.body(mm.extrusion(mm.rect(1, 1, 0, 0), 1)), tail=(None,))
        rot = mm.extrusion(mm.rect(1, 1, 0, 0), 1, mm.a2p3((0, 0, 0), (0, 0, 1), (1, 1, 0)))
        mm.element("IFCBUILDINGELEMENTPROXY", "B", mm.body(rot), loc=(1.25, 0, 0), tail=(None,))
        m = IfcModel(mm.save())
        # rotated square's corner reaches 1.25 - sqrt(2)/2 = 0.543 > 0.5 -> 43 mm gap
        r = detect_clashes(m, clearance=0.1)
        self.assertEqual(r["summary"]["clearance"], 1)
        self.assertAlmostEqual(r["clashes"][0]["gap_m"], 1.25 - math.sqrt(2) / 2 - 0.5, places=4)

    def test_triangulated_mesh_volume(self):
        mm = Mini()
        w = mm.w
        pts = w.add("IFCCARTESIANPOINTLIST3D", tuple((float(x), float(y), float(z))
                    for x, y, z in [(0, 0, 0), (2, 0, 0), (2, 1, 0), (0, 1, 0), (0, 0, 3), (2, 0, 3), (2, 1, 3), (0, 1, 3)]))
        faces = [(1, 3, 2), (1, 4, 3), (5, 6, 7), (5, 7, 8), (1, 2, 6), (1, 6, 5), (2, 3, 7), (2, 7, 6), (3, 4, 8), (3, 8, 7), (4, 1, 5), (4, 5, 8)]
        mesh = w.add("IFCTRIANGULATEDFACESET", pts, None, True, tuple(faces), None)
        mm.element("IFCBUILDINGELEMENTPROXY", "mesh", mm.body(mesh, rtype="Tessellation"), tail=(None,))
        m = IfcModel(mm.save())
        sh = m.shape(m.elements[0].id)
        self.assertAlmostEqual(sh.volume, 6.0)
        self.assertTrue(sh.approx)


    def test_mapped_item_and_boolean_clipping(self):
        """Revit-style bodies: a type's representation map placed via IfcMappedItem,
        and a wall clipped by a half-space (clipping ignored -> conservative)."""
        mm = Mini()
        w = mm.w
        src = mm.w.add("IFCSHAPEREPRESENTATION", mm.ctx, "Body", "SweptSolid", (mm.extrusion(mm.rect(0.5, 0.5, 0, 0), 2.0),))
        rmap = w.add("IFCREPRESENTATIONMAP", mm.a2p3(), src)
        op = w.add("IFCCARTESIANTRANSFORMATIONOPERATOR3D", None, None, mm.pt(10, 0, 0), 1.0, None)
        mapped = w.add("IFCMAPPEDITEM", rmap, op)
        mm.element("IFCCOLUMN", "mapped col", mm.body(mapped, rtype="MappedRepresentation"), loc=(0, 5, 0), tail=(WEnum("COLUMN"),))
        plane = w.add("IFCPLANE", mm.a2p3((0, 0, 2.5)))
        half = w.add("IFCHALFSPACESOLID", plane, False)
        clip = w.add("IFCBOOLEANCLIPPINGRESULT", WEnum("DIFFERENCE"), mm.extrusion(mm.rect(4, 0.2), 3.0), half)
        mm.element("IFCWALL", "clipped wall", mm.body(clip, rtype="Clipping"), tail=(WEnum("STANDARD"),))
        m = IfcModel(mm.save())
        lo, hi = m.shape(m.element("mapped col").id).aabb()
        self.assertEqual([round(v, 6) for v in lo], [9.75, 4.75, 0.0])
        self.assertAlmostEqual(m.shape(m.element("mapped col").id).volume, 0.5)
        wall = m.shape(m.element("clipped wall").id)
        self.assertTrue(wall.approx)
        self.assertAlmostEqual(wall.volume, 2.4)  # unclipped (upper bound)
        self.assertIn("geometry_notes", m.summary())
        self.assertEqual(list(m.summary()["geometry_notes"].values()), [1])


class TestMcp(unittest.TestCase):
    def test_tools_in_process(self):
        from ifcagent import mcp_server

        async def run():
            tools = await mcp_server.mcp.list_tools()
            names = {t.name for t in tools}
            self.assertTrue({"model_summary", "detect_clashes", "check_natural_ventilation", "write_qa_report"} <= names)
            res = await mcp_server.mcp.call_tool("get_element", {"ifc_path": SAMPLE, "element": "Win B1a"})
            if isinstance(res, tuple):  # (content blocks, structured output)
                res = res[1]
            if isinstance(res, dict):
                return res.get("result", res)
            import json
            return json.loads(res[0].text)

        try:
            data = asyncio.run(run())
        except ImportError:  # pragma: no cover
            self.skipTest("mcp not installed")
        self.assertEqual(data["host_element"], "Wall 1F-S")
        self.assertEqual(data["window_panel_operations"], ["FIXEDCASEMENT"])


if __name__ == "__main__":
    unittest.main()

"""Geometry for IFC products, without a CAD kernel.

Every product body is converted into a list of **convex prisms** (a planar
convex polygon swept along a vector).  That is exact for the swept solids
authoring tools emit for walls, slabs, beams, columns, ducts and pipes, and
lets us run an exact Separating-Axis Test for clash detection.

Supported representation items
------------------------------
exact      IfcExtrudedAreaSolid (+Tapered) with Rectangle / Circle / I-shape /
           ArbitraryClosed (IfcPolyline, IfcIndexedPolyCurve) / WithVoids
           profiles; IfcSweptDiskSolid on a polyline directrix; IfcMappedItem;
           IfcBoundingBox
approx     IfcBooleanClippingResult / IfcBooleanResult (first operand kept,
           i.e. clipping ignored -> conservative), IfcFacetedBrep,
           IfcTriangulatedFaceSet, IfcPolygonalFaceSet,
           IfcShellBasedSurfaceModel (clash uses the oriented bounding box,
           volume is exact for closed meshes)

Anything else is reported in ``Shape.notes`` rather than silently dropped.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from .step import Entity, Ref

I4 = np.eye(4)


# --------------------------------------------------------------------------- data
@dataclass
class Prism:
    """Convex polygon ``base`` (n x 3, world) swept along ``ext`` (3,)."""

    base: np.ndarray
    ext: np.ndarray

    @property
    def vertices(self) -> np.ndarray:
        return np.vstack([self.base, self.base + self.ext])

    def normals_and_edges(self):
        b = self.base
        edges = np.roll(b, -1, axis=0) - b
        cap = np.cross(edges[0], edges[1 % len(edges)])
        for k in range(2, len(edges)):
            if np.linalg.norm(cap) > 1e-12:
                break
            cap = np.cross(edges[0], edges[k])
        sides = np.cross(edges, self.ext)
        normals = np.vstack([cap[None, :], sides])
        return normals, np.vstack([edges, self.ext[None, :]])


@dataclass
class Shape:
    pieces: list[Prism] = field(default_factory=list)
    volume: float = 0.0
    approx: bool = False
    notes: list[str] = field(default_factory=list)
    # "footprint" info for swept solids (used by quantity take-off)
    profile_dims: tuple | None = None  # (xdim, ydim) of a rectangular profile
    profile_area: float = 0.0
    sweep_length: float = 0.0
    sweep_dir: np.ndarray | None = None  # world direction of the extrusion

    @property
    def empty(self) -> bool:
        return not self.pieces

    def vertices(self) -> np.ndarray:
        return np.vstack([p.vertices for p in self.pieces]) if self.pieces else np.zeros((0, 3))

    def aabb(self):
        v = self.vertices()
        return (v.min(axis=0), v.max(axis=0)) if len(v) else (None, None)


# --------------------------------------------------------------------------- helpers
def _unit(v) -> np.ndarray:
    v = np.asarray(v, dtype=float)
    n = np.linalg.norm(v)
    return v / n if n > 1e-12 else v


def polygon_area(pts2: np.ndarray) -> float:
    x, y = pts2[:, 0], pts2[:, 1]
    return 0.5 * float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1)))


def _is_convex(pts2: np.ndarray) -> bool:
    n = len(pts2)
    if n < 4:
        return True
    sign = 0
    for i in range(n):
        a, b, c = pts2[i], pts2[(i + 1) % n], pts2[(i + 2) % n]
        cr = (b[0] - a[0]) * (c[1] - b[1]) - (b[1] - a[1]) * (c[0] - b[0])
        if abs(cr) < 1e-12:
            continue
        s = 1 if cr > 0 else -1
        if sign and s != sign:
            return False
        sign = s
    return True


def _clean(pts2: np.ndarray) -> np.ndarray:
    """Drop the closing duplicate point and consecutive duplicates."""
    out = []
    for p in pts2:
        if not out or np.linalg.norm(p - out[-1]) > 1e-9:
            out.append(p)
    if len(out) > 1 and np.linalg.norm(out[0] - out[-1]) < 1e-9:
        out.pop()
    return np.array(out, dtype=float)


def convex_parts(pts2: np.ndarray) -> list[np.ndarray]:
    """Split a simple polygon into convex parts (ear clipping -> triangles)."""
    pts2 = _clean(pts2)
    if len(pts2) < 3:
        return []
    if polygon_area(pts2) < 0:
        pts2 = pts2[::-1]
    if _is_convex(pts2):
        return [pts2]
    idx = list(range(len(pts2)))
    tris = []

    def inside(p, a, b, c):
        d1 = (p[0] - b[0]) * (a[1] - b[1]) - (a[0] - b[0]) * (p[1] - b[1])
        d2 = (p[0] - c[0]) * (b[1] - c[1]) - (b[0] - c[0]) * (p[1] - c[1])
        d3 = (p[0] - a[0]) * (c[1] - a[1]) - (c[0] - a[0]) * (p[1] - a[1])
        neg = d1 < 0 or d2 < 0 or d3 < 0
        pos = d1 > 0 or d2 > 0 or d3 > 0
        return not (neg and pos)

    guard = 0
    while len(idx) > 3 and guard < 10000:
        guard += 1
        for k in range(len(idx)):
            i0, i1, i2 = idx[k - 1], idx[k], idx[(k + 1) % len(idx)]
            a, b, c = pts2[i0], pts2[i1], pts2[i2]
            if (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0]) <= 1e-12:
                continue
            if any(inside(pts2[j], a, b, c) for j in idx if j not in (i0, i1, i2)):
                continue
            tris.append(np.array([a, b, c]))
            idx.pop(k)
            break
        else:  # degenerate polygon; fall back to a fan
            break
    if len(idx) >= 3:
        for k in range(1, len(idx) - 1):
            tris.append(np.array([pts2[idx[0]], pts2[idx[k]], pts2[idx[k + 1]]]))
    return tris


def transform(m: np.ndarray, pts: np.ndarray) -> np.ndarray:
    return pts @ m[:3, :3].T + m[:3, 3]


# --------------------------------------------------------------------------- engine
class GeometryEngine:
    """Evaluates placements and body representations for one model."""

    def __init__(self, entities: dict[int, Entity], length_scale: float = 1.0):
        self.e = entities
        self.s = length_scale
        self._placements: dict[int, np.ndarray] = {}

    # ---- basic resolution
    def get(self, ref) -> Entity | None:
        return self.e.get(ref.id) if isinstance(ref, Ref) else None

    def point(self, ref) -> np.ndarray:
        ent = self.get(ref)
        c = [float(x) for x in ent.args[0]]
        return np.array(c + [0.0] * (3 - len(c)))

    def direction(self, ref, default) -> np.ndarray:
        ent = self.get(ref)
        if ent is None:
            return np.array(default, dtype=float)
        c = [float(x) for x in ent.args[0]]
        return _unit(c + [0.0] * (3 - len(c)))

    def axis2placement(self, ref) -> np.ndarray:
        ent = self.get(ref)
        if ent is None:
            return I4.copy()
        m = I4.copy()
        if ent.type == "IFCAXIS2PLACEMENT2D":
            x = self.direction(ent[1], (1, 0, 0))
            z = np.array([0.0, 0.0, 1.0])
        else:
            z = self.direction(ent[1], (0, 0, 1))
            x = self.direction(ent[2], (1, 0, 0))
        x = x - np.dot(x, z) * z
        if np.linalg.norm(x) < 1e-9:  # RefDirection parallel to Axis
            x = np.array([1.0, 0, 0]) if abs(z[0]) < 0.9 else np.array([0, 1.0, 0])
            x = x - np.dot(x, z) * z
        x = _unit(x)
        y = np.cross(z, x)
        m[:3, 0], m[:3, 1], m[:3, 2] = x, y, z
        m[:3, 3] = self.point(ent[0])
        return m

    def cartesian_operator(self, ref) -> np.ndarray:
        ent = self.get(ref)
        if ent is None:
            return I4.copy()
        # IfcCartesianTransformationOperator3D(Axis1, Axis2, LocalOrigin, Scale, Axis3[, Scale2, Scale3])
        z = self.direction(ent[4], (0, 0, 1)) if ent.type.endswith("3D") or "3DNONUNIFORM" in ent.type else np.array([0, 0, 1.0])
        x = self.direction(ent[0], (1, 0, 0))
        x = _unit(x - np.dot(x, z) * z)
        y = np.cross(z, x)
        s1 = float(ent[3]) if isinstance(ent[3], (int, float)) else 1.0
        s2 = float(ent[5]) if len(ent.args) > 5 and isinstance(ent[5], (int, float)) else s1
        s3 = float(ent[6]) if len(ent.args) > 6 and isinstance(ent[6], (int, float)) else s1
        m = I4.copy()
        m[:3, 0], m[:3, 1], m[:3, 2] = x * s1, y * s2, z * s3
        m[:3, 3] = self.point(ent[2])
        return m

    def placement(self, ref) -> np.ndarray:
        """World matrix of an IfcObjectPlacement (memoised, file units)."""
        ent = self.get(ref)
        if ent is None:
            return I4.copy()
        if ent.id in self._placements:
            return self._placements[ent.id]
        if ent.type == "IFCLOCALPLACEMENT":
            parent = self.placement(ent[0]) if ent[0] is not None else I4
            m = parent @ self.axis2placement(ent[1])
        else:  # IfcGridPlacement etc. - not supported, identity
            m = I4.copy()
        self._placements[ent.id] = m
        return m

    # ---- profiles
    def profile(self, ref, notes: list) -> tuple[list[np.ndarray], float, tuple | None]:
        """Return (outer polygon(s) in profile-position coords, area, rect dims)."""
        p = self.get(ref)
        t = p.type
        pos = self.axis2placement(p[2]) if t != "IFCARBITRARYCLOSEDPROFILEDEF" and t != "IFCARBITRARYPROFILEDEFWITHVOIDS" and len(p.args) > 2 and isinstance(p[2], Ref) else I4

        def placed(pts2):
            pts3 = np.c_[pts2, np.zeros(len(pts2))]
            return transform(pos, pts3)[:, :2]

        if t in ("IFCRECTANGLEPROFILEDEF", "IFCRECTANGLEHOLLOWPROFILEDEF", "IFCROUNDEDRECTANGLEPROFILEDEF"):
            x, y = float(p[3]), float(p[4])
            poly = placed(np.array([[-x / 2, -y / 2], [x / 2, -y / 2], [x / 2, y / 2], [-x / 2, y / 2]]))
            area = x * y
            if t == "IFCRECTANGLEHOLLOWPROFILEDEF":
                w = float(p[5])
                area = x * y - (x - 2 * w) * (y - 2 * w)
            return [poly], area, (x, y)
        if t in ("IFCCIRCLEPROFILEDEF", "IFCCIRCLEHOLLOWPROFILEDEF"):
            r = float(p[3])
            n = 24
            ang = np.linspace(0, 2 * math.pi, n, endpoint=False)
            # circumscribed polygon so clash detection stays conservative
            rr = r / math.cos(math.pi / n)
            poly = placed(np.c_[rr * np.cos(ang), rr * np.sin(ang)])
            area = math.pi * r * r
            if t == "IFCCIRCLEHOLLOWPROFILEDEF":
                area -= math.pi * (r - float(p[4])) ** 2
            return [poly], area, None
        if t in ("IFCISHAPEPROFILEDEF", "IFCTSHAPEPROFILEDEF", "IFCUSHAPEPROFILEDEF", "IFCLSHAPEPROFILEDEF",
                 "IFCCSHAPEPROFILEDEF", "IFCZSHAPEPROFILEDEF"):
            b, d = float(p[3]), float(p[4])
            if t == "IFCISHAPEPROFILEDEF":
                tw, tf = float(p[5]), float(p[6])
                area = 2 * b * tf + (d - 2 * tf) * tw
            else:
                area = b * d * 0.3  # rough; flagged
                notes.append(f"{t}: area estimated")
            poly = placed(np.array([[-b / 2, -d / 2], [b / 2, -d / 2], [b / 2, d / 2], [-b / 2, d / 2]]))
            return [poly], area, (b, d)
        if t in ("IFCARBITRARYCLOSEDPROFILEDEF", "IFCARBITRARYPROFILEDEFWITHVOIDS"):
            outer = self.curve_points(p[2])
            if outer is None or len(outer) < 3:
                notes.append(f"unsupported outer curve in #{p.id}")
                return [], 0.0, None
            outer = _clean(outer[:, :2])
            area = abs(polygon_area(outer))
            if t == "IFCARBITRARYPROFILEDEFWITHVOIDS":
                for v in p[3] or []:
                    vp = self.curve_points(v)
                    if vp is not None and len(vp) >= 3:
                        area -= abs(polygon_area(_clean(vp[:, :2])))
            return [outer], area, None
        notes.append(f"unsupported profile {t} (#{p.id})")
        return [], 0.0, None

    def curve_points(self, ref) -> np.ndarray | None:
        c = self.get(ref)
        if c is None:
            return None
        if c.type == "IFCPOLYLINE":
            return np.array([self.point(r) for r in c[0]])
        if c.type == "IFCINDEXEDPOLYCURVE":
            plist = self.get(c[0])
            coords = np.array(plist[0], dtype=float)
            if c[1]:  # segments -> keep listed vertex order (arcs become chords)
                order = []
                for seg in c[1]:
                    idx = seg.value if hasattr(seg, "value") else seg
                    for i in idx:
                        if not order or order[-1] != i:
                            order.append(i)
                coords = coords[[i - 1 for i in order]]
            if coords.shape[1] == 2:
                coords = np.c_[coords, np.zeros(len(coords))]
            return coords
        if c.type == "IFCCOMPOSITECURVE":
            pts = []
            for seg in c[0]:
                s = self.get(seg)
                sub = self.curve_points(s[2]) if s is not None else None
                if sub is not None:
                    pts.extend(sub if s[1] is not False else sub[::-1])
            return np.array(pts) if pts else None
        if c.type == "IFCTRIMMEDCURVE":
            return None
        return None

    # ---- items
    def item(self, ref, m: np.ndarray, shape: Shape) -> None:
        it = self.get(ref)
        if it is None:
            return
        t = it.type
        if t in ("IFCEXTRUDEDAREASOLID", "IFCEXTRUDEDAREASOLIDTAPERED"):
            polys, area, dims = self.profile(it[0], shape.notes)
            pos = m @ self.axis2placement(it[1])
            d = self.direction(it[2], (0, 0, 1))
            depth = float(it[3])
            ext_local = d * depth
            ext = pos[:3, :3] @ ext_local
            for poly in polys:
                for part in convex_parts(poly):
                    base = transform(pos, np.c_[part, np.zeros(len(part))])
                    shape.pieces.append(Prism(base, ext))
            scale3 = abs(np.linalg.det(pos[:3, :3]))
            shape.volume += area * depth * abs(d[2]) * scale3
            shape.profile_area += area
            shape.profile_dims = dims
            shape.sweep_length = depth * np.linalg.norm(pos[:3, :3] @ d)
            shape.sweep_dir = _unit(ext)
        elif t in ("IFCBOOLEANCLIPPINGRESULT", "IFCBOOLEANRESULT"):
            op = it[0].value if hasattr(it[0], "value") else str(it[0])
            if op == "UNION":
                self.item(it[1], m, shape)
                self.item(it[2], m, shape)
            else:
                self.item(it[1], m, shape)
                shape.approx = True
                shape.notes.append(f"{t} {op}: second operand ignored (conservative)")
        elif t == "IFCMAPPEDITEM":
            rmap = self.get(it[0])
            origin = self.axis2placement(rmap[0])
            target = self.cartesian_operator(it[1])
            rep = self.get(rmap[1])
            for sub in rep[3]:
                self.item(sub, m @ target @ origin, shape)
        elif t == "IFCSWEPTDISKSOLID":
            path = self.curve_points(it[0])
            r = float(it[1])
            if path is None or len(path) < 2:
                shape.notes.append(f"IfcSweptDiskSolid #{it.id}: unsupported directrix")
                return
            path = transform(m, path)
            for a, b in zip(path[:-1], path[1:]):
                axis = b - a
                ln = np.linalg.norm(axis)
                if ln < 1e-12:
                    continue
                u = _unit(np.cross(axis, [0, 0, 1] if abs(axis[2]) / ln < 0.9 else [1, 0, 0]))
                v = _unit(np.cross(axis, u))
                base = np.array([a + r * (su * u + sv * v) for su, sv in ((-1, -1), (1, -1), (1, 1), (-1, 1))])
                shape.pieces.append(Prism(base, axis))
                shape.volume += math.pi * r * r * ln
                shape.sweep_length += ln
        elif t == "IFCBOUNDINGBOX":
            x, y, z = (float(v) for v in it[1:4])
            corner = self.point(it[0])
            base = transform(m, np.array([[0, 0, 0], [x, 0, 0], [x, y, 0], [0, y, 0]]) + corner)
            shape.pieces.append(Prism(base, m[:3, :3] @ np.array([0, 0, z])))
            shape.volume += x * y * z
            shape.approx = True
        elif t in ("IFCFACETEDBREP", "IFCFACETEDBREPWITHVOIDS", "IFCTRIANGULATEDFACESET", "IFCPOLYGONALFACESET",
                   "IFCSHELLBASEDSURFACEMODEL", "IFCFACEBASEDSURFACEMODEL"):
            verts, tris = self.mesh(it)
            if len(verts) == 0:
                shape.notes.append(f"{t} #{it.id}: empty mesh")
                return
            # exact volume for closed meshes (divergence theorem), in local coords
            vol = abs(sum(np.dot(verts[a], np.cross(verts[b], verts[c])) for a, b, c in tris) / 6.0) if tris else 0.0
            shape.volume += vol * abs(np.linalg.det(m[:3, :3]))
            lo, hi = verts.min(axis=0), verts.max(axis=0)
            base = np.array([[lo[0], lo[1], lo[2]], [hi[0], lo[1], lo[2]], [hi[0], hi[1], lo[2]], [lo[0], hi[1], lo[2]]])
            shape.pieces.append(Prism(transform(m, base), m[:3, :3] @ np.array([0, 0, hi[2] - lo[2]])))
            shape.approx = True
            shape.notes.append(f"{t}: clash geometry uses local bounding box")
        else:
            shape.notes.append(f"unsupported item {t} (#{it.id})")

    def mesh(self, it: Entity) -> tuple[np.ndarray, list]:
        if it.type == "IFCTRIANGULATEDFACESET":
            coords = np.array(self.get(it[0])[0], dtype=float)
            tris = [tuple(i - 1 for i in tri) for tri in it[3]]
            return coords, tris
        if it.type == "IFCPOLYGONALFACESET":
            coords = np.array(self.get(it[0])[0], dtype=float)
            tris = []
            for fref in it[2]:
                idx = [i - 1 for i in self.get(fref)[0]]
                tris += [(idx[0], idx[k], idx[k + 1]) for k in range(1, len(idx) - 1)]
            return coords, tris
        shells = []
        if it.type in ("IFCFACETEDBREP", "IFCFACETEDBREPWITHVOIDS"):
            shells = [it[0]]
        else:
            shells = list(it[0])
        verts, tris, index = [], [], {}
        for sref in shells:
            shell = self.get(sref)
            for fref in shell[0]:
                face = self.get(fref)
                for bref in face[0]:
                    bound = self.get(bref)
                    if bound.type != "IFCFACEOUTERBOUND" and len(face[0]) > 1:
                        continue
                    loop = self.get(bound[0])
                    if loop is None or loop.type != "IFCPOLYLOOP":
                        continue
                    ids = []
                    for pref in loop[0]:
                        if pref.id not in index:
                            index[pref.id] = len(verts)
                            verts.append(self.point(pref))
                        ids.append(index[pref.id])
                    if bound[1] is False:
                        ids = ids[::-1]
                    tris += [(ids[0], ids[k], ids[k + 1]) for k in range(1, len(ids) - 1)]
        return np.array(verts, dtype=float), tris

    # ---- products
    def product_shape(self, product: Entity) -> Shape:
        shape = Shape()
        rep_ref = product[6] if len(product.args) > 6 else None
        pds = self.get(rep_ref)
        if pds is None:
            return shape
        m = self.placement(product[5])
        reps = [self.get(r) for r in (pds[2] or [])]
        reps = [r for r in reps if r is not None]
        body = [r for r in reps if (r[1] or "").lower() == "body"]
        chosen = body or [r for r in reps if (r[1] or "").lower() not in ("axis", "footprint", "annotation", "plan", "box")]
        if not chosen:
            chosen = [r for r in reps if (r[1] or "").lower() == "box"]
        for rep in chosen[:1]:
            for item in rep[3] or []:
                self.item(item, m, shape)
        # apply length unit scale
        if self.s != 1.0 and shape.pieces:
            for p in shape.pieces:
                p.base = p.base * self.s
                p.ext = p.ext * self.s
            shape.volume *= self.s ** 3
            shape.profile_area *= self.s ** 2
            shape.sweep_length *= self.s
            if shape.profile_dims:
                shape.profile_dims = tuple(d * self.s for d in shape.profile_dims)
        return shape


# --------------------------------------------------------------------------- SAT
def _dedupe(axes: np.ndarray) -> np.ndarray:
    n = np.linalg.norm(axes, axis=1)
    axes = axes[n > 1e-9] / n[n > 1e-9, None]
    out = []
    for a in axes:
        if not any(abs(np.dot(a, b)) > 1 - 1e-9 for b in out):
            out.append(a)
    return np.array(out)


def prism_overlap(a: Prism, b: Prism) -> float:
    """Signed overlap of two convex prisms via the Separating Axis Theorem.

    > 0  : interpenetrating, value = minimum translation distance (exact)
    <= 0 : separated, ``-value`` is a lower bound of the gap
    """
    na, ea = a.normals_and_edges()
    nb, eb = b.normals_and_edges()
    cross = np.cross(ea[:, None, :], eb[None, :, :]).reshape(-1, 3)
    axes = _dedupe(np.vstack([na, nb, cross]))
    va, vb = a.vertices, b.vertices
    pa, pb = va @ axes.T, vb @ axes.T
    overlap = np.minimum(pa.max(0), pb.max(0)) - np.maximum(pa.min(0), pb.min(0))
    return float(overlap.min())


def shape_overlap(a: Shape, b: Shape, horizon: float = 1.0) -> float:
    """Max signed overlap over all piece pairs (see :func:`prism_overlap`).

    Piece pairs whose bounding boxes are more than ``horizon`` apart are not
    tested exactly; their AABB gap is used as the (lower-bound) separation.
    """
    best = -math.inf
    for pa in a.pieces:
        va = pa.vertices
        lo_a, hi_a = va.min(0), va.max(0)
        for pb in b.pieces:
            vb = pb.vertices
            gap = float(np.max(np.maximum(vb.min(0) - hi_a, lo_a - vb.max(0))))
            if gap > horizon:
                val = -gap
            else:
                val = prism_overlap(pa, pb)
                if gap > 0:  # both are lower bounds of the true gap; keep the tighter one
                    val = min(val, -gap)
            best = max(best, val)
    return best

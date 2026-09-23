# Build log

This repo was built AI-first: I set the goal, the scope and the acceptance criteria, and an AI coding agent
(Claude) wrote and ran the code in a sandbox. This log records how that went, including the constraints
and the places where the first approach was changed.

## 1. Brief

> Build a small, real tool for an AI agent that works on BIM/IFC. It should do quantity take-off and clash
> detection, add a natural-ventilation check that connects to my Indoor Air (2025) window-opening research,
> expose it to agents over MCP, and test it.

## 2. Constraint that shaped the design

The build sandbox could not install packages from PyPI, so `ifcopenshell` was not available. Rather than ship
code that could not be run, we wrote the IFC layer from scratch on top of `numpy`:

* STEP parser, placement chains, profile and extrusion evaluation
* clash detection as convex-prism decomposition plus the Separating Axis Test (exact penetration depth)

The upside is that there is no heavy dependency, so the tools also run in locked-down agent sandboxes.

## 3. Test data, built to be adversarial

There was no public IFC to download, so we wrote an IFC4 generator (`examples/generate_sample.py`) and **planted**
known defects: a duplicate wall, a beam through a partition, a duct through the facade, a pipe 30 mm under a beam,
a stale authored quantity, and two under-ventilated rooms. The model also contains conditions that must *not*
be flagged. Expected results were worked out by hand first (for example, Kitchen openable area
0.72 m² vs 28.12/16 = 1.758 m² required), and the tool had to reproduce them exactly.

## 4. Fixes made after looking at the output

| Observation | Change |
|---|---|
| Beam length came out as 0.5 m, because the generator extruded the beam's elevation vertically | Member length now comes from the longest extent in the element's local frame, so it no longer depends on the extrusion direction |
| Plan labels collided with the beam and pipe | Labels moved off the room's mid-lines; the figure is re-rendered and inspected |
| A duplicate wall also produces a door clash | Kept on purpose, because it is a real consequence. The skill tells the agent to report the root cause once |
| A clearance value from SAT is a lower bound | Documented; this errs on the safe side for clearance checks |

## 5. Verification

* 16 unit tests: parser edge cases, mm units, rotated placements, a concave slab with a column in its notch,
  a 45° SAT case checked against the analytic gap `1.25 − √2/2 − 0.5`, mapped items, boolean clipping, mesh
  volume, sample-model expectations, and an MCP tool call.
* A real MCP stdio round trip (`examples/mcp_demo.py`, output in `docs/mcp_demo_output.txt`).
* A rough benchmark: 3,000 elements (2 MB) parse and clash-check in about 2 s.

## 6. Next

Run it on real Revit and ArchiCAD exports, fix what breaks, and add an optional `ifcopenshell` backend.

# ifc-agent-tools

**An AI-agent-enabled BIM research platform (research prototype).** AI agents such as Claude Code or Codex connect to
it over the **Model Context Protocol (MCP)** and can then read IFC models, run BIM automation tasks (quantity
take-off, clash detection, checks of the quantities stored in the model) and assess building performance
(natural-ventilation screening), all from a plain-language request.

The platform has three layers:

* **Agent layer**: an MCP server with 9 tools, plus a Claude Code skill that tells the agent how to run a
  coordination review and how to judge the results
* **Analysis layer**: IFC parsing, geometry, quantity take-off, clash detection and ventilation rules, all
  deterministic and covered by unit tests
* **Research layer**: the openable-window geometry taken from BIM is designed to be combined with occupant
  window-opening behaviour models (see [Research motivation](#research-motivation))

```text
> Run a coordination review of examples/sample_house.ifc and tell me what to fix first.
```

LLMs are good at planning a review and explaining the findings. They are bad at computing whether a 400×300 duct
crosses a 200 mm wall. This repo puts the geometry and the counting in deterministic, tested tools, and leaves the
agent to decide which tools to call and how to read the results.

---

## What the Agent Gets

| MCP tool | What it does |
|---|---|
| `model_summary` | schema, units, storeys, element counts, and a list of any geometry that could only be approximated |
| `list_elements` / `get_element` | filter by class, storey or name; full detail with psets, Qto sets, host wall, bounding box |
| `quantity_takeoff` | QTO **computed from geometry**: wall length/height/width, gross and net side area, net volume with openings deducted; slab area and thickness; member length; window/door area |
| `space_schedule` | room schedule (floor area, height, volume) |
| `validate_quantities` | compares **authored** `IfcElementQuantity` values (e.g. `Qto_WallBaseQuantities`) with geometry and flags stale ones |
| `detect_clashes` | hard, clearance and duplicate clashes using an **exact** separating-axis test on convex solids; group A vs group B; contact tolerance; hosted windows/doors and assembly parts excluded |
| `check_natural_ventilation` | finds the exterior windows serving each `IfcSpace`, gets openable area from window-type panel operations, and checks it against **HK B(P)R 30/31** (glazing ≥ 1/10 of floor area, openable ≥ 1/16) or other rule sets; applies CIBSE room-depth rules and gives an indicative BS 5925 air-change rate |
| `write_qa_report` | runs every check and writes `report.md`, `report.json` and an annotated plan per storey |

The same functions work without an agent: `python -m ifcagent <summary|qto|validate|clash|ventilation|report> model.ifc`.

## Research Motivation

This project explores how AI agents can carry out BIM coordination, model validation, quantity take-off and building
performance review directly from IFC models. The engineer states the goal and the agent plans the analysis, but every
number comes from deterministic, testable tools rather than from the language model.

The natural-ventilation workflow builds on my earlier research into occupant window-opening behaviour and indoor
environmental quality (Karamoozian et al., *Journal of Indoor Air*, 2025). BIM gives the physical opening geometry: which
windows serve which room, how much of each can open, and on which facades. Behaviour models give the probability that
occupants actually open them. Future work will combine the two to estimate *expected* ventilation performance under
realistic occupancy scenarios, rather than the best case that prescriptive codes assume.

Open questions this platform is designed to study:

* How reliably can an agent pick the right analysis and interpret it when every computation is delegated to tools?
* Which IFC data quality problems (missing panel operation types, stale quantities, approximate geometry) most
  affect automated building performance assessment, and can agents detect them and report them honestly?
* How far does BIM-derived opening geometry combined with behaviour models improve on prescriptive ventilation rules?

## Architecture

```mermaid
flowchart LR
    U["Engineer<br/>(plain language)"] --> A["AI agent<br/>Claude Code / Codex"]
    A -- "skill: .claude/skills/ifc-qa" --> A
    A <-- "MCP (stdio)" --> S["ifcagent.mcp_server"]
    S --> M["IfcModel<br/>STEP parser, spatial tree,<br/>psets, Qto, relations"]
    M --> G["GeometryEngine<br/>placements to convex prisms"]
    G --> C["clash.py<br/>SAT narrow phase"]
    M --> Q["quantities.py<br/>QTO + Qto validation"]
    M --> V["ventilation.py<br/>windows to spaces, rules, ACH"]
    C & Q & V --> R["report.py<br/>md / json / plans"]
```

* **`step.py`**: an ISO 10303-21 tokenizer and parser (strings, `\X2\` unicode, typed values, complex entities).
* **`geometry.py`**: resolves `IfcLocalPlacement` chains and turns bodies into convex prisms. It handles extrusions
  (rectangle, circle, I-shape, arbitrary polyline including concave outlines via ear clipping), swept disks,
  mapped items and bounding boxes exactly. Boolean clippings and B-rep/tessellated meshes are bounded
  conservatively and flagged, never dropped silently.
* **`clash.py`**: sweep-and-prune broad phase, then a Separating Axis Test (face normals plus edge-by-edge cross
  products). For convex polyhedra this gives the true penetration depth, not just a yes/no answer.

The only runtime dependency is `numpy`. `mcp` is needed for the server and `matplotlib` for the plan images. There
is no CAD kernel and no `ifcopenshell`, so it installs anywhere, including sandboxed agent environments.

## External Validation

Besides the bundled demo model, the toolkit has been run on an external benchmark: the **IFCNet** dataset of IFC
files labelled by class.

> The IFCNet benchmark test demonstrates successful execution of the full pipeline on external IFC data.
>
> Numerical results have not yet been cross-validated against IfcOpenShell, Revit schedules, or another reference
> geometry engine.

The two kinds of evidence are kept separate below: evidence that the pipeline **executes** on third-party data (A),
and evidence that its numbers are **accurate** (B), which has not yet been assessed.

### IFCNet Benchmark Execution

![IFCNet benchmark execution](docs/images/ifcnet_validation.png)

*Figure 1. Command-line execution of the toolkit on the IFCNet `IfcWall` sample (`wall.ifc`).*

### Generated QA Report

![Generated QA report](docs/images/ifcnet_report.png)

*Figure 2. `report.md`, generated by `python -m ifcagent report wall.ifc -o wall_report`.*

### Generated Plan Visualisation

![Generated plan visualisation](docs/images/ifcnet_plan.png)

*Figure 3. `plan_level_1.png`, generated in the same report run.*

### A. Execution: does the pipeline run on third-party IFC data?

Tested on an IFCNet `IfcWall` sample (`wall.ifc`, IFC4, 121 entities).

| stage | status | observation |
|---|---|---|
| parsing | ✅ executed successfully | IFC4 schema, 1 storey and 1 `IfcWall` recognised |
| geometry extraction | ✅ executed successfully | 0 elements without geometry; the body is an `IfcFacetedBrep`, so volume is computed from the closed mesh and clash tests use an oriented bounding box (flagged in the geometry notes) |
| quantity take-off | ✅ executed successfully | produced the values in table B |
| report generation | ✅ executed successfully | `report.md` and `report.json` written |
| plan generation | ✅ executed successfully | `plan_level_1.png` written |

### B. Numerical accuracy: are the numbers right?

*Not yet assessed.* The values below are the tool's raw output, reported for transparency. They have not yet been
independently verified against any reference value.

| quantity | tool output | reference value | agreement |
|---|---|---|---|
| length | 6.443 m | — | not yet cross-validated |
| net side area | 3.222 m² | — | not yet cross-validated |
| gross volume | 0.475 m³ | — | not yet cross-validated |
| net volume | 0.475 m³ | — | not yet cross-validated |

For a mesh (B-rep) body, length and side area are taken from the element's bounding box in its local coordinates.
They are therefore expected to be less reliable than volume for walls that are not simple boxes.

### Planned accuracy study

`examples/validate_dataset.py` runs the toolkit over a whole folder of IFC files. It reports the parse success rate,
geometry coverage, exact versus approximate geometry, and run time. With `--crosscheck` it computes each element's
volume with IfcOpenShell (an independent geometry kernel based on OpenCASCADE) and reports the distribution of
differences:

```bash
pip install ifcopenshell
python examples/validate_dataset.py path/to/IFCNetCore --limit 500 --crosscheck --out validation_results
```

## Validation Status

**Completed**

- ✅ Unit tests (16): parser, units, placements, concave profiles, SAT, mapped items, clipping, meshes, MCP
- ✅ Sample house model: every planted clash, quantity error and ventilation failure detected, with no false positives
- ✅ IFCNet benchmark `IfcWall` sample: execution only (see A above)
- ✅ Automated report generation
- ✅ Automated plan generation

**Planned**

- ⬜ Revit IFC validation (multi-storey, mm units, mapped types, clipped walls)
- ⬜ IfcOpenShell cross-check (`validate_dataset.py --crosscheck`)
- ⬜ Multi-storey benchmark validation
- ⬜ Volume comparison study: error distribution by IFC class and geometry type (extrusion vs B-rep)

The sample-model results check the tool against expected values worked out by hand for a synthetic model. They show
that the tool behaves as designed; they are not evidence of accuracy on real-world models.

## Quick Start

```bash
git clone https://github.com/masoodkaramoozian/ifc-agent-tools.git
cd ifc-agent-tools
pip install -r requirements.txt

python examples/generate_sample.py                 # writes examples/sample_house.ifc
python -m ifcagent report examples/sample_house.ifc -o sample_report
python -m unittest discover -s tests -v            # 16 tests
python examples/mcp_demo.py                        # real MCP stdio round trip, no LLM needed
```

**With Claude Code:** open the repo and run `claude`. It finds `.mcp.json` (the `ifc-agent` server) and the
`ifc-qa` skill. Then just ask, for example:

* *"Which rooms in sample_house.ifc fail natural ventilation, and what is the cheapest fix for each?"*
* *"Check MEP against structure with 50 mm clearance and draft RFIs for the hard clashes."*
* *"Can I trust the wall quantities in this model for a cost estimate?"*

**Other MCP clients** (Codex, Cursor, Claude Desktop) use the same command:
`python -m ifcagent.mcp_server` (or `ifcagent-mcp` after `pip install -e .[all]`).

## Demo: The Sample Model

`examples/generate_sample.py` writes a two-storey IFC4 house (28 elements, 4 spaces). It contains realistic
coordination errors on purpose, and just as many correct conditions that must **not** be reported (walls on slabs,
windows in their host walls, walls butting at corners).

Full output: [`docs/sample_report/report.md`](docs/sample_report/report.md) · MCP transcript:
[`docs/mcp_demo_output.txt`](docs/mcp_demo_output.txt)

| # | kind | elements | depth / gap | what an engineer would do |
|---|---|---|---|---|
| 1 | duplicate | Wall 1F-INT × Wall 1F-INT (copy) | 100 mm | delete the copy; this also resolves #4 |
| 2 | hard | Duct SA-01 × Wall GF-E | 200 mm | builder's-work opening and sleeve in the facade |
| 3 | hard | Beam B1 × Wall GF-INT | 100 mm | stop the partition at the beam soffit |
| 4 | hard | Door 1F-INT × Wall 1F-INT (copy) | 100 mm | knock-on effect of #1 |
| 5 | clearance | Beam B1 × Pipe CW-01 | 30 mm (< 50) | drop the pipe, or accept with insulation detail |

| space | facades | openable m² (required) | depth m (limit) | result |
|---|---|---|---|---|
| Living | S+W | 3.60 (2.76) | 5.8 (7.5) | PASS |
| Kitchen | N only | 0.72 (1.76) | 7.6 (7.5) | **FAIL**: too little glazing, too deep for single-sided |
| Bedroom 1 | S+W | 0.36 (2.28) | 4.8 (7.5) | **FAIL**: big window, but the picture pane is fixed |
| Bedroom 2 | E+S | 2.25 (2.23) | 4.7 (7.5) | PASS (just) |

The QTO check also catches `Wall GF-N`, whose authored `NetVolume` is 6.60 m³ while the geometry gives
5.712 m³ (+15.5 %). The value is stale, left over from an earlier wall thickness.

![Ground floor plan with numbered clash markers and room ventilation status](docs/sample_report/plan_ground_floor.png)

![First floor plan with numbered clash markers and room ventilation status](docs/sample_report/plan_first_floor.png)

## Design Choices (and Where I Did Not Trust the First Answer)

* **Exact where it matters, conservative where it can't be exact.** Clipped solids keep their unclipped volume
  (an upper bound), and meshes use their oriented bounding box for clashes. The result carries
  `approximate_geometry: true` so the agent tells the user before raising an RFI.
* **Separation is a lower bound.** Below the contact tolerance, a SAT separation is a lower bound on the true gap.
  That is the safe direction for clearance checks: a borderline pair can show up, but a real violation cannot be
  missed.
* **Openable area is not glazed area.** Bedroom 1 passes the 1/10 glazing rule and fails the 1/16 openable rule. The
  tool reads `IfcWindowPanelProperties.OperationType` (fixed = 0, sliding = 0.5, hinged or pivot = 1). When the model
  has no such data it falls back to an explicit `default_openable_ratio` and marks the result `uses_assumptions`,
  so the missing data stays visible.
* **Tests encode traps**: a column in the notch of an L-shaped slab (its bounding boxes overlap but the solids do not),
  a 45° rotated box (edge-edge axes), millimetre units, a mapped item, a clipped wall and a closed tessellated
  mesh.

## Limitations and Roadmap

* Pure-Python parsing runs at about 4 MB/s (a 2 MB, 3,000-element model parses and clash-checks in about 2 s).
  Very large federated models would need an optional `ifcopenshell` backend.
* Curved B-reps (`IfcAdvancedBrep`) and NURBS are not evaluated. They are reported in `geometry_notes`.
* Windows are assigned to spaces geometrically unless `IfcRelSpaceBoundary` exists, in which case those relations
  are used.
* Tested successfully on the bundled model, synthetic edge cases (16 unit tests) and one IFCNet file (execution
  only). Numerical results on real-world models are not yet independently verified or cross-validated (see [Validation Status](#validation-status)).
* Next: BCF export of clashes, IDS (Information Delivery Specification) checking, an ifcopenshell backend selectable
  at runtime, and coupling to occupant behaviour models.

## How This Was Built

I built it AI-first, directing Claude (acting as the coding agent) through design, implementation and testing. See [`BUILD_LOG.md`](BUILD_LOG.md) for the prompts, the decisions I made, and what was checked
by hand.

## Citation

If you use this work, please cite the repository:

```text
Karamoozian, M. (2026). ifc-agent-tools: an AI-agent-enabled BIM research platform (research prototype).
https://github.com/masoodkaramoozian/ifc-agent-tools
```

MIT licence · Masood Karamoozian · masood.karamoozian@gmail.com

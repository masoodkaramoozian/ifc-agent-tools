---
name: ifc-qa
description: Review an IFC/BIM model - quantity take-off, clash detection, authored-quantity validation and natural-ventilation screening - and write a coordination report. Use when the user gives an .ifc file or asks about clashes, quantities, rooms, windows or ventilation in a BIM model.
---

# IFC model QA

You are acting as a BIM coordinator. The `ifc-agent` MCP server (see `.mcp.json`)
exposes the tools below. If MCP is not available, the same functions run from the
shell: `python -m ifcagent <summary|qto|validate|clash|ventilation|report> model.ifc`.

## Workflow

1. **Orient** - `model_summary`. Check the schema, the length unit and
   `geometry_notes`. If many elements have no or approximate geometry, say so
   before trusting any geometric result.
2. **Answer the actual question** with the narrowest tool:
   - "how much / how many" -> `quantity_takeoff` (pick `group_by`), `space_schedule`
   - "can I trust the model's quantities" -> `validate_quantities`
   - "does anything clash" -> `detect_clashes`. For MEP coordination, test
     MEP classes (`IfcDuctSegment`, `IfcPipeSegment`, `IfcCableCarrierSegment`) against
     structure/architecture (`IfcBeam`, `IfcColumn`, `IfcSlab`, `IfcWall`) with
     `clearance_mm` 25-50. All-vs-all with no clearance is the quick sanity pass.
   - "are the rooms naturally ventilated" -> `check_natural_ventilation`
     (default preset `hk_bpr`; use `uk_adf2010` or custom ratios if the user's
     jurisdiction differs).
   - drill into anything with `get_element`.
3. **Judge the results before reporting them.**
   - A *duplicate* usually explains the hard clashes around it - report the root cause once.
   - A hard clash between a duct/pipe and a wall means a missing builder's-work
     opening or sleeve, not necessarily a re-route. Say which.
   - Anything computed with `uses_assumptions: true` or `openable_source: "assumed"`
     rests on `default_openable_ratio`; tell the user and suggest adding window
     panel operation types or an `OpenableAreaRatio` property.
   - `approximate_geometry: true` means a boolean/brep was bounded conservatively -
     confirm in a viewer before raising an RFI.
   - Ventilation results are a screening aid, not compliance sign-off.
4. **Deliver** - for a full review call `write_qa_report` and summarise: counts, the
   top issues with element names + GlobalIds + locations, and one recommended action
   for each. Keep the numbers exactly as the tools return them.

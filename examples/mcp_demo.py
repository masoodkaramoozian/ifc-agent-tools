"""End-to-end MCP round trip, no LLM needed.

Starts ``python -m ifcagent.mcp_server`` as a subprocess over stdio - exactly
how Claude Code or Codex would - lists its tools and calls a few of them the
way an agent would during a coordination review.

    python examples/mcp_demo.py [model.ifc]
"""
from __future__ import annotations

import asyncio
import json
import os
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
IFC = os.path.abspath(sys.argv[1]) if len(sys.argv) > 1 else os.path.join(ROOT, "examples", "sample_house.ifc")


def show(title: str, result) -> dict:
    data = result.structuredContent
    if data is None:
        data = json.loads(result.content[0].text)
    data = data.get("result", data) if isinstance(data, dict) else data
    print(f"\n=== {title}")
    print(json.dumps(data, indent=1)[:1500])
    return data


async def main() -> None:
    params = StdioServerParameters(command=sys.executable, args=["-m", "ifcagent.mcp_server"], cwd=ROOT,
                                   env={**os.environ, "PYTHONPATH": ROOT})
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            print("tools:", ", ".join(t.name for t in tools.tools))
            show("model_summary", await session.call_tool("model_summary", {"ifc_path": IFC}))
            show("MEP vs structure clashes (50 mm clearance)", await session.call_tool(
                "detect_clashes", {"ifc_path": IFC, "group_a": ["IfcDuctSegment", "IfcPipeSegment"],
                                   "group_b": ["IfcBeam", "IfcWall", "IfcSlab"], "clearance_mm": 50}))
            vent = show("natural ventilation (HK B(P)R)", await session.call_tool(
                "check_natural_ventilation", {"ifc_path": IFC}))
            failing = [s["long_name"] for s in vent["spaces"] if not s["pass"]]
            print("\nfailing spaces:", failing)
            show("validate_quantities", await session.call_tool("validate_quantities", {"ifc_path": IFC}))


if __name__ == "__main__":
    asyncio.run(main())

#!/usr/bin/env python3
"""Dump the live MCP server's tool registry to references/_tool_inventory.json.

The inventory is the source of truth the skill's documentation is tested
against, so it is generated from the server itself rather than hand-written.

    python3 skills/blender-agent-mcp/scripts/gen_tool_inventory.py
    # or: make skill-inventory

The MCP tool catalog is introspected from the server's registry — no Blender
needed, and nothing is invoked. Bridge-command metadata (mutates / needs_gui) is
read from Blender's own registry when Blender is installed, because some
commands register through wrapper decorators whose flags are invisible to a
source scan; without Blender it falls back to parsing the decorators and reports
those flags as null rather than guessing. `bridge_source` records which path ran.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import pathlib
import sys

SKILL_DIR = pathlib.Path(__file__).resolve().parent.parent
REPO = SKILL_DIR.parent.parent
OUT = SKILL_DIR / "references" / "_tool_inventory.json"

sys.path.insert(0, str(REPO / "mcp_server" / "src"))


def _module_of(tool_name: str, mapping: dict[str, str]) -> str:
    return mapping.get(tool_name, "unknown")


def collect_tools() -> tuple[list[dict], list[str], dict]:
    """Register every tool module and read back the resulting catalog."""
    from blender_agent_mcp import server, tools as tools_pkg

    # Record which module contributes which tool by registering one at a time.
    from mcp.server.mcpserver import MCPServer
    import importlib

    ownership: dict[str, str] = {}
    for module_name in tools_pkg.MODULES:
        try:
            module = importlib.import_module(f"blender_agent_mcp.tools.{module_name}")
        except ModuleNotFoundError:
            continue
        probe = MCPServer(name=f"probe-{module_name}")
        module.register(probe)
        for tool in asyncio.run(probe.list_tools()):
            ownership[tool.name] = module_name

    server.register_all()
    catalog = asyncio.run(server.mcp.list_tools())

    entries = []
    for tool in sorted(catalog, key=lambda t: (_module_of(t.name, ownership), t.name)):
        # mcp 2.x renamed inputSchema -> input_schema; accept either.
        schema = getattr(tool, "input_schema", None) or getattr(tool, "inputSchema", None) or {}
        properties = schema.get("properties", {}) or {}
        required = set(schema.get("required", []) or [])
        params = []
        for pname, pschema in properties.items():
            params.append({
                "name": pname,
                "type": _type_of(pschema),
                "required": pname in required,
                "default": pschema.get("default", None),
                "description": (pschema.get("description") or "").strip(),
            })
        description = (tool.description or "").strip()
        entries.append({
            "name": tool.name,
            "module": _module_of(tool.name, ownership),
            "summary": description.split("\n")[0].strip(),
            "description": description,
            "needs_gui": "GUI Blender only" in description,
            "params": params,
        })
    return entries, list(tools_pkg.LOADED), dict(tools_pkg.FAILED)


def _type_of(schema: dict) -> str:
    if "type" in schema:
        return schema["type"]
    for key in ("anyOf", "oneOf"):
        if key in schema:
            options = [s.get("type") for s in schema[key] if s.get("type") != "null"]
            options = [o for o in options if o]
            if options:
                return "|".join(dict.fromkeys(options))
    return schema.get("format", "any")


BLENDER = "/Applications/Blender.app/Contents/MacOS/Blender"

_REGISTRY_DUMP = """
import sys, json
sys.path.insert(0, {repo!r})
import blender_extension
from blender_extension import registry
print("===INV===")
print(json.dumps([
    {{"command": name, "module": name.split(".")[0] if "." in name else "core",
      "mutates": meta["mutates"], "needs_gui": meta["needs_gui"],
      "doc": meta["doc"]}}
    for name, meta in sorted(registry.META.items())
]))
print("===END===")
"""


def collect_bridge_commands() -> tuple[list[dict], str]:
    """Read the extension's command registry.

    Prefers Blender's own registry — authoritative, and it captures commands
    registered through wrapper decorators whose flags are not visible in the
    source. Falls back to parsing the decorators when Blender is unavailable.
    """
    runtime = _bridge_from_blender()
    if runtime is not None:
        return runtime, "blender-runtime"
    return _bridge_from_source(), "ast-fallback"


def _bridge_from_blender() -> list[dict] | None:
    import subprocess
    import tempfile

    if not pathlib.Path(BLENDER).exists():
        return None
    script = _REGISTRY_DUMP.format(repo=str(REPO))
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as fh:
        fh.write(script)
        path = fh.name
    try:
        proc = subprocess.run(
            [BLENDER, "--background", "--factory-startup", "--python", path],
            capture_output=True, text=True, timeout=180,
        )
    except (subprocess.SubprocessError, OSError):
        return None
    finally:
        pathlib.Path(path).unlink(missing_ok=True)

    out = proc.stdout
    if "===INV===" not in out or "===END===" not in out:
        return None
    body = out.split("===INV===", 1)[1].split("===END===", 1)[0].strip()
    try:
        return sorted(json.loads(body), key=lambda c: c["command"])
    except json.JSONDecodeError:
        return None


def _bridge_from_source() -> list[dict]:
    """Parse ``@command("name", ...)`` decorators, including wrapper factories.

    Matches any decorator call whose first positional argument is a
    ``module.name`` string, so helper decorators that forward to ``command()``
    are counted too. Their flags live inside the wrapper and are reported as
    None rather than guessed.
    """
    import ast
    import re

    name_re = re.compile(r"^[a-z_]+\.[a-z_0-9]+$")
    commands = []
    handlers = REPO / "blender_extension" / "handlers"
    for path in sorted(handlers.glob("*.py")):
        if path.name == "__init__.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            for deco in node.decorator_list:
                if not isinstance(deco, ast.Call) or not deco.args:
                    continue
                first = deco.args[0]
                if not (isinstance(first, ast.Constant) and isinstance(first.value, str)):
                    continue
                if not name_re.match(first.value):
                    continue
                direct = getattr(deco.func, "id", "") == "command"
                flags = {kw.arg: getattr(kw.value, "value", None) for kw in deco.keywords}
                commands.append({
                    "command": first.value,
                    "module": first.value.split(".")[0],
                    "handler": node.name,
                    "mutates": bool(flags.get("mutates", False)) if direct else None,
                    "needs_gui": bool(flags.get("needs_gui", False)) if direct else None,
                })
    return sorted(commands, key=lambda c: c["command"])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true",
                        help="exit non-zero if the inventory on disk is stale")
    parser.add_argument("--out", default=str(OUT))
    args = parser.parse_args()

    tools, loaded, failed = collect_tools()
    if failed:
        sys.exit(f"error: tool modules failed to load: {failed}")

    payload = {
        "generated_by": "skills/blender-agent-mcp/scripts/gen_tool_inventory.py",
        "tool_count": len(tools),
        "modules": loaded,
        "bridge_command_count": 0,
        "bridge_source": None,
        "tools": tools,
        "bridge_commands": [],
    }
    bridge, source = collect_bridge_commands()
    payload["bridge_commands"] = bridge
    payload["bridge_command_count"] = len(bridge)
    payload["bridge_source"] = source

    out_path = pathlib.Path(args.out)
    rendered = json.dumps(payload, indent=1, sort_keys=False) + "\n"

    if args.check:
        if not out_path.is_file():
            sys.exit(f"error: {out_path} does not exist — run this script")
        if out_path.read_text(encoding="utf-8") != rendered:
            sys.exit(
                f"error: {out_path} is stale. Regenerate it:\n"
                f"  python3 {pathlib.Path(__file__).relative_to(REPO)}"
            )
        print(f"inventory is current: {len(tools)} tools, {len(bridge)} bridge commands")
        return

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(rendered, encoding="utf-8")
    print(f"wrote {out_path}")
    print(f"  {len(tools)} MCP tools across {len(loaded)} modules")
    print(f"  {len(bridge)} bridge commands "
          f"({sum(1 for c in bridge if c['needs_gui'])} GUI-only, "
          f"{sum(1 for c in bridge if c['mutates'])} mutating)")


if __name__ == "__main__":
    main()

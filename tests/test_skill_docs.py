"""Guard the skill's documentation against drift from the real tool catalog.

The skill is only useful if it documents the tools that actually exist with the
parameters they actually take. These tests fail when someone adds, renames or
re-signatures a tool without regenerating the inventory and updating the docs.

Regenerate with:
    python3 skills/blender-agent-mcp/scripts/gen_tool_inventory.py
"""

from __future__ import annotations

import asyncio
import json
import pathlib
import re

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
SKILL = REPO / "skills" / "blender-agent-mcp"
INVENTORY = SKILL / "references" / "_tool_inventory.json"
TOOL_REFERENCE = SKILL / "references" / "tool-reference.md"
SKILL_MD = SKILL / "SKILL.md"


@pytest.fixture(scope="module")
def inventory() -> dict:
    if not INVENTORY.is_file():
        pytest.fail(
            f"{INVENTORY} is missing. Generate it:\n"
            "  python3 skills/blender-agent-mcp/scripts/gen_tool_inventory.py"
        )
    return json.loads(INVENTORY.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def live_tools() -> dict[str, dict]:
    """The tool catalog as the running MCP server reports it."""
    from blender_agent_mcp import server, tools as tools_pkg

    server.register_all()
    assert not tools_pkg.FAILED, f"tool modules failed to import: {tools_pkg.FAILED}"
    catalog = asyncio.run(server.mcp.list_tools())
    out = {}
    for tool in catalog:
        schema = (getattr(tool, "input_schema", None)
                  or getattr(tool, "inputSchema", None) or {})
        out[tool.name] = {
            "params": set((schema.get("properties") or {}).keys()),
            "required": set(schema.get("required") or []),
            "description": (tool.description or "").strip(),
        }
    return out


# ---------------------------------------------------------------------------
# inventory vs the live server
# ---------------------------------------------------------------------------

def test_inventory_matches_live_tool_names(inventory, live_tools):
    recorded = {t["name"] for t in inventory["tools"]}
    live = set(live_tools)
    assert recorded == live, (
        f"inventory is stale.\n"
        f"  missing from inventory: {sorted(live - recorded)}\n"
        f"  no longer on server:    {sorted(recorded - live)}\n"
        f"Regenerate: python3 skills/blender-agent-mcp/scripts/gen_tool_inventory.py"
    )


def test_inventory_matches_live_parameters(inventory, live_tools):
    problems = []
    for entry in inventory["tools"]:
        live = live_tools.get(entry["name"])
        if live is None:
            continue
        recorded = {p["name"] for p in entry["params"]}
        if recorded != live["params"]:
            problems.append(
                f"{entry['name']}: inventory {sorted(recorded)} != live {sorted(live['params'])}")
    assert not problems, "parameter drift:\n  " + "\n  ".join(problems)


def test_inventory_required_flags_match(inventory, live_tools):
    problems = []
    for entry in inventory["tools"]:
        live = live_tools.get(entry["name"])
        if live is None:
            continue
        recorded = {p["name"] for p in entry["params"] if p["required"]}
        if recorded != live["required"]:
            problems.append(
                f"{entry['name']}: required {sorted(recorded)} != live {sorted(live['required'])}")
    assert not problems, "required-parameter drift:\n  " + "\n  ".join(problems)


def test_every_tool_is_documented(inventory):
    """A tool with no description is invisible to an agent."""
    undocumented = [t["name"] for t in inventory["tools"] if not t["description"].strip()]
    assert not undocumented, f"tools with no docstring: {undocumented}"


# ---------------------------------------------------------------------------
# docs vs the inventory
# ---------------------------------------------------------------------------

def _reference_text() -> str:
    if not TOOL_REFERENCE.is_file():
        pytest.fail(f"{TOOL_REFERENCE} is missing")
    return TOOL_REFERENCE.read_text(encoding="utf-8")


def test_every_tool_appears_in_the_reference(inventory):
    text = _reference_text()
    # Tools are documented as headings like "### stroke_line".
    documented = set(re.findall(r"^#{2,4}\s+`?([a-z_][a-z0-9_]*)`?\s*$",
                                text, re.MULTILINE))
    missing = [t["name"] for t in inventory["tools"] if t["name"] not in documented]
    assert not missing, (
        f"{len(missing)} tool(s) missing from tool-reference.md: {sorted(missing)[:25]}"
    )


def test_reference_documents_no_phantom_tools(inventory):
    """Documenting a tool that does not exist is worse than omitting it."""
    text = _reference_text()
    real = {t["name"] for t in inventory["tools"]}
    documented = set(re.findall(r"^#{3,4}\s+`?([a-z_][a-z0-9_]*)`?\s*$",
                                text, re.MULTILINE))
    # Section headings that are not tool names are fine; only flag things that
    # look like tool docs, i.e. carry a parameter table right after.
    phantom = sorted(
        name for name in documented - real
        if re.search(rf"^#{{3,4}}\s+`?{re.escape(name)}`?\s*$\n(?:.*\n)*?\|\s*param",
                     text, re.MULTILINE | re.IGNORECASE)
    )
    assert not phantom, f"tool-reference.md documents non-existent tools: {phantom}"


def test_gui_only_tools_are_flagged_in_the_reference(inventory):
    text = _reference_text()
    gui_tools = [t["name"] for t in inventory["tools"] if t["needs_gui"]]
    assert gui_tools, "inventory records no GUI-only tools — that is suspicious"
    unflagged = []
    for name in gui_tools:
        match = re.search(rf"^#{{2,4}}\s+`?{re.escape(name)}`?\s*$", text, re.MULTILINE)
        if not match:
            continue  # covered by test_every_tool_appears_in_the_reference
        section = text[match.start(): match.start() + 1500]
        if "GUI" not in section:
            unflagged.append(name)
    assert not unflagged, f"GUI-only tools not flagged as such: {unflagged}"


# ---------------------------------------------------------------------------
# skill structure
# ---------------------------------------------------------------------------

def test_skill_md_exists_with_frontmatter():
    assert SKILL_MD.is_file(), f"{SKILL_MD} is missing"
    text = SKILL_MD.read_text(encoding="utf-8")
    assert text.startswith("---\n"), "SKILL.md must open with YAML frontmatter"
    end = text.index("\n---", 3)
    front = text[4:end]
    assert re.search(r"^name:\s*blender-agent-mcp\s*$", front, re.MULTILINE)
    assert re.search(r"^description:", front, re.MULTILINE)


def test_skill_md_description_within_listing_budget():
    """description + when_to_use is truncated at 1536 chars in the skill listing."""
    text = SKILL_MD.read_text(encoding="utf-8")
    front = text[4:text.index("\n---", 3)]
    match = re.search(r"^description:\s*(.*?)(?=\n[a-z_-]+:|\Z)", front,
                      re.MULTILINE | re.DOTALL)
    assert match, "no description in frontmatter"
    description = re.sub(r"\s+", " ", match.group(1)).strip().strip(">|").strip()
    assert len(description) <= 1536, (
        f"description is {len(description)} chars; the listing truncates at 1536"
    )
    assert len(description) > 150, "description too thin to trigger reliably"


def test_skill_md_stays_within_line_budget():
    lines = SKILL_MD.read_text(encoding="utf-8").splitlines()
    assert len(lines) <= 500, (
        f"SKILL.md is {len(lines)} lines; keep it under 500 and move detail to "
        "references/"
    )


def test_all_reference_files_exist_and_are_substantial():
    expected = {
        "tool-reference.md": 300,
        "sculpting.md": 150,
        "weight-painting.md": 150,
        "rigging-animation.md": 120,
        "recipes.md": 150,
        "troubleshooting.md": 80,
    }
    problems = []
    for name, minimum in expected.items():
        path = SKILL / "references" / name
        if not path.is_file():
            problems.append(f"{name} is missing")
            continue
        count = len(path.read_text(encoding="utf-8").splitlines())
        if count < minimum:
            problems.append(f"{name} has {count} lines, expected >= {minimum}")
    assert not problems, "; ".join(problems)


def _known_identifiers(inventory: dict) -> set[str]:
    """Tool names plus every parameter and returned-field name they use."""
    known = set()
    for tool in inventory["tools"]:
        known.add(tool["name"])
        for param in tool["params"]:
            known.add(param["name"])
        # Result fields appear in prose as `like_this`; harvest them from the
        # docstrings so legitimate field references are not flagged.
        known.update(re.findall(r"`([a-z][a-z0-9_]{2,})`", tool["description"]))
    for command in inventory["bridge_commands"]:
        known.add(command["command"])
        known.add(command["command"].split(".")[-1])
    return known


# Prose words that look like identifiers but are not tools or params.
_PROSE_ALLOWLIST = {
    "has_view3d", "resolution_percentage", "blender_version", "png_b64",
    "created_objects", "ignored_options", "applied_options", "needs_gui",
    "frame_current", "frame_start", "frame_end", "vertices_after",
    "vertices_before", "total_levels", "matrix_world", "bound_box",
    "use_unified_size", "size_written_to", "strength_written_to",
    "radial_supported", "dropped_points", "missed_rays", "screenshot_note",
    "mode_used", "only_selected", "link_count", "node_count", "action_slot",
    "rotation_mode", "rotation_euler", "rotation_quaternion", "data_path",
    "object_count", "bridge_commands", "tool_count", "bridge_source",
    "remesh_voxel_size", "sculpt_tool", "use_seam", "active_render",
    "coverage_percent", "overlap_likely", "loops_outside_0_1", "texel_density_hint",
    "use_dynamic_topology_sculpting", "detail_type_method", "detail_refine_method",
    "constant_detail_resolution", "detail_size", "unprojected_size",
    "use_symmetry_x", "use_symmetry_y", "use_symmetry_z", "use_symmetry_feather",
    "brush_asset_reference", "relative_asset_identifier", "asset_library_type",
    "vert_mapping", "data_type", "input_schema", "total_keyframes",
    "keyframes_changed", "discarded_unsaved_changes", "is_dirty",
    "per_bone_weight_summary", "socket_type", "in_out", "default_value",
    "items_tree", "node_group", "bl_idname", "use_edge_sharp",
}


@pytest.mark.parametrize("doc_name", [
    "SKILL.md",
    "references/sculpting.md",
    "references/weight-painting.md",
    "references/rigging-animation.md",
    "references/recipes.md",
    "references/troubleshooting.md",
])
def test_docs_do_not_reference_nonexistent_tools(inventory, doc_name):
    """Catch a documented call like `select_geometry(...)` when the tool is
    actually `mesh_select_geometry`. This is the failure mode that makes a skill
    actively harmful: the agent confidently calls something that does not exist.
    """
    path = SKILL / doc_name
    if not path.is_file():
        pytest.skip(f"{doc_name} not present")
    text = path.read_text(encoding="utf-8")
    known = _known_identifiers(inventory) | _PROSE_ALLOWLIST

    # Anything written in call form is unambiguously meant as a tool call.
    called = set(re.findall(r"^\s*([a-z][a-z0-9_]{2,})\s*\(", text, re.MULTILINE))
    bogus = sorted(name for name in called if name not in known)
    assert not bogus, (
        f"{doc_name} calls tools that do not exist: {bogus}\n"
        f"Check the real names in references/_tool_inventory.json."
    )


def test_skill_md_routes_to_every_reference_file():
    """A reference nobody is told to open may as well not exist."""
    text = SKILL_MD.read_text(encoding="utf-8")
    for name in ("tool-reference.md", "sculpting.md", "weight-painting.md",
                 "rigging-animation.md", "recipes.md", "troubleshooting.md"):
        assert name in text, f"SKILL.md never points the agent at {name}"

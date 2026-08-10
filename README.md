# blender-agent-mcp

Deep, scriptable control of **Blender** for AI agents, over **MCP**.

Two halves of one system:

| Part | What it is |
| --- | --- |
| `blender_extension/` | A Blender 4.2+ extension (developed and tested on **5.2 LTS**) that runs a loopback TCP bridge inside Blender and executes commands safely on the main thread. |
| `mcp_server/` | A stdio MCP server (official `mcp` Python SDK) that any MCP client — Claude Code, Claude Desktop — connects to. It relays calls to the bridge. |

It covers objects, mesh editing, modifiers, **sculpting** (real brush strokes),
**weight painting** (data-API first), rigging, shading, UV, animation, geometry
nodes, import/export, and version-correct documentation search.

---

## Requirements

* macOS 13 or newer (Apple Silicon primary; Intel supported)
* **Blender ≥ 4.2**, installed at `/Applications/Blender.app`. Developed against **5.2 LTS**.
* [`uv`](https://docs.astral.sh/uv/) — `brew install uv`
* Python 3.11+ for the MCP server (uv fetches one if needed)

Blender's CLI, used by every make target and by headless tests:

```bash
/Applications/Blender.app/Contents/MacOS/Blender
```

---

## Install

### 1. Build the extension

```bash
make build-ext
```

This writes `dist/blender_agent_mcp-<version>.zip`. It uses Blender's own
`--command extension build`, so the manifest is validated exactly as the
installer will validate it.

### 2. Install it into Blender

Either through the UI:

1. **Blender ▸ Edit ▸ Preferences ▸ Get Extensions**
2. The **▾** menu (top right) ▸ **Install from Disk…**
3. Pick `dist/blender_agent_mcp-<version>.zip`
4. Approve the **network** and **files** permissions when prompted
5. Make sure the add-on's checkbox is ticked

…or from the command line:

```bash
make install-ext
```

### 3. Start the bridge

In the 3D Viewport press **N** ▸ **Agent MCP** tab ▸ **Start Server**.

The panel shows connection state, the port, an autostart-on-file-load toggle, and
the last 20 commands with their durations. The bridge binds **127.0.0.1 only** and
is never reachable from another machine.

### 4. Register the MCP server

**Claude Code** — from anywhere:

```bash
claude mcp add blender-agent -- uv run --directory /ABSOLUTE/PATH/TO/blender-agent-mcp/mcp_server blender-agent-mcp
```

Add `-s user` to make it available in every project instead of just this one.
Verify with `claude mcp list` — it should report `✔ Connected`.

**Claude Desktop** — edit
`~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "blender-agent": {
      "command": "uv",
      "args": [
        "run",
        "--directory",
        "/ABSOLUTE/PATH/TO/blender-agent-mcp/mcp_server",
        "blender-agent-mcp"
      ]
    }
  }
}
```

Restart Claude Desktop afterwards. Use absolute paths in both cases — the server
is launched with an unpredictable working directory.

---

## Try it

With Blender open and the bridge started, ask your agent:

> Show me the scene, then add a UV sphere, voxel remesh it at 0.03, and take a screenshot.

A good first health check is the `health` tool: it reports whether the bridge is
reachable, which Blender is on the other end, and whether a 3D Viewport exists.

---

## Architecture

```
MCP client ──stdio──▶ mcp_server ──TCP 127.0.0.1:9876──▶ Blender extension
                                   JSON lines               │
                                                            ▼
                                              socket thread (no bpy!)
                                                            │  queue
                                                            ▼
                                              bpy.app.timers pump  ← main thread
                                                            │
                                                            ▼
                                                    handlers/*.py
```

`bpy` is not thread-safe. The socket threads only parse JSON and enqueue work; a
`bpy.app.timers` callback drains that queue on Blender's main thread and hands
results back. Nothing touches Blender state off the main thread.

### Visible agent presence

GUI Blender shows what the connected agent is doing instead of leaving its work
invisible:

- The supplied upward cursor SVG is rendered as a crisp Blender GPU overlay in
  the relevant editor: shader/geometry/compositor nodes, UV/Image Editor,
  animation editors, Video Sequencer, or the 3D Viewport fallback.
- The cursor rests with a gentle left tilt and idle wiggle. During travel it
  rotates its tip into the direction of motion, follows a damped spring path,
  and smoothly settles back into its tilted pose at the destination.
- Retry-safe shader graph builds move the cursor after each real node mutation,
  so node construction is visible while it happens. Custom Python graph loops
  can call `agent_activity.step("Blur", [x, y])` for the same behavior.
- `execute_python` sends the cursor off the editor's right edge, then has it drag
  a macOS-style **Agent Terminal** into the center. The command types in before
  streaming stdout/stderr; the cursor waits outside the lower-right corner,
  clicks the red control on completion, and carries on as the window closes.
- Python gets a `run_terminal(command, cwd=None, timeout=120, check=True)` helper.
  It streams a zsh command string—or an argv list without a shell—into that
  terminal as it runs.

```python
execute_python(
    code="run_terminal('python3 -u render_preview.py', cwd='/path/to/show')",
    timeout=180,
)
```

The **Agent MCP** N-panel has toggles for the cursor, terminal, overlay scale and
pre-close pause, plus a **Preview** button. Overlays are disabled safely in
headless Blender. Commands an agent runs completely outside this MCP bridge
cannot be observed; use `run_terminal` when their live output should appear in
Blender.

### Wire protocol

JSON, one object per line, over TCP.

```jsonc
// request
{"id": "7", "cmd": "objects.create_primitive", "params": {"kind": "CUBE"}}
// success
{"id": "7", "ok": true,  "result": {"name": "Cube"}}
// failure — the agent always learns why
{"id": "7", "ok": false, "error": "KeyError: 'Sphere'", "traceback": "Traceback ..."}
```

* Default budget **10 s** per command; slow tools (remesh, quadriflow, render,
  playblast) raise it per call.
* Images come back as `{"png_b64": ..., "width": ..., "height": ...}` and are
  turned into real MCP image content blocks, so agents can see them.
* Built-in commands: `ping`, `get_version`, `list_commands`, `shutdown`.

### Port

Default **9876**. Change it in the N-panel, or override everything with the
`BLENDER_AGENT_PORT` environment variable (it must be set for *both* Blender and
the MCP server).

---

## What needs GUI Blender

Anything that needs a real `VIEW_3D` area cannot work under `blender --background`:

* `viewport_screenshot`, `playblast`, `set_viewport_shading`
* all `sculpt_stroke*` / `stroke_*` / `radial_strokes` tools
* `weight_gradient`, `weight_heatmap`
* mask and face-set *gesture* tools

These report an actionable error rather than a confusing OpenGL failure. Every
other tool — objects, mesh, modifiers, weights via the data API, rigging,
shading, UV, animation, import/export — works headless.

`list_bridge_commands` reports `needs_gui` per command, so an agent can check
before it commits to an approach.

---

## Tool catalog

231 tools across these modules. Every tool carries a docstring covering
what it does, when to use it instead of an alternative, parameter units, and its
gotchas — that documentation is the interface agents actually read.

| Module | Covers |
| --- | --- |
| `core` | health, versions, scene/object introspection, modes, undo/redo, `execute_python`, `describe_api`, screenshots, renders |
| `objects` | primitives, empties/cameras/lights, transforms, parenting, collections, join/separate, selection, align & snap |
| `mesh` | bmesh-backed select/extrude/inset/bevel/subdivide/merge/fill/bridge/symmetrize/normals/shading/triangulate/decimate |
| `modifiers` | generic add/list/set/apply/remove/reorder plus subsurf, mirror, solidify, boolean, shrinkwrap, armature, multires, remesh, data transfer |
| `sculpt` | brushes (asset-based), symmetry, real brush strokes, dyntopo, voxel/quadriflow remesh, masks, face sets, mesh filters |
| `weights` | vertex groups, bulk weight read/write, auto weights, normalize/mirror/smooth/clean/quantize/limit, gradient, transfer, diagnostics, heatmap |
| `rig` | armature creation, edit/pose bones, constraints, IK setup, mesh parenting, shape keys, drivers, bone collections |
| `shading` | PBR materials, retry-safe batch node graphs, Color Ramps, generated/custom image textures, viewport shading, baking |
| `uv` | seams, unwrap, smart project, pack islands, UV stats |
| `anim` | frames, fps, single/bulk object, bone and custom-property keys, interpolation, actions, NLA, playblast |
| `cinematics` | shot markers and camera cuts, Video Sequencer image/movie/audio/text/color editing, final MP4/PNG animation renders |
| `geonodes` | geometry-nodes modifier and node-group construction |
| `settings` | persistent render/output, color management, units and World lighting |
| `properties` | custom ID properties for metadata, rig controls and driver inputs |
| `io` | OBJ / FBX / glTF / USD / STL / PLY / Alembic import & export, blend save/open/append |
| `docs` | manual + Python API search via intersphinx, page fetch, tutorial search, caching |

### Two ways to ask "how does this work?"

* **`describe_api("bpy.ops.sculpt.brush_stroke")`** — live RNA introspection of the
  *running* Blender. Exact parameter names, enums and defaults; never stale.
* **`search_blender_manual(...)` / `search_python_api(...)` / `get_doc_page(...)`** —
  prose, concepts and version-correct deep links, from the official Sphinx
  inventories.

Use the first for signatures, the second for understanding.

---

## The Agent Skill

The repo ships an [Agent Skill](https://code.claude.com/docs/en/skills) at
[`skills/blender-agent-mcp/`](skills/blender-agent-mcp/) that teaches an agent
how to *use* these tools well — session-start protocol, the observe-act-verify
loop, tool-selection decision tables, and deep references on sculpting, weight
painting, rigging and troubleshooting.

Registering the MCP server gives an agent the tools; installing the skill gives
it the judgement to sequence them.

### Install it

Skills load from a `skills/<skill-name>/SKILL.md` directory. Pick a scope:

```bash
# This project only — everyone who clones the repo gets it
mkdir -p .claude/skills && ln -s ../../skills/blender-agent-mcp .claude/skills/blender-agent-mcp
```

```bash
# All your projects
mkdir -p ~/.claude/skills && cp -R skills/blender-agent-mcp ~/.claude/skills/
```

| Scope | Path |
| --- | --- |
| Personal | `~/.claude/skills/blender-agent-mcp/SKILL.md` |
| Project | `.claude/skills/blender-agent-mcp/SKILL.md` |
| Plugin | `<plugin>/skills/blender-agent-mcp/SKILL.md` |

A symlink keeps the project copy in sync with the repo; `cp -R` does not.
Confirm it loaded by typing `/blender-agent-mcp` — Claude also invokes it on its
own whenever a request involves Blender.

### Regenerate the inventory after adding tools

`references/_tool_inventory.json` is generated from the live MCP server and is
what the documentation tests check against. After adding or changing any tool:

```bash
make skill-inventory
```

Then update `references/tool-reference.md` to match and run `make test`. The
tests fail if a tool exists but is undocumented, if a documented tool does not
exist, if any parameter list has drifted, or if a GUI-only tool is not flagged.

## Development

```bash
make build-ext     # build dist/*.zip (validated by Blender itself)
make install-ext   # build + install + enable in Blender
make run-server    # run the MCP server on stdio
make test          # unit tests + headless integration smoke
make smoke         # headless integration only
make demo          # GUI acceptance demo (needs Blender + bridge running)
make probe         # re-verify every documented API assumption
```

### After upgrading Blender

Run `make probe` first. It checks every assumption recorded in
[`docs/BLENDER_5X_API_NOTES.md`](docs/BLENDER_5X_API_NOTES.md) against the new
build and exits non-zero when one breaks — including "an operator we work around
because it was removed has come back", so workarounds can be retired.

That notes file exists because several APIs changed in ways that break code
written from memory. A few that bite hardest on 5.2:

* `mcp.server.fastmcp` was **removed** in mcp SDK 2.0 — the class is `MCPServer`.
* `Brush.sculpt_tool` **no longer exists**; brushes are assets activated by
  identifier, and their real names are `Inflate/Deflate`, `Scrape/Fill`,
  `Crease Sharp`… not the friendly names in most tutorials.
* `paint.brush_select` and `object.vertex_group_transfer_weight` were removed.
* `wm.fbx_import` exists but `wm.fbx_export` does not.
* Manifest permission reasons are capped at **64 characters**.

---

## Troubleshooting

**"Could not reach Blender on 127.0.0.1:9876"**
Blender must be *running with the bridge started*: N-panel ▸ Agent MCP ▸ Start
Server. Check the port matches. Then call `reconnect`.

**"Port 9876 is already in use"**
Something else holds it — often a previous Blender that did not shut down
cleanly. Find it with `lsof -nP -iTCP:9876 -sTCP:LISTEN`, or just pick another
port in the panel and set `BLENDER_AGENT_PORT` to match for the MCP server.

**Port 9876 collides with Blender's own MCP add-on.** Blender Lab's official
`mcp` extension also defaults to `DEFAULT_PORT = 9876`. If both are installed
and started, whichever starts first wins and the other fails to bind. Worse, the
MCP server will happily connect to the *other* server and then time out, which
reads like "Blender is not responding" rather than "wrong server". If `health`
connects but every command times out, check which add-on owns the port:

```bash
lsof -nP -iTCP:9876 -sTCP:LISTEN
```

Then either stop the other server, or move this bridge to another port in the
N-panel and set `BLENDER_AGENT_PORT` to match for the MCP server.

**macOS firewall prompt on first start**
macOS may ask whether Blender should accept incoming connections. Allowing it is
safe — the bridge binds loopback only, so nothing off this machine can reach it.
Denying it does not break loopback connections either.

**Extension disabled after a Blender update**
Blender may disable extensions built for an older version. Re-tick it in
Preferences ▸ Add-ons, or `make install-ext` again.

**A tool says it needs a 3D Viewport**
You are on headless Blender, or no VIEW_3D area is open. See
[What needs GUI Blender](#what-needs-gui-blender).

**Commands time out**
The default budget is 10 s. Remeshing, quadriflow, Cycles renders and playblasts
take longer — pass a bigger `timeout`. If *everything* times out, Blender's main
thread is blocked (a modal operator, an open file dialog, or a render in
progress); dismiss it in the UI.

**`claude mcp list` shows the server failing**
Run the command by hand to see the real error:
`uv run --directory /ABS/PATH/mcp_server blender-agent-mcp`.

---

## Safety notes

* The bridge binds **loopback only** and has no authentication — anything already
  running as your user on this machine can drive Blender through it. Stop the
  server when you are not using it.
* `execute_python` runs arbitrary code inside Blender with your privileges. Its
  `run_terminal` helper can also run local shell commands. They are deliberate
  escape hatches; prefer the specific tool when one exists.
* Destructive file operations (`open_blend`, overwriting with `save_blend`)
  refuse unless called with `confirm: true`.

## License

GPL-3.0-or-later, matching Blender's Python API.

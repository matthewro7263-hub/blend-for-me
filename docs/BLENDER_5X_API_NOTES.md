# Verified Blender / MCP API notes

Everything here was **verified by live introspection** against the actual installed
software on 2026-08-08, not from memory. Blender **5.2.0 LTS** (Python 3.13.13),
`mcp` Python SDK **2.0.0**, Claude Code **2.1.220**, macOS 27.0 (arm64).

Re-verify with `make probe` after a Blender upgrade.

---

## 1. MCP Python SDK 2.0 — FastMCP was renamed

`mcp.server.fastmcp` **does not exist in 2.0.0**. `ModuleNotFoundError`.

The FastMCP-style ergonomic server is now `MCPServer`:

```python
from mcp.server.mcpserver import MCPServer, Image, Context

mcp = MCPServer(name="blender-agent-mcp", instructions="...")

@mcp.tool()
def my_tool(x: int) -> str:
    """Docstring becomes the tool description agents read."""
    ...

mcp.run(transport="stdio")
```

* `Image(path=None, data: bytes | None, format: str | None)` → `.to_image_content()`.
  Returning an `Image` from a tool yields a proper MCP image content block.
* `mcp.tool()` accepts `name`, `title`, `description`, `annotations`, `structured_output`.
* `mcp.run(transport="stdio" | "sse" | "streamable-http")`.
* Requires Python >= 3.10.

## 2. Blender extension manifest (schema 1.0.0)

Source of truth: `/Applications/Blender.app/Contents/Resources/5.2/scripts/templates_toml/blender_manifest.toml`.

* `[permissions]` is a **TOML table of `key = "reason"` strings**, not a list.
* Valid keys: `files`, `network`, `clipboard`, `camera`, `microphone`.
* Reason must be a short sentence with **no trailing period**.
* When using `network`, the docs say to also check `bpy.app.online_access`.
  Note: `bpy.app.online_access` is `False` under `--factory-startup` and when the
  user disables "Allow Online Access". A **localhost-only** bridge still works; we
  surface the flag in the UI rather than refusing to start.
* `[build] paths_exclude_pattern` defaults to `["__pycache__/", "/.git/", "/*.zip"]`.

## 3. Operators that DO NOT exist in 5.2 (would-be guesses that fail)

| Guess | Reality in 5.2 |
| --- | --- |
| `paint.brush_select` | **removed** — use `brush.asset_activate` |
| `object.vertex_group_transfer_weight` | **removed** — use `object.data_transfer(data_type='VGROUP_WEIGHTS')` |
| `sculpt.mask_box_gesture` / `sculpt.mask_lasso_gesture` | live under `paint.*` |
| `wm.fbx_export` | does not exist; `wm.fbx_import` **does** (new C++ importer). Export = `export_scene.fbx` |
| `wm.gltf_import` / `wm.gltf_export` | do not exist; use `import_scene.gltf` / `export_scene.gltf` |

Verified-present: `wm.obj_import/export`, `wm.stl_import/export`, `wm.ply_import/export`,
`wm.usd_import/export`, `wm.alembic_import/export`, `wm.fbx_import`, `object.shade_auto_smooth`,
`object.shade_smooth_by_angle`, `object.quadriflow_remesh`, `sculpt.mesh_filter`.

## 4. Brushes are ASSETS — `Brush.sculpt_tool` is gone

`bpy.types.Brush` has **no** `sculpt_tool`, no `weight_tool`, no `curve_preset`,
no `unprojected_radius` in 5.2. Selecting a brush by enum is impossible.

Activate by asset identifier instead:

```python
bpy.ops.brush.asset_activate(
    asset_library_type='ESSENTIALS',
    relative_asset_identifier="brushes/essentials_brushes-mesh_sculpt.blend/Brush/Clay Strips",
)
```

Essentials libraries live at
`bpy.utils.system_resource('DATAFILES', path="assets/brushes")`.

**Sculpt brush asset names in 5.2** (63) — note the friendly names in most tutorials
do *not* match, so the extension ships an alias map:

`Draw`, `Draw Sharp`, `Clay`, `Clay Strips`, `Clay Thumb`, `Blob`, `Grab`, `Grab 2D`,
`Snake Hook`, `Elastic Grab`, `Elastic Snake Hook`, `Smooth`, `Pose`, `Mask`, `Nudge`,
`Thumb`, `Twist`, `Layer`, `Boundary`, `Sharpen`, `Blur`, `Density`, `Airbrush`,
`Trim`, `Pull`, `Plateau`, `Smear`, `Relax Slide`, `Relax Pinch`, `Scene Project`,
`Grab Silhouette`, `Face Set Paint`, plus compound-named ones:

| Friendly (spec) | Real 5.2 asset |
| --- | --- |
| Crease | `Crease Sharp` (also `Crease Polish`) |
| Inflate | `Inflate/Deflate` |
| Flatten | `Flatten/Contrast` |
| Scrape | `Scrape/Fill` |
| Fill | `Fill/Deepen` |
| Pinch | `Pinch/Magnify` |
| Elastic Deform | `Elastic Grab` |

Weight-paint brushes (`essentials_brushes-mesh_weight.blend`): `Paint`, `Blur`,
`Average`, `Smear` — only four.

### Brush sizing gotcha
`Brush.size` is an **INT (pixels)**. `UnifiedPaintSettings` has `size`,
`unprojected_size`, `strength`, `weight`, and `use_unified_size` /
`use_unified_strength` / `use_unified_weight`. **If the unified flag is on, writing
`brush.size` silently does nothing** — write `unified_paint_settings.size` instead.
Note those settings are **per paint mode** in 5.x (`tool_settings.sculpt.…`,
`tool_settings.weight_paint.…`), not a shared block on `tool_settings`; see §8.
The extension writes whichever is authoritative and reports which one it used.

Automasking moved off `Brush` onto `Sculpt.mesh_automasking_settings`
(`bpy.types.MeshAutomaskingSettings`).

## 5. `sculpt.brush_stroke` — exact stroke element schema

```
sculpt.brush_stroke(
    stroke=[...],                # collection of OperatorStrokeElement
    mode='NORMAL'|'INVERT',
    brush_toggle='None'|'SMOOTH'|'ERASE'|'MASK',
    pen_flip=bool,
    override_location=bool,      # see below
    ignore_background_click=bool,
)
```

`OperatorStrokeElement` fields (all must be supplied per point):

| field | type |
| --- | --- |
| `name` | string |
| `location` | float[3] — **object space** |
| `mouse` | float[2] — region pixels |
| `mouse_event` | float[2] — region pixels |
| `pressure` | float |
| `size` | float |
| `x_tilt`, `y_tilt` | float |
| `time` | float |
| `is_start` | bool |

`override_location` docstring: *"Override the given `location` array by recalculating
object space positions from the provided `mouse_event` positions."* So with
`override_location=False` (default) the **`location` array is used directly** — we can
drive strokes from 3D coordinates and only need `mouse` for the screen-space radius.
Still requires a real `VIEW_3D` area via `temp_override`.

## 6. Radial symmetry was removed

`bpy.types.Sculpt` in 5.2 exposes only `use_symmetry_x/y/z`, `use_symmetry_feather`,
`symmetrize_direction`, `tile_x/y/z`, `tile_offset`. There is **no `radial_symmetry`**.
`sculpt_symmetry()` sets the axes and reports `radial_supported: false` rather than
raising.

## 7. Misc verified signatures

* `object.voxel_remesh()` takes **no arguments** — set `mesh.remesh_voxel_size`,
  `remesh_voxel_adaptivity`, `use_remesh_preserve_volume` on the mesh first.
* `sculpt.dynamic_topology_toggle()` takes no arguments — configure
  `tool_settings.sculpt.detail_size` / `constant_detail_resolution` /
  `detail_type_method` (`RELATIVE`/`CONSTANT`/`BRUSH`/`MANUAL`) /
  `detail_refine_method` first.
* `sculpt.mesh_filter(type=...)` enum: `SMOOTH`, `SCALE`, `INFLATE`, `SPHERE`,
  `RANDOM`, `RELAX`, `RELAX_FACE_SETS`, `SURFACE_SMOOTH`, `SHARPEN`,
  `ENHANCE_DETAILS`, `ERASE_DISPLACEMENT`.
* `sculpt.face_sets_init(mode=...)`: `LOOSE_PARTS`, `MATERIALS`, `NORMALS`,
  `UV_SEAMS`, `CREASES`, `BEVEL_WEIGHT`, `SHARP_EDGES`, `FACE_SET_BOUNDARIES`.
* `paint.mask_flood_fill(mode='VALUE'|'VALUE_INVERSE'|'INVERT', value=float)`.
* `paint.weight_gradient(type='LINEAR'|'RADIAL', xstart, xend, ystart, yend, flip)`
  — **INT screen coordinates**, so it needs world→region projection and a `VIEW_3D`.
* `object.parent_set(type=...)` includes `ARMATURE_AUTO` (automatic weights),
  `ARMATURE_NAME` (empty groups), `ARMATURE_ENVELOPE`.
* `object.data_transfer(data_type='VGROUP_WEIGHTS', vert_mapping=...)` where
  `vert_mapping` ∈ `TOPOLOGY`, `NEAREST`, `EDGE_NEAREST`, `EDGEINTERP_NEAREST`,
  `POLY_NEAREST`, `POLYINTERP_NEAREST`, `POLYINTERP_VNORPROJ`.
* `render.opengl(animation, write_still, view_context, sequencer, render_keyed_only)`
  — the playblast path.
* Render engines in a factory-startup scene: `['BLENDER_EEVEE']`. EEVEE's identifier
  is plain `BLENDER_EEVEE` again in 5.x (**not** `BLENDER_EEVEE_NEXT`). `CYCLES`
  only appears once the bundled `cycles` add-on is enabled — enable it before setting
  the engine.
* `bpy.types.Context.temp_override` and `bpy.app.timers` both present.

## 8. Discovered while building (all verified live on 5.2)

* **Unified paint settings moved.** `ToolSettings.unified_paint_settings` does not
  exist. They are per paint mode: `tool_settings.sculpt.unified_paint_settings`,
  `tool_settings.weight_paint.unified_paint_settings`. When `use_unified_size` is
  on, writing `brush.size` is silently ignored.
* **`Action.fcurves` is gone** — Blender 4.4+ *slotted actions*. Curves live at
  `action.layers[].strips[].channelbags[].fcurves`, and a channelbag belongs to an
  `ActionSlot` (an object's is `obj.animation_data.action_slot`). Legacy actions
  still expose `.fcurves`, so `handlers/anim.py::iter_fcurves` handles both.
* **Sculpt-session operators SEGFAULT under `--background`.** Not "fail" — they
  take the whole process down, losing unsaved work. Confirmed for
  `paint.mask_flood_fill`, `sculpt.mask_filter`, `sculpt.mask_from_cavity`,
  `sculpt.face_sets_init`, `sculpt.face_sets_create`,
  `sculpt.face_set_change_visibility`, `sculpt.mesh_filter`. They are all
  `needs_gui=True` and guarded by `_require_sculpt_session()` **before** the
  operator is invoked. `object.voxel_remesh` also crashes if called from Sculpt
  Mode headless — the handler drops to Object Mode first.
* **Manifest permission reasons are capped at 64 characters.** Blender's own
  builder rejects longer ones: *"a value no longer than 64 characters expected"*.
* **`bpy.data.libraries.load()` refuses the currently-open .blend** with
  "Cannot load from the current blend file".
* **Frame range assignment self-clamps.** Setting `frame_start` above
  `frame_end` silently drags the other value along, so an invalid range must be
  rejected *before* assignment.
* **`Object.bound_box` on an evaluated object still reports the original cage** —
  rebuild bounds from the evaluated mesh's vertices for modifier-aware results.
* **`socket.timeout` IS `TimeoutError`, which subclasses `OSError`** — an
  `except OSError` reconnect handler will swallow command timeouts unless
  `TimeoutError` is re-raised first (bit us in `bridge_client.call`).
* **f-strings cannot be docstrings.** `f"""..."""` as a function's first statement
  leaves `__doc__` as `None`, which would ship MCP tools with no description.

## 9. Sculpt changes read stale until the session flushes

Verified live on GUI Blender 5.2. After `sculpt.brush_stroke` succeeds, **both**
`obj.data.vertices` and `obj.evaluated_get(depsgraph).to_mesh()` keep reporting
pre-stroke positions until the sculpt session flushes. A clay strip that had
actually moved max radius from 1.000 to 1.052 still measured 1.000 immediately
afterwards, and only appeared after an unrelated later call.

This is a trap for exactly the verify-after-mutate loop the tools encourage: an
agent strokes, measures, sees no change and concludes the stroke silently
failed. `handlers/sculpt.py::flush_sculpt` therefore runs `obj.update_tag()` +
`view_layer.update()` before every stroke and mesh-filter result is built, so a
subsequent read is accurate. Leaving Sculpt Mode also flushes.

Screenshots are unaffected — the viewport draws from the sculpt session, so a
`viewport_screenshot` shows the change even while the mesh data still reads
stale. When a stroke's numbers and its screenshot disagree, trust the screenshot.

## 10. Shader, image and Video Sequencer APIs added to the probe

Verified live while adding production authoring tools:

* Shader nodes accept ID custom properties, so `build_node_graph` stores a stable
  caller id on each node instead of relying on Blender's auto-numbered display
  names. `ShaderNodeValToRGB.color_ramp.elements` is mutable and supports adding,
  removing and recoloring stops.
* `bpy.data.images.new(..., is_data=True)` still accepts the constructor flag, but
  the resulting Blender 5.2 `Image` exposes **no `is_data` property**. The durable
  observable state is `image.colorspace_settings.name == "Non-Color"`.
* The old `SequenceEditor.sequences` collection is gone. Blender 5.2 uses
  `SequenceEditor.strips` / `strips_all`; constructors are collection methods
  `new_image`, `new_movie`, `new_sound`, `new_scene`, `new_effect`, etc.
* New sequencer timing fields are `left_handle`, `right_handle`, `duration`,
  `content_start` and `content_end`. `frame_start` / `frame_final_*` still work but
  emit 6.0-removal warnings. Moving source and handles together still requires
  `frame_start` in 5.2, so that one compatibility write is warning-suppressed.
* A timeline camera cut is a regular `TimelineMarker` whose `.camera` points at a
  camera object. Blender switches cameras automatically as the playhead crosses
  those markers.
* Generated images, text/color/image/sound strips, two-camera markers, bulk
  slotted-action keys and a final headless two-frame sequencer PNG render are all
  covered by `tests/headless_smoke.py` rather than only by introspection.

## 11. Agent activity overlay APIs

Verified live in GUI Blender 5.2 while adding the Blend for me presence UI:

* `SpaceView3D`, `SpaceNodeEditor`, `SpaceSequenceEditor`,
  `SpaceDopeSheetEditor`, `SpaceGraphEditor`, `SpaceImageEditor`, and
  `SpaceTextEditor` all expose `draw_handler_add/remove` and accept a
  `WINDOW` / `POST_PIXEL` callback using `gpu`, `gpu_extras.batch`, and `blf`.
* `bpy.data.images.load()` does **not** decode SVG; it returns a zero-sized image
  and logs `unknown file-format`. The extension therefore packages the original
  cursor SVG, samples its cubic Bézier path, and draws the vector geometry.
* `mathutils.geometry.tessellate_polygon` returns no faces for this clockwise
  contour as 2D vectors in 5.2. Supplying 3D vectors works, but returns triangle
  **indices** rather than vectors; the loader handles that form explicitly.
* Editor sidebars are overlay regions. Their open `UI` region width must be
  deducted when placing a right-anchored terminal, or the sidebar covers it.
* `bpy.ops.wm.redraw_timer(type="DRAW_WIN_SWAP", iterations=1)` can expose
  synchronous main-thread Python output as it arrives. Calls are rate-limited and
  capped during node batches; ordinary animation uses `bpy.app.timers` redraws.
  The operator emits `Draw window and swap` timing diagnostics through redirected
  stdout, so `LiveTextStream` filters those internal lines from both the overlay
  and the `execute_python` result.
* The terminal choreography is time-based rather than event-loop-step-based, so
  it remains smooth when redraw cadence varies: leave right, spring-drag to
  center, type, wait at lower right, travel to red, click, close. Cursor geometry
  rotates around its SVG tip and eases back to an 18-degree left idle tilt.

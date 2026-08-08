---
name: blender-agent-mcp
description: >-
  Drive Blender directly to model, sculpt, retopologise, rig, weight-paint,
  UV-unwrap, shade, texture, animate, render and export 3D assets through the
  blender-agent-mcp bridge. Use this skill whenever the user asks to make,
  build, model, sculpt, carve, rig, skin, weight-paint, pose, animate, texture,
  shade, light, render, bake, unwrap, retopologise, remesh, import or export
  anything in Blender or any 3D scene — and whenever they mention meshes,
  vertices, edges, faces, normals, modifiers, subsurf, booleans, armatures,
  bones, IK, vertex groups, weights, shape keys, brushes, sculpt strokes,
  dyntopo, multires, voxel remesh, UVs, seams, materials, shaders, nodes,
  geometry nodes, keyframes, playblasts, GLB/FBX/OBJ/USD/STL/Alembic, or .blend
  files. Applies even when the user never says "MCP", "bridge" or "extension" —
  "make me a low-poly tree in Blender", "why does my knee deform badly", "rig
  this character", "export this for Unity" all mean use this skill. It supplies
  the session-start protocol, the observe-act-verify loop, tool-selection
  decision tables, and deep references for sculpting, weight painting, rigging
  and troubleshooting.
---

# Driving Blender through blender-agent-mcp

## What this is

An MCP server relays your tool calls over a loopback TCP bridge into a running
Blender, where they execute on Blender's main thread. 231 tools across 16
modules: core, objects, mesh, modifiers, sculpt, weights, rig, shading, uv,
anim, cinematics, geonodes, settings, properties, io, docs.

Most tools work in headless Blender. These need **GUI Blender** with a real 3D
viewport and fail with a clear message otherwise: all sculpt brush strokes,
sculpt masks/face-sets/mesh-filters, `weight_gradient`, `weight_heatmap`,
`viewport_screenshot`, `playblast`, `set_viewport_shading`.

## Session start protocol

Run this every session, before any other tool. Never act on an assumed scene.

0. **Confirm you have the right server.** These tools come from
   **blender-agent-mcp**. If `health` is not among your available tools, or the
   only Blender server present exposes a different surface (`execute_blender_code`,
   `get_objects_summary`, `get_screenshot_of_window_as_image`), this skill does
   not apply to it. Say so and ask the user to enable blender-agent-mcp rather
   than improvising against the other server's tools.
1. `health` — is the bridge up, and is a viewport available?
2. `get_blender_version` — confirms version and `has_view3d`.
3. `get_scene_info` — real object names, active object, mode, frame.
4. `viewport_screenshot` — see the actual state before changing it.

If `health` reports not connected, tell the user exactly this:

> Blender isn't reachable. Please: (1) open the Blender desktop app, (2) Edit ▸
> Preferences ▸ Add-ons and enable **Agent MCP Bridge**, (3) in the 3D Viewport
> press **N**, open the **Agent MCP** tab and press **Start Server**, (4) check
> the port shown there is 9876 (or tell me which port it uses).

Then **stop and wait for the user to confirm** they have done it. Do not call
`reconnect` speculatively — they have not acted yet. Once they confirm, call
`reconnect`, then re-run this protocol from step 1: reconnecting tells you the
socket is up, not what state the scene is in.

If `has_view3d` is false you are on headless Blender: say so up front and steer
to data-API tools. Do not plan a sculpting session you cannot execute.

## The core loop: Observe → Act → Verify

Every mutating call gets verified before you build on it.

| After | Verify with |
| --- | --- |
| Any sculpt stroke | `viewport_screenshot` — stroke tools return one automatically (`return_screenshot` defaults true) |
| `sculpt_mesh_filter` | It does **not** screenshot by default. Pass `return_screenshot=True` or take one yourself. |
| Remesh, subdivide, decimate, boolean | `mesh_stats` — check the vertex/face delta is what you expected |
| Transform, parent, modifier change | `get_object_info` |
| Weight edits | `weight_heatmap`, plus `report_unweighted_verts` / `report_over_influenced` |
| Material or lighting change | `viewport_screenshot(shading_mode="MATERIAL")` |
| Any pose change | `viewport_screenshot` |

Never chain more than 2–3 mutations without verifying. A wrong assumption
compounds silently: three "successful" calls on the wrong object still return
`ok`.

### A worked loop

Growing a limb on a sculpt. Note the verification between every step:

```
undo_checkpoint(label="before left foreleg")
get_object_info(name="Puppy")                  # confirm origin + world matrix
mesh_select_geometry(name="Puppy", domain="VERT",
                     box_min=[-0.6,-1.0,-0.9], box_max=[-0.2,-0.4,-0.3], space="OBJECT")
mask_from_selection(object="Puppy", invert=True)   # protect everything else
mask_filter(filter_type="SMOOTH", iterations=2)    # soften the mask border
sculpt_mesh_filter(type="INFLATE", strength=0.35, return_screenshot=True)
viewport_screenshot(max_size=900)              # did it grow where intended?
mesh_stats(name="Puppy")                       # did the topology survive?
```

If the screenshot shows the wrong region, `undo` and re-cut the bounding box —
do not stack a second filter on top to compensate.

## Units and conventions

Getting these wrong is the single most common source of silently wrong results.

| Quantity | Unit | Notes |
| --- | --- | --- |
| Brush `size_px` | **screen pixels** | Its world footprint depends on zoom. 40–80 is normal. |
| `radius` in `radial_strokes` | **world units** | Unlike brush size. Easy to confuse. |
| `voxel_size` | **world units** | ~2–5% of the object's bounding-box height to start. |
| All angles | **radians** | `angle_limit`, `roll`, `rotation_euler`, auto-smooth angle. 66° = 1.152. |
| Strength, weight, factor, ratio | **0–1** | `strength` above 1 on a mesh filter can tear geometry. |
| `resolution_percentage` | **0–100** | The one percentage that is not 0–1. |
| Frames | **integers** | Not seconds. Divide by fps yourself. |
| Quaternions | **[w, x, y, z]** | Blender's order, not [x, y, z, w]. |
| Screen coords (`mask_box`, `weight_gradient`) | **region pixels, origin bottom-left** | Screenshots have a top-left origin — flip y. |
| `view_path_2d` in `stroke_on_surface` | **0–1 fractions** when `normalized=True` | The easier mode; default. |

Say the unit out loud when you report a value to the user. "I inflated by 0.35
(a 0–1 factor)" prevents a whole class of misunderstanding.

## Timeouts for slow work

**Only 18 of the 203 tools accept a `timeout` argument.** Passing it to one that
does not is an argument error — on exactly the call you were trying to rescue.

Tools that take `timeout` (seconds), with sensible values for heavy meshes:

| Tool | Start with |
| --- | --- |
| `auto_weights` | 180 |
| `smooth_weights`, `mirror_weights`, `transfer_weights` | 120 |
| `render_frame` | 300+, depending on samples |
| `playblast` | 600 |
| `bake` | 300 |
| `import_model`, `export_model` | 180 |
| `apply_modifier`, `mesh_decimate`, `multires_subdivide`, `multires_unsubdivide`, `multires_apply_base` | 120–300 |
| `report_unweighted_verts`, `report_over_influenced`, `per_bone_weight_summary` | 60–120 |
| `execute_python` | as needed |

**`voxel_remesh`, `quadriflow_remesh`, `smart_uv_project` and `pack_islands`
take no `timeout`.** They are slow — quadriflow runs for minutes — but their
budget is set server-side. If one times out, do **not** re-issue it with a
`timeout` argument. Re-check with `get_scene_info` and `mesh_stats` first: the
operation has usually completed, and re-running it would remesh twice.

A timeout is not a crash. Verify state before retrying anything.

## Golden rules

- **Checkpoint before risk.** `undo_checkpoint(label="before limb blockout")`
  ahead of any multi-step or experimental sequence. Name it for what it precedes
  so you can tell the user precisely what a rollback undoes.
- **Never guess an operator's parameters.** Call `describe_api("bpy.ops.mesh.bevel")`.
  It reads live RNA from the running Blender, so it is never version-wrong.
- **Prefer the dedicated tool over `execute_python`.** Dedicated tools push undo,
  validate arguments and return structured results. When you do fall back to
  `execute_python`, say so and explain why no tool covered it.
- **Data API over UI simulation for weights.** Write vertex groups with
  `set_weights` / `assign_weights`. Reserve brush strokes for geometry that must
  actually be sculpted.
- **Keep payloads small.** `get_weights` is chunked — page it. Cap screenshots
  (default 1024px is plenty; raise only to inspect fine detail). Never request
  full vertex lists on a dense mesh.
- **Destructive file ops need `confirm: true` AND the user's explicit OK.**
  `open_blend` discards unsaved work; `save_blend` over a different existing file
  overwrites it. Ask first, every time.
- **State your units.** Say which each value is: world units, screen pixels,
  radians, or a 0–1 factor. Brush `size_px` is pixels; `radius` in
  `radial_strokes` is world units; every angle is radians; strengths are 0–1.
- **Address objects by their real names** from `get_scene_info`, never by a name
  you assumed or invented.

## Tool selection guide

### Changing shape

| Goal | Tool |
| --- | --- |
| Whole-mesh adjustment (inflate, smooth, sharpen) | `sculpt_mesh_filter` |
| Localised organic change | brush strokes: `stroke_line`, `stroke_curve`, `stroke_on_surface` |
| Reset topology to uniform density | `voxel_remesh` |
| Production-clean quad topology | `quadriflow_remesh` (slow — allow minutes) |
| Detail only where you brush | `dyntopo_enable` |
| Non-destructive / parametric | modifiers: `add_subsurf`, `add_mirror`, `add_solidify`, `add_boolean` |
| Precise, measurable edits | mesh tools: `mesh_extrude`, `mesh_inset`, `mesh_bevel`, `mesh_subdivide` |

### Weights

| Situation | Approach |
| --- | --- |
| Fresh rig | `auto_weights` → the cleanup chain. `references/weight-painting.md` §1 is the **single authority** for that chain's order and arguments; where a recipe abbreviates it, follow weight-painting.md. |
| Bad deformation at one joint | `select_verts_by_weight` → `smooth_weights` / `assign_weights` → `weight_heatmap` |
| Copying to clothing or a new mesh | `transfer_weights` |
| Preparing for a game engine | `limit_total(4)` → `normalize_all` → `report_over_influenced` |

### Finding things out

| Question | Tool |
| --- | --- |
| Exact parameters of an operator | `describe_api` — offline, live, exact |
| How does this feature work conceptually | `search_blender_manual` |
| Which Python symbol do I want | `search_python_api` |
| How do people do this technique | `find_tutorials` |

Use `describe_api` for signatures and the docs tools for understanding. They
answer different questions; reaching for the wrong one wastes a round trip.

## First response to an error

Read the error text before reacting — these tools return actionable messages,
including the full Blender traceback. Then:

| Symptom | First move |
| --- | --- |
| "Could not reach Blender on 127.0.0.1:9876" | Give the user the four setup steps above, then `reconnect`. |
| "needs a real 3D Viewport" / would crash headless | You are headless. Switch to the data-API equivalent or tell the user to use GUI Blender. |
| "poll() failed, context is incorrect" | Wrong mode. `set_mode` explicitly, then retry. |
| "no sculpt brush matching X" | `sculpt_list_brushes` — the real 5.x names are compound. |
| Timed out | Check `get_scene_info` / `mesh_stats` first — it usually finished. Only retry with a bigger `timeout` if that tool actually accepts one (see above). |
| Enum rejected, valid values listed | Use one of the listed values verbatim. They come from live RNA. |
| Tool succeeded but nothing visibly changed | Wrong object, wrong mode, or masked geometry. Check `get_scene_info` and `get_sculpt_state`. |

Open `references/troubleshooting.md` for anything not on this list, before
improvising or retrying blind.

## Planning multi-step jobs

For anything beyond a couple of calls:

1. State the plan to the user in 3–6 steps before starting.
2. `undo_checkpoint` at each boundary, labelled for what follows.
3. Execute one step, verify, report briefly, then continue.
4. On a bad result, `undo` back to the last checkpoint rather than patching
   forward. Compensating for a mistake with a second operation usually
   compounds it.
5. Do irreversible things last: applying modifiers, `voxel_remesh` (destroys UVs
   and vertex groups), saving over a file.
6. `apply_transforms` is the exception — it must happen **early**, before
   remeshing, rigging or export, because `voxel_size` and bone coordinates are
   world units and a scaled object throws them off. It permanently bakes the
   user's object scale, so tell them you are doing it and why.

Order that matters, and burns people who get it wrong:

- Remesh **before** unwrapping and weighting — remeshing discards both.
- Bevel **before** subsurf in a modifier stack, or edges melt.
- UV unwrap **before** baking.
- Weight cleanup **before** posing for review, or you debug the wrong thing.

## Sharp edges worth knowing before you hit them

**Parameter names are not uniform across modules.** `mesh.*` tools take `name`;
most others take `object`; batch tools take `names`. An unrecognised key is now a
hard error that lists the accepted ones — it used to fall back to the active
object and report on the wrong thing. If you get that error, read the list it
gives you rather than guessing again.

**`_timeout` is reserved.** Every request carries it so the bridge can bound its
own wait; you never pass it yourself. When a call times out, the message names
the budget it used — raise the tool's `timeout` argument if it has one (see
above), never add `_timeout`.

**To look at something, use `frame_object`.** There is no orbit/pan/zoom tool, so
a camera is your only way to choose a viewpoint. `frame_object(target=...)`
positions and aims in one call and accounts for aspect ratio; `aim_at` rotates
an existing object at a target. Do not derive the euler yourself — the sign
conventions are easy to invert and the mistake is invisible.

**Emissive geometry inside a light blocks that light.** A bulb mesh around a
point lamp casts shadows over the whole scene and the render comes back dark
with no error. Clear it with
`set_object_visibility(object="Bulb", shadow=False)`.

**Sockets can share a name.** `ShaderNodeMix` has four inputs called `A` (one per
data type). Passing the name writes the first and returns an `ambiguous` warning
naming the indices; pass the integer index to target a specific one.

**Check `blank` on a render.** `render_frame` returns a `content` record; when
`blank` is true the frame is empty or a flat colour — usually nothing in frame,
or no light. A transparent render is otherwise reported as a success.

## Reference router

Open these — do not work from memory.

| File | Open it when |
| --- | --- |
| `references/tool-reference.md` | You need a tool's exact params, units, returns or gotchas. All 203 tools. **Do not read it whole — it is 4,800 lines. Grep for `### <tool_name>` and read the ~15 lines after it.** |
| `references/sculpting.md` | **Before any sculpting session.** Pass structure, brush table with starting values, detail-management decision tree, stroke planning with worked coordinates. |
| `references/weight-painting.md` | **Before any weighting or skinning work.** The post-auto-weights cleanup chain, how to read a heatmap, diagnostics interpretation, game-export checklist. |
| `references/rigging-animation.md` | Building armatures, IK, constraints, posing, keyframes, playblasts. |
| `references/recipes.md` | The task resembles a known end-to-end workflow. Eight literal tool-call sequences, checkpoints included. |
| `references/troubleshooting.md` | **First stop on any error**, before you retry or improvise. |
| `references/recipes.md` §4 then `references/weight-painting.md` §2 | The user reports a bad or odd **result with no error** — a collapsing joint, a limb that tears, a wrong-looking deform. |
| `references/recipes.md` §6 then `references/weight-painting.md` §5 | Exporting to a game engine, or any "will this work in Unity/Unreal" question. |

## What this toolset does not do well

Say so rather than flailing:

- **Artistic judgement.** Nothing evaluates whether proportions read correctly.
  Screenshot and assess, or ask the user — do not assume a stroke looked good.
- **Physics, cloth, fluids, particles.** No tools. Reachable only via
  `execute_python`, and awkward there.
- **Precise hand-painted texturing.** Materials and image textures are covered;
  painting onto a texture is not.
- **Sculpting without a GUI.** Strokes, masks, face sets and mesh filters all
  need a real viewport. Headless gets you objects, mesh edits, modifiers,
  weights via the data API, rigging, UVs, animation and import/export.
- **Retopology by hand.** `quadriflow_remesh` is automatic; there is no manual
  retopo tooling.
- **Moving the viewport.** There is no orbit/pan/zoom tool. The view is wherever
  the user left it. To see a specific angle, either `add_camera` at the position
  you need and `viewport_screenshot(camera_view=True)`, or ask the user to orbit
  and tell you when they are done. This matters when stroke points come back in
  `dropped_points` (they projected off-screen) or a heatmap region faces away.

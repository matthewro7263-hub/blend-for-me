# Troubleshooting

First stop on any error or unexpected result. Read the symptom, apply the fix,
then re-verify with `get_scene_info` or `viewport_screenshot` before continuing.
Do not retry the same call hoping for a different answer — nothing here is flaky.

## Read the error before you react

Errors arrive in three flavours. Tell them apart, because the fix differs.

| Flavour | Looks like | Meaning |
| --- | --- | --- |
| `NotConnected` | "Could not reach Blender on 127.0.0.1:9876." + numbered steps | Never left the MCP server. Blender or the bridge is down. |
| `TimeoutError` | "Blender did not respond within 10s." | The command is probably still running in Blender. Raise `timeout`, do not resend blind. |
| `BridgeError` | A Blender-side message, followed by `--- Blender traceback ---` | The command ran and refused or failed. The message names the fix. |

**Error text names the *bridge command*, not the MCP tool.** The handler that
raises says `mesh.select_geometry`; the tool you call is `mesh_select_geometry`.
Translate before you act:

| Error text says | Call this tool |
| --- | --- |
| `mesh.select_geometry` | `mesh_select_geometry` |
| `mesh.extrude_selection` | `mesh_extrude` |
| `mesh.stats` | `mesh_stats` |
| `modifiers.add_multires` | `add_multires` |
| `modifiers.multires_subdivide` | `multires_subdivide` |
| `sculpt.dyntopo_enable` | `dyntopo_enable` |
| `sculpt.list_brushes` | `sculpt_list_brushes` |
| `sculpt.get_state` | `get_sculpt_state` |
| `weights.auto_weights` | `auto_weights` |
| `weights.vgroup_create` | `vgroup_create` |
| `io.save_blend` | `save_blend` |

## Symptom → cause → fix

| Symptom (what you actually see) | Root cause | Fix |
| --- | --- | --- |
| Stroke reports `points_applied` > 0 but a follow-up `mesh_stats` / `execute_python` measurement shows no change | Sculpt Mode holds displacement in its session; mesh data reads stale until it flushes | Trust the **screenshot**, not the numbers — the viewport draws from the session. The stroke tools now flush before returning; if you measure by another route, `set_mode(mode="OBJECT")` first. |
| `Could not reach Blender on 127.0.0.1:9876.` | Bridge not started, or Blender closed | Give the user the four-step start instructions (below), then `reconnect()`. |
| `Port 9876 is already in use.` reported in Blender's UI | A stale Blender or another process holds the port | User runs `lsof -nP -iTCP:9876 -sTCP:LISTEN`, kills it — or changes the port in the N-panel and exports `BLENDER_AGENT_PORT=9877` for the MCP server. |
| `bridge already running on port 9876` | Start pressed twice | Nothing to fix. Call `reconnect()`. |
| `This operation needs a real 3D Viewport.` | Headless Blender (`--background`) or no VIEW_3D area | Confirm with `health()` → `has_view3d`. Switch to the headless-safe equivalent (table below) or ask the user for GUI Blender. |
| `This sculpt operation needs a live sculpt session and would crash Blender in background mode.` | Sculpt-session op attempted headless | The refusal is protecting unsaved work — these segfault, not fail. Use `mask_from_selection`, `voxel_remesh`, `quadriflow_remesh`, `get_sculpt_state`. |
| `Operator ... poll() failed, context is incorrect` | Wrong mode, or an operator run without the VIEW_3D override | `set_mode(mode="OBJECT", object="Body")` first. See the mode contract below. |
| `Blender did not respond within 10s.` | Default 10 s budget too small | Pass a bigger `timeout` (per-tool table below). Never resend at the same budget. |
| `need at least 2 projectable points, got 1 (11 dropped). Is the viewport looking at the object?` | Stroke points project behind the view camera | There is **no orbit tool**. `add_camera(location=...)` aimed at the work + `viewport_screenshot(camera_view=True)` to confirm, then re-issue the stroke; or ask the user to orbit Blender's viewport and say when done. |
| `point [0.0, 0.4, 1.9] projects behind the viewport camera` | `weight_gradient` endpoint off-screen | Same fix — both endpoints must be visible in the viewport. |
| `no sculpt brush matching 'Inflate'. Available brushes: [...]` | 5.x brushes are assets with compound names | Use the alias table below, or `sculpt_list_brushes()` and copy an exact name. |
| `no object named 'Cube.001'; call get_scene_info for real names` | Invented or stale object name | `get_scene_info()` and use the literal name it returns. |
| `'Sphere' is a CURVE, sculpting needs a MESH` | Wrong object type | Convert, or address the mesh object. |
| `'Body' is not in the active view layer, so no operator can touch it.` | Object exists in `bpy.data` but is unlinked or in an excluded collection | `collection_link(names=["Body"], collection="Scene Collection")`, or re-enable the collection. |
| `'Rig' has no pose data. It is probably not linked into the current scene` | Armature created but never linked; depsgraph never evaluated it | Link it into a scene collection, then retry. |
| `cannot put 'Body' into POSE mode (...). Usually the object is hidden, unselectable, or in a collection excluded from the view layer` | Visibility/selectability flags | Unhide and make selectable, then retry the same call. |
| `extrude_selection has nothing to extrude. Call mesh.select_geometry(domain='FACE', ...) first.` | Empty selection | `mesh_select_geometry(name="Body", domain="FACE", indices=[12, 13])` then `mesh_extrude`. |
| `bridge_edge_loops produced no faces.` | Fewer than two distinct open boundary loops selected | Inspect with `mesh_stats(name="Body")`; select real boundary edges. |
| `cannot apply 'Subdivision': the Mesh 'Cube' is shared by 3 objects` | Multi-user mesh data | `apply_modifier(object="Cube", modifier="Subdivision", single_user=True)` — or unlink the other users. |
| `cannot apply 'Mirror': Blender refuses to apply modifiers to a mesh with shape keys` | Shape keys present | Keep the modifier live, or delete the shape keys first (`shapekey_list` to see them). |
| `'Body' has no Multires modifier — add one with modifiers.add_multires first` | Multires call before the modifier exists | `add_multires(object="Body")` then `multires_subdivide(object="Body", levels=2)`. |
| `sculpt_levels=4 exceeds total_levels=2; subdivide first` | Level above what has been generated | `multires_subdivide(object="Body", levels=2)` first. |
| `dynamic topology is off — call sculpt.dyntopo_enable first` | Flood-fill without dyntopo | `dyntopo_enable(detail=12.0, mode="RELATIVE")` then `dyntopo_flood_fill()`. |
| `'Head' has no UV layer to pack — unwrap first` | Pack before unwrap | `smart_uv_project(object="Head", island_margin=0.02)` or `unwrap(object="Head")`, then `pack_islands`. |
| `'edges' is required: a list of edge indices, or 'SHARP'` | `mark_seams` called with no edges | `mark_seams(edges="SHARP", object="Head", angle=0.5236)` (0.5236 rad = 30°). |
| Weights written, mesh does not deform | No armature modifier / parented without deform / bone has `use_deform` off | Full diagnosis below. |
| `'Body' has no armature modifier, so the deform bones are unknown.` | Weight diagnostics on an unbound mesh | Pass `armature="Rig"` explicitly, or bind with `auto_weights(mesh="Body", armature="Rig")`. |
| `'Body' has no vertex group 'DEF-arm.L'; existing groups: [...]` | Group name mismatch with bone name | Copy an exact name from `vgroup_list(mesh="Body")` / `list_bones(armature="Rig")`. |
| `'Body' has no active vertex group. Pass 'group', or create one with weights.vgroup_create.` | Group-scoped op with nothing active | Pass `group="DEF-spine"` explicitly on every weights call. |
| Keyframes on `rotation_euler` do nothing to a bone | Bone is in `QUATERNION` rotation mode; euler channels are ignored | Use `pose_bone(..., rotation_quaternion=[...])`, or let `pose_bone` flip the mode (see below). |
| `could not key 'rotaion_euler' on 'Cube'` | Typo'd or non-animatable data path | `describe_api("bpy.types.Object")` and use the exact property name. |
| `no keyframe on 'location' at frame 24` | Removing a key that isn't there | `list_keyframes(object="Cube", data_path="location")` first. |
| `end (10) is before start (24)` | Inverted frame range | Blender self-clamps silently, so this is rejected up front. Pass `set_frame_range(start=1, end=120)`. |
| `Cycles is unavailable in this Blender, and baking requires it — EEVEE cannot bake.` | Cycles add-on absent | Ask the user to enable the bundled Cycles add-on. No workaround. |
| `engine 'CYCLES' unavailable; valid: ['BLENDER_EEVEE']` | Same, from `render_frame` | Drop `engine` to use EEVEE, or have Cycles enabled. EEVEE's id is `BLENDER_EEVEE`, never `BLENDER_EEVEE_NEXT`. |
| `material 'Skin' has no Principled BSDF to hook into.` | `load_image_texture(hook_to=...)` on a bare material | `add_node(material="Skin", type="ShaderNodeBsdfPrincipled")`, or drop `hook_to` and wire with `link_nodes`. |
| `no faces are selected, so nothing was assigned.` | `assign_material(to_selected_faces=True)` with empty selection | Select faces, or call with `to_selected_faces=False`. |
| `selected_only=True but nothing is selected.` | Export with empty selection | `select_objects(names=["Body", "Rig"])` first, or `selected_only=False`. |
| `cannot infer a format from '.blend1'. Pass format= explicitly` | Unknown extension | `export_model(path="/tmp/out.glb", format="GLTF")`. |
| `cannot list contents from /x.blend because it is the file currently open.` | `list_blend_contents` / `append_from_blend` aimed at the open file | Its datablocks are already in session — address them by name via `get_scene_info()`. |
| `opening /x.blend discards everything not saved ... Pass confirm=true` | `open_blend` without confirm | Ask the user, then `open_blend(path="/x.blend", confirm=True)`. |
| `/x.blend already exists and is not the file currently open. Pass confirm=true to overwrite it.` | `save_blend` onto a different existing file | Ask the user, then `save_blend(path="/x.blend", confirm=True)`. |
| `this scene has never been saved, so there is no path to save to.` | Bare `save_blend()` on an unsaved scene | `save_blend(path="/Users/you/work/scene.blend")`. |
| Response is enormous / truncated | Unpaged `get_weights`, oversized screenshot | Page with `offset`/`limit`; cap `max_size`. See below. |
| UVs and vertex groups vanished after a remesh | `voxel_remesh` rebuilds topology from scratch | Expected. Remesh *before* unwrapping and weighting — order of operations below. |
| Every call fails after Blender restarts or updates | Extension disabled / bridge not auto-started | Re-enable **Agent MCP Bridge**, Start Server, then `reconnect()`. |
| `health()` returns `connected: true` but the scene looks wrong | You are talking to a *different* Blender instance | `get_scene_info()` and `get_blender_version()`; confirm the filepath with the user. |

---

## Bridge down

Give the user this, verbatim, and do not improvise workarounds — nothing works
without the bridge:

1. Open the Blender **desktop app** (not `blender --background`).
2. Edit ▸ Preferences ▸ Add-ons — enable **Agent MCP Bridge**.
3. In the 3D Viewport press **N** ▸ **Agent MCP** tab ▸ **Start Server**.
4. Confirm the port reads **9876**.

Then `reconnect()` and re-run the session-start protocol from the top —
`health()`, `get_blender_version()`, `get_scene_info()`.

**Port already taken.** The N-panel reports `Port 9876 is already in use.` Ask
the user to run `lsof -nP -iTCP:9876 -sTCP:LISTEN` and kill the stale process
(usually a Blender that quit without releasing the socket). If they would rather
move ports, they change it in the N-panel **and** the MCP server must be started
with `BLENDER_AGENT_PORT=9877` (host override: `BLENDER_AGENT_HOST`). Both sides,
or nothing connects.

**Mid-session death.** Ordinary calls already retry once transparently. Two
consecutive `NotConnected` results means Blender is gone. After `reconnect()`
succeeds, **never assume the scene survived** — re-run `get_scene_info()` and
`viewport_screenshot()`, compare against what you last did, and tell the user
which of your changes are still present.

---

## Headless Blender

`health()` reports `background: true` / `has_view3d: false`. Say so up front and
re-plan; do not queue work you cannot execute.

| Refuses headless | Headless-safe substitute |
| --- | --- |
| `stroke_line`, `stroke_curve`, `stroke_on_surface`, `sculpt_stroke`, `radial_strokes` | None. Shape via `voxel_remesh`, `mesh_proportional_transform`, modifiers. |
| `sculpt_mesh_filter` (SMOOTH, INFLATE, SHARPEN…) | No direct analogue. Approximate SMOOTH with `add_subsurf(object="Body", levels=1)` or `add_remesh(object="Body", mode="VOXEL", voxel_size=0.02)`; INFLATE with `add_solidify` or `add_shrinkwrap(offset=0.01)`. |
| `mask_box`, `mask_filter`, `mask_by_cavity`, `clear_mask`, `invert_mask` | `mask_from_selection(value=1.0)` — works headless, drives off the mesh selection. |
| `face_sets_init`, `face_sets_create`, `face_set_visibility`, `reveal_all` | None. Use `separate(name="Body", by="LOOSE")` or material slots to partition. |
| `weight_gradient`, `brush_stroke` (weights) | `set_weights`, `assign_weights`, `smooth_weights` — the data API, and better anyway. |
| `weight_heatmap` | `report_unweighted_verts`, `report_over_influenced`, `per_bone_weight_summary`. |
| `viewport_screenshot`, `playblast`, `set_viewport_shading` | `render_frame(engine="BLENDER_EEVEE", resolution=[960, 540])`. |
| `dyntopo_flood_fill` | `voxel_remesh(voxel_size=0.02)`. |

**Why sculpt-session tools refuse instead of trying.** Under `--background` the
PBVH-walking operators (`paint.mask_flood_fill`, `sculpt.mask_filter`,
`sculpt.mask_from_cavity`, `sculpt.face_sets_init`, `sculpt.face_sets_create`,
`sculpt.face_set_change_visibility`, `sculpt.mesh_filter`) **segfault** — verified
on 5.2 in isolated processes. That takes the whole Blender process down and loses
every unsaved change. The guard fires before the operator is invoked. Treat the
refusal as a save, not an obstacle, and tell the user why.

`voxel_remesh` is the exception: it works headless because the handler drops to
Object Mode first, then restores Sculpt Mode. Calling `object.voxel_remesh`
from Sculpt Mode headless crashes.

---

## Mode contract: which tool forces what

`poll() failed, context is incorrect` almost always means the object was in the
wrong mode, or another object was stuck in a non-Object mode and blocked the
switch.

| Tool group | Mode it needs / forces |
| --- | --- |
| `mesh_*` (bevel, inset, extrude, subdivide, decimate…) | Object Mode required — refuses with "needs 'Body' to be in Object Mode (it is in SCULPT)". In Edit Mode the live edit-mesh is used instead. |
| `enter_sculpt`, all stroke tools, masks, face sets, filters | Force Sculpt Mode on the target object. |
| `voxel_remesh` | Drops to Object Mode, remeshes, restores the previous mode. |
| `auto_weights` (`reuse_binding=True`) | Forces Weight Paint Mode — `paint.weight_from_bones` polls there, **not** in Pose Mode. |
| `smooth_weights`, `clean_weights`, `normalize`, `levels`, `quantize`, `invert` | Object Mode, or Weight Paint where the poll demands it; `only_selected=True` switches to Edit Mode and restricts to the selection. |
| `pose_bone`, `keyframe_pose`, `reset_pose` | Pose Mode on the armature. |
| `edit_bone`, `add_bones`, `remove_bones`, `symmetrize_bones` | Edit Mode on the armature; restored afterwards. |
| `apply_modifier`, `bake` | Object Mode; both save and restore the previous mode. |

Fix pattern, always the same shape:

```
set_mode(mode="OBJECT", object="Body")
mesh_bevel(name="Body", width=0.02, segments=2)
```

If `set_mode` itself fails with "is not in scene 'Scene'" or "is in the scene but
not in view layer", the object is unlinked or its collection is excluded — link
it or re-enable the collection, do not retry.

---

## Timeouts

Default budget for every bridge call is **10 s**. Long tools ship larger
built-in budgets; several also expose a `timeout` parameter you can raise.

| Tool | Built-in budget | Raise via `timeout`? | Recommended when it times out |
| --- | --- | --- | --- |
| `quadriflow_remesh` | 600 s | **No parameter** | Lower `target_faces` (try 5000 → 2000) and retry. Minutes on dense meshes is normal. |
| `voxel_remesh` | 300 s | No parameter | Raise `voxel_size` (0.01 → 0.03). Halving voxel size roughly octuples cost. |
| `playblast` | 600 s | Yes | `playblast(out_path="/tmp/a.mp4", frame_start=1, frame_end=120, timeout=1200)` |
| `render_frame` | 300 s | Yes | `render_frame(engine="CYCLES", samples=64, timeout=900)` |
| `bake` | 300 s | Yes | `bake(object="Body", type="AO", size=1024, samples=32, timeout=900)` |
| `apply_modifier` | 300 s | Yes | `apply_modifier(object="Body", modifier="Subdivision", timeout=600)` |
| `mesh_decimate` | 300 s | Yes | `mesh_decimate(name="Body", ratio=0.25, timeout=600)` |
| `multires_subdivide` | 300 s | Yes | `multires_subdivide(object="Body", levels=1, timeout=900)` — subdivide one level at a time. |
| `import_model` / `export_model` | 180 s | Yes | `export_model(path="/tmp/a.glb", timeout=600)` |
| `auto_weights` / `transfer_weights` | 180 s | Yes | `auto_weights(mesh="Body", armature="Rig", timeout=600)` |
| `smooth_weights` / `mirror_weights` | 120 s | Yes | `smooth_weights(mesh="Body", factor=0.5, iterations=2, timeout=300)` |
| `dyntopo_flood_fill` | 300 s | No parameter | Coarsen with `dyntopo_enable(detail=20.0)` before flooding. |
| `execute_python` | 30 s | Yes | `execute_python(code="...", timeout=120)` |
| stroke tools | 90 s (180 s for `radial_strokes`) | No parameter | Reduce `steps` (24 → 12) or `count`. |

A timeout does **not** cancel the operation — Blender keeps working on it.
Wait, then call `mesh_stats(name="Body")` or `get_scene_info()` to see whether it
finished, before deciding to retry.

---

## Brush names

Blender 5.x brushes are **assets**; `Brush.sculpt_tool` no longer exists, so no
enum lookup is possible. Friendly tutorial names mostly do not match the asset
names. `sculpt_set_brush(name=...)` resolves aliases, case, and punctuation-free
spellings; anything else raises with the whole list attached.

| You want | Real 5.2 asset name |
| --- | --- |
| Inflate | `Inflate/Deflate` |
| Crease | `Crease Sharp` (also `Crease Polish`) |
| Flatten | `Flatten/Contrast` |
| Scrape | `Scrape/Fill` |
| Fill | `Fill/Deepen` |
| Pinch | `Pinch/Magnify` |
| Elastic Deform | `Elastic Grab` |

```
sculpt_list_brushes()
sculpt_set_brush(name="Clay Strips", size_px=60, strength=0.5)
```

**Size does not change?** `size_px` is **screen pixels**, and writing
`brush.size` is silently ignored while unified size is on. The handler writes
whichever field the UI reads and reports which, so check `size_written_to` in the
result: it is either `"brush.size"` or `"unified_paint_settings.size"`. Same for
`strength_written_to`. If neither appears, you did not pass the parameter.

**Radial symmetry is gone.** `sculpt_symmetry(radial_counts=8)` returns a `note`
saying it was ignored — it was removed in 5.x. Use
`radial_strokes(center=[0, 0, 1.2], radius=0.35, count=8, steps=10, axis="Z")`
instead; `radius` there is **world units**, unlike `size_px`.

---

## Weights look right but the mesh does not deform

Work down this list in order; stop at the first thing that is wrong.

1. **Is there an armature modifier at all?**
   `list_modifiers(object="Body")` — look for `type: "ARMATURE"` with
   `object: "Rig"`. Weight diagnostics raise `"'Body' has no armature modifier,
   so the deform bones are unknown"` when it is missing.
   Fix: `add_armature(object="Body", armature="Rig")`.

2. **Was the mesh parented without deform?** `parent_objects(child="Body",
   parent="Rig", type="OBJECT")` parents but adds no modifier — the mesh rides
   the rig rigidly and never skins. Use
   `parent_mesh_to_armature(mesh="Body", armature="Rig", mode="AUTOMATIC")`
   or `auto_weights(mesh="Body", armature="Rig")`.

3. **Are the bones marked deform?** `list_bones(armature="Rig")` and check
   `use_deform`. A bone with `use_deform=False` is skipped by the modifier even
   with perfect weights. Fix: `edit_bone(armature="Rig", bone="arm.L",
   use_deform=True)`.

4. **Do group names match bone names exactly?** The modifier binds by name.
   `vgroup_list(mesh="Body")` against `list_bones(armature="Rig")`. A group
   called `Arm.L` does nothing for a bone called `arm.L`. Fix:
   `vgroup_rename(mesh="Body", name="Arm.L", new_name="arm.L")`.

5. **Is `use_vertex_groups` on?** `set_modifier_prop(object="Body",
   modifier="Armature", prop="use_vertex_groups", value=True)`.

6. **Are the weights actually non-zero?**
   `report_unweighted_verts(mesh="Body", armature="Rig")` — anything listed gets
   no deformation at all. Then `per_bone_weight_summary(mesh="Body")` to see
   which bones own nothing.

7. **Verify in the viewport, not in the numbers.**
   `pose_bone(armature="Rig", bone="arm.L", rotation_euler=[0, 0, 0.7854])`
   (0.7854 rad = 45°) then `viewport_screenshot()`.

`transfer_weights` uses `object.data_transfer` with
`method="POLYINTERP_NEAREST"` by default — `object.vertex_group_transfer_weight`
was removed in 5.x. If a transfer lands wrong, try `method="NEAREST"` for
matching topology, or raise `max_distance`.

---

## Rotation channels

A pose bone in `QUATERNION` mode ignores `rotation_euler` entirely — writes and
keyframes both land in dead channels and nothing moves.

- `pose_bone(armature="Rig", bone="arm.L", rotation_euler=[0, 0, 0.5])` on a
  quaternion bone **switches the bone to `XYZ`** so the value takes effect. That
  is a real change to the rig; mention it, and expect any existing quaternion
  F-curves on that bone to stop driving it.
- To stay in quaternion:
  `pose_bone(armature="Rig", bone="arm.L", rotation_quaternion=[0.966, 0, 0, 0.259])`.
- `keyframe_pose` keys the channel matching the bone's current
  `rotation_mode` — check it in `list_bones(armature="Rig")` output before
  keying, not after.
- Keying manually with
  `insert_keyframe(data_path="pose.bones[\"arm.L\"].rotation_euler", ...)` on a
  quaternion bone succeeds and does nothing visible. That is the silent failure.

Every angle in this toolset is **radians**. 90° = 1.5708, 45° = 0.7854,
30° = 0.5236.

---

## Oversized payloads

| Tool | Cap it with | Sane value |
| --- | --- | --- |
| `get_weights` | `offset` + `limit` | `get_weights(mesh="Body", group="arm.L", offset=0, limit=500)`; follow `next_offset` until it comes back `null`. |
| `viewport_screenshot` | `max_size` | 1024 px default is enough; 1600 only to inspect fine detail. |
| `weight_heatmap` | `max_size` | `weight_heatmap(mesh="Body", group="arm.L", max_size=800)` |
| `mesh_stats` | `limit` | Default 1000. Never raise it to dump a dense mesh. |
| `get_scene_info` / `list_objects` | `limit` | 200 / 500 defaults are fine; big scenes truncate, which is correct. |
| `report_over_influenced` | `limit` | 1000; if it truncates, fix what you got and re-run. |
| `get_node_graph`, `geonodes_get_graph` | `limit` | 200–1000. |

Never request a full vertex list on a dense mesh. Sample, fix, re-measure.

---

## voxel_remesh wiped my UVs and vertex groups

Expected, not a bug. `voxel_remesh` and `quadriflow_remesh` build brand-new
topology; anything bound to the old vertex indices — UV layers, vertex groups,
shape keys, sculpt masks, face sets — does not survive. `preserve_attributes` on
`voxel_remesh` helps for generic attributes but is not a guarantee for UVs.

Correct order, once and only once:

```
undo_checkpoint(label="before remesh")
voxel_remesh(voxel_size=0.02, preserve_volume=True)
mesh_stats(name="Body")
smart_uv_project(object="Body", island_margin=0.02)
auto_weights(mesh="Body", armature="Rig")
```

Remesh **before** unwrapping and weighting. If you already unwrapped, either
re-unwrap after the remesh, or skip the remesh entirely and clean topology with
`mesh_merge_by_distance(name="Body", threshold=0.0005)` and
`mesh_decimate(name="Body", ratio=0.5)`.

If weights are already painted and you must remesh, remesh a *duplicate* and
`transfer_weights(source="Body", target="Body.001", method="POLYINTERP_NEAREST")`
back onto it.

---

## Destructive file operations

Both refuse without `confirm=True`, and `confirm=True` alone is not enough —
**ask the user first, every time.**

| Call | Refusal | What it destroys |
| --- | --- | --- |
| `open_blend(path="/x.blend")` | "opening /x.blend discards everything not saved in the current scene (currently MODIFIED — unsaved changes would be lost)" | The entire current session |
| `save_blend(path="/x.blend")` | "/x.blend already exists and is not the file currently open. Pass confirm=true to overwrite it." | Someone else's file |

The refusal message tells you whether the scene is dirty. Quote that to the user
when you ask. Offer `save_blend(path="/tmp/backup.blend")` first.

`list_blend_contents` and `append_from_blend` refuse the currently-open file
outright: *"cannot list contents from /x.blend because it is the file currently
open"* — Blender cannot load a library from itself. Its contents are already in
the session; find them with `get_scene_info()` and `material_list()`.

---

## When nothing above fits

1. `health()` — is it even connected, and does it have a viewport?
2. `get_scene_info()` — are you addressing the object you think you are?
3. `describe_api("bpy.ops.mesh.bevel")` — read the live RNA rather than guessing
   a parameter. It cannot be version-wrong; your memory can.
4. `list_bridge_commands()` — confirm the capability exists at all.
5. `undo()` back to your last `undo_checkpoint`, and say plainly what you rolled
   back.

Only then fall back to `execute_python(code="...", timeout=60)` — and when you
do, tell the user you are bypassing the dedicated tools and why none covered it.
`execute_python` pushes no undo step of its own; take an
`undo_checkpoint(label="before manual python")` first.

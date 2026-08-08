# Tool reference

Every one of the 203 tools the blender-agent-mcp server exposes, grouped by module.
Params, types and defaults are generated from the live server; units, returns,
gotchas and examples are hand-verified against the handler source and
`docs/BLENDER_5X_API_NOTES.md` (Blender 5.2.0 LTS).

## Read this first

**Units.** State which you mean, every time.

| Quantity | Unit | Examples |
| --- | --- | --- |
| Distances, positions, sizes | world units (metres by default) | `location`, `voxel_size`, `thickness`, `radius` |
| Angles | RADIANS, never degrees | `rotation`, `angle`, `roll`, `pole_angle`, `normal_angle` |
| Brush size | SCREEN PIXELS | `size_px`, `radius_px` |
| Strengths, weights, factors | 0-1 | `strength`, `weight`, `factor`, `mix_factor` |
| Colours | 0-1 per channel (NOT 0-255) | `base_color`, `color`, `emission_color` |
| Mask box coordinates | region pixels, origin BOTTOM-LEFT | `mask_box(xmin, xmax, ymin, ymax)` |
| Node positions | node-editor units | `add_node(location=...)` |
| Frames | frame number | `frame`, `frame_start`, `frame_end` |
| `timeout` | seconds | every long-running tool |

90° = `1.5708`. 45° = `0.7854`. 30° = `0.5236`. 66° = `1.152`. 5° = `0.0873`.

**GUI Blender only.** These 22 tools need a real 3D Viewport. Sculpt-session
operators do not merely fail under `blender --background` — they SEGFAULT and take
unsaved work with them, so the handlers refuse before the operator is ever invoked:

`brush_stroke`, `clear_mask`, `dyntopo_flood_fill`, `face_set_visibility`, `face_sets_create`, `face_sets_init`, `invert_mask`, `mask_box`, `mask_by_cavity`, `mask_filter`, `playblast`, `radial_strokes`, `reveal_all`, `sculpt_mesh_filter`, `sculpt_stroke`, `set_viewport_shading`, `stroke_curve`, `stroke_line`, `stroke_on_surface`, `viewport_screenshot`, `weight_gradient`, `weight_heatmap`.

Check `health().gui_tools_available` before planning any of them.

**Brushes are assets in 5.x.** `Brush.sculpt_tool` no longer exists. Friendly names
are aliased for you by `sculpt_set_brush`:

| You say | Real 5.2 asset |
| --- | --- |
| `Inflate` / `Deflate` | `Inflate/Deflate` |
| `Crease` | `Crease Sharp` |
| `Flatten` / `Contrast` | `Flatten/Contrast` |
| `Scrape` | `Scrape/Fill` |
| `Fill` / `Deepen` | `Fill/Deepen` |
| `Pinch` / `Magnify` | `Pinch/Magnify` |
| `Elastic Deform` / `Elastic` | `Elastic Grab` |
| `Relax` | `Relax Slide` |

Weight-paint brushes are only four: `Paint`, `Blur`, `Average`, `Smear`.

**Removed in 5.x — do not reach for these.** Radial symmetry (use `radial_strokes`).
`object.vertex_group_transfer_weight` (use `transfer_weights`, which runs
`data_transfer` with `POLYINTERP_NEAREST`). `paint.brush_select` (brushes are assets).
`Mesh.use_auto_smooth` (use `mesh_shade_auto_smooth`). `Action.fcurves` on slotted
actions (use `list_keyframes`). `ToolSettings.unified_paint_settings` moved to
per-paint-mode blocks, and writing `brush.size` is silently ignored while unified
size is on.

**Order that matters.** `voxel_remesh` and `add_remesh` DESTROY UVs and vertex
groups: remesh first, then unwrap, then weight. Triangulate and decimate last.
`clean_weights` → `limit_total` → `normalize_all`, in that order, before any export.

## Modules

| Module | Tools | Covers |
| --- | --- | --- |
| [core](#core) | 15 | Bridge health, scene state, mode, undo, live RNA lookup, screenshots, `execute_python` |
| [objects](#objects) | 22 | Create, delete, transform, parent, align, collections |
| [mesh](#mesh) | 20 | Selection-driven mesh edits, topology repair, shading, decimate |
| [modifiers](#modifiers) | 18 | Add / inspect / tune / reorder / apply the modifier stack, Multires |
| [sculpt](#sculpt) | 27 | Brushes, strokes, masks, face sets, filters, voxel + quadriflow remesh, dyntopo |
| [weights](#weights) | 27 | Vertex groups, binding, weight maths, diagnostics, heatmap |
| [rig](#rig) | 23 | Armatures, bones, posing, constraints, IK, shape keys, drivers |
| [shading](#shading) | 11 | Materials, shader nodes, viewport shading, Cycles bake |
| [uv](#uv) | 9 | Seams, unwrap, smart project, packing, UV layers, stats |
| [anim](#anim) | 11 | Frames, keyframes, interpolation, actions, NLA, playblast |
| [geonodes](#geonodes) | 9 | Node groups, graph construction, modifier inputs |
| [io](#io) | 6 | Import / export models, .blend save / open / append |
| [docs](#docs) | 5 | Manual and Python-API search, page fetch, tutorials, cache |

---

## core

15 tools. Bridge health, scene introspection, mode, undo, live RNA introspection, imagery, and the `execute_python` escape hatch. Run `health` → `get_blender_version` → `get_scene_info` → `viewport_screenshot` before anything else.

### describe_api

Introspect a live `bpy.ops.*` operator or `bpy.types.*` type via RNA.

**vs:** `search_blender_manual` / `search_python_api` — describe_api answers "what are this operator's parameters"; the docs tools answer "how does this feature work". Reaching for the wrong one wastes a round trip.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `path` | string | e.g. `bpy.ops.sculpt.brush_stroke`, `bpy.ops.object`, `bpy.types.Brush` | — | yes |

Returns: `{kind, path, description, properties, functions}` read from live RNA.

Gotchas:

- Reads the RUNNING Blender's RNA, so it is never version-wrong. Use it instead of guessing an operator's parameter names.
- Signatures only. For "how does this work" use `search_blender_manual`.

```python
describe_api(path="bpy.ops.mesh.bevel")
```

### execute_python

Run arbitrary Python inside Blender. The escape hatch — prefer a real tool.

**vs:** any dedicated tool — dedicated tools push undo, validate arguments and return structured results. Reach for this only for genuinely novel operations, and say why.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `code` | string | Python source, executed with `bpy` in scope | — | yes |
| `timeout` | number | seconds to wait (default 30) | `30.0` | no |

Returns: `{stdout, stderr, result, error, traceback}`. The call never raises — a failure comes back as `error` + `traceback` so you always get the diagnosis.

Gotchas:

- The escape hatch, not a shortcut. Dedicated tools push undo, validate arguments and return structured results; this does none of that.
- Failures come back as `error` + `traceback` rather than raising, so check those fields — a silent `result: null` is not success.
- Say out loud why no tool covered the case when you fall back to this.

```python
execute_python(code="import bpy\nlen(bpy.data.objects)", timeout=15.0)
```

### get_blender_version

Blender / Python versions, build info and bridge statistics.

No parameters.

Returns: `{blender_version, blender_version_string, build_branch, python_version, background, online_access, has_view3d, bridge_port, stats}`.

Gotchas:

- `has_view3d` is the field that decides whether a sculpting session is even possible. Read it before planning one.
- `background: true` means Blender was launched with `--background` and every GUI-only tool will refuse.

```python
get_blender_version()
```

### get_object_info

Everything about one object: transforms, mesh stats, modifiers, groups, materials.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `name` | string | object name from `get_scene_info` | — | yes |

Returns: `{name, type, location, rotation_euler, rotation_mode, scale, dimensions, matrix_world, parent, parent_type, modifiers, vertex_groups, materials, collections, hide_viewport, hide_render}` plus one of `mesh` / `armature` / `camera` / `light`.

Gotchas:

- `matrix_world` is here — this is how you convert between local and world space before placing sculpt strokes or empties.

```python
get_object_info(name="Body")
```

### get_scene_info

Snapshot of the scene: objects, collections, active object, mode, frame.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `limit` | integer | cap on the returned list (counts stay exact) | `200` | no |

Returns: `{scene, filepath, frame_current, frame_start, frame_end, fps, render_engine, unit_system, object_count, objects, truncated, collections, active_object, mode, selected}`.

Gotchas:

- The only trustworthy source of object names. Never address an object by a name you assumed.
- `limit` caps the object list; `object_count` is always exact.

```python
get_scene_info(limit=200)
```

### health

Check whether the Blender bridge is reachable, and report what it can do.

No parameters.

Returns: `{connected, host, port, blender, background, has_view3d, gui_tools_available, commands_served}`. `connected:false` plus `error` when the bridge is down — nothing else works until that clears.

Gotchas:

- Call this first when anything fails. If `connected` is false, no workaround exists — the user must start the bridge from the 3D Viewport N-panel.
- `gui_tools_available:false` means every sculpt stroke, mask, face set, mesh filter, `weight_gradient`, `weight_heatmap`, `viewport_screenshot`, `playblast` and `set_viewport_shading` will refuse. Plan a data-API route instead.

```python
health()
```

### list_bridge_commands

List every command the connected Blender bridge serves.

No parameters.

Returns: `{commands, count}`; each command carries `mutates` and `needs_gui`.

Gotchas:

- Use this to spot a version mismatch between this MCP server and the installed Blender extension: a tool that exists here but not there fails with an unhelpful "unknown command".

```python
list_bridge_commands()
```

### list_objects

List scene objects, optionally of one type.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `type_filter` | string | Blender type id: MESH, ARMATURE, CAMERA, LIGHT, EMPTY, CURVE | none | no |
| `limit` | integer | cap on the returned object list | `500` | no |

Returns: `{count, objects, truncated}`.

Gotchas:

- `limit` caps the list; `count` is always exact. Filter by `type_filter` rather than pulling 500 objects and sorting them yourself.

```python
list_objects(type_filter="MESH", limit=100)
```

### reconnect

Force-drop and re-establish the TCP connection to Blender.

No parameters.

Returns: `{connected, host, port}` from the fresh TCP connection.

Gotchas:

- Ordinary calls already reconnect once transparently. Reach for this only after restarting Blender or the bridge from the N-panel.

```python
reconnect()
```

### redo

Step one undo level forward.

No parameters.

Returns: `{redone, mode}`.

Gotchas:

- Only steps forward through undo levels that `undo` created. Any new mutation discards the redo stack.

```python
redo()
```

### render_frame

Render the current frame properly and return the image.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `engine` | string | `BLENDER_EEVEE` or `CYCLES` | none | no |
| `resolution` | array | `[width, height]` pixels | none | no |
| `samples` | integer | Cycles samples, or EEVEE TAA samples | none | no |
| `timeout` | number | seconds to wait for the bridge call | `300.0` | no |

Returns: A PNG image content block. Scene render settings are restored afterwards.

Gotchas:

- Much slower than `viewport_screenshot` — iterate with the screenshot, finish with this.
- `CYCLES` enables the bundled Cycles add-on and picks Metal GPU compute on macOS. EEVEE's id is plain `BLENDER_EEVEE` in 5.x, NOT `BLENDER_EEVEE_NEXT`.

```python
render_frame(engine="BLENDER_EEVEE", resolution=[960, 540], samples=32, timeout=300.0)
```

### set_mode

Switch interaction mode for an object.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `mode` | string | `OBJECT`, `EDIT`, `SCULPT`, `POSE`, `WEIGHT_PAINT`, `VERTEX_PAINT`, `TEXTURE_PAINT` | — | yes |
| `object` | string | object name; omit for the active object | none | no |

Returns: `{object, mode}`.

Gotchas:

- Many operators silently do nothing in the wrong mode. Set it explicitly rather than assuming it carried over.

```python
set_mode(mode="SCULPT", object="Body")
```

### undo

Step one undo level back.

No parameters.

Returns: `{undone, mode}`.

Gotchas:

- Every mutating tool pushes its own undo step, so one call steps back exactly one tool call — unless the user touched the viewport in between.
- Undo does not restore the mode you were in; check `mode` in the result.

```python
undo()
```

### undo_checkpoint

Push a named undo step, so you can return to this exact state later.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `label` | string | name it for what it PRECEDES, so a rollback is describable | `agent checkpoint` | no |

Returns: `{pushed}`.

Gotchas:

- Every mutating tool already pushes its own undo step. Use this to mark a milestone before a risky multi-step experiment.

```python
undo_checkpoint(label="before limb blockout")
```

### viewport_screenshot

See the 3D viewport. Returns an image you can actually look at.

**GUI Blender only** — needs a real 3D Viewport; refuses under
`blender --background`.

**vs:** `render_frame` — screenshot for every iteration (fast, ~1 s); render_frame only for a final look.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `shading_mode` | string | `WIREFRAME`, `SOLID`, `MATERIAL`, `RENDERED` — for this shot only | none | no |
| `camera_view` | boolean | render through the scene camera instead of the current view | `false` | no |
| `max_size` | integer | longest image edge in pixels — 1024 is plenty; larger costs context | `1024` | no |

Returns: A PNG image content block you can actually look at, not a dict.

Gotchas:

- 1024 px is plenty for judging form. Raising `max_size` costs a lot of context for very little extra information.
- `shading_mode` applies to this shot only; the viewport is restored afterwards. Use `set_viewport_shading` for a persistent change.

```python
viewport_screenshot(shading_mode="SOLID", max_size=1024)
```

---

## objects

22 tools. Create, delete, transform, parent, align and organise objects. All headless-safe. Every tool addresses objects by their real name from `get_scene_info`.

### add_camera

Create a camera, by default making it the scene's render camera.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `location` | array | `[x, y, z]` world units | none | no |
| `rotation` | array | `[rx, ry, rz]` RADIANS | none | no |
| `lens` | number | focal length in MILLIMETRES on a 36 mm sensor | none | no |
| `type` | string | `PERSP`, `ORTHO`, `PANO`, `CUSTOM` | `PERSP` | no |
| `clip_start` | number | near clip, world units | none | no |
| `clip_end` | number | far clip, world units | none | no |
| `make_active` | boolean | set `scene.camera` so renders use this camera | `true` | no |
| `name` | string | object name | none | no |
| `collection` | string | collection name | none | no |

Returns: `{name, type, location, rotation_euler, scale, dimensions, collections}` plus camera data.

Gotchas:

- A camera with zero rotation looks straight DOWN (-Z). To look horizontally from the front use `rotation=[1.5708, 0, 0]`.
- This does not aim at anything by itself — use `aim_at` afterwards, or skip both and use `frame_object`, which positions AND aims in one call.

```python
add_camera(location=[0, -4.0, 1.6], rotation=[1.4835, 0, 0], lens=50.0, clip_end=200.0)
```

### aim_at

Rotate an object so its local -Z axis points at a target. No trigonometry needed.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `object` | string | object to rotate | none | **yes** |
| `target` | string \| array | object name, or `[x, y, z]` world point | none | **yes** |
| `use_bounds` | boolean | aim at the target's bounding-box centre rather than its origin | `true` | no |

Returns: `{object, aimed_at, rotation_euler}` — euler in RADIANS.

Gotchas:

- -Z is the direction cameras and spot lights look; for other objects "forward" may not be -Z.
- Sets `rotation_mode` to `XYZ`, overwriting a quaternion rotation mode.
- Aiming at an object's *origin* is often wrong — origins sit at corners or on the floor. Leave `use_bounds` true.

```python
aim_at(object="Key_Light", target="Lamp_Shade")
aim_at(object="Cam", target=[0.0, 0.0, 1.2])
```

### frame_object

Place AND aim a camera so a target fills the frame. The fastest way to see something.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `target` | string | object to frame | none | **yes** |
| `camera` | string | camera to move; defaults to the scene camera | none | no |
| `margin` | number | padding factor; 1.0 fits exactly, <1.0 crops | `1.25` | no |
| `direction` | array | `[x, y, z]` direction FROM object TO camera, normalised internally | `[0, -1, 0.35]` | no |
| `make_active` | boolean | set as `scene.camera` | `true` | no |

Returns: `{camera, target, location, rotation_euler, distance, bounds_radius, focal_length_mm, is_scene_camera}`.

**vs:** `add_camera` + `aim_at` is two calls and leaves you to compute distance. Use `frame_object` unless you need an exact camera position.

Gotchas:

- Errors when no `camera` is given and the scene has none — `add_camera` first.
- Accounts for the render aspect ratio, so it frames correctly for portrait output. Change resolution BEFORE framing.
- Fits the bounding **sphere**, so a long thin object leaves space at the sides.
- Follow with `viewport_screenshot(camera_view=True)` to see the result.

```python
frame_object(target="Lamp_Shade", camera="Cam", margin=1.3, direction=[1.0, -1.0, 0.5])
```

### set_camera

Change an existing camera's lens, type, clipping and depth of field.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `camera` | string | camera object name | none | **yes** |
| `lens` | number | focal length in MILLIMETRES | none | no |
| `type` | string | `PERSP`, `ORTHO`, `PANO` | none | no |
| `ortho_scale` | number | viewport width in world units (ORTHO only) | none | no |
| `clip_start` / `clip_end` | number | near/far clip, world units | none | no |
| `shift_x` / `shift_y` | number | lens shift, fraction of sensor | none | no |
| `dof_distance` | number | focus distance, world units | none | no |
| `dof_object` | string | object to focus on | none | no |
| `fstop` | number | aperture; lower is shallower | none | no |
| `use_dof` | boolean | enable/disable depth of field | none | no |
| `make_active` | boolean | set as `scene.camera` | `false` | no |

Returns: `{camera, type, lens, clip_start, clip_end, use_dof, focus_distance, aperture_fstop, is_scene_camera}`.

Gotchas:

- Setting any DOF parameter enables DOF automatically.
- Geometry nearer than `clip_start` or beyond `clip_end` silently vanishes — a very common cause of "my object disappeared from the render".

```python
set_camera(camera="Cam", lens=85.0, fstop=2.8, dof_object="Lamp_Shade")
```

### set_light

Change an existing light. Tune lighting with this rather than deleting and re-creating.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `light` | string | light object name | none | **yes** |
| `energy` | number | WATTS for POINT/SPOT/AREA; **irradiance W/m² for SUN** | none | no |
| `color` | array | `[r, g, b]`, each 0-1 | none | no |
| `type` | string | `POINT`, `SUN`, `SPOT`, `AREA` | none | no |
| `size` | number | radius (POINT/SPOT), edge length (AREA), **angular diameter in RADIANS (SUN)** | none | no |
| `use_shadow` | boolean | whether the light casts shadows | none | no |
| `spot_size` | number | cone angle in RADIANS, SPOT only | none | no |
| `spot_blend` | number | 0-1 cone edge softness | none | no |

Returns: `{light, type, energy, color, use_shadow}` plus `size_field` naming the property `size` was written to.

Gotchas:

- SUN energy is irradiance: sensible values are ~1-5, NOT the hundreds of watts a point light needs. This is the most common lighting mistake.
- `size` maps to a different property per light type; the result tells you which one was written.
- Bigger `size` means softer shadows and a dimmer-looking key at the same energy.

```python
set_light(light="Bulb", energy=60.0, color=[1.0, 0.85, 0.7], size=0.05)
```

### set_object_visibility

Viewport/render visibility plus per-ray visibility flags.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `object` | string | object name | none | **yes** |
| `hide_viewport` | boolean | hide in the viewport | none | no |
| `hide_render` | boolean | exclude from renders | none | no |
| `camera` | boolean | visible to camera rays | none | no |
| `shadow` | boolean | casts shadows | none | no |
| `diffuse` / `glossy` / `transmission` / `volume_scatter` | boolean | contribution per ray type | none | no |

Returns: `{object, applied, ray_visibility, hide_viewport, hide_render}` and `unsupported` for flags the engine lacks.

Gotchas:

- **Emissive geometry inside a light blocks that light.** A bulb mesh around a point light casts shadows onto the whole scene and the render comes back dark with no error. Fix with `shadow=False` on the bulb.
- `camera=False` makes an object light the scene without appearing in it — the standard trick for softboxes and practical lights.
- Ray flags are Cycles-only; EEVEE ignores them and reports them in `unsupported`.

```python
set_object_visibility(object="Bulb_Mesh", shadow=False, camera=True)
```

### add_empty

Create an Empty — a transform with no geometry, for rigging and pivots.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `display_type` | string | empty display shape (cosmetic only) | `PLAIN_AXES` | no |
| `size` | number | viewport display size, world units — cosmetic, does not scale children | `1.0` | no |
| `location` | array | `[x, y, z]` world units | none | no |
| `rotation` | array | `[rx, ry, rz]` RADIANS | none | no |
| `name` | string | object name | none | no |
| `collection` | string | collection name | none | no |

Returns: `{name, type, location, rotation_euler, scale, dimensions, collections}`.

Gotchas:

- `size` is cosmetic — it does not scale children. Use `transform_object` on the empty for that.
- An Empty has a zero-size bounding box, so `object_bounds` reports `degenerate: true` and `snap_to_ground` just moves it to `ground_z`.

```python
add_empty(display_type="PLAIN_AXES", size=0.2, location=[0.45, 0, 1.1], name="IK.hand.L")
```

### add_light

Create a light. Units differ per light type — read the notes below.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `type` | string | `POINT`, `SUN`, `SPOT`, `AREA` | `POINT` | no |
| `energy` | number | WATTS for POINT/SPOT/AREA; W/m² for SUN | none | no |
| `color` | array | `[r, g, b]` each 0-1 (NOT 0-255) | none | no |
| `size` | number | POINT/SPOT: emitter radius (world units); AREA: panel edge (world units); SUN: angular diameter (RADIANS) | none | no |
| `location` | array | `[x, y, z]` world units | none | no |
| `rotation` | array | `[rx, ry, rz]` RADIANS | none | no |
| `shape` | string | AREA light shape | none | no |
| `size_y` | number | AREA light second edge, world units | none | no |
| `spot_size` | number | full cone angle, RADIANS | none | no |
| `spot_blend` | number | SPOT only, 0-1 edge softness of the cone | none | no |
| `name` | string | object name | none | no |
| `collection` | string | collection name | none | no |

Returns: `{name, type, location, …}` plus which softness property `size` was written to.

Gotchas:

- `energy` units differ by type: WATTS for POINT/SPOT/AREA (Blender's default point light is 1000 W at ~1 m), but W/m² for SUN where 1-10 is sensible. 1000 on a SUN blows out the render.
- `color` is 0-1 per channel, not 0-255.
- A SUN ignores `location` for brightness — only its `rotation` matters.

```python
add_light(type="AREA", energy=200.0, size=2.0, location=[2.0, -2.0, 3.0], rotation=[0.7854, 0, 0.7854], name="KeyLight")
```

### align_objects

Line objects up on one or more axes.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `names` | array | list of object names | — | yes |
| `axis` | string|array | `X`, `Y`, `Z`, or a list like `["X", "Y"]` | `Z` | no |
| `mode` | string | `CENTERS`, `NEGATIVE`, `POSITIVE` | `CENTERS` | no |
| `relative_to` | string | what the objects align TO | `SELECTION` | no |
| `active` | string | which object is the anchor / active | none | no |
| `bb_quality` | boolean | use the accurate (slower) bounding box | `true` | no |

Returns: `{objects, axes, mode, relative_to, active}`.

Gotchas:

- This MOVES objects. To only read positions use `object_bounds`.
- Pass the friendly names — `CENTERS`/`NEGATIVE`/`POSITIVE`, not Blender's `OPT_1`..`OPT_4`.

```python
align_objects(names=["Bolt.001", "Bolt.002", "Bolt.003"], axis=["X"], mode="CENTERS", relative_to="ACTIVE", active="Bolt.001")
```

### apply_transforms

Bake an object's transform into its mesh, resetting the transform to identity.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `names` | array | list of object names | — | yes |
| `location` | boolean | also zero the location (moves the origin to the world origin) | `false` | no |
| `rotation` | boolean | bake rotation, leaving `rotation_euler` at 0 | `true` | no |
| `scale` | boolean | bake scale, leaving `scale` at 1,1,1 | `true` | no |
| `isolate_users` | boolean | give each object its own mesh copy first (breaks instancing, costs memory) | `false` | no |

Returns: `{objects, applied}`.

Gotchas:

- Do this before sculpting, remeshing, or any modifier whose result depends on real size (Bevel, Solidify, Remesh) — non-uniform object scale makes those visibly wrong.
- Blender refuses on a mesh shared by several objects ("Cannot apply to a multi user"). `isolate_users=true` copies the mesh first, which breaks instancing and costs memory.

```python
apply_transforms(names=["Body"], rotation=True, scale=True)
```

### collection_create

Create a collection, optionally nested inside an existing one.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `name` | string | collection name | — | yes |
| `parent` | string | existing collection to nest inside | none | no |
| `allow_duplicate_name` | boolean | create a suffixed second collection instead of refusing | `false` | no |

Returns: `{name, requested_name, parent, children}`.

Gotchas:

- A duplicate name is refused by default; two collections called "Props" is a debugging nightmare.

```python
collection_create(name="Props")
```

### collection_link

Add objects to a collection WITHOUT removing them from their current ones.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `names` | array | list of object names | — | yes |
| `collection` | string | collection name | — | yes |
| `create` | boolean | create the collection at the scene root when missing | `true` | no |

Returns: `{collection, linked, already_linked, membership}`.

Gotchas:

- Adds membership WITHOUT removing existing membership — an object can belong to any number of collections. Use `collection_move` when it should live in exactly one.

```python
collection_link(names=["Sword"], collection="Props", create=True)
```

### collection_list

The scene's collection tree, with object counts and nesting depth.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `limit` | integer | cap on the returned list (counts stay exact) | `1000` | no |
| `names_per_collection` | integer | cap on names listed per collection | `50` | no |

Returns: `{scene, master_collection, active_collection, collections, count, truncated, unlinked_collections}`.

Gotchas:

- `unlinked_collections` lists collections in the file that are not in this scene's tree — objects in them will not render.

```python
collection_list(limit=100, names_per_collection=20)
```

### collection_move

Move objects into a collection, unlinking them from every other one.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `names` | array | list of object names | — | yes |
| `collection` | string | collection name | — | yes |
| `create` | boolean | create the collection at the scene root when missing | `true` | no |

Returns: `{collection, moved, was_in, membership}` — `was_in` is your undo list.

Gotchas:

- This UNLINKS from every other collection. Use `collection_link` to add membership instead of replacing it.
- `was_in` records the previous membership so you can put things back.

```python
collection_move(names=["Sword", "Shield"], collection="Props")
```

### create_primitive

Add a mesh primitive to the scene. The normal way to create geometry.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `kind` | string | primitive kind | `cube` | no |
| `size` | number | primary dimension in WORLD UNITS — edge length for cube/plane/grid/monkey, RADIUS for spheres/cylinder/circle, base radius for cone, major radius for torus | none | no |
| `location` | array | `[x, y, z]` world units | none | no |
| `rotation` | array | `[rx, ry, rz]` RADIANS | none | no |
| `scale` | array | `[sx, sy, sz]` multipliers, 1.0 = unchanged | none | no |
| `name` | string | object name; may come back suffixed `.001` | none | no |
| `collection` | string | collection name | none | no |
| `vertices` | integer | radial segment count | none | no |
| `segments` | integer | longitudinal segments for uv_sphere (default 32) | none | no |
| `ring_count` | integer | latitudinal rings | none | no |
| `subdivisions` | integer | ico_sphere subdivision level | none | no |
| `depth` | number | world units | none | no |
| `radius2` | number | cone tip radius, world units; 0 = a point | none | no |
| `minor_radius` | number | torus tube radius, world units | none | no |
| `x_subdivisions` | integer | grid vertices per side | none | no |
| `y_subdivisions` | integer | grid vertices per side | none | no |
| `fill_type` | string | cap style: `NOTHING`, `NGON`, `TRIFAN` | none | no |

Returns: `{name, type, location, rotation_euler, scale, dimensions, collections, kind, requested_name, vertices, polygons}` — read `name`, not the one you asked for.

Gotchas:

- `size` means different things per kind: full edge length for cube/plane/grid/monkey, but RADIUS for spheres, cylinder and circle. A `size=2` cube spans -1..+1.
- `rotation` is RADIANS. Passing 90 spins the object ~14 turns.
- `scale` lands on the object transform, not the mesh. `apply_transforms` afterwards if a modifier needs real-world size.
- `subdivisions` on an ico_sphere grows fast: 5 ≈ 10k tris, 7 ≈ 160k. Stay at or below 4 for blockouts.

```python
create_primitive(kind="uv_sphere", size=0.9, location=[0, 0, 1.2], segments=32, ring_count=16, name="Head")
```

### delete_objects

Delete objects by name. Undoable via `undo`.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `names` | array | object names; unknown names come back in `missing` rather than raising | — | yes |
| `purge_orphan_data` | boolean | also delete zero-user mesh/curve/light/camera/armature datablocks | `false` | no |

Returns: `{deleted, missing, purged_datablocks, remaining}`.

Gotchas:

- Children of a deleted object are NOT deleted; they just lose their parent. Delete leaf-first or list every member.
- `purge_orphan_data` is off by default because a zero-user datablock is still recoverable until the file is saved.

```python
delete_objects(names=["WindowCutter"], purge_orphan_data=True)
```

### duplicate_object

Copy one object, with or without copying its mesh data.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `name` | string | object to copy | — | yes |
| `linked` | boolean | share the mesh datablock (an instance) instead of copying it | `false` | no |
| `new_name` | string | name for the copy; may be suffixed | none | no |
| `location` | array | `[x, y, z]` world units for the copy; omit to leave it on top of the original | none | no |
| `collection` | string | collection name | none | no |

Returns: `{name, type, location, …, children_not_duplicated}`.

Gotchas:

- `linked=true` SHARES the mesh datablock — sculpting either one changes both, and `apply_transforms` refuses without `isolate_users=true`.
- Children are not duplicated. `children_not_duplicated` lists them so you can handle them yourself.

```python
duplicate_object(name="Bolt", linked=True, new_name="Bolt.L", location=[0.2, 0, 0])
```

### join_objects

Merge several objects into one. Destructive — the others are consumed.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `names` | array | objects to merge — all must be the same type as the target | — | yes |
| `target` | string | the survivor, which keeps its name, origin, transform and modifiers | none | no |

Returns: `{name, type, …}` for the surviving target.

Gotchas:

- Destructive — every object except `target` is consumed. All inputs must be the same type.
- Materials are combined into the target's slots and vertex groups merge BY NAME, so two different `Bone` groups become one.

```python
join_objects(names=["Torso", "ArmL", "ArmR"], target="Torso")
```

### object_bounds

Measure one object: bounding box in local AND world space, centre, size.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `name` | string | object name | — | yes |
| `use_evaluated` | boolean | measure the MODIFIER RESULT instead of the base mesh | `false` | no |

Returns: `{name, type, use_evaluated, local, world, dimensions, location, matrix_world, degenerate}`; `local`/`world` each carry min, max, center, size and the 8 corners.

Gotchas:

- `bounds_local` ignores the object transform; `world` accounts for location/rotation/scale. Compare `world.center` against `location` to see how far the origin sits from the visual centre.
- `use_evaluated=true` measures the MODIFIER RESULT — a Subdivision Surface shrinks a cube by about 16%, so the answers genuinely differ.

```python
object_bounds(name="Body", use_evaluated=True)
```

### parent_objects

Parent objects to another object, including all the armature-deform variants.

**vs:** `parent_mesh_to_armature` / `auto_weights` — use `parent_objects` for plain object/bone parenting; use the rig tools when you actually want skinning.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `child` | string|array | one object name, or a list of them | — | yes |
| `parent` | string | parent name (bone / collection / object) | — | yes |
| `type` | string | `OBJECT`, `ARMATURE`, `ARMATURE_NAME`, `ARMATURE_AUTO`, `ARMATURE_ENVELOPE`, `BONE`, `BONE_RELATIVE`, `CURVE`, `FOLLOW`, `PATH_CONST`, `LATTICE`, `VERTEX`, `VERTEX_TRI` | `OBJECT` | no |
| `keep_transform` | boolean | preserve the child's WORLD position when parenting | `true` | no |
| `bone` | string | bone name | none | no |
| `xmirror` | boolean | mirror the generated weights across X for a symmetric character | `false` | no |

Returns: `{parent, type, children}`.

Gotchas:

- `ARMATURE_AUTO` can fail with "Bone Heat Weighting: failed to find solution" on non-manifold or self-intersecting meshes. Clean the mesh, or use `ARMATURE_NAME` and weight by hand.
- `keep_transform=false` reinterprets the child's local transform against the parent, which usually makes it jump.
- The `ARMATURE*` and `BONE` types require `parent` to actually be an armature.

```python
parent_objects(child=["Sword"], parent="Rig", type="BONE", bone="hand.R", keep_transform=True)
```

### select_objects

Set the object selection and the active object.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `names` | array | list of object names | — | yes |
| `mode` | string | `SET`, `ADD`, `REMOVE` | `SET` | no |
| `deselect_others` | boolean | override the default (true for SET, false for ADD/REMOVE) | none | no |
| `active` | string | which object is the anchor / active | none | no |

Returns: `{selected, active, mode, deselect_others}`.

Gotchas:

- Most tools take object names directly, so you rarely need this. It matters for `snap_cursor_to(target="SELECTED")` and for `execute_python`.
- Hidden objects cannot be selected — the call fails naming the object.

```python
select_objects(names=["Body", "Rig"], mode="SET", active="Rig")
```

### separate

Split one mesh into several objects. The inverse of `join_objects`.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `name` | string | MESH object to split | — | yes |
| `by` | string | `LOOSE`, `MATERIAL` or `SELECTED` | `LOOSE` | no |

Returns: `{source, by, created, objects, source_vertices}`.

Gotchas:

- `by="SELECTED"` on a freshly created primitive moves the ENTIRE mesh out, because a new primitive has all its vertices selected. Check `source_vertices` in the response.

```python
separate(name="Imported", by="LOOSE")
```

### set_active

Make one object the active object.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `name` | string | object name | — | yes |
| `select` | boolean | also select it (false leaves an existing selection alone) | `true` | no |

Returns: `{active, type, mode, selected}`.

Gotchas:

- Active and selected are different. `set_mode`, sculpt tools and `join_objects` all act on the ACTIVE object.

```python
set_active(name="Body", select=True)
```

### set_origin

Move an object's origin — the pivot that rotation, scale and parenting use.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `names` | array | list of object names | — | yes |
| `type` | string | `ORIGIN_GEOMETRY`, `GEOMETRY_ORIGIN`, `ORIGIN_CURSOR`, `ORIGIN_CENTER_OF_MASS`, `ORIGIN_CENTER_OF_VOLUME` | `ORIGIN_GEOMETRY` | no |
| `center` | string | `MEDIAN` (vertex average) or `BOUNDS` (bounding-box centre) | `MEDIAN` | no |

Returns: `{objects, type, center}`.

Gotchas:

- `ORIGIN_GEOMETRY` moves the ORIGIN to the geometry. `GEOMETRY_ORIGIN` is the inverse and makes the object visibly jump — they are easy to confuse.
- `ORIGIN_CURSOR` reads the 3D cursor; set it with `snap_cursor_to` first.

```python
set_origin(names=["Crate"], type="ORIGIN_GEOMETRY", center="BOUNDS")
```

### snap_cursor_to

Move the 3D cursor, which several other operations pivot around.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `target` | string | `WORLD_ORIGIN`, `LOCATION`, `OBJECT`, `SELECTED` | `WORLD_ORIGIN` | no |
| `location` | array | `[x, y, z]` world units | none | no |
| `object` | string | object name; omit for the active object | none | no |
| `use_bounds` | boolean | use the bounding-box CENTRE instead of the object ORIGIN | `false` | no |

Returns: `{cursor, target, use_bounds}`.

Gotchas:

- `target="SELECTED"` needs a selection — call `select_objects` first or it fails.
- `use_bounds` uses the bounding-box centre rather than the object ORIGIN; they differ exactly when the origin is off-centre.

```python
snap_cursor_to(target="OBJECT", object="Head", use_bounds=True)
```

### snap_to_ground

Drop objects straight down so they rest on a horizontal plane.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `names` | array | list of object names | — | yes |
| `ground_z` | number | world Z of the floor plane | `0.0` | no |
| `together` | boolean | move the whole set by one shared offset, preserving relative heights | `false` | no |
| `use_evaluated` | boolean | measure the MODIFIER RESULT instead of the base mesh | `false` | no |

Returns: `{moved, ground_z, together, use_evaluated, objects}`.

Gotchas:

- Z only. The object is never rotated, so a tilted object rests on its lowest corner.
- Turn on `use_evaluated` when Subdivision/Displace/Solidify changes the silhouette, or the object floats or sinks by the modifier's difference.

```python
snap_to_ground(names=["Crate", "Barrel"], ground_z=0.0, use_evaluated=True)
```

### transform_object

Move, rotate or scale an object. The primary way to place things.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `name` | string | object name | — | yes |
| `location` | array | `[x, y, z]` world units, in the object's PARENT space | none | no |
| `rotation` | array | `[rx, ry, rz]` RADIANS | none | no |
| `scale` | array | `[sx, sy, sz]` multipliers, 1.0 = unchanged | none | no |
| `mode` | string | `absolute` sets outright; `delta` adds location/rotation and multiplies scale | `absolute` | no |
| `rotation_mode` | string | `XYZ`…`ZYX`, `QUATERNION`, `AXIS_ANGLE` — switch before passing Euler `rotation` | none | no |

Returns: `{name, type, location, rotation_euler, scale, dimensions, collections}` after the write.

Gotchas:

- `location` is in the object's PARENT space. For a parented object that is not the world position you see — check `matrix_world`.
- `rotation` is RADIANS. 90° is 1.5708.
- Rotation and scale act around the ORIGIN, not the visual centre. Fix the origin with `set_origin` first if something pivots wrongly.
- `mode="delta"` edits the normal channels relatively; it does NOT touch Blender's separate `delta_location` / `delta_rotation_euler` properties.

```python
transform_object(name="Head", location=[0, 0, 1.62], rotation=[0, 0, 0.2618], mode="absolute")
```

---

## mesh

20 tools. Selection-driven mesh editing plus whole-object topology and shading operations. Almost all of these refuse to run without a selection and say so — `mesh_select_geometry` is the gateway.

### mesh_bevel

Round off the selected edges (or corners) so they catch light.

**vs:** a `BEVEL` modifier via `add_modifier` — the tool bakes geometry now; the modifier stays retunable and reorderable.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `name` | string | MESH object name | — | yes |
| `width` | number | world units, interpreted per `offset_type` | `0.1` | no |
| `segments` | integer | cross-section subdivisions — 1 chamfer, 2-4 rounded, >6 rarely pays | `1` | no |
| `affect` | string | `EDGES` (default) or `VERTICES` | `EDGES` | no |
| `profile` | number | 0-1; 0.5 = circular arc | `0.5` | no |
| `offset_type` | string | how `width` is measured | `OFFSET` | no |
| `clamp_overlap` | boolean | stop the bevel eating past neighbouring geometry — leave on | `true` | no |
| `loop_slide` | boolean | prefer sliding along existing edge loops | `true` | no |
| `mark_seam` | boolean | tag the new edges as UV seams | `false` | no |
| `mark_sharp` | boolean | tag the new edges as sharp | `false` | no |
| `harden_normals` | boolean | adjust custom normals so flat faces stay flat (smooth-shaded meshes only) | `false` | no |
| `material` | integer | material slot index for the new faces; -1 inherits from neighbours | `-1` | no |
| `miter_outer` | string | `SHARP`, `PATCH`, `ARC` | `SHARP` | no |
| `miter_inner` | string | `SHARP`, `ARC` | `SHARP` | no |
| `spread` | number | world units, ARC miters only | none | no |

Returns: `{name, before, after, created, beveled, affect, width, segments, new_faces}`.

Gotchas:

- Destructive — this bakes real geometry. Use a Bevel modifier when you still want to retune the width.
- Face count grows roughly `segments` new faces per beveled edge. Re-check `mesh_stats` before beveling again.
- Hard-surface widths are small: 0.005-0.05 world units. Segments above ~6 rarely pay for themselves.
- Needs an EDGE selection (a VERT selection when `affect="VERTICES"`).

```python
mesh_bevel(name="Panel", width=0.012, segments=3, affect="EDGES", profile=0.5, clamp_overlap=True)
```

### mesh_bridge_edge_loops

Connect two open edge loops with a tube of faces.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `name` | string | MESH object name, from `get_scene_info` | — | yes |
| `use_pairs` | boolean | bridge loops in matched pairs rather than as one chain | `false` | no |
| `use_cyclic` | boolean | force the bridge to close into a loop | `false` | no |
| `use_merge` | boolean | collapse the two loops into one instead of building faces between them | `false` | no |
| `merge_factor` | number | 0-1 along the span | `0.5` | no |
| `twist_offset` | integer | vertex-correspondence rotation, in steps | `0` | no |

Returns: `{name, before, after, created, bridged_edges, new_faces}`.

Gotchas:

- Both loops must be in the SAME object and both must be open boundary loops. Join the objects first if they are separate.
- A visibly twisted bridge is fixed by nudging `twist_offset` by ±1.

```python
mesh_bridge_edge_loops(name="Body", use_merge=False, twist_offset=0)
```

### mesh_decimate

Reduce polygon count in one shot: add a Decimate modifier and apply it.

**vs:** `quadriflow_remesh` — decimate is fast and wrecks edge flow; quadriflow is slow and produces topology you can still work with.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `name` | string | MESH object name, from `get_scene_info` | — | yes |
| `ratio` | number | fraction of faces to KEEP, 0.0-1.0 (not a percentage) | `0.5` | no |
| `decimate_type` | string | `COLLAPSE`, `UNSUBDIV`, `DISSOLVE` | `COLLAPSE` | no |
| `use_collapse_triangulate` | boolean | keep the triangles COLLAPSE produces | `false` | no |
| `iterations` | integer | UNSUBDIV only — subdivision levels to undo | none | no |
| `angle_limit` | number | RADIANS, DISSOLVE only. Default 0.0873 (5°) | none | no |
| `use_dissolve_boundaries` | boolean | also dissolve open boundary edges (can eat silhouette detail) | none | no |
| `vertex_group` | string | COLLAPSE only — restrict decimation to this group | none | no |
| `invert_vertex_group` | boolean | decimate everything EXCEPT the group | `false` | no |
| `vertex_group_factor` | number | 0-1 bias from the vertex group | none | no |
| `symmetry_axis` | string | `X`, `Y` or `Z` | none | no |
| `timeout` | number | seconds to wait for the bridge call | `300.0` | no |

Returns: `{name, before, after, removed, decimate_type, requested_ratio, achieved_face_ratio}` — `achieved_face_ratio` often exceeds `ratio` because COLLAPSE triangulates.

Gotchas:

- Destructive and immediate — no modifier is left behind to retune.
- Destroys edge loops and UV quality. Do it at the END of a workflow, after modelling and UV work.
- `achieved_face_ratio` often exceeds `ratio` because COLLAPSE triangulates first.
- `angle_limit` is RADIANS and applies to DISSOLVE only; `iterations` applies to UNSUBDIV only.

```python
mesh_decimate(name="Sculpt", ratio=0.15, decimate_type="COLLAPSE", timeout=300.0)
```

### mesh_delete_geometry

Delete the selected geometry, choosing how much goes with it.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `name` | string | MESH object name, from `get_scene_info` | — | yes |
| `domain` | string | `VERT`, `EDGE`, `FACE`, `FACE_ONLY`, `EDGE_FACE`, `FACE_KEEP_BOUNDARY` | `VERT` | no |

Returns: `{name, before, after, removed, domain, bmesh_context, deleted_elements}`.

Gotchas:

- `FACE_ONLY` is the one you want before `mesh_fill_holes` or `mesh_bridge_edge_loops` — it leaves the vertex and edge cage intact. Plain `FACE` takes the cage with it.

```python
mesh_delete_geometry(name="Body", domain="FACE_ONLY")
```

### mesh_edge_ring_subdivide

Loop cut: add edge loops running around the mesh, perpendicular to a seed edge.

**vs:** `mesh_subdivide` — use this when you want ONE loop in the right place (a waistline, a bend joint, a subsurf control loop), not general density.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `name` | string | MESH object name, from `get_scene_info` | — | yes |
| `seed_edges` | array | edge indices to grow rings from | none | no |
| `cuts` | integer | parallel loops to insert — 1 at the midpoint, 2 evenly spaced | `1` | no |
| `smoothness` | number | 0 keeps the surface put; 1 rounds it outward | `0.0` | no |
| `smooth_falloff` | string | curve used by `smoothness` | `SMOOTH` | no |
| `quad_corner_type` | string | how a quad with two adjacent cut edges is resolved | `STRAIGHT_CUT` | no |
| `limit` | integer | cap on the returned list (counts stay exact) | `1000` | no |

Returns: `{name, before, after, created, seed_edges, ring_edges, cuts, new_loop_edges, ring_edge_indices, truncated}` — check `ring_edges`, it tells you whether the ring actually walked.

Gotchas:

- The ring stops at triangles, n-gons, non-manifold junctions and open boundaries. On a triangulated mesh the "ring" is just the seed edge — `ring_edges` tells you.
- The loop runs PERPENDICULAR to the seed edge. If the cut came out the wrong way round, seed from an edge at 90° to the one you picked.
- Blender's interactive `mesh.loopcut_slide` is modal and cannot run headless; this is the equivalent, not a wrapper.

```python
mesh_edge_ring_subdivide(name="Arm", seed_edges=[12], cuts=2, smoothness=0.0)
```

### mesh_extrude

Extrude the current selection and push the new geometry out.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `name` | string | MESH object name, from `get_scene_info` | — | yes |
| `offset` | array | `[x, y, z]` translation in world units, read in `space` | none | no |
| `normal_offset` | number | world units along each new vertex's own normal | none | no |
| `space` | string | `OBJECT` or `WORLD` for the coordinates above | `OBJECT` | no |

Returns: `{name, before, after, created, extruded_from, offset_local, normal_offset, new_vertices, new_faces}`.

Gotchas:

- Default `offset` of [0,0,0] extrudes in place and leaves zero-area side faces. Always pass an offset or a `normal_offset`.
- On a curved surface use `normal_offset`; a single `offset` shears it.
- Requires a selection — `mesh_select_geometry` first.

```python
mesh_extrude(name="Tower", offset=[0, 0, 1.5], space="OBJECT")
```

### mesh_fill_holes

Cap open boundary loops with new faces.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `name` | string | MESH object name, from `get_scene_info` | — | yes |
| `sides` | integer | maximum edges a hole may have; 0 = no limit | `0` | no |

Returns: `{name, before, after, created, filled_faces, boundary_edges, sides, used_selection}`; `filled_faces: 0` plus a note when the mesh was already closed.

Gotchas:

- The generated faces are n-gons. For clean quads across a large opening, `mesh_bridge_edge_loops` between two loops beats filling.
- `sides=4` plugs pinholes while leaving a large deliberate opening alone.

```python
mesh_fill_holes(name="Shell", sides=4)
```

### mesh_flip_normals

Reverse face winding unconditionally.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `name` | string | MESH object name, from `get_scene_info` | — | yes |

Returns: `{name, faces, used_selection, signed_volume_after}`.

Gotchas:

- Blindly reverses everything. On a partly-broken mesh that just moves the problem around — use `mesh_recalculate_normals`.

```python
mesh_flip_normals(name="Shell")
```

### mesh_inset

Inset the selected faces, creating a border ring around them.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `name` | string | MESH object name, from `get_scene_info` | — | yes |
| `thickness` | number | border width in WORLD UNITS (not a 0-1 fraction) | `0.1` | no |
| `depth` | number | displacement of the inset faces along their normal, world units | `0.0` | no |
| `individual` | boolean | inset every face separately (the grid-of-panels look) rather than as one region | `false` | no |
| `use_boundary` | boolean | inset open mesh boundaries too | none | no |
| `use_even_offset` | boolean | keep the border an even width around corners | none | no |
| `use_relative_offset` | boolean | scale `thickness` by each face's size instead of using an absolute distance | none | no |
| `use_outset` | boolean | grow the border outward instead of inward (ignored when `individual`) | none | no |

Returns: `{name, before, after, created, inset_faces, individual, thickness, depth}`.

Gotchas:

- `thickness` is WORLD UNITS, not a 0-1 fraction. Too large for the face and the inset collapses to a point.
- The inset faces are left selected, so `mesh_extrude` chains straight on with no extra selection call.

```python
mesh_inset(name="Panel", thickness=0.03, depth=0.0, individual=True)
```

### mesh_merge_by_distance

Weld vertices that sit closer together than a threshold.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `name` | string | MESH object name, from `get_scene_info` | — | yes |
| `threshold` | number | maximum world-unit distance between vertices that get welded | `0.0001` | no |
| `use_connected` | boolean | only weld vertices that share an edge (slower, safer) | `false` | no |

Returns: `{name, before, after, removed, threshold, considered_vertices, used_selection}`.

Gotchas:

- 0.0001 removes exact duplicates only. Raise it cautiously — a threshold near your smallest real feature collapses genuine detail, and only undo reverses it.
- The standard repair after `mesh_symmetrize` or a mirrored duplicate. Re-check `is_watertight` afterwards.

```python
mesh_merge_by_distance(name="Body", threshold=0.0005)
```

### mesh_proportional_transform

Move the selected vertices and drag nearby ones along with a soft falloff.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `name` | string | MESH object name, from `get_scene_info` | — | yes |
| `translate` | array | `[x, y, z]` displacement in world units | none | no |
| `proportional_size` | number | radius of influence, world units (Euclidean, not topological) | `1.0` | no |
| `falloff` | string | weight curve | `SMOOTH` | no |
| `space` | string | `OBJECT` or `WORLD` for the coordinates above | `OBJECT` | no |
| `seed` | integer | seed for `falloff='RANDOM'` | `0` | no |

Returns: `{name, translate_local, proportional_size, falloff, moved_selected, moved_by_falloff, total_vertices}`. `moved_by_falloff: 0` means the radius caught nothing.

Gotchas:

- `proportional_size` is a EUCLIDEAN distance, not a topological one — a nearby but disconnected surface WILL be dragged too.
- If `moved_by_falloff` comes back 0, the radius caught nothing: raise `proportional_size`.

```python
mesh_proportional_transform(name="Terrain", translate=[0, 0, 0.6], proportional_size=1.4, falloff="SMOOTH")
```

### mesh_recalculate_normals

Make face winding consistent so the surface faces outward.

**vs:** `mesh_flip_normals` — recalculate repairs a partly-wrong mesh per shell; flip only helps when the whole surface is uniformly backwards.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `name` | string | MESH object name, from `get_scene_info` | — | yes |
| `inside` | boolean | point normals INWARD (skybox / interior geometry) | `false` | no |

Returns: `{name, faces, inside, used_selection, signed_volume_before, signed_volume_after}` — the volume sign flip is the proof it worked.

Gotchas:

- Prefer this over `mesh_flip_normals`: it works out the correct orientation per shell, so it repairs a mesh where only some faces are wrong.
- Needs a closed shell to decide which way is "out"; on an open surface the direction is a guess.

```python
mesh_recalculate_normals(name="Shell", inside=False)
```

### mesh_select_geometry

Choose which vertices/edges/faces the next edit will act on.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `name` | string | MESH object name | — | yes |
| `domain` | string | `VERT`, `EDGE` or `FACE` | `VERT` | no |
| `mode` | string | `SET`, `ADD`, `SUBTRACT` | `SET` | no |
| `indices` | array | explicit element indices in `domain` | none | no |
| `box_min` | array | `[x, y, z]` lower corner, world units (may be omitted for a half-space) | none | no |
| `box_max` | array | `[x, y, z]` upper corner, world units (may be omitted for a half-space) | none | no |
| `normal` | array | `[x, y, z]` direction vector (need not be unit length) | none | no |
| `normal_angle` | number | RADIANS tolerance around `normal` | none | no |
| `material_index` | integer | 0-based material slot | none | no |
| `linked_from` | array | seed element indices; expands to whole connected components | none | no |
| `random_percent` | number | 0-100 PERCENT (not a 0-1 fraction) | none | no |
| `random_seed` | integer | RNG seed | `0` | no |
| `select_all` | boolean | select every element in `domain` | `false` | no |
| `invert` | boolean | invert the result, applied last, after `mode` | `false` | no |
| `space` | string | `OBJECT` or `WORLD` for the coordinates above | `OBJECT` | no |
| `limit` | integer | cap on the returned list (counts stay exact) | `1000` | no |

Returns: `{name, domain, mode, matched, indices, truncated}` — `matched` is exact, `indices` is capped by `limit`.

Gotchas:

- Selecting by geometry beats selecting by index: indices shift on every topology change, so an index list from before an extrude points somewhere else afterwards.
- `normal_angle` is RADIANS. Use ~0.1 to grab exactly one flat face of a box; the default 0.785 (45°) grabs a whole side.
- `random_percent` is 0-100, not a 0-1 fraction.
- Works in Object Mode as well as Edit Mode — you do not need to switch first.

```python
mesh_select_geometry(name="Crate", domain="FACE", normal=[0, 0, 1], normal_angle=0.1, mode="SET")
```

### mesh_shade_auto_smooth

Smooth-shade only where faces meet at a shallow angle, keeping creases sharp.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `name` | string | MESH object name, from `get_scene_info` | — | yes |
| `angle` | number | RADIANS — 0.5236 = 30°, 0.7854 = 45°, 1.0472 = 60° | `0.5235987755982988` | no |
| `keep_sharp_edges` | boolean | preserve edges already marked sharp | `true` | no |
| `use_modifier` | boolean | add a retunable "Smooth by Angle" modifier instead of baking `sharp_edge` | `false` | no |

Returns: `{name, angle_radians, angle_degrees, method, modifiers, smooth_faces, total_faces}`.

Gotchas:

- `angle` is RADIANS. 0.5236 = 30°, 0.7854 = 45°, 1.0472 = 60°. Passing 30 smooths everything.
- The legacy `Mesh.use_auto_smooth` / `auto_smooth_angle` properties were REMOVED in 4.1 and do not exist in 5.2. This uses `object.shade_smooth_by_angle` (bakes a `sharp_edge` attribute) or, with `use_modifier=true`, `object.shade_auto_smooth` (a retunable geometry-nodes modifier).
- Requires Object Mode.

```python
mesh_shade_auto_smooth(name="Hull", angle=0.5236, keep_sharp_edges=True)
```

### mesh_shade_flat

Shade faces flat, so every face reads as a distinct plane.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `name` | string | MESH object name, from `get_scene_info` | — | yes |
| `selected_only` | boolean | false (default) shades the WHOLE object, matching Object ▸ Shade Flat | `false` | no |
| `clear_sharp_edges` | boolean | also remove every "sharp edge" mark | `false` | no |

Returns: `{name, faces, selected_only, cleared_sharp_edges}`.

Gotchas:

- `selected_only=false` (the default) shades the WHOLE object, matching Blender's Object ▸ Shade Flat. That default is deliberate: a leftover selection would otherwise silently shade part of the mesh.
- This is the way to undo an unwanted `mesh_shade_smooth`.

```python
mesh_shade_flat(name="Crate")
```

### mesh_shade_smooth

Shade faces smooth, interpolating normals across them.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `name` | string | MESH object name, from `get_scene_info` | — | yes |
| `selected_only` | boolean | false (default) shades the WHOLE object, matching Object ▸ Shade Smooth | `false` | no |
| `clear_sharp_edges` | boolean | also remove every "sharp edge" mark | `false` | no |

Returns: `{name, faces, selected_only, cleared_sharp_edges}`.

Gotchas:

- On a hard-surface model this smears the edges into mush. Use `mesh_shade_auto_smooth` there.
- `selected_only=false` (the default) shades the WHOLE object, matching Blender's Object ▸ Shade Smooth.

```python
mesh_shade_smooth(name="Head")
```

### mesh_stats

Measure a mesh: counts, triangle count, topology problems, bounding box.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `name` | string | MESH object name | — | yes |
| `limit` | integer | cap on the returned list (counts stay exact) | `1000` | no |

Returns: `{name, mode, counts, triangles, faces_by_kind, selected, diagnostics, surface_area, volume_signed, volume_reliable, bounds_local, bounds_world, uv_layers, material_slots, shape_keys, samples, truncated, limit}`. `samples` holds real indices per problem class — feed them straight back into `mesh_select_geometry(indices=...)`.

Gotchas:

- `volume_signed` only means anything when `is_watertight` is true — check `volume_reliable`. A negative volume on a watertight mesh means normals point inward; fix with `mesh_recalculate_normals`.
- Reads the object's own mesh data with modifiers NOT applied.

```python
mesh_stats(name="Body", limit=200)
```

### mesh_subdivide

Add resolution by cutting the selected edges into segments.

**vs:** `mesh_edge_ring_subdivide` vs `add_subsurf` — subdivide to densify a selection; edge-ring for a single control loop that keeps all-quads; subsurf when you want it non-destructive and retunable.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `name` | string | MESH object name, from `get_scene_info` | — | yes |
| `cuts` | integer | new vertices per selected edge — 1 halves each edge, 2 thirds it | `1` | no |
| `smoothness` | number | 0 keeps the surface put; 1 rounds it outward | `0.0` | no |
| `smooth_falloff` | string | curve used by `smoothness` | `SMOOTH` | no |
| `fractal` | number | random displacement in world units | none | no |
| `along_normal` | number | 0-1 bias of `fractal` toward the vertex normal | none | no |
| `seed` | integer | RNG seed | `0` | no |
| `quad_corner_type` | string | how a quad with two adjacent cut edges is resolved | `STRAIGHT_CUT` | no |
| `use_grid_fill` | boolean | fill fully-cut quads with a clean grid rather than a fan | `true` | no |

Returns: `{name, before, after, created, subdivided_edges, cuts, smoothness, new_inner_vertices}`.

Gotchas:

- Requires an EDGE selection. For a single control loop use `mesh_edge_ring_subdivide` instead — it keeps the mesh all-quads.
- `fractal` is a world-unit displacement, so its right magnitude depends on the object's size.

```python
mesh_subdivide(name="Plane", cuts=3, smoothness=0.0)
```

### mesh_symmetrize

Mirror one half of the mesh onto the other, discarding the old half.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `name` | string | MESH object name, from `get_scene_info` | — | yes |
| `axis` | string | which half is KEPT and copied across: `-X` (default), `+X`, `-Y`, … | `-X` | no |
| `threshold` | number | world units within which centre-line vertices are welded | `0.0001` | no |

Returns: `{name, before, after, delta, axis, bmesh_direction, threshold, used_selection}`.

Gotchas:

- The mirror plane passes through the object's LOCAL ORIGIN — not the world origin and not the bounding-box centre. Move the origin to the intended centre line first.
- `axis` names the half that is KEPT: `-X` keeps negative X and writes it onto +X.
- Symmetrizes the selection when there is one. A leftover selection silently narrows the operation — check `used_selection`.

```python
mesh_symmetrize(name="Body", axis="-X", threshold=0.0005)
```

### mesh_triangulate

Convert quads and n-gons into triangles.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `name` | string | MESH object name, from `get_scene_info` | — | yes |
| `quad_method` | string | how each quad is split | `BEAUTY` | no |
| `ngon_method` | string | how n-gons are split | `BEAUTY` | no |

Returns: `{name, before, after, created, converted_faces, quad_method, ngon_method, used_selection}`.

Gotchas:

- Do this LAST. Triangles break edge loops, so loop cuts, bevels and subdivision all behave badly afterwards.

```python
mesh_triangulate(name="Prop", quad_method="BEAUTY", ngon_method="BEAUTY")
```

---

## modifiers

18 tools. Non-destructive stack: add, discover every settable property, tune, reorder, apply, remove. `list_modifiers` before `set_modifier_prop`, always.

### add_armature

Add an Armature modifier — bind a mesh to a skeleton for posing.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `object` | string | object name; omit for the active object | — | yes |
| `armature` | string | ARMATURE object name | — | yes |
| `use_deform_preserve_volume` | boolean | dual-quaternion skinning; stops candy-wrapper collapse at twisted joints | none | no |
| `use_vertex_groups` | boolean | deform from vertex groups matching bone names | none | no |
| `use_bone_envelopes` | boolean | deform from bone envelope volumes instead of weights (crude) | none | no |
| `vertex_group` | string | vertex group masking the WHOLE modifier's influence (not the per-bone weights) | none | no |
| `name` | string | modifier name; omit for Blender's default "Armature" | none | no |
| `settings` | object | property name -> value, written onto the datablock after creation | none | no |

Returns: `{object, modifier}`.

Gotchas:

- This only creates the binding. Without vertex groups named after the bones nothing deforms.
- `use_deform_preserve_volume` (dual-quaternion skinning) stops the candy-wrapper collapse at shoulders and wrists. Off by default in Blender; usually worth turning on for characters.

```python
add_armature(object="Body", armature="Rig", use_deform_preserve_volume=True)
```

### add_boolean

Add a Boolean modifier — cut, fuse or intersect with another mesh.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `object` | string | object name; omit for the active object | — | yes |
| `target` | string | the cutter MESH object; it is NOT hidden or deleted for you | — | yes |
| `operation` | string | `DIFFERENCE`, `UNION`, `INTERSECT` | `DIFFERENCE` | no |
| `solver` | string | `FLOAT`, `EXACT`, `MANIFOLD` (`FAST` is accepted and translated to `FLOAT`) | none | no |
| `use_self` | boolean | let the EXACT solver handle self-intersecting input (slower) | none | no |
| `use_hole_tolerant` | boolean | let the EXACT solver cope with small holes (slower) | none | no |
| `name` | string | modifier name; omit for Blender's default "Boolean" | none | no |
| `settings` | object | property name -> value, written onto the datablock after creation | none | no |

Returns: `{object, modifier}` plus the resolved solver.

Gotchas:

- The cutter is NOT deleted or hidden — hide it yourself or it still renders.
- The fast solver is `FLOAT` in 5.x; `FAST` is accepted and translated. `MANIFOLD` is fastest but needs watertight, non-self-intersecting input on both meshes.
- A messy result usually means coplanar faces, flipped normals, or a non-closed cutter. Nudge the cutter so faces are not exactly coincident, or switch to `EXACT`.

```python
add_boolean(object="Wall", target="WindowCutter", operation="DIFFERENCE", solver="EXACT")
```

### add_data_transfer

Add a Data Transfer modifier — copy weights, colours, UVs or normals across meshes.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `object` | string | object name; omit for the active object | — | yes |
| `source` | string | MESH object to read the data from | — | yes |
| `data_types` | array | list of transfer tokens, e.g. `VGROUP_WEIGHTS`, `UV`, `CUSTOM_NORMAL` | none | no |
| `vert_mapping` | string | how a destination vertex finds source data | none | no |
| `loop_mapping` | string | mapping for face-corner data | none | no |
| `edge_mapping` | string | mapping for edge data | none | no |
| `poly_mapping` | string | mapping for face data | none | no |
| `mix_mode` | string | how incoming data combines with what is there | none | no |
| `mix_factor` | number | 0-1 blend against existing data | none | no |
| `max_distance` | number | world units; source geometry beyond this is ignored | none | no |
| `vertex_group` | string | vertex group on the DESTINATION limiting where data lands | none | no |
| `name` | string | modifier name; omit for Blender's default "DataTransfer" | none | no |
| `settings` | object | property name -> value, written onto the datablock after creation | none | no |

Returns: `{object, modifier}` with the per-domain flags this tool routed for you.

Gotchas:

- `object.vertex_group_transfer_weight` was REMOVED in 5.x. This modifier and `object.data_transfer` are the replacements.
- 5.2 stores transfer flags in four per-domain enums; this tool routes each token in `data_types` to the right one and enables that domain. There is no single `data_types` property on the modifier.
- `POLYINTERP_NEAREST` is the right `vert_mapping` for weights across differing topology.
- Nothing becomes real weights until you `apply_modifier`.

```python
add_data_transfer(object="Jacket", source="Body", data_types=["VGROUP_WEIGHTS"], vert_mapping="POLYINTERP_NEAREST")
```

### add_mirror

Add a Mirror modifier — symmetrical modelling from one half.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `object` | string | object name; omit for the active object | — | yes |
| `axis` | array | axes to mirror across, e.g. `["X"]` or `["X", "Z"]` — the object's LOCAL axes, about its origin | none | no |
| `use_clip` | boolean | stop vertices crossing the mirror plane while editing | none | no |
| `use_mirror_merge` | boolean | weld vertices that land on the mirror plane | none | no |
| `merge_threshold` | number | world units | none | no |
| `mirror_object` | string | object name whose transform defines the mirror plane | none | no |
| `bisect_axis` | array | axes to cut away before mirroring, same format as `axis` | none | no |
| `flip_axis` | array | axes whose bisect keeps the other side | none | no |
| `name` | string | modifier name; omit for Blender's default "Mirror" | none | no |
| `settings` | object | property name -> value, written onto the datablock after creation | none | no |

Returns: `{object, modifier}` with the resolved `use_axis` vector.

Gotchas:

- In 5.2 there is no `use_mirror_x`/`y`/`z` — the axes live in the 3-boolean vectors `use_axis`, `use_bisect_axis`, `use_bisect_flip_axis`. This tool writes them for you.
- Mirroring happens about the object's ORIGIN. If the origin is not on the seam, move it first.

```python
add_mirror(object="Body", axis=["X"], use_clip=True, merge_threshold=0.001)
```

### add_modifier

Add any modifier by its Blender type id, with optional settings in one call.

**vs:** the dedicated `add_*` tools — use this only for types with no dedicated tool (ARRAY, BEVEL, DISPLACE, WELD, SIMPLE_DEFORM, NODES …).

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `object` | string | object name; omit for the active object | — | yes |
| `type` | string | modifier type id, UPPERCASE: `SUBSURF`, `MIRROR`, `BEVEL`, `ARRAY`, `BOOLEAN`, `DECIMATE`, `SOLIDIFY`, `REMESH`, `WELD`, `DISPLACE`, `NODES`, … | — | yes |
| `name` | string | modifier name; omit for Blender's UI default | none | no |
| `settings` | object | property name -> value, written onto the datablock after creation | none | no |

Returns: `{object, modifier}` where `modifier` is `{name, type, index, show_viewport, …}`.

Gotchas:

- Prefer the dedicated `add_*` tools for the common types — they name their arguments so you cannot misspell a property.
- A wrong `type` is rejected with the full valid list; an unknown `settings` key raises with every settable property. A failed call is a legitimate way to discover names.
- The modifier lands at the END of the stack and bakes nothing until `apply_modifier`.

```python
add_modifier(object="Rail", type="ARRAY", settings={"count": 8, "relative_offset_displace": [1.05, 0, 0]})
```

### add_multires

Add a Multiresolution modifier — the base for multi-level sculpting.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `object` | string | object name; omit for the active object | — | yes |
| `render_levels` | integer | Multires/Subsurf level at render time | none | no |
| `quality` | integer | solver accuracy, 1-10 (default 3) | none | no |
| `use_creases` | boolean | respect edge crease weights | none | no |
| `name` | string | modifier name; omit for Blender's default "Multires" | none | no |
| `settings` | object | property name -> value, written onto the datablock after creation | none | no |

Returns: `{object, modifier}` — a fresh Multires has zero levels and changes nothing.

Gotchas:

- A fresh Multires has ZERO levels and changes nothing — follow with `multires_subdivide`.
- Multires must be first in the stack and Blender refuses to move anything above it.
- Dyntopo does not coexist with Multires.

```python
add_multires(object="Body", quality=3)
```

### add_remesh

Add a Remesh modifier — rebuild the topology as an even, uniform mesh.

**vs:** `voxel_remesh` — the modifier is non-destructive and previewable; the sculpt tool rewrites the mesh data immediately and is the one to use mid-sculpt.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `object` | string | object name; omit for the active object | — | yes |
| `mode` | string | `VOXEL` (default) or the octree modes `BLOCKS`, `SMOOTH`, `SHARP` | `VOXEL` | no |
| `voxel_size` | number | VOXEL mode only — voxel edge length in world units (default 0.1) | none | no |
| `octree_depth` | integer | resolution as a power of two | none | no |
| `adaptivity` | number | 0-1; >0 simplifies flat regions | none | no |
| `use_smooth_shade` | boolean | output smooth-shaded faces instead of flat | none | no |
| `name` | string | modifier name; omit for Blender's default "Remesh" | none | no |
| `settings` | object | property name -> value, written onto the datablock after creation | none | no |

Returns: `{object, modifier}`.

Gotchas:

- Throws away UVs, vertex groups and creases. Remesh first, unwrap and weight afterwards.
- Halving `voxel_size` roughly octuples memory. On a 2 m character, 0.02 is a reasonable sculpting density.

```python
add_remesh(object="Blob", mode="VOXEL", voxel_size=0.02, adaptivity=0.0)
```

### add_shrinkwrap

Add a Shrinkwrap modifier — pull a mesh onto the surface of another.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `object` | string | object name; omit for the active object | — | yes |
| `target` | string | surface MESH object to wrap onto | — | yes |
| `wrap_method` | string | projection strategy | none | no |
| `offset` | number | distance to hold off the target surface, world units | none | no |
| `wrap_mode` | string | how `offset` is read relative to the target | none | no |
| `vertex_group` | string | vertex group limiting the effect; weights act as per-vertex strength | none | no |
| `name` | string | modifier name; omit for Blender's default "Shrinkwrap" | none | no |
| `settings` | object | property name -> value, written onto the datablock after creation | none | no |

Returns: `{object, modifier}`.

Gotchas:

- `PROJECT` does nothing until at least one of `use_project_x/y/z` is switched on via `settings`.

```python
add_shrinkwrap(object="Jacket", target="Body", wrap_method="NEAREST_SURFACEPOINT", offset=0.005)
```

### add_solidify

Add a Solidify modifier — give a flat surface real thickness.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `object` | string | object name; omit for the active object | — | yes |
| `thickness` | number | shell thickness, world units (Blender default 0.01) | none | no |
| `offset` | number | -1..1 — where the original surface sits in the shell (-1 outer, 0 centred, 1 inner) | none | no |
| `even_thickness` | boolean | keep thickness even across corners (writes `use_even_offset`) | none | no |
| `use_rim` | boolean | fill the open boundary edges so the result is closed | none | no |
| `solidify_mode` | string | `EXTRUDE` (fast) or `NON_MANIFOLD` | none | no |
| `name` | string | modifier name; omit for Blender's default "Solidify" | none | no |
| `settings` | object | property name -> value, written onto the datablock after creation | none | no |

Returns: `{object, modifier}`.

Gotchas:

- `offset=-1` (Blender's default) keeps the original surface as the OUTER one; 0 centres it.
- `even_thickness` writes `use_even_offset`, which is the real property name.

```python
add_solidify(object="Cloak", thickness=0.01, offset=-1.0, even_thickness=True)
```

### add_subsurf

Add a Subdivision Surface modifier — the standard way to smooth a mesh.

**vs:** `add_multires` — subsurf smooths and stores no displacement; multires stores sculpted detail per level so you can fix broad form at level 1 without losing pores at level 5.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `object` | string | object name; omit for the active object | — | yes |
| `levels` | integer | subdivision levels | none | no |
| `render_levels` | integer | Multires/Subsurf level at render time | none | no |
| `use_limit_surface` | boolean | evaluate the exact limit surface so viewport and render agree | none | no |
| `subdivision_type` | string | `CATMULL_CLARK` or `SIMPLE` | none | no |
| `quality` | integer | solver accuracy, 1-10 (default 3) | none | no |
| `name` | string | modifier name; omit for Blender's default "Subdivision" | none | no |
| `settings` | object | property name -> value, written onto the datablock after creation | none | no |

Returns: `{object, modifier}` with the resolved levels.

Gotchas:

- Each level quadruples the face count: level 3 on a 10k-face mesh is 640k faces. Keep `levels` at 1-2 while working.
- Subsurf stores no per-level displacement. For sculptable multi-level detail use `add_multires`.

```python
add_subsurf(object="Head", levels=2, render_levels=3)
```

### apply_modifier

Bake a modifier permanently into the mesh and remove it from the stack.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `object` | string | object name; omit for the active object | — | yes |
| `modifier` | string | modifier name, or stack index as a number | — | yes |
| `single_user` | boolean | copy shared mesh data first so Blender will apply | `false` | no |
| `timeout` | number | seconds to wait for the bridge call | `300.0` | no |

Returns: `{object, modifier, type, mode_before, vertices_before, vertices_after, note}`. The previous mode is reported and NOT restored.

Gotchas:

- Blender refuses on a mesh that has shape keys — delete them or keep the modifier live.
- Multi-user data needs `single_user=true`; linked library data must be made local first.
- The object is taken out of Edit/Sculpt mode and the previous mode is NOT restored.
- Applying a modifier that is not at index 0 evaluates it as if it were first; the response says so in `note`.

```python
apply_modifier(object="Head", modifier="Subdivision", timeout=300.0)
```

### list_modifiers

Read an object's whole modifier stack, including every tunable property.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `object` | string | object name; omit for the active object | — | yes |
| `include_properties` | boolean | dump every settable property per modifier (large for Ocean/Fluid/Cloth) | `true` | no |
| `limit` | integer | cap on the returned list (counts stay exact) | `1000` | no |

Returns: `{object, object_type, count, modifiers, truncated}`; each modifier lists every SETTABLE property with its value, type and — for enums — the valid options in this build.

Gotchas:

- Read this before `set_modifier_prop` rather than guessing: several properties changed in 4.x/5.x. Mirror has no `use_mirror_x` — it has a 3-element `use_axis` vector. Boolean's fast solver is `FLOAT`, not `FAST`.
- `index` 0 is the top of the stack and is evaluated first.

```python
list_modifiers(object="Body", include_properties=True, limit=50)
```

### multires_apply_base

Reshape the Multires base mesh to match the sculpted result.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `object` | string | object name; omit for the active object | — | yes |
| `modifier` | string | modifier name, or stack index as a number | none | no |
| `timeout` | number | seconds to wait for the bridge call | `300.0` | no |

Returns: `{object, modifier, levels, sculpt_levels, render_levels, total_levels, vertices}`.

Gotchas:

- Moves the level-0 cage to follow the sculpt; the sculpted levels are kept.
- The underlying 5.2 operator is `object.multires_base_apply` (words in that order); `object.multires_apply_base` does not exist.

```python
multires_apply_base(object="Body", timeout=300.0)
```

### multires_subdivide

Add subdivision levels to a Multires modifier.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `object` | string | object name; omit for the active object | — | yes |
| `levels` | integer | levels to ADD in this call | `1` | no |
| `mode` | string | `CATMULL_CLARK`, `SIMPLE`, `LINEAR` | `CATMULL_CLARK` | no |
| `modifier` | string | modifier name, or stack index as a number | none | no |
| `timeout` | number | seconds to wait for the bridge call | `300.0` | no |

Returns: `{object, modifier, levels, sculpt_levels, render_levels, total_levels, vertices}`.

Gotchas:

- Each level quadruples vertices and memory. Level 6 on a 5k-face base is roughly 20 million faces — enough to stall Blender. Go one level at a time.
- `LINEAR` subdivides the displaced surface, preserving the current sculpted shape exactly.

```python
multires_subdivide(object="Body", levels=1, mode="CATMULL_CLARK", timeout=300.0)
```

### multires_unsubdivide

Rebuild a *lower* Multires level by reversing one subdivision.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `object` | string | object name; omit for the active object | — | yes |
| `modifier` | string | modifier name, or stack index as a number | none | no |
| `timeout` | number | seconds to wait for the bridge call | `300.0` | no |

Returns: `{object, modifier, levels, sculpt_levels, render_levels, total_levels, vertices}`.

Gotchas:

- Only works when the current topology really is a subdivision of a coarser mesh; otherwise Blender reports "No valid subdivisions found to rebuild a lower level" and nothing changes.
- This is NOT the inverse of `multires_subdivide` — to drop levels you just added, lower `levels` with `set_modifier_prop`.

```python
multires_unsubdivide(object="Body")
```

### remove_modifier

Delete a modifier, discarding its effect (the opposite of `apply_modifier`).

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `object` | string | object name; omit for the active object | — | yes |
| `modifier` | string | modifier name, or stack index as a number | — | yes |

Returns: `{object, removed, type, remaining}`.

Gotchas:

- Discards the modifier's effect entirely — the opposite of `apply_modifier`. Nothing is baked into the mesh.

```python
remove_modifier(object="Wall", modifier="Boolean")
```

### reorder_modifier

Move a modifier to a different position in the stack.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `object` | string | object name; omit for the active object | — | yes |
| `modifier` | string | modifier name, or stack index as a number | — | yes |
| `index` | integer | target stack position, 0-based; negative counts from the end | — | yes |

Returns: `{object, modifier, from_index, to_index, stack}`.

Gotchas:

- Stack order is evaluation order. Mirror BEFORE Subsurf gives a clean seam; the other way round does not.
- Blender refuses to move anything above Multires ("Cannot move above a modifier requiring original data"); the refusal is surfaced verbatim.

```python
reorder_modifier(object="Body", modifier="Mirror", index=0)
```

### set_modifier_prop

Change settings on an existing modifier.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `object` | string | object name; omit for the active object | — | yes |
| `modifier` | string | modifier name, or stack index as a number | — | yes |
| `prop` | string | property name, or input socket name/index | none | no |
| `value` | any | distances = world units, angles = RADIANS, factors = 0-1; pointers take an object NAME string | none | no |
| `settings` | object | property name -> value, written onto the datablock after creation | none | no |

Returns: `{object, modifier, type, set}`.

Gotchas:

- Pointer properties (Boolean.object, Shrinkwrap.target, Mirror.mirror_object, Armature.object) take an object NAME string, or null to clear.
- Distances are world units, angles RADIANS, factors 0-1.

```python
set_modifier_prop(object="Body", modifier="Mirror", settings={"use_axis": [True, False, False], "use_clip": True})
```

---

## sculpt

27 tools. Brush strokes, masks, face sets, filters and remeshing. Everything marked GUI-only would SEGFAULT under `--background`, so those tools refuse before the operator is ever invoked. `voxel_remesh`, `quadriflow_remesh`, `mask_from_selection` and `get_sculpt_state` do work headless.

### clear_mask

Clear the sculpt mask so the whole surface is editable again.

**GUI Blender only** — this walks the PBVH and would SEGFAULT under
`blender --background`, so the handler refuses before invoking the operator.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `value` | number | value to flood the mask with, 0-1 | `0.0` | no |
| `object` | string | object name; omit for the active object | none | no |

Returns: `{cleared}`.

Gotchas:

- Pass `value=1.0` to flood-mask everything instead of clearing.

```python
clear_mask(value=0.0, object="Body")
```

### dyntopo_disable

Turn dynamic topology off, keeping the current geometry.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `object` | string | object name; omit for the active object | none | no |

Returns: `{enabled}`.

Gotchas:

- Keeps the current geometry; only the dynamic subdivision stops. The vertex count you ended up with is what you keep.

```python
dyntopo_disable(object="Body")
```

### dyntopo_enable

Turn on dynamic topology — the mesh subdivides under the brush as you sculpt.

**vs:** `voxel_remesh` — dyntopo adds detail only where you brush and discards vertex colours/multires; voxel rebuilds the whole surface at one density.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `detail` | number | `detail_size` for RELATIVE, `constant_detail_resolution` for CONSTANT, a percentage for BRUSH | none | no |
| `mode` | string | `RELATIVE` (screen-relative), `CONSTANT` (fixed world resolution), `BRUSH`, `MANUAL` | `RELATIVE` | no |
| `refine_method` | string | `SUBDIVIDE`, `COLLAPSE`, `SUBDIVIDE_COLLAPSE` | none | no |
| `object` | string | object name; omit for the active object | none | no |

Returns: `{enabled, mode, detail_size, constant_detail_resolution, detail_percent, refine_method}`.

Gotchas:

- Discards vertex colours and multires, and does not coexist with a Multires modifier.
- `sculpt.dynamic_topology_toggle()` takes no arguments; detail is configured through `detail_size` / `constant_detail_resolution` / `detail_type_method` first.

```python
dyntopo_enable(detail=12.0, mode="RELATIVE", refine_method="SUBDIVIDE_COLLAPSE", object="Body")
```

### dyntopo_flood_fill

Re-tessellate the whole mesh to the current dyntopo detail level.

**GUI Blender only** — this walks the PBVH and would SEGFAULT under
`blender --background`, so the handler refuses before invoking the operator.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `object` | string | object name; omit for the active object | none | no |

Returns: `{object, vertices_before, vertices_after}`.

Gotchas:

- Requires dyntopo to already be enabled.
- Applies the detail setting everywhere at once, which can multiply the vertex count in a single call — check `vertices_after`.

```python
dyntopo_flood_fill(object="Body")
```

### enter_sculpt

Make an object active and switch to Sculpt Mode.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `object` | string | object name; omit for the active object | none | no |

Returns: `{object, mode, vertices}`.

Gotchas:

- Sculpting needs a MESH — convert curves and text first.
- A low-poly primitive sculpts badly. `voxel_remesh` it first.

```python
enter_sculpt(object="Body")
```

### face_set_visibility

Show or hide face sets: TOGGLE, SHOW_ACTIVE or HIDE_ACTIVE.

**GUI Blender only** — this walks the PBVH and would SEGFAULT under
`blender --background`, so the handler refuses before invoking the operator.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `mode` | string | `TOGGLE`, `SHOW_ACTIVE`, `HIDE_ACTIVE` | `TOGGLE` | no |
| `active_face_set` | integer | face-set id | none | no |
| `object` | string | object name; omit for the active object | none | no |

Returns: `{mode}`.

Gotchas:

- Hidden geometry is excluded from sculpting entirely. Call `reveal_all` when finished — forgetting is the classic reason a brush looks broken.

```python
face_set_visibility(mode="HIDE_ACTIVE", object="Body")
```

### face_sets_create

Create a face set from the mask, visible geometry, all, or selection.

**GUI Blender only** — this walks the PBVH and would SEGFAULT under
`blender --background`, so the handler refuses before invoking the operator.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `mode` | string | `MASKED`, `VISIBLE`, `ALL`, `SELECTION` | `MASKED` | no |
| `object` | string | object name; omit for the active object | none | no |

Returns: `{created_from}`.

Gotchas:

- Face sets are named regions you can hide or isolate — the practical way to work on one limb without disturbing the rest.
- `MASKED` turns your current mask into a face set, which is the normal route from a selection to an isolatable region.

```python
face_sets_create(mode="MASKED", object="Body")
```

### face_sets_init

Generate face sets automatically from mesh structure.

**GUI Blender only** — this walks the PBVH and would SEGFAULT under
`blender --background`, so the handler refuses before invoking the operator.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `mode` | string | `LOOSE_PARTS`, `MATERIALS`, `NORMALS`, `UV_SEAMS`, `CREASES`, `BEVEL_WEIGHT`, `SHARP_EDGES`, `FACE_SET_BOUNDARIES` | `LOOSE_PARTS` | no |
| `threshold` | number | angle sensitivity for `NORMALS` mode | none | no |
| `object` | string | object name; omit for the active object | none | no |

Returns: `{initialized_from}`.

Gotchas:

- `LOOSE_PARTS` is the usual choice right after joining objects.

```python
face_sets_init(mode="LOOSE_PARTS", object="Body")
```

### get_sculpt_state

Current brush, size/strength, dyntopo, symmetry, mask and face-set state.

No parameters.

Returns: `{mode, object, brush, unified, symmetry, dyntopo, has_mask, has_face_sets}`. `unified.use_unified_size` tells you where a size change has to be written.

Gotchas:

- Read this before changing settings: `unified.use_unified_size` decides whether a size change has to go to `unified_paint_settings.size` instead of `brush.size`.

```python
get_sculpt_state()
```

### invert_mask

Invert the sculpt mask — protected becomes editable and vice versa.

**GUI Blender only** — this walks the PBVH and would SEGFAULT under
`blender --background`, so the handler refuses before invoking the operator.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `object` | string | object name; omit for the active object | none | no |

Returns: `{inverted}`.

Gotchas:

- Masked geometry is PROTECTED. Mask the region you want to keep safe, or mask the region you care about and invert.

```python
invert_mask(object="Body")
```

### mask_box

Mask a rectangular screen region.

**GUI Blender only** — this walks the PBVH and would SEGFAULT under
`blender --background`, so the handler refuses before invoking the operator.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `xmin` | integer | region pixels, origin BOTTOM-LEFT | — | yes |
| `xmax` | integer | region pixels, origin BOTTOM-LEFT | — | yes |
| `ymin` | integer | region pixels, origin BOTTOM-LEFT | — | yes |
| `ymax` | integer | region pixels, origin BOTTOM-LEFT | — | yes |
| `mode` | string | `VALUE`, `VALUE_INVERSE`, `INVERT` | `VALUE` | no |
| `value` | number | mask value 0-1 | `1.0` | no |
| `front_faces_only` | boolean | mask only front-facing geometry | `false` | no |
| `object` | string | object name; omit for the active object | none | no |

Returns: `{masked, region}` — `region` is the viewport size the pixel box was read against.

Gotchas:

- Coordinates are REGION PIXELS with the origin BOTTOM-LEFT. A `viewport_screenshot` has a top-left origin, so you must flip y.
- Masked areas are PROTECTED from sculpting. To work only inside the box, mask it then `invert_mask`.

```python
mask_box(xmin=120, xmax=640, ymin=200, ymax=560, mode="VALUE", value=1.0)
```

### mask_by_cavity

Mask by surface cavity — automatically finds creases and crevices.

**GUI Blender only** — this walks the PBVH and would SEGFAULT under
`blender --background`, so the handler refuses before invoking the operator.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `mix_mode` | string | how incoming data combines with what is there | none | no |
| `mix_factor` | number | 0-1 blend against existing data | none | no |
| `object` | string | object name; omit for the active object | none | no |

Returns: `{masked, applied}`.

Gotchas:

- The aging/wear pass: mask the cavities, `invert_mask`, then sculpt only the raised areas — or skip the invert to work only in the crevices.

```python
mask_by_cavity(mix_mode="MIX", mix_factor=1.0, object="Body")
```

### mask_filter

Grow, shrink, smooth or sharpen the existing mask.

**GUI Blender only** — this walks the PBVH and would SEGFAULT under
`blender --background`, so the handler refuses before invoking the operator.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `filter_type` | string | `GROW`, `SHRINK`, `SMOOTH`, `SHARPEN`, `CONTRAST_INCREASE`, `CONTRAST_DECREASE` | `SMOOTH` | no |
| `iterations` | integer | repeat count | `1` | no |
| `auto_iteration_count` | boolean | let Blender pick the iteration count | `false` | no |
| `object` | string | object name; omit for the active object | none | no |

Returns: `{filter_type, iterations}`.

Gotchas:

- `SMOOTH` after `mask_from_selection` softens hard mask borders so sculpting blends instead of leaving a visible step.
- `GROW` with `iterations=5` spreads far further than with 1.

```python
mask_filter(filter_type="SMOOTH", iterations=3, object="Body")
```

### mask_from_selection

Build the sculpt mask from the mesh's selected vertices.

**vs:** `mask_box` — mask_from_selection works HEADLESS and is precise; mask_box needs a GUI and pixel coordinates.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `value` | number | mask value 0-1 | `1.0` | no |
| `invert` | boolean | mask the UNSELECTED vertices instead — usually what you want | `false` | no |
| `object` | string | object name; omit for the active object | none | no |

Returns: `{object, masked_vertices, total_vertices}`.

Gotchas:

- The only mask tool that works HEADLESS — it writes the mask attribute directly instead of running a sculpt-session operator.
- `invert=true` masks the UNselected vertices, which is usually what you want: it protects everything except your selection.

```python
mask_from_selection(value=1.0, invert=True, object="Body")
```

### multires_set_level

Set which Multires subdivision level you sculpt and display at.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `sculpt_levels` | integer | Multires level used while sculpting | none | no |
| `levels` | integer | subdivision levels | none | no |
| `render_levels` | integer | Multires/Subsurf level at render time | none | no |
| `object` | string | object name; omit for the active object | none | no |

Returns: `{object, modifier, levels, sculpt_levels, render_levels, total_levels}`.

Gotchas:

- Requires a Multires modifier that has actually been subdivided; a level above `total_levels` is refused.
- Sculpt large forms at level 1-2 and fine detail at the top level — that is the whole point of Multires.

```python
multires_set_level(sculpt_levels=2, levels=2, render_levels=3, object="Body")
```

### quadriflow_remesh

Retopologise into clean, evenly-flowing quads. Slow but high quality.

**vs:** `voxel_remesh` — quadriflow last, once the form is final; voxel every time strokes have stretched the topology.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `target_faces` | integer | desired quad count | `5000` | no |
| `mode` | string | `FACES` (use `target_faces`), `RATIO` (`target_ratio`), `EDGE` (`target_edge_length`) | `FACES` | no |
| `target_ratio` | number | fraction of the current face count | none | no |
| `target_edge_length` | number | world units | none | no |
| `preserve_sharp` | boolean | keep sharp edges — important for hard-surface models | none | no |
| `preserve_boundary` | boolean | keep open boundaries in place | none | no |
| `smooth_normals` | boolean | output smooth-shaded normals | none | no |
| `use_mesh_symmetry` | boolean | remesh symmetrically | none | no |
| `seed` | integer | RNG seed for the quad layout | none | no |
| `object` | string | object name; omit for the active object | none | no |

Returns: `{object, mode, vertices_before, vertices_after, faces_before, faces_after}`.

Gotchas:

- Slow: tens of seconds to minutes. Use `voxel_remesh` during sculpting and this only for the final production mesh.
- Fails on non-manifold meshes — `voxel_remesh` first if it errors.

```python
quadriflow_remesh(target_faces=8000, mode="FACES", preserve_sharp=True, object="Body")
```

### radial_strokes

Strokes radiating out from a centre point, evenly spaced.

**GUI Blender only** — this walks the PBVH and would SEGFAULT under
`blender --background`, so the handler refuses before invoking the operator.

**vs:** `sculpt_symmetry` — radial symmetry no longer exists in 5.x, so this is the only way to get evenly spaced repeated strokes around a hub.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `center` | array | `[x, y, z]` world units | — | yes |
| `radius` | number | stroke length in WORLD units (not pixels) | — | yes |
| `count` | integer | number of strokes around the circle | `6` | no |
| `steps` | integer | sample count along the path | `8` | no |
| `axis` | string | `X`, `Y` or `Z` — the axis the circle is perpendicular to | `Z` | no |
| `inward` | boolean | stroke from the rim toward the centre (matters for Snake Hook and Grab) | `false` | no |
| `space` | string | `OBJECT` or `WORLD` for the coordinates above | `OBJECT` | no |
| `mode` | string | `NORMAL` or `INVERT` | `NORMAL` | no |
| `size_px` | integer | brush radius in SCREEN PIXELS (not world units) | none | no |
| `return_screenshot` | boolean | return a viewport image alongside the result | `true` | no |
| `object` | string | object name; omit for the active object | none | no |

Returns: A summary `{object, strokes, points_applied, vertices}` AND a viewport PNG.

Gotchas:

- `radius` is WORLD units while `size_px` is SCREEN PIXELS — the one place the two units sit side by side.
- This is the practical replacement for radial symmetry, which 5.x removed.
- `inward` matters for directional brushes such as Snake Hook and Grab.

```python
radial_strokes(center=[0, 0, 1.1], radius=0.35, count=8, steps=10, axis="Z", size_px=45)
```

### reveal_all

Unhide all geometry hidden by face-set visibility.

**GUI Blender only** — this walks the PBVH and would SEGFAULT under
`blender --background`, so the handler refuses before invoking the operator.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `object` | string | object name; omit for the active object | none | no |

Returns: `{revealed}`.

Gotchas:

- Call this whenever you finish with `face_set_visibility`. Forgotten hidden geometry is the usual reason a brush appears to do nothing.

```python
reveal_all(object="Body")
```

### sculpt_list_brushes

Every available sculpt brush asset name, plus the friendly-name aliases.

No parameters.

Returns: `{brushes, aliases}` — the authoritative asset names plus the friendly-name alias map.

Gotchas:

- Blender 5.x brushes are ASSETS with compound names. When a friendly name is not matching, this is the authoritative list.

```python
sculpt_list_brushes()
```

### sculpt_mesh_filter

Apply a filter to the whole mesh at once, no brushing.

**GUI Blender only** — this walks the PBVH and would SEGFAULT under
`blender --background`, so the handler refuses before invoking the operator.

**vs:** brush strokes — the filter changes the WHOLE mesh (or the unmasked part) at once; strokes change one place. Mask, then filter, is how you inflate exactly one region.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `type` | string | `SMOOTH`, `SCALE`, `INFLATE`, `SPHERE`, `RANDOM`, `RELAX`, `RELAX_FACE_SETS`, `SURFACE_SMOOTH`, `SHARPEN`, `ENHANCE_DETAILS`, `ERASE_DISPLACEMENT` | `SMOOTH` | no |
| `strength` | number | effect amount; NEGATIVE reverses it (INFLATE at -1 deflates) | `1.0` | no |
| `iterations` | integer | repeat count | `1` | no |
| `deform_axis` | string | axis restriction for the filter | none | no |
| `orientation` | string | filter orientation | none | no |
| `return_screenshot` | boolean | return a viewport image alongside the result | `false` | no |
| `object` | string | object name; omit for the active object | none | no |

Returns: A summary `{type, strength, iterations, object}` and, when `return_screenshot` is true, a viewport PNG.

Gotchas:

- Respects the current mask, so this is how you inflate exactly one region: mask everything else, then `INFLATE`.
- Negative `strength` reverses the filter (INFLATE at -1 deflates).
- `strength` above ~1 with several iterations can blow the mesh apart. Step up gradually and screenshot.

```python
sculpt_mesh_filter(type="INFLATE", strength=0.35, iterations=2, return_screenshot=True)
```

### sculpt_set_brush

Activate a sculpt brush and set its parameters.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `name` | string | brush name; friendly aliases are resolved (`Inflate` → `Inflate/Deflate`) | none | no |
| `size_px` | integer | brush radius in SCREEN PIXELS (not world units) | none | no |
| `strength` | number | 0-1 | none | no |
| `direction` | string | `ADD` or `SUBTRACT` — this is how you carve instead of build | none | no |
| `hardness` | number | 0-1 falloff sharpness at the brush edge | none | no |
| `auto_smooth` | number | 0-1 smoothing blended into every dab | none | no |
| `normal_radius` | number | 0-1 | none | no |
| `falloff_shape` | string | `SPHERE` (default) or `PROJECTED` | none | no |
| `use_frontface` | boolean | affect only front-facing geometry | none | no |
| `object` | string | object name; omit for the active object | none | no |

Returns: `{brush, resolved_from, size_px, size_written_to, strength, strength_written_to, …}`. `size_written_to` is either `brush.size` or `unified_paint_settings.size`.

Gotchas:

- `size_px` is SCREEN PIXELS, so the world footprint depends on viewport zoom. 40-80 is a normal working range.
- Friendly names are aliased: `Inflate` → `Inflate/Deflate`, `Crease` → `Crease Sharp`, `Scrape` → `Scrape/Fill`, `Fill` → `Fill/Deepen`, `Pinch` → `Pinch/Magnify`, `Elastic Deform` → `Elastic Grab`, `Flatten` → `Flatten/Contrast`. An unknown name returns the full valid list.
- Writing `brush.size` is SILENTLY IGNORED while unified size is on. The tool writes whichever field is authoritative and reports it in `size_written_to`.
- `direction="SUBTRACT"` is how you carve instead of build.

```python
sculpt_set_brush(name="Clay Strips", size_px=55, strength=0.5, direction="ADD")
```

### sculpt_stroke

Apply a brush stroke through a list of 3D points.

**GUI Blender only** — this walks the PBVH and would SEGFAULT under
`blender --background`, so the handler refuses before invoking the operator.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `points` | array | ≥2 entries of `{"location": [x,y,z], "pressure": 0-1, "size": px}` | — | yes |
| `space` | string | `OBJECT` or `WORLD` for the coordinates above | `OBJECT` | no |
| `mode` | string | `NORMAL`, or `INVERT` to flip the brush for this stroke | `NORMAL` | no |
| `size_px` | integer | brush radius in SCREEN PIXELS (not world units) | none | no |
| `return_screenshot` | boolean | return a viewport image alongside the result | `true` | no |
| `object` | string | object name; omit for the active object | none | no |

Returns: A summary `{object, points_applied, vertices, dropped_points}` AND a viewport PNG when `return_screenshot` is true.

Gotchas:

- Points that project behind the camera come back in `dropped_points` rather than being silently skipped. There is no orbit tool: `add_camera` at the angle you need and screenshot with `camera_view=True`, or ask the user to orbit.
- The real brush engine runs, so the active brush, strength, symmetry and dyntopo settings all apply.
- `size_px` overrides the brush size for this stroke only, in SCREEN PIXELS.

```python
sculpt_stroke(points=[{"location": [0, -0.9, 0.35], "pressure": 0.6}, {"location": [0, -0.2, 0.55], "pressure": 1.0}], space="OBJECT", size_px=50)
```

### sculpt_symmetry

Set mirror symmetry axes for sculpting.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `x` | boolean | mirror across local X | none | no |
| `y` | boolean | mirror across local Y | none | no |
| `z` | boolean | mirror across local Z | none | no |
| `feather` | boolean | soften the symmetry seam | none | no |
| `radial_counts` | integer | accepted but IGNORED — radial symmetry was removed in 5.x | none | no |

Returns: `{x, y, z, feather, radial_supported}` plus a `note` when `radial_counts` was ignored.

Gotchas:

- Mirroring is about the object's LOCAL origin. An off-centre origin mirrors to the wrong place — check `get_object_info` first.
- Radial symmetry was REMOVED in 5.x. `radial_counts` is accepted, reported as `radial_supported: false`, and never silently applied. Use `radial_strokes` instead.

```python
sculpt_symmetry(x=True, y=False, z=False, feather=True)
```

### stroke_curve

Smooth brush stroke along a spline through control points.

**GUI Blender only** — this walks the PBVH and would SEGFAULT under
`blender --background`, so the handler refuses before invoking the operator.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `control_points` | array | two or more `[x, y, z]` in world units | — | yes |
| `steps` | integer | sample count along the path | `24` | no |
| `space` | string | `OBJECT` or `WORLD` for the coordinates above | `OBJECT` | no |
| `mode` | string | `NORMAL` or `INVERT` | `NORMAL` | no |
| `size_px` | integer | brush radius in SCREEN PIXELS (not world units) | none | no |
| `return_screenshot` | boolean | return a viewport image alongside the result | `true` | no |
| `object` | string | object name; omit for the active object | none | no |

Returns: A summary `{object, points_applied, vertices, dropped_points}` AND a viewport PNG.

Gotchas:

- The spline passes THROUGH every control point (Catmull-Rom), so place them on the ridge you want, not around it.

```python
stroke_curve(control_points=[[0, -0.8, 0.3], [0, -0.2, 0.62], [0, 0.4, 0.4]], steps=24, size_px=45)
```

### stroke_line

Straight brush stroke from point a to point b.

**GUI Blender only** — this walks the PBVH and would SEGFAULT under
`blender --background`, so the handler refuses before invoking the operator.

**vs:** `stroke_curve` vs `stroke_on_surface` — line for a straight ridge or crease; curve for organic flow through known 3D points; on_surface when you would rather draw on the screenshot than compute coordinates.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `a` | array | `[x, y, z]` start point, world units | — | yes |
| `b` | array | `[x, y, z]` end point, world units | — | yes |
| `steps` | integer | sample count along the path | `12` | no |
| `space` | string | `OBJECT` or `WORLD` for the coordinates above | `OBJECT` | no |
| `mode` | string | `NORMAL` or `INVERT` | `NORMAL` | no |
| `size_px` | integer | brush radius in SCREEN PIXELS (not world units) | none | no |
| `return_screenshot` | boolean | return a viewport image alongside the result | `true` | no |
| `object` | string | object name; omit for the active object | none | no |

Returns: A summary `{object, points_applied, vertices, dropped_points}` AND a viewport PNG.

Gotchas:

- Below ~8 `steps` the stroke reads as separate dabs rather than a continuous line.
- Coordinates are world/object units; `size_px` is screen pixels. Do not mix them up.

```python
stroke_line(a=[0, -0.9, 0.35], b=[0, 0.2, 0.55], steps=14, size_px=55)
```

### stroke_on_surface

Draw a stroke by tracing a 2D path across the viewport.

**GUI Blender only** — this walks the PBVH and would SEGFAULT under
`blender --background`, so the handler refuses before invoking the operator.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `view_path_2d` | array | list of `[x, y]` viewport points | — | yes |
| `normalized` | boolean | coordinates are 0-1 fractions of the viewport; false = raw region pixels | `true` | no |
| `mode` | string | `NORMAL` or `INVERT` | `NORMAL` | no |
| `size_px` | integer | brush radius in SCREEN PIXELS (not world units) | none | no |
| `return_screenshot` | boolean | return a viewport image alongside the result | `true` | no |
| `object` | string | object name; omit for the active object | none | no |

Returns: A summary `{object, points_applied, vertices, missed_rays}` AND a viewport PNG.

Gotchas:

- Much easier than computing 3D coordinates: take a `viewport_screenshot` first, then aim in 0-1 viewport fractions.
- Rays that miss the model are reported in `missed_rays`.

```python
stroke_on_surface(view_path_2d=[[0.42, 0.55], [0.5, 0.62], [0.58, 0.55]], normalized=True, size_px=40)
```

### voxel_remesh

Rebuild the mesh as an even voxel grid. The core sculpting workflow step.

**vs:** `quadriflow_remesh` vs `dyntopo_enable` — voxel for uniform density mid-sculpt (seconds, welds separate blobs, all-quad-ish but topology-blind); quadriflow for the final production mesh (minutes, clean flowing quads, fails on non-manifold); dyntopo when you do not yet know where detail is needed and want it added only under the brush.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `voxel_size` | number | cell size in WORLD units — cost scales roughly quadratically | `0.05` | no |
| `preserve_volume` | boolean | keep the original volume rather than shrinking | `true` | no |
| `adaptivity` | number | 0-1; >0 simplifies flat regions | none | no |
| `preserve_attributes` | boolean | carry mesh attributes through the remesh where possible | none | no |
| `fix_poles` | boolean | reduce pole vertices in the voxel output | none | no |
| `object` | string | object name; omit for the active object | none | no |

Returns: `{object, voxel_size, vertices_before, vertices_after, faces_before, faces_after}`.

Gotchas:

- DESTROYS UVs and vertex groups. Remesh BEFORE unwrapping or weighting, never after.
- Cost scales roughly quadratically. On a 2-unit object, 0.05 is a sane start, 0.02 is dense, 0.01 may produce millions of vertices and take minutes.
- It welds intersecting parts into one surface — that is how you build a creature from separate blobs.
- `object.voxel_remesh()` takes no arguments; the handler writes `mesh.remesh_voxel_size` and friends first, and drops out of Sculpt Mode headless because the operator crashes there.

```python
voxel_remesh(voxel_size=0.02, preserve_volume=True, object="Body")
```

---

## weights

27 tools. Vertex groups and skin weights. The data-API tools (`assign_weights`, `set_weights`, `get_weights`) are exact and headless — prefer them. Brush and gradient tools need a GUI and are a last resort.

### assign_weights

Write one weight value to a set of vertices. The workhorse write tool.

**vs:** `set_weights` vs `brush_stroke` — assign when every target vertex gets the SAME value; set_weights when each vertex needs its own; brush_stroke only when you genuinely want painterly falloff and have a GUI.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `mesh` | string | MESH object name | — | yes |
| `group` | string | vertex group name — must already exist | — | yes |
| `verts_spec` | string|array|object | `ALL`, `SELECTED`, a list of vertex indices, or `{min, max, space}` box in world units | `ALL` | no |
| `weight` | number | 0-1 (not 0-100) | `1.0` | no |
| `mode` | string | `REPLACE`, `ADD`, `SUBTRACT` | `REPLACE` | no |

Returns: `{mesh, group, mode, weight, verts_spec, vertices_written}`.

Gotchas:

- `weight` is 0.0-1.0, not 0-100.
- `SUBTRACT` down to 0 still leaves the vertex ASSIGNED with weight 0. Use `set_weights(remove_zero=true)` or `clean_weights` to truly unassign it.
- Pure data API — no brush, no viewport, works under `--background`.

```python
assign_weights(mesh="Body", group="hand.L", verts_spec={"min": [0.35, -0.1, 0.9], "max": [0.6, 0.1, 1.1], "space": "LOCAL"}, weight=1.0, mode="REPLACE")
```

### auto_weights

Bind a mesh to an armature and generate its weights. Start of every rig.

**vs:** `parent_mesh_to_armature` — same bind, different framing: `auto_weights` also offers `reuse_binding=true` to re-solve weights on an already-bound mesh without touching parenting or modifiers.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `mesh` | string | MESH object name | — | yes |
| `armature` | string | ARMATURE object name | — | yes |
| `method` | string | `AUTOMATIC` (bone heat), `ENVELOPE`, `EMPTY` | `AUTOMATIC` | no |
| `reuse_binding` | boolean | re-solve weights on an already-bound mesh, leaving parenting and modifiers alone | `false` | no |
| `keep_transform` | boolean | preserve the child's WORLD position when parenting | `false` | no |
| `xmirror` | boolean | mirror the generated weights across X for a symmetric character | `false` | no |
| `timeout` | number | seconds to wait for the bridge call | `180.0` | no |

Returns: `{mesh, armature, method, operator, vertex_groups, deform_bones, bones_without_group, modifiers}`. `bones_without_group` is your first check that the bind worked.

Gotchas:

- `AUTOMATIC` fails on non-manifold or self-intersecting geometry with "Bone Heat Weighting: failed to find solution". `ENVELOPE` still works there.
- Only bones with `use_deform` get a group. Check `bones_without_group` first, then `report_unweighted_verts`.
- `reuse_binding=true` re-runs the solver on an already-bound mesh via `paint.weight_from_bones`, leaving parenting and modifiers alone. It requires an existing Armature modifier and does not support `EMPTY`.

```python
auto_weights(mesh="Body", armature="Rig", method="AUTOMATIC", timeout=180.0)
```

### brush_stroke

Drag the weight brush along a path of 3D points.

**GUI Blender only** — needs a real 3D Viewport; refuses under
`blender --background`.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `mesh` | string | MESH object name | — | yes |
| `points` | array | ordered `[[x, y, z], …]`, at least two, all in front of the viewport camera | — | yes |
| `group` | string | vertex group name; omit for the active group | none | no |
| `weight` | number | 0-1 (not 0-100) | `1.0` | no |
| `radius_px` | number | brush radius in SCREEN PIXELS (not world units) | `50.0` | no |
| `strength` | number | 0-1 | none | no |
| `mode` | string | `NORMAL` paints toward `weight`; `INVERT` paints away from it | `NORMAL` | no |
| `space` | string | `WORLD` (default) or `LOCAL` | `WORLD` | no |
| `pressure` | number | simulated stylus pressure, 0-1 | `1.0` | no |

Returns: `{mesh, group, points, weight, radius_px}`.

Gotchas:

- Use this LAST. `assign_weights` and `set_weights` are exact, headless and reproducible; a brush stroke depends on camera, occlusion and falloff.
- `radius_px` is SCREEN PIXELS — the world footprint changes with camera distance.
- Only geometry facing the viewport is affected.

```python
brush_stroke(mesh="Body", points=[[0.3, 0, 1.2], [0.45, 0, 1.15]], group="upper_arm.L", weight=1.0, radius_px=50.0, strength=0.6)
```

### clean_weights

Unassign weights below a threshold. Removes the dust before export.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `mesh` | string | MESH object name | — | yes |
| `threshold` | number | weights at or below this are unassigned, 0-1 | `0.01` | no |
| `group` | string | vertex group name; omit for the active group | none | no |
| `keep_single` | boolean | never strand a vertex with zero groups — keep its strongest influence | `false` | no |
| `group_select_mode` | string | `ACTIVE`, `ALL` or `BONE_DEFORM` | `ACTIVE` | no |
| `only_selected` | boolean | restrict to the mesh's current vertex selection (runs in Edit mode) | `false` | no |

Returns: `{mesh, group, mode_used, only_selected}`.

Gotchas:

- Auto weights leave thousands of near-zero influences that bloat exports and push real influences out of the top-4 on `limit_total`. Run this FIRST.
- Turn on `keep_single` when cleaning `ALL` on a rigged mesh, or you strand vertices at the origin.

```python
clean_weights(mesh="Body", threshold=0.01, group_select_mode="ALL", keep_single=True)
```

### enter_weight_paint

Put a mesh into Weight Paint mode, optionally with its rig posed for bone picking.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `mesh` | string | MESH object name | — | yes |
| `armature_for_posing` | string | ARMATURE object name to drop into Pose Mode | none | no |

Returns: `{mesh, mode, armature, armature_mode, vertex_groups, active_group}`.

Gotchas:

- Almost nothing in this module needs Weight Paint mode. `assign_weights` / `set_weights` / `get_weights` write and read `vertex_groups` directly from any mode and work headless.
- Leaving Weight Paint mode drops the armature back to Object mode.

```python
enter_weight_paint(mesh="Body", armature_for_posing="Rig")
```

### get_weights

Read a page of weights from one group. Chunked — safe on dense meshes.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `mesh` | string | MESH object name | — | yes |
| `group` | string | vertex group name; omit for the active group | — | yes |
| `offset` | integer | vertex index to start scanning from; pass back `next_offset` | `0` | no |
| `limit` | integer | cap on the returned list (counts stay exact) | `1000` | no |
| `include_zero` | boolean | also return vertices not in the group, as `weight: 0.0, assigned: false` | `false` | no |

Returns: `{mesh, group, group_index, total_vertices, offset, next_offset, returned, truncated, weights}`. `next_offset` is null once the scan reached the end.

Gotchas:

- Chunked. Page it with `next_offset`; never request a whole dense mesh in one go.
- For a whole-mesh overview prefer `per_bone_weight_summary` — one small call instead of many pages.

```python
get_weights(mesh="Body", group="forearm.L", offset=0, limit=1000)
```

### invert

Replace every weight with `1 - weight`.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `mesh` | string | MESH object name | — | yes |
| `group` | string | vertex group name; omit for the active group | none | no |
| `auto_assign` | boolean | add unassigned vertices at weight 1 (their implied 0 inverts to 1) | `true` | no |
| `auto_remove` | boolean | unassign vertices whose weight inverts to 0 rather than storing a zero | `true` | no |
| `group_select_mode` | string | `ACTIVE`, `ALL` or `BONE_DEFORM` | `ACTIVE` | no |
| `only_selected` | boolean | restrict to the mesh's current vertex selection (runs in Edit mode) | `false` | no |

Returns: `{mesh, group, mode_used, only_selected}`.

Gotchas:

- `auto_assign=true` (Blender's default) ADDS unassigned vertices to the group at weight 1, since their implied 0 inverts to 1. On a deform group that can suddenly bind the whole mesh to one bone — set it false unless you mean it.

```python
invert(mesh="Body", group="forearm.L", auto_assign=True, auto_remove=True)
```

### levels

Remap weights with `(weight + offset) * gain`, clamped to 0..1.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `mesh` | string | MESH object name | — | yes |
| `gain` | number | multiplier applied after `offset`; 1.0 = no change | `1.0` | no |
| `offset` | number | added to every weight before the gain, roughly -1..1 | `0.0` | no |
| `group` | string | vertex group name; omit for the active group | none | no |
| `group_select_mode` | string | `ACTIVE`, `ALL` or `BONE_DEFORM` | `ACTIVE` | no |
| `only_selected` | boolean | restrict to the mesh's current vertex selection (runs in Edit mode) | `false` | no |

Returns: `{mesh, group, mode_used, only_selected}`.

Gotchas:

- `gain` above 1 sharpens the falloff; a positive `offset` lifts the whole group toward 1. Results are clamped to 0..1, so an aggressive gain flattens the top of the falloff.

```python
levels(mesh="Body", gain=1.4, offset=-0.05, group="forearm.L")
```

### limit_total

Keep only the N strongest influences per vertex. The game-export gate.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `mesh` | string | MESH object name | — | yes |
| `max_influences` | integer | bone influences per vertex | `4` | no |
| `group_select_mode` | string | `ACTIVE`, `ALL` or `BONE_DEFORM` | `ALL` | no |
| `only_selected` | boolean | restrict to the mesh's current vertex selection (runs in Edit mode) | `false` | no |

Returns: `{mesh, group, mode_used, only_selected}` — verify with `report_over_influenced`, not with this return.

Gotchas:

- The game-export gate: most real-time engines take 4 influences per vertex and drop the rest silently.
- Order matters — `clean_weights`, then `limit_total`, then `normalize_all`, because dropping influences leaves the remainder summing to less than 1.

```python
limit_total(mesh="Body", max_influences=4, group_select_mode="BONE_DEFORM")
```

### mirror_weights

Mirror weights across a local axis, swapping .L/.R group names as it goes.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `mesh` | string | MESH object name | — | yes |
| `axis` | string | `X`, `Y` or `Z` in the object's LOCAL space | `X` | no |
| `use_topology` | boolean | pair vertices by mesh topology instead of mirrored position — X AXIS ONLY | `false` | no |
| `all_groups` | boolean | mirror every vertex group instead of just one | `false` | no |
| `flip_group_names` | boolean | write into the name-flipped group (`Arm.L` -> `Arm.R`) | `true` | no |
| `group` | string | vertex group name; omit for the active group | none | no |
| `tolerance` | number | object-space world units for mirrored-pair matching | `0.0001` | no |
| `timeout` | number | seconds to wait for the bridge call | `120.0` | no |

Returns: `{mesh, method, axis, groups, use_topology}` plus `unpaired_vertices` on the KD-tree path.

Gotchas:

- Blender 5.2's `object.vertex_group_mirror` only mirrors along X. For `axis="X"` you get the operator with full name-flipping and topology support; for `Y`/`Z` the tool falls back to a KD-tree pairing (`method: "kdtree"`), ignores `use_topology`, and flips only `.L/.R`, `_L/_R`, `Left/Right`.
- `axis` is the object's LOCAL axis — a rotated object's local X is not world X.
- Check `unpaired_vertices` to see whether `tolerance` was generous enough.

```python
mirror_weights(mesh="Body", axis="X", all_groups=True, flip_group_names=True, timeout=120.0)
```

### normalize

Scale one group so its highest weight becomes exactly 1.0.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `mesh` | string | MESH object name | — | yes |
| `group` | string | vertex group name; omit for the active group | none | no |
| `only_selected` | boolean | restrict to the mesh's current vertex selection (runs in Edit mode) | `false` | no |

Returns: `{mesh, group, mode_used, only_selected}`.

Gotchas:

- Per-GROUP: it scales one group's peak to 1.0 and touches nothing else. `normalize_all` is the per-VERTEX one.

```python
normalize(mesh="Body", group="forearm.L")
```

### normalize_all

Make each vertex's weights sum to 1.0 across groups. Run this before export.

**vs:** `normalize` — normalize_all makes each VERTEX sum to 1 across groups (what exporters need); normalize scales ONE group's peak to 1.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `mesh` | string | MESH object name | — | yes |
| `lock_active` | boolean | hold the active group fixed and redistribute the rest around it | `true` | no |
| `group_select_mode` | string | `ACTIVE`, `ALL` or `BONE_DEFORM` | `ALL` | no |
| `only_selected` | boolean | restrict to the mesh's current vertex selection (runs in Edit mode) | `false` | no |

Returns: `{mesh, group, mode_used, only_selected}`.

Gotchas:

- Run this before any export. A vertex whose deform weights do not sum to 1 deforms wrongly, and most engines assume normalized weights.
- `lock_active=true` holds the active group fixed and redistributes the rest around it.
- Fails with "All groups are locked" when `vgroup_lock` locked everything.

```python
normalize_all(mesh="Body", lock_active=False, group_select_mode="BONE_DEFORM")
```

### per_bone_weight_summary

Per deform bone: vertex count, total, max and mean weight. Cheap whole-rig view.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `mesh` | string | MESH object name | — | yes |
| `armature` | string | ARMATURE object name | none | no |
| `limit` | integer | cap on the returned list (counts stay exact) | `1000` | no |
| `timeout` | number | seconds to wait for the bridge call | `60.0` | no |

Returns: `{mesh, armature, deform_bone_count, bones, truncated, bones_with_no_group, empty_bones, groups_not_deform_bones}`. Those last three fields do the diagnosing.

Gotchas:

- `empty_bones` is the classic symptom of a heat solve that quietly failed on part of the mesh; `groups_not_deform_bones` catches misspelled bone names.

```python
per_bone_weight_summary(mesh="Body", armature="Rig", limit=200)
```

### quantize

Round weights onto N evenly spaced steps.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `mesh` | string | MESH object name | — | yes |
| `steps` | integer | sample count along the path | `4` | no |
| `group` | string | vertex group name; omit for the active group | none | no |
| `group_select_mode` | string | `ACTIVE`, `ALL` or `BONE_DEFORM` | `ACTIVE` | no |
| `only_selected` | boolean | restrict to the mesh's current vertex selection (runs in Edit mode) | `false` | no |

Returns: `{mesh, group, mode_used, only_selected}`.

Gotchas:

- Mostly a stylisation and debugging tool: `steps=2` gives a hard 0-or-1 split, which makes a group's boundary obvious in `weight_heatmap`.

```python
quantize(mesh="Body", steps=2, group="forearm.L")
```

### report_over_influenced

Find vertices bound to more deform bones than a game engine will accept.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `mesh` | string | MESH object name | — | yes |
| `max_influences` | integer | bone influences per vertex | `4` | no |
| `armature` | string | ARMATURE object name | none | no |
| `threshold` | number | weights at or below this do not count as an influence | `0.0` | no |
| `limit` | integer | cap on the returned list (counts stay exact) | `1000` | no |
| `timeout` | number | seconds to wait for the bridge call | `60.0` | no |

Returns: `{mesh, armature, max_influences, vertex_count, over_influenced_count, influence_histogram, vertices, truncated, fix}` — `influence_histogram` maps influence count to vertex count.

Gotchas:

- Run before every real-time export. `influence_histogram` tells you at a glance whether the mesh is already export-clean.
- The fix is `clean_weights` → `limit_total` → `normalize_all`, in that order.

```python
report_over_influenced(mesh="Body", max_influences=4, armature="Rig", limit=200)
```

### report_unweighted_verts

Find vertices with zero total deform weight — they will not follow the rig.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `mesh` | string | MESH object name | — | yes |
| `armature` | string | ARMATURE object name | none | no |
| `threshold` | number | total deform weight at or below this counts as unweighted | `0.0` | no |
| `limit` | integer | cap on the returned list (counts stay exact) | `1000` | no |
| `timeout` | number | seconds to wait for the bridge call | `60.0` | no |

Returns: `{mesh, armature, vertex_count, deform_groups, unweighted_count, vertices, truncated}`; each vertex includes its object-space `co`, ready to paste into a bounding-box `verts_spec`.

Gotchas:

- The single most useful check after `auto_weights`. Unweighted vertices stay pinned while the rest of the mesh moves, which reads as tearing.
- Each reported vertex includes its object-space `co`, so the coordinates paste straight into a bounding-box `verts_spec`.

```python
report_unweighted_verts(mesh="Body", armature="Rig", threshold=0.001, limit=200)
```

### select_verts_by_weight

Select vertices whose weight in a group falls inside a range.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `mesh` | string | MESH object name | — | yes |
| `group` | string | vertex group name; omit for the active group | — | yes |
| `min` | number | lower weight bound, inclusive, 0-1 | `0.0` | no |
| `max` | number | upper weight bound, inclusive, 0-1 | `1.0` | no |
| `include_unassigned` | boolean | treat vertices not in the group as weight 0 and match them | `false` | no |
| `extend` | boolean | add to the existing selection instead of replacing it | `false` | no |
| `limit` | integer | cap on the returned vertex list (`selected` stays exact) | `1000` | no |

Returns: `{mesh, group, min, max, selected, vertices, truncated, note}`.

Gotchas:

- Writes the selection into the mesh data. The tool briefly drops to Object mode to write it and restores your mode, so it is safe to call from Edit or Weight Paint.

```python
select_verts_by_weight(mesh="Body", group="forearm.L", min=0.01, max=0.2, limit=500)
```

### set_weights

Bulk-write an explicit vertex-index to weight map into one group.

**vs:** `weight_gradient` — set_weights is exact, headless and reproducible; the gradient depends on where the viewport camera points.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `mesh` | string | MESH object name | — | yes |
| `group` | string | vertex group name — must already exist | — | yes |
| `weights` | object | `{"<vertex_index>": weight}` map, weights 0-1 | — | yes |
| `remove_zero` | boolean | a weight of 0 UNASSIGNS the vertex instead of storing a zero | `false` | no |

Returns: `{mesh, group, vertices_written, vertices_removed}`.

Gotchas:

- Out-of-range indices raise rather than being silently skipped.
- Keep batches to a few thousand entries per call; split larger writes.
- `remove_zero` is the difference between "this bone has no influence here" and "an influence of exactly nothing" — it matters for `limit_total` and for game exporters.

```python
set_weights(mesh="Body", group="forearm.L", weights={"412": 1.0, "413": 0.82, "414": 0.51}, remove_zero=True)
```

### smooth_weights

Blend each weight toward its connected neighbours. Fixes hard creases.

**vs:** `levels` — smooth blends toward neighbours to fix creasing; levels remaps the whole group's contrast without changing its shape.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `mesh` | string | MESH object name | — | yes |
| `factor` | number | 0-1 per iteration | `0.5` | no |
| `iterations` | integer | repeat count | `1` | no |
| `expand` | number | -1.0 to 1.0; positive grows the region | `0.0` | no |
| `group` | string | vertex group name; omit for the active group | none | no |
| `group_select_mode` | string | `ACTIVE`, `ALL` or `BONE_DEFORM` | `ACTIVE` | no |
| `only_selected` | boolean | restrict to the mesh's current vertex selection (runs in Edit mode) | `false` | no |
| `timeout` | number | seconds to wait for the bridge call | `120.0` | no |

Returns: `{mesh, group, mode_used, only_selected}`.

Gotchas:

- The cure for the faceted, blocky deformation auto weights leave at joints.
- Several passes at a moderate `factor` beat one pass at 1.0, which collapses the group.
- `object.vertex_group_smooth` refuses to run in Object mode, so the tool enters Weight Paint (or Edit when `only_selected`) and restores your mode. It still works headless.

```python
smooth_weights(mesh="Body", factor=0.5, iterations=4, expand=0.0, group="shoulder.L")
```

### transfer_weights

Copy vertex-group weights from one mesh onto another.

**vs:** `add_data_transfer` — the tool is the one-shot version that applies immediately; the modifier stays live until you apply it.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `source` | string | MESH object to read weights from | — | yes |
| `target` | string | MESH object to write weights to; its weights in matched groups are overwritten | — | yes |
| `method` | string | vertex mapping: `POLYINTERP_NEAREST` (default), `TOPOLOGY`, `NEAREST`, `EDGE_NEAREST`, `EDGEINTERP_NEAREST`, `POLY_NEAREST`, `POLYINTERP_VNORPROJ` | `POLYINTERP_NEAREST` | no |
| `name_matching` | boolean | match destination groups BY NAME (false matches by index and scrambles weights) | `true` | no |
| `layers_select_src` | string | `ALL`, `ACTIVE` or `BONE_DEFORM` | `ALL` | no |
| `mix_mode` | string | `REPLACE`, `MIX`, `ADD`, `SUB`, `MUL`, `ABOVE_THRESHOLD`, `BELOW_THRESHOLD` | `REPLACE` | no |
| `mix_factor` | number | 0-1 blend against existing data | `1.0` | no |
| `max_distance` | number | world units; source geometry beyond this is ignored | none | no |
| `use_create` | boolean | create destination groups that do not exist yet | `true` | no |
| `use_object_transform` | boolean | account for both objects' world transforms | `true` | no |
| `timeout` | number | seconds to wait for the bridge call | `180.0` | no |

Returns: `{source, target, method, name_matching, groups_created, target_groups, note}`.

Gotchas:

- `object.vertex_group_transfer_weight` was REMOVED in 5.x; this runs `object.data_transfer(data_type='VGROUP_WEIGHTS')`.
- `name_matching=false` matches by INDEX and will scramble weights unless both meshes have identical group ordering.
- `POLYINTERP_VNORPROJ` is the better choice when the target is offset from the source, e.g. clothing over a body.
- Use `max_distance` to stop a nearby limb bleeding onto the wrong part.

```python
transfer_weights(source="Body", target="Jacket", method="POLYINTERP_NEAREST", layers_select_src="BONE_DEFORM", max_distance=0.05)
```

### vgroup_create

Create an empty vertex group and make it active.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `mesh` | string | MESH object name | — | yes |
| `name` | string | vertex group name | — | yes |

Returns: `{mesh, name, index, created}`. `created:false` means it already existed and was just activated.

Gotchas:

- Idempotent: an existing group is activated and returned with `created: false` rather than duplicated. Always read the returned `name`.

```python
vgroup_create(mesh="Body", name="hand.L")
```

### vgroup_delete

Delete one vertex group, or every group on the mesh.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `mesh` | string | MESH object name | — | yes |
| `name` | string | vertex group name; required unless `all` is true | none | no |
| `all` | boolean | delete EVERY vertex group (destructive — the armature stops deforming) | `false` | no |

Returns: `{mesh, deleted, remaining}`.

Gotchas:

- Deleting a group shifts the indices of every group after it. Re-read `vgroup_list` instead of caching indices.
- `all=true` stops an armature modifier deforming the mesh at all.

```python
vgroup_delete(mesh="Body", name="Group")
```

### vgroup_list

List every vertex group with its index, lock state and assigned-vertex count.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `mesh` | string | MESH object name | — | yes |
| `armature` | string | ARMATURE object name | none | no |

Returns: `{mesh, count, active_group, vertex_count, groups}`; each group carries index, lock state, assigned-vertex count, `total_weight` and — when an armature is known — `is_deform_bone`.

Gotchas:

- Group names are CASE-SENSITIVE and must match deform bone names exactly, or the armature modifier ignores them. `is_deform_bone` is how you spot a typo.
- `total_weight` of 0 means the group exists but holds nothing — the classic sign of a heat solve that failed on part of the mesh.

```python
vgroup_list(mesh="Body", armature="Rig")
```

### vgroup_lock

Lock or unlock groups so normalize and auto-normalize cannot rewrite them.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `mesh` | string | MESH object name | — | yes |
| `name` | string|array | one group name or a list; omit to affect EVERY group | none | no |
| `locked` | boolean | true locks, false unlocks | `true` | no |

Returns: `{mesh, locked, groups, all_locked}` — `all_locked:true` is why `normalize_all` will fail next.

Gotchas:

- Locking EVERY group makes `normalize_all` fail with "All groups are locked".
- Locking is the standard way to protect hand-tuned weights while `normalize_all` redistributes the rest.

```python
vgroup_lock(mesh="Body", name=["spine", "spine.001"], locked=True)
```

### vgroup_rename

Rename a vertex group.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `mesh` | string | MESH object name | — | yes |
| `name` | string | current vertex group name | — | yes |
| `new_name` | string | new name | — | yes |

Returns: `{mesh, old_name, name, index}`.

Gotchas:

- This is how you re-bind weights to a different bone — the armature modifier matches groups to bones purely by name.

```python
vgroup_rename(mesh="Body", name="Bone.001", new_name="upper_arm.L")
```

### weight_gradient

Paint a linear or radial weight gradient between two 3D points.

**GUI Blender only** — needs a real 3D Viewport; refuses under
`blender --background`.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `mesh` | string | MESH object name | — | yes |
| `start` | array | `[x, y, z]` where the gradient is at full `weight` | — | yes |
| `end` | array | `[x, y, z]` where the gradient reaches 0 | — | yes |
| `group` | string | vertex group name; omit for the active group | none | no |
| `type` | string | `LINEAR` (band) or `RADIAL` (concentric around `start`) | `LINEAR` | no |
| `weight` | number | 0-1 (not 0-100) | `1.0` | no |
| `space` | string | `WORLD` (default) or `LOCAL` | `WORLD` | no |
| `flip` | boolean | swap the gradient ends, so it runs 0 -> weight | `false` | no |

Returns: `{mesh, group, type, weight, start_region_px, end_region_px, region_size}` — the projected pixel coordinates it actually painted between.

Gotchas:

- Prefer `set_weights` when you can compute the falloff yourself: exact, headless, and independent of where the viewport camera happens to point.
- Both points must be IN FRONT of the viewport camera or the call fails with an explanation.
- Only geometry visible in the viewport is painted. Verify with `weight_heatmap` afterwards.
- `paint.weight_gradient` takes INT screen coordinates; this tool projects your 3D points into region pixels for you.

```python
weight_gradient(mesh="Body", start=[0.2, 0, 1.4], end=[0.55, 0, 1.1], group="upper_arm.L", type="LINEAR", weight=1.0, space="WORLD")
```

### weight_heatmap

See one group's weights as the blue-to-red heatmap. Returns an image.

**GUI Blender only** — needs a real 3D Viewport; refuses under
`blender --background`.

**vs:** `viewport_screenshot` — the heatmap forces Weight Paint mode and the overlay colours; a plain screenshot shows you nothing about weights.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `mesh` | string | MESH object name | — | yes |
| `group` | string | vertex group name; omit for the active group | none | no |
| `max_size` | integer | longest image edge in pixels — raise only to inspect fine detail | `1024` | no |
| `show_contours` | boolean | draw iso-weight contour lines over the colours | `false` | no |
| `use_render` | boolean | `false` (default) grabs viewport pixels — the ONLY way weight colours appear | `false` | no |

Returns: A PNG image content block: blue 0, green ~0.5, red 1.0, black = not in the group at all.

Gotchas:

- Blue 0, green ~0.5, red 1.0, BLACK means the vertex is not in the group at all — black is the finding, not a rendering glitch.
- `use_render=true` gives a clean pass with no viewport chrome but the weight colours are MISSING, because they are drawn by the overlay engine. Leave it false.
- Shows whatever view the user left. If the area you care about faces away, `add_camera` there and pass `camera_view=True` — there is no orbit tool.

```python
weight_heatmap(mesh="Body", group="forearm.L", max_size=1024, show_contours=True)
```

---

## rig

23 tools. Armatures, bones, posing, constraints, IK, shape keys, drivers and bone collections. Every tool here runs through the data API and works headless.

### add_bone_constraint

Add any bone constraint type and set its properties generically.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `armature` | string | ARMATURE object name | — | yes |
| `bone` | string | bone name | — | yes |
| `type` | string | constraint type id — `COPY_ROTATION`, `DAMPED_TRACK`, `LIMIT_ROTATION`, `STRETCH_TO`, `CHILD_OF`, … | — | yes |
| `settings` | object | property -> value; object pointers take a NAME string, `subtarget` is a bone name, angles RADIANS, `influence` 0-1 | none | no |
| `name` | string | constraint name | none | no |

Returns: `{armature, bone, constraint, type, applied, is_valid, writable_settings}`. Call it once with `settings` omitted to see `writable_settings`.

Gotchas:

- For IK prefer `setup_ik` — it creates the target and pole and solves the pole angle.
- `subtarget` is required whenever `target` is an armature; a missing one is the usual cause of `is_valid: false`.
- A first call with `settings` omitted is a cheap way to read `writable_settings`.

```python
add_bone_constraint(armature="Rig", bone="head", type="DAMPED_TRACK", settings={"target": "AimTarget", "track_axis": "TRACK_Y", "influence": 1.0})
```

### add_bones

Add bones to an existing armature.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `armature` | string | ARMATURE object name | — | yes |
| `bones` | array | list of bone names | — | yes |

Returns: `{armature, added, bone_count}`.

Gotchas:

- Same bone dict format as `create_armature`: head/tail in armature-local space, `roll` in RADIANS, `connect` MOVES the head onto the parent's tail.

```python
add_bones(armature="Rig", bones=[{"name": "tail.001", "head": [0, 0.2, 1.0], "tail": [0, 0.5, 0.95], "parent": "spine", "connect": False, "use_deform": True}])
```

### add_driver

Drive a property from an expression over other properties or bones.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `object` | string | object name; omit for the active object | — | yes |
| `data_path` | string | RNA property path, e.g. `location`, `rotation_euler` | — | yes |
| `expression` | string | Python expression over the driver variables | `var` | no |
| `variables` | array | list of driver-variable dicts | none | no |
| `index` | integer | vector component (0=X, 1=Y, 2=Z); omit to drive every component | none | no |
| `host` | string | `OBJECT`, `DATA` or `SHAPE_KEYS` — which datablock owns the property | `OBJECT` | no |
| `driver_type` | string | `SCRIPTED` (uses `expression`), `AVERAGE`, `SUM`, `MIN`, `MAX` | `SCRIPTED` | no |

Returns: `{object, host, id, replaced_existing, drivers, count}`; each driver carries `is_valid`.

Gotchas:

- Shape key values are not on the object — they need `host="SHAPE_KEYS"`.
- Angles arriving from `TRANSFORMS` variables are in RADIANS.
- `ROTATION_DIFF` / `LOC_DIFF` need TWO targets: pass `targets: [{...}, {...}]` instead of the flat fields.
- Re-running on the same property REPLACES the variables rather than accumulating duplicates, so it is safe to iterate. Check `is_valid`.

```python
add_driver(object="Body", host="SHAPE_KEYS", data_path='key_blocks["bicep_bulge"].value', expression="max(0.0, -rot)", variables=[{"name": "rot", "type": "TRANSFORMS", "id": "Rig", "bone": "forearm.L", "transform_type": "ROT_X", "transform_space": "LOCAL_SPACE"}])
```

### apply_pose_as_rest

Freeze the current pose as the new rest pose. Destructive — read this.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `armature` | string | ARMATURE object name | — | yes |
| `bones` | array | list of bone names | none | no |

Returns: `{armature, applied_to, result, deformed_meshes, warning}`.

Gotchas:

- Bound meshes are NOT re-fitted — they keep their current vertex positions, so this only looks right if the mesh was already deformed into this pose.
- Blender skips meshes with shape keys entirely.
- Existing keyframes are now measured from the new rest pose, so any animation shifts. Screenshot before and after.

```python
apply_pose_as_rest(armature="Rig", bones=["spine", "spine.001"])
```

### bone_collection_assign

Add bones to a bone collection, or remove them from it.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `armature` | string | ARMATURE object name | — | yes |
| `collection` | string | bone collection name | — | yes |
| `bones` | array | list of bone names | — | yes |
| `unassign` | boolean | remove the bones from the collection instead of adding them | `false` | no |

Returns: `{armature, collection, operation, bones, bone_count}` — per-bone `changed` is false when it was already in/out.

Gotchas:

- Membership is many-to-many — assigning does not remove a bone from any other collection.

```python
bone_collection_assign(armature="Rig", collection="Controls", bones=["IK.hand.L", "IK.hand.R"])
```

### bone_collection_create

Create a bone collection — the 4.x+ replacement for bone layers.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `armature` | string | ARMATURE object name | — | yes |
| `name` | string | bone collection name | `Bones` | no |
| `parent` | string | existing bone collection to nest under | none | no |
| `bones` | array | list of bone names | none | no |

Returns: `{armature, collection, assigned}`.

Gotchas:

- Bone collections replaced the 32 fixed bone layers in 4.x — they are named, nestable and unlimited.
- Hiding a parent hides its children's bones too.

```python
bone_collection_create(armature="Rig", name="Controls", bones=["IK.hand.L"])
```

### bone_collection_visibility

Show, hide or solo a bone collection.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `armature` | string | ARMATURE object name | — | yes |
| `collection` | string | bone collection name | — | yes |
| `is_visible` | boolean | show or hide this collection's bones | none | no |
| `is_solo` | boolean | hide every collection that is not soloed | none | no |
| `toggle` | boolean | flip `is_visible` instead of setting it (ignored when `is_visible` is supplied) | `false` | no |

Returns: `{armature, collection, is_solo_active}`.

Gotchas:

- While ANY collection is soloed, `is_visible` on the others has no effect. `is_solo_active` in the response is the usual reason a "visible" collection stays hidden.
- Display only — hidden bones still deform and still evaluate constraints.

```python
bone_collection_visibility(armature="Rig", collection="Deform", is_visible=False)
```

### bone_collections_list

List an armature's bone collections, their nesting and membership.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `armature` | string | ARMATURE object name | — | yes |
| `include_bones` | boolean | list each collection's member bones | `true` | no |
| `limit` | integer | cap on the returned list (counts stay exact) | `1000` | no |

Returns: `{armature, count, roots, collections, truncated, is_solo_active}` with both `is_visible` and `is_visible_effectively`.

Gotchas:

- When bones are mysteriously invisible, compare `is_visible` against `is_visible_effectively`: a hidden ancestor or an active solo overrides the collection's own flag.

```python
bone_collections_list(armature="Rig", include_bones=False, limit=100)
```

### create_armature

Create an armature and build its whole bone hierarchy in one call.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `name` | string | name for both the object and its armature data | `Armature` | no |
| `bone_tree` | array | list of bone dicts: name, head, tail, roll, parent, connect, use_deform | none | no |
| `location` | array | armature object origin in WORLD space | none | no |

Returns: `{object, armature_data, location, bones, bone_count, mode}` — read `bones[].name`, Blender renames on collision.

Gotchas:

- `head` and `tail` are in ARMATURE-OBJECT LOCAL space, not world space. They coincide only while `location` is (0,0,0) and the object is unrotated and unscaled.
- `head == tail` is rejected — Blender silently deletes zero-length bones on leaving edit mode.
- Names collide silently (Blender appends `.001`). Read `bones[].name`.
- Name bones `.L`/`.R` if you ever intend to call `symmetrize_bones`.
- Parents may be forward references to bones defined later in the same list.

```python
create_armature(name="Rig", location=[0, 0, 0], bone_tree=[{"name": "spine", "head": [0, 0, 0.9], "tail": [0, 0, 1.25]}, {"name": "upper_arm.L", "head": [0.18, 0, 1.22], "tail": [0.45, 0, 1.18], "parent": "spine", "connect": False}])
```

### edit_bone

Edit one bone's REST geometry (edit mode), not its pose.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `armature` | string | ARMATURE object name | — | yes |
| `bone` | string | bone name | — | yes |
| `head` | array | `[x, y, z]` in ARMATURE-LOCAL space | none | no |
| `tail` | array | `[x, y, z]` in ARMATURE-LOCAL space | none | no |
| `roll` | number | twist about the bone axis, RADIANS | none | no |
| `parent` | string | new parent bone name; `""` unparents | none | no |
| `use_connect` | boolean | snap this bone's head onto the parent's tail and keep it there | none | no |
| `use_deform` | boolean | whether the bone deforms bound meshes — set FALSE for IK targets and controls | none | no |
| `name` | string | rename the bone (vertex groups are NOT renamed) | none | no |

Returns: `{armature, bone, changed}`.

Gotchas:

- This edits the REST skeleton. To move a bone while animating use `pose_bone`.
- Renaming does NOT rename vertex groups, so a rename silently breaks existing skinning. Rename before you bind.
- `parent=""` unparents and clears `use_connect`.

```python
edit_bone(armature="Rig", bone="forearm.L", tail=[0.72, 0.02, 1.1], roll=0.0873, use_connect=True)
```

### keyframe_pose

Keyframe bone poses at a frame.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `armature` | string | ARMATURE object name | — | yes |
| `bones` | array | list of bone names | none | no |
| `frame` | integer | frame number; omit for the current frame | none | no |
| `channels` | array | `LOC`, `ROT`, `SCALE`, `LOCROT`, `LOCROTSCALE`, `ALL`, or exact property names | none | no |

Returns: `{armature, frame, inserted, action}`.

Gotchas:

- `ROT` resolves per bone to whichever rotation channel that bone actually uses. Keying `rotation_quaternion` on an XYZ bone records nothing visible.
- This records the current pose; set it with `pose_bone` first.

```python
keyframe_pose(armature="Rig", bones=["upper_arm.L", "forearm.L"], frame=24, channels=["LOCROT"])
```

### list_bones

List an armature's bones with parents, heads and tails.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `armature` | string | ARMATURE object name | — | yes |
| `space` | string | `DATA` (rest, read-only), `EDIT` (rest, editable — the only place roll is visible), `POSE` (constraint-evaluated), `AUTO` | `AUTO` | no |
| `limit` | integer | cap on the returned list (counts stay exact) | `1000` | no |

Returns: `{armature, space, count, bones, truncated, mode}`.

Gotchas:

- `space="EDIT"` is the only place `roll` is visible, and reading it briefly enters and leaves edit mode.
- Call this before any other rig tool — `create_armature` and `symmetrize_bones` both rename on collision.

```python
list_bones(armature="Rig", space="EDIT", limit=200)
```

### parent_mesh_to_armature

Bind a mesh to an armature so the bones deform it.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `mesh` | string | MESH object name | — | yes |
| `armature` | string | ARMATURE object name | — | yes |
| `mode` | string | `AUTOMATIC` (bone heat), `ENVELOPE`, `EMPTY` | `AUTOMATIC` | no |
| `keep_transform` | boolean | preserve the child's WORLD position when parenting | `false` | no |

Returns: `{mesh, armature, mode, parent_type, result, armature_modifiers, vertex_groups, vertex_groups_created, note}`.

Gotchas:

- Only bones with `use_deform` get a vertex group — which is why IK targets and control bones should be created with `use_deform: false`.
- `AUTOMATIC` fails outright on non-manifold geometry or when bones sit outside the mesh volume.

```python
parent_mesh_to_armature(mesh="Body", armature="Rig", mode="AUTOMATIC")
```

### pose_bone

Move, rotate or scale a bone's POSE — the transform animation records.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `armature` | string | ARMATURE object name | — | yes |
| `bone` | string | bone name | — | yes |
| `location` | array | `[x, y, z]` bone-local offset in `LOCAL` space — NOT a world offset | none | no |
| `rotation_euler` | array | `[rx, ry, rz]` RADIANS | none | no |
| `rotation_quaternion` | array | `[w, x, y, z]` — w FIRST | none | no |
| `scale` | array | `[sx, sy, sz]` multipliers, 1.0 = unchanged | none | no |
| `space` | string | `LOCAL` (bone channels), `POSE` (armature space), `WORLD` | `LOCAL` | no |
| `mode` | string | `absolute` sets; `delta` adds location and multiplies rotation/scale | `absolute` | no |
| `rotation_mode` | string | force `QUATERNION`, `XYZ`…`ZYX`, or `AXIS_ANGLE` | none | no |

Returns: `{armature, bone, space, mode, rotation_mode, location, rotation_quaternion, rotation_euler, scale, matrix_pose, head_pose, tail_pose}`.

Gotchas:

- `location` in `LOCAL` space is the bone's own channel along its own axes — NOT a world offset.
- `rotation_quaternion` is `[w, x, y, z]`, w FIRST — the opposite of many other tools.
- Passing `rotation_euler` to a quaternion bone switches it to `XYZ`, because the euler channels are otherwise ignored; the response reports the mode.
- Nothing here is keyframed. Call `keyframe_pose` or the value is lost the moment the frame changes on an animated bone.

```python
pose_bone(armature="Rig", bone="forearm.L", rotation_euler=[-0.6109, 0, 0], space="LOCAL", mode="absolute")
```

### remove_bones

Delete bones from an armature, re-parenting their children upward.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `armature` | string | ARMATURE object name | — | yes |
| `bones` | array | list of bone names | — | yes |

Returns: `{armature, removed, reparented, bone_count}`.

Gotchas:

- Children are re-parented upward and disconnected, so the hierarchy never breaks.
- Vertex groups on bound meshes are NOT removed — the mesh keeps a now-inert group of the same name.

```python
remove_bones(armature="Rig", bones=["Bone.003"])
```

### reset_pose

Snap bones back to their rest pose (clears location, rotation, scale).

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `armature` | string | ARMATURE object name | — | yes |
| `bones` | array | list of bone names | none | no |

Returns: `{armature, reset, count}`.

Gotchas:

- Clears pose CHANNELS only. It does not remove keyframes, so on an animated rig the pose returns as soon as the frame changes, and an IK-driven bone snaps straight back.

```python
reset_pose(armature="Rig")
```

### setup_ik

Build a working IK setup: constraint plus the target and pole it needs.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `armature` | string | ARMATURE object name | — | yes |
| `chain_tip` | string | bone name — the LAST bone of the IK chain | — | yes |
| `chain_length` | integer | bones the IK solver owns, counting up from the tip | `2` | no |
| `target` | string | existing object to reach for; omit to create one at the tip's tail | none | no |
| `target_bone` | string | bone name inside `target` when it is an armature | none | no |
| `target_type` | string | `EMPTY` (a plain-axes empty in the scene) or `BONE` (an unparented non-deforming bone) | `EMPTY` | no |
| `auto_pole` | boolean | create a pole target so the elbow/knee bends predictably | `false` | no |
| `pole_target` | string | existing pole object name | none | no |
| `pole_bone` | string | bone name to use as the pole | none | no |
| `pole_angle` | number | RADIANS about the root->target axis | none | no |
| `pole_distance` | number | armature-local units from the joint | none | no |
| `use_tail` | boolean | the tip bone's TAIL reaches the target (false = its head) | `true` | no |
| `use_stretch` | boolean | let the IK chain stretch to reach | `false` | no |
| `name` | string | constraint name | none | no |

Returns: `{armature, chain_tip, chain, chain_count, constraint, target, subtarget, pole_target, pole_subtarget, pole_angle, pole_angle_solved, created, is_valid}`.

Gotchas:

- `chain_tip` is the LAST bone of the chain. For upper_arm → forearm → hand that is `forearm`, not `hand`.
- `chain_length=0` means "all the way to the root" and will swing the whole spine.
- `auto_pole` places the pole in the plane the chain already bends in — a perfectly straight rest pose gives the wrong answer, so pre-bend the chain slightly in edit mode.
- Omitting `pole_angle` together with `auto_pole` SOLVES the angle numerically, which removes the usual "the arm flipped 90 degrees" problem. Supply it only to override.
- Check `is_valid`, then screenshot. IK is the single easiest thing to get subtly wrong.

```python
setup_ik(armature="Rig", chain_tip="forearm.L", chain_length=2, target_type="EMPTY", auto_pole=True)
```

### shapekey_create

Add a shape key to a mesh, curve, surface or lattice.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `object` | string | object name; omit for the active object | — | yes |
| `name` | string | shape key name | `Key` | no |
| `from_mix` | boolean | capture the current blend of all active keys instead of the base shape | `false` | no |
| `value` | number | initial value, 0-1 by default | none | no |

Returns: `{object, created_basis, key, key_count}`. `created_basis:true` means a `Basis` was made first.

Gotchas:

- If the object has no shape keys yet, a `Basis` is created first (`created_basis: true`). Without it your first key would silently become the rest shape and deform nothing.
- A new key holds the base shape — it does nothing until you move vertices while it is the active key.

```python
shapekey_create(object="Head", name="smile", value=0.0)
```

### shapekey_from_mix

Snapshot the current blend of all shape keys into one new key.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `object` | string | object name; omit for the active object | — | yes |
| `name` | string | shape key name | `Mix` | no |

Returns: `{object, created_basis, key, key_count}`.

Gotchas:

- Identical to `shapekey_create(from_mix=true)`. Set the contributing keys with `shapekey_set_value` first — this captures the current blend, it does not compute one.

```python
shapekey_from_mix(object="Head", name="smile_wide")
```

### shapekey_keyframe

Keyframe shape key values at a frame.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `object` | string | object name; omit for the active object | — | yes |
| `keys` | array | list of shape key names | none | no |
| `frame` | integer | frame number; omit for the current frame | none | no |
| `value` | number | set this on each key before keying | none | no |

Returns: `{object, frame, keyframed, action, note}` — the action lives on the Key datablock, not the object.

Gotchas:

- Shape key animation lives on the KEY datablock, not the object, so it is a separate action from the object's own animation.

```python
shapekey_keyframe(object="Head", keys=["smile"], frame=12, value=1.0)
```

### shapekey_list

List an object's shape keys: values, slider ranges, masks and order.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `object` | string | object name; omit for the active object | — | yes |
| `limit` | integer | cap on the returned list (counts stay exact) | `1000` | no |

Returns: `{object, has_shape_keys, datablock, use_relative, reference_key, count, keys, truncated}`.

Gotchas:

- Start here before touching shape keys: each key's slider range is what silently clamps `shapekey_set_value`.
- Returns `has_shape_keys: false` for an object that has none yet, rather than raising.

```python
shapekey_list(object="Head", limit=100)
```

### shapekey_set_value

Set a shape key's value and its slider range, mute and masking.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `object` | string | object name; omit for the active object | — | yes |
| `key` | string | shape key name | — | yes |
| `value` | number | 0-1 by default; CLAMPED to `[slider_min, slider_max]` | none | no |
| `slider_min` | number | shape key slider lower bound | none | no |
| `slider_max` | number | shape key slider upper bound | none | no |
| `mute` | boolean | disable the shape key without changing its value | none | no |
| `vertex_group` | string | restrict the key's effect to this group; `""` removes the restriction | none | no |
| `relative_key` | string | shape key this one is measured against (normally `Basis`) | none | no |

Returns: `{object, key}` plus a `clamped` note when the slider range bit.

Gotchas:

- `value` is CLAMPED to `[slider_min, slider_max]`. Check the `clamped` note when a shape did not move as far as you expected; `slider_min`/`slider_max` are applied first, so both can go in one call.

```python
shapekey_set_value(object="Head", key="smile", value=0.75, slider_max=1.5)
```

### symmetrize_bones

Mirror side-suffixed bones across the X axis, copying constraints.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `armature` | string | ARMATURE object name | — | yes |
| `direction` | string | `NEGATIVE_X` (default) or `POSITIVE_X` — the only two values in 5.2 | `NEGATIVE_X` | no |
| `bones` | array | list of bone names | none | no |

Returns: `{armature, direction, selected, created, bone_count, note}` — an empty `created` means no bone carried a side suffix.

Gotchas:

- Only mirrors bones whose names carry a side suffix (`.L`/`.R`, `_L`/`_R`, `left`/`right`). A bone named `arm` produces nothing AND the call still succeeds — check the `created` list.
- Existing mirrored bones are overwritten rather than duplicated, so it is safe to re-run.
- Only `NEGATIVE_X` and `POSITIVE_X` exist in 5.2.

```python
symmetrize_bones(armature="Rig", direction="POSITIVE_X")
```

---

## shading

11 tools. Materials, shader node graphs, viewport shading and Cycles texture baking.

### add_node

Add one shader node to a material, optionally configured in the same call.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `material` | string | material name | — | yes |
| `type` | string | the node's `bl_idname`, e.g. `ShaderNodeTexImage` — NOT the UI label | — | yes |
| `props` | object | node attribute or input-socket name -> value | none | no |
| `location` | array | `[x, y]` node-editor units, +x right, +y up (cosmetic) | none | no |
| `label` | string | text shown on the node header | none | no |

Returns: `{material, node, applied}` — `node` is the assigned name, which may differ from the default.

Gotchas:

- `type` is the `bl_idname` (`ShaderNodeTexNoise`), not the UI label and not the short `type` enum. A near miss raises with close matches from the live build.
- The node is created DISCONNECTED — wire it with `link_nodes`.
- Omitting `location` drops it at the origin, usually on top of another node.

```python
add_node(material="Skin", type="ShaderNodeTexNoise", props={"Scale": 12.0, "Detail": 4.0}, location=[-600, 200], label="pores")
```

### assign_material

Put an existing material into an object's material slot.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `object` | string | object name; omit for the active object | — | yes |
| `material` | string | material name | — | yes |
| `slot` | integer | 0-based material slot index | none | no |
| `to_selected_faces` | boolean | assign to the current face selection rather than the whole object | `false` | no |

Returns: `{object, material, slot, slot_count, slots, faces_assigned, mode}`.

Gotchas:

- With `slot` omitted and `to_selected_faces=false`, the ACTIVE slot is overwritten. That is what makes "give this object that material" work.
- `to_selected_faces` reads the selection from the mesh; make it first. Nothing selected raises rather than silently doing nothing.

```python
assign_material(object="Body", material="Skin", slot=0)
```

### bake

Bake surface detail into a texture with Cycles. Slow — expect tens of seconds.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `object` | string | object name; omit for the active object | — | yes |
| `type` | string | `AO`, `NORMAL`, `DIFFUSE`, `COMBINED`, `SHADOW`, `POSITION`, `UV`, `ROUGHNESS`, `EMIT`, `ENVIRONMENT`, `GLOSSY`, `TRANSMISSION` | `AO` | no |
| `image_name` | string | image datablock name | none | no |
| `size` | integer | square texture resolution in pixels (1024 = 1024×1024) | `1024` | no |
| `margin` | integer | pixels of bleed painted outside each UV island | `16` | no |
| `samples` | integer | render/bake samples per pixel | `32` | no |
| `filepath` | string | absolute output path for the PNG | none | no |
| `return_image` | boolean | return the baked PNG as a viewable image (false for large bakes) | `true` | no |
| `normal_space` | string | `TANGENT` (default) or `OBJECT` | none | no |
| `pass_filter` | array | list of `COLOR`, `DIRECT`, `INDIRECT` | none | no |
| `use_clear` | boolean | wipe the image before baking (false to bake several objects into one texture) | `true` | no |
| `timeout` | number | seconds to wait for the bridge call | `300.0` | no |

Returns: A PNG image content block when `return_image` is true, otherwise `{image, filepath, size, samples, uv_auto_created, bake_target_nodes, empty_material_slots}`.

Gotchas:

- Cycles only — EEVEE cannot bake. The scene is switched to Cycles and the previous engine, selection, active object and mode are restored afterwards.
- Cost scales with `size` squared. Probe at 256 before committing to 2048.
- A UV map is auto-created if the mesh has none, but it is a default layout, NOT a real unwrap. Check `uv_auto_created` and unwrap properly if the result looks smeared.
- A black or blank result almost always means overlapping/absent UVs, no thickness for AO to occlude, or no lights for COMBINED.
- `return_image=false` for large bakes — a 2048 PNG costs a lot of context.

```python
bake(object="Body", type="AO", size=1024, margin=16, samples=64, filepath="/tmp/body_ao.png", return_image=False, timeout=300.0)
```

### create_material

Create a Principled BSDF material from plain PBR values.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `name` | string | material name; may come back suffixed `.001` | — | yes |
| `base_color` | array | `[R, G, B, A]` each 0-1 (NOT 0-255) | none | no |
| `metallic` | number | 0-1; use 0 or 1 | none | no |
| `roughness` | number | 0-1; 0 = mirror, 1 = chalk | none | no |
| `ior` | number | index of refraction (~1.45 glass, ~1.33 water) | none | no |
| `alpha` | number | 0-1 opacity | none | no |
| `emission_color` | array | `[R, G, B, A]` each 0-1 | none | no |
| `emission_strength` | number | radiance multiplier; 0 = no glow | none | no |
| `normal_map` | string | path to a tangent-space normal map image | none | no |

Returns: `{material, principled_node, applied, missing_sockets, normal_map, available_inputs}`. A value whose socket is absent lands in `missing_sockets` instead of failing the call.

Gotchas:

- Colours are 0-1 per channel, not 0-255.
- Emission stays off until `emission_strength` is above 0 — setting only `emission_color` does nothing visible.
- `alpha` below 1 also flips the material to blended transparency; EEVEE otherwise renders it fully opaque.
- Socket names are the 4.x/5.x ones: `Emission Color` (was `Emission`), `Specular IOR Level` (was `Specular`), `Subsurface Weight` (was `Subsurface`). Anything absent lands in `missing_sockets`.
- Makes the datablock only — follow with `assign_material`.

```python
create_material(name="Skin", base_color=[0.8, 0.55, 0.45, 1.0], metallic=0.0, roughness=0.55, ior=1.45)
```

### get_node_graph

Read a material's whole shader node tree: nodes, sockets and links.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `material` | string | material name | — | yes |
| `limit` | integer | cap on the returned list (counts stay exact) | `1000` | no |

Returns: `{material, node_count, link_count, nodes, links, active_node, truncated}`; each node reports `bl_idname`, `type`, `location` and every input socket with `index`, `type`, `is_linked`, `default_value`.

Gotchas:

- Node NAMES are what every other node tool addresses, and Blender auto-numbers duplicates (`Image Texture.001`). Read them here rather than assuming.
- A linked socket's `default_value` is ignored at render time — the link wins.

```python
get_node_graph(material="Skin", limit=200)
```

### link_nodes

Connect a node output to a node input inside one material.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `material` | string | material name | — | yes |
| `from_node` | string | source node name | — | yes |
| `from_socket` | string|integer | output socket name, or 0-based index | — | yes |
| `to_node` | string | destination node name | — | yes |
| `to_socket` | string|integer | input socket name, or 0-based index | — | yes |

Returns: `{material, from_node, from_socket, to_node, to_socket, valid, link_count}`. `valid:false` means a type-mismatched link was created and will render as an error.

Gotchas:

- Use socket INDICES when names repeat: `ShaderNodeMix` has four `A`/`B` pairs and four `Result` outputs, one set per data type.
- Linking into an already-linked input replaces the old link. Inputs take one connection; outputs fan out.
- `valid: false` means a type-mismatched link was created and renders as an error.

```python
link_nodes(material="Skin", from_node="Noise Texture", from_socket="Fac", to_node="Principled BSDF", to_socket="Roughness")
```

### load_image_texture

Load an image file into a material as an Image Texture node, optionally wired up.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `material` | string | material name | — | yes |
| `path` | string | absolute path, or a `//relative` Blender path, on the machine running Blender | — | yes |
| `colorspace` | string | `sRGB` for colour maps, `Non-Color` for data maps | none | no |
| `hook_to` | string | Principled BSDF input socket to connect to | none | no |
| `image_name` | string | image datablock name | none | no |

Returns: `{material, image, filepath, size, image_node, created_nodes, colorspace, hooked_to}`.

Gotchas:

- `hook_to="Normal"` also inserts a Normal Map node, because a raw normal image plugged straight into Normal looks subtly bad rather than obviously broken.
- Hooking to a non-colour socket forces `Non-Color`, so roughness/metallic/normal maps are not gamma-decoded. Pass `colorspace` only when the file disagrees with its use.
- The path is on the machine running Blender, not on yours.

```python
load_image_texture(material="Skin", path="/assets/textures/skin_normal.png", hook_to="Normal")
```

### material_list

List materials, either every one in the file or one object's slots.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `object` | string | object name; omit for the active object | none | no |
| `limit` | integer | cap on the returned list (counts stay exact) | `1000` | no |

Returns: `{count, materials, truncated}`; each entry carries `users` and a `principled` summary.

Gotchas:

- A material with `users: 0` is dropped on file reload unless `fake_user` is set.
- Pass `object` to see slot order; empty slots appear as `{"slot": n, "name": null}`.

```python
material_list(object="Body", limit=100)
```

### remove_node

Delete a node from a material's shader tree.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `material` | string | material name | — | yes |
| `node` | string | node name from the graph dump | — | yes |

Returns: `{material, removed, was_material_output, node_count, material_output_nodes, warning}`.

Gotchas:

- Every link touching the node goes with it and nothing is re-routed — a node removed mid-chain leaves a gap you must re-link.
- Deleting `Material Output` is allowed and leaves the material rendering black; the result carries a `warning`.

```python
remove_node(material="Skin", node="Noise Texture")
```

### set_node_prop

Change one setting on a node — either a node attribute or an input socket.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `material` | string | material name | — | yes |
| `node` | string | node name from the graph dump | — | yes |
| `prop` | string | property name, or input socket name/index | — | yes |
| `value` | any | number for VALUE, `[R,G,B,A]` 0-1 for RGBA, `[x,y,z]` for VECTOR, enum identifier string, or a datablock name for a pointer | — | yes |

Returns: `{material, node, target, value, linked}`; `target` is `"property"` or `"socket"`, and `linked:true` warns you the value is being ignored.

Gotchas:

- `linked: true` in the result means you just set a value nothing reads — the link wins.
- Principled sockets use the 4.x/5.x names, so `Emission` and `Specular` raise with the new name in the message.
- Shader-type sockets have no value at all; link into them instead.

```python
set_node_prop(material="Skin", node="Principled BSDF", prop="Roughness", value=0.32)
```

### set_viewport_shading

Switch how the 3D viewport draws.

**GUI Blender only** — needs a real 3D Viewport; refuses under
`blender --background`.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `type` | string | type enum | none | no |
| `color_type` | string | SOLID-shading tint source | none | no |
| `studio_light` | string | studio HDRI / matcap name | none | no |

Returns: `{type, color_type, studio_light, light, available_types, available_color_types, available_studio_lights}` — one call tells you what this build supports.

Gotchas:

- `color_type` only applies in SOLID shading. `RANDOM` is the quickest way to tell adjacent objects apart in a screenshot.
- Changes the viewport, not the render — it does not affect `render_frame`.
- Read `available_studio_lights` from the result rather than guessing a name.

```python
set_viewport_shading(type="SOLID", color_type="RANDOM")
```

---

## uv

9 tools. Seams, unwrapping, packing and layout diagnostics. All headless. Remesh BEFORE any of this — `voxel_remesh` destroys UVs.

### mark_seams

Mark (or clear) UV seams — the cuts `unwrap` opens the mesh along.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `edges` | array|string | list of edge indices, or the string `SHARP` | — | yes |
| `object` | string | object name; omit for the active object | none | no |
| `clear` | boolean | remove seams from these edges instead of adding them | `false` | no |
| `angle` | number | RADIANS threshold for `edges="SHARP"`. Default 0.524 (30°) | none | no |

Returns: `{object, edges_marked, cleared, total_seams}`.

Gotchas:

- `angle` is RADIANS. Default 0.524 (30°).
- Without seams `unwrap` produces one stretched island. If you do not want to author seams, use `smart_uv_project`.

```python
mark_seams(edges="SHARP", object="Hull", angle=0.5236)
```

### pack_islands

Repack existing UV islands to use the 0-1 space efficiently.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `object` | string | object name; omit for the active object | none | no |
| `margin` | number | 0-1 gap between islands | none | no |
| `rotate` | boolean | allow rotating islands; turn OFF when texture direction matters | none | no |
| `scale` | boolean | allow scaling islands to fit | none | no |
| `merge_overlap` | boolean | merge islands that overlap exactly | none | no |
| `shape_method` | string | `CONCAVE` (tightest), `CONVEX`, `AABB` (fastest) | none | no |
| `margin_method` | string | how `margin` is measured | none | no |

Returns: `{object, applied}`.

Gotchas:

- Turn `rotate` off when texture direction matters (wood grain, text).
- Requires existing UVs.

```python
pack_islands(object="Hull", margin=0.02, rotate=True, shape_method="CONCAVE")
```

### smart_uv_project

Automatic UVs — splits the mesh by angle, no seams needed.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `object` | string | object name; omit for the active object | none | no |
| `angle_limit` | number | RADIANS split threshold. Blender's default 1.152 (66°) | none | no |
| `island_margin` | number | 0-1 gap between islands | none | no |
| `area_weight` | number | 0-1 bias toward face area | none | no |
| `correct_aspect` | boolean | compensate for a non-square texture aspect | none | no |
| `scale_to_bounds` | boolean | expand the layout to fill the whole 0-1 UV space | none | no |

Returns: `{object, applied, uv_layers}`.

Gotchas:

- The right first choice after sculpting or remeshing, when there are no seams.
- `angle_limit` is RADIANS — 1.152 (66°) is Blender's default.
- Leave at least `island_margin=0.02` when baking or neighbouring islands bleed.
- Produces many small islands: fine for baking, awkward for hand-painting.

```python
smart_uv_project(object="Body", angle_limit=1.152, island_margin=0.02)
```

### unwrap

Unwrap the mesh along its marked seams.

**vs:** `smart_uv_project` — unwrap needs authored seams and gives layouts a human can paint on; smart project needs nothing and gives many small islands, fine for baking.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `object` | string | object name; omit for the active object | none | no |
| `method` | string | `ANGLE_BASED` (default), `CONFORMAL`, `MINIMUM_STRETCH` | `ANGLE_BASED` | no |
| `margin` | number | 0-1 space between islands | none | no |
| `fill_holes` | boolean | close holes in the UV islands | none | no |
| `correct_aspect` | boolean | compensate for a non-square texture aspect | none | no |
| `margin_method` | string | how `margin` is measured | none | no |

Returns: `{object, method, seams, applied, uv_layers, note}` — a `seams: 0` warning is the usual cause of one stretched island.

Gotchas:

- Requires seams. The result warns when the seam count is zero — that is the usual cause of a single distorted island.
- Sets Edit Mode internally and restores your mode after. Works headless.

```python
unwrap(object="Hull", method="ANGLE_BASED", margin=0.02)
```

### uv_layer_create

Add a UV layer.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `name` | string | UV layer name | `UVMap` | no |
| `object` | string | object name; omit for the active object | none | no |
| `active` | boolean | which object is the anchor / active | `true` | no |

Returns: `{object, created, layers}`.

Gotchas:

- A second layer is the usual setup for lightmaps or baked AO, keeping the original layout for texturing.
- `active` only affects editing. For a bake you also need `uv_layer_set_active(for_render=true)`.

```python
uv_layer_create(name="Lightmap", object="Hull", active=True)
```

### uv_layer_list

List an object's UV layers, showing which is active for editing and render.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `object` | string | object name; omit for the active object | none | no |

Returns: `{object, layers}` with `active` and `active_render` flags per layer.

Gotchas:

- `active` and `active_render` can differ — a common cause of a bake going to the wrong layer.

```python
uv_layer_list(object="Hull")
```

### uv_layer_remove

Remove a UV layer by name. Cannot be undone by re-adding — UVs are lost.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `name` | string | UV layer name | — | yes |
| `object` | string | object name; omit for the active object | none | no |

Returns: `{object, removed, layers}`.

Gotchas:

- Cannot be undone by re-adding — the UVs are lost.

```python
uv_layer_remove(name="UVMap.001", object="Hull")
```

### uv_layer_set_active

Make a UV layer active for editing, and optionally for rendering.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `name` | string | UV layer name | — | yes |
| `object` | string | object name; omit for the active object | none | no |
| `for_render` | boolean | also make it the layer materials and bakes use | `false` | no |

Returns: `{object, active, active_render}`.

Gotchas:

- Set `for_render=true` when switching layers for a bake; the editing-active layer alone does not affect rendering.

```python
uv_layer_set_active(name="Lightmap", object="Hull", for_render=True)
```

### uv_stats

Diagnose a UV layout before you rely on it.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `object` | string | object name; omit for the active object | none | no |
| `uv_layer` | string | UV layer name | none | no |

Returns: `{object, has_uvs, uv_layer, uv_layers, faces, islands, uv_area, coverage_percent, overlap_likely, bounds, loops_outside_0_1, mesh_surface_area, texel_density_hint}`.

Gotchas:

- `coverage_percent` well under 100 means wasted texture space — repack.
- `overlap_likely: true` means a bake will produce artefacts.
- `loops_outside_0_1` above zero is intentional only if you are using UDIMs.

```python
uv_stats(object="Hull", uv_layer="UVMap")
```

---

## anim

11 tools. Frames, keyframes, interpolation, actions, NLA and OpenGL playblasts.

### assign_action

Assign an action to an object, creating it if it does not exist.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `action` | string | action name; created when missing | — | yes |
| `object` | string | object name; omit for the active object | none | no |
| `create_if_missing` | boolean | create the datablock when it does not exist | `true` | no |

Returns: `{object, action, frame_range}`.

Gotchas:

- Assign a named action BEFORE keying, so takes stay separable and can later be pushed into the NLA with `nla_push_down`.

```python
assign_action(action="Walk", object="Rig", create_if_missing=True)
```

### insert_keyframe

Key a property at a frame, optionally setting its value first.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `data_path` | string | RNA property path, e.g. `location`, `rotation_euler` | — | yes |
| `frame` | integer | frame number; omit for the current frame | none | no |
| `object` | string | object name; omit for the active object | none | no |
| `bone` | string | bone name | none | no |
| `index` | integer | vector component (0=x, 1=y, 2=z); -1 keys all | `-1` | no |
| `value` | array | set before keying; Euler rotations RADIANS, quaternions `[w, x, y, z]` | none | no |

Returns: `{object, data_path, frame, index}`.

Gotchas:

- A bone's rotation channel depends on its `rotation_mode` — keying `rotation_euler` on a quaternion bone will not animate it.
- Pass `bone` and the short `data_path`; the full `pose.bones["name"].<path>` is built for you.
- Euler values are RADIANS; quaternions are `[w, x, y, z]`.

```python
insert_keyframe(data_path="rotation_euler", frame=24, object="Rig", bone="forearm.L", index=-1, value=[-0.6109, 0, 0])
```

### list_actions

List every action in the file with frame range, curve count and users.

No parameters.

Returns: `{count, actions}` with frame range, curve count and users per action.

Gotchas:

- An action with `users: 0` is dropped when the file is saved and reopened unless it has a fake user.

```python
list_actions()
```

### list_keyframes

List an object's animated channels and their keyframes.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `object` | string | object name; omit for the active object | none | no |
| `data_path` | string | RNA property path, e.g. `location`, `rotation_euler` | none | no |
| `limit` | integer | cap on the returned list (counts stay exact) | `200` | no |

Returns: `{object, action, channels, total_keyframes}`; each channel lists `data_path`, `array_index` and every key's frame, value, interpolation and easing.

Gotchas:

- `Action.fcurves` is gone in 4.4+ (slotted actions) — curves live at `action.layers[].strips[].channelbags[].fcurves`. This tool handles both layouts, so read from here rather than via `execute_python`.

```python
list_keyframes(object="Rig", data_path="location", limit=200)
```

### nla_push_down

Push the active action down into a new NLA strip.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `object` | string | object name; omit for the active object | none | no |
| `track_name` | string | NLA track name | none | no |

Returns: `{object, track, strip, frame_start, frame_end}`.

Gotchas:

- Leaves the object with NO active action, ready for the next take. Non-destructive — the action datablock is unchanged.

```python
nla_push_down(object="Rig", track_name="Walk")
```

### playblast

Render an OpenGL preview of the animation.

**GUI Blender only** — needs a real 3D Viewport; refuses under
`blender --background`.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `out_path` | string | movie file for MP4; a filename PREFIX for PNG sequences | — | yes |
| `frame_start` | integer | frame number | none | no |
| `frame_end` | integer | frame number | none | no |
| `format` | string | `MP4` (H.264) or `PNG` (image sequence) | `MP4` | no |
| `resolution` | array | `[width, height]` pixels | none | no |
| `fps` | integer | whole frames per second | none | no |
| `percentage` | integer | resolution percentage, 1-100 | `100` | no |
| `timeout` | number | seconds to wait for the bridge call | `600.0` | no |

Returns: `{path, exists, bytes, frames, frame_start, frame_end, format, fps}` — the path and frame count, never the video bytes.

Gotchas:

- Viewport-quality preview, not a final render. Use `render_frame` for quality.
- For PNG the `out_path` is a filename PREFIX and Blender appends frame numbers; for MP4 it is the movie file.
- Overwrites `out_path` without asking. Returns the path and frame count, never the video bytes.

```python
playblast(out_path="/tmp/walk.mp4", frame_start=1, frame_end=48, format="MP4", percentage=50, timeout=600.0)
```

### remove_keyframe

Delete a keyframe. Errors when there is no key at that frame.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `data_path` | string | RNA property path, e.g. `location`, `rotation_euler` | — | yes |
| `frame` | integer | frame number; omit for the current frame | none | no |
| `object` | string | object name; omit for the active object | none | no |
| `bone` | string | bone name | none | no |
| `index` | integer | vector component; -1 removes all | `-1` | no |

Returns: `{object, data_path, frame, removed}`.

Gotchas:

- Errors when there is no key at that frame — call `list_keyframes` first rather than guessing.

```python
remove_keyframe(data_path="location", frame=24, object="Rig", index=-1)
```

### set_fps

Set the scene frame rate.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `fps` | integer | whole frames per second | — | yes |
| `fps_base` | number | divisor for fractional rates (30 / 1.001 = NTSC 29.97) | none | no |

Returns: `{fps, fps_base, effective_fps}`.

Gotchas:

- Changing fps does NOT rescale existing keyframes; a 24 fps animation played at 48 runs twice as fast.

```python
set_fps(fps=24)
```

### set_frame

Move the playhead to a frame.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `frame` | integer | frame number; omit for the current frame | — | yes |

Returns: `{frame_current}`.

Gotchas:

- Set this before reading transforms or screenshotting an animated scene — positions are evaluated at the current frame.

```python
set_frame(frame=24)
```

### set_frame_range

Set the scene's playback/render frame range.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `start` | integer | frame number | none | no |
| `end` | integer | frame number | none | no |
| `step` | integer | frame step | none | no |

Returns: `{start, end, step}`.

Gotchas:

- An end before the start is REFUSED. Blender otherwise self-clamps the two against each other and silently accepts a range you did not ask for.

```python
set_frame_range(start=1, end=48, step=1)
```

### set_interpolation

Set interpolation and easing on existing keyframes.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `interpolation` | string | `CONSTANT`, `LINEAR`, `BEZIER`, or an easing curve | none | no |
| `easing` | string | `AUTO`, `EASE_IN`, `EASE_OUT`, `EASE_IN_OUT` | none | no |
| `object` | string | object name; omit for the active object | none | no |
| `data_path` | string | restrict to one channel; omit for every channel | none | no |
| `frame_range` | array | `[first, last]` frame numbers | none | no |

Returns: `{object, keyframes_changed, interpolation, easing}`.

Gotchas:

- Applies to keys that already exist; it never creates any.
- `easing` is only meaningful for the easing interpolation types, not for LINEAR or CONSTANT.

```python
set_interpolation(interpolation="BEZIER", easing="EASE_IN_OUT", object="Rig", frame_range=[1, 48])
```

---

## geonodes

9 tools. Geometry-nodes groups, graph construction and modifier inputs — the parametric control surface.

### add_geonodes_modifier

Attach a Geometry Nodes modifier to an object.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `group` | string | node group name; created pre-wired when missing | none | no |
| `object` | string | object name; omit for the active object | none | no |
| `name` | string | modifier name, used to address it later | `GeometryNodes` | no |
| `create_if_missing` | boolean | create the datablock when it does not exist | `true` | no |

Returns: `{object, modifier, group, inputs}`.

Gotchas:

- Non-destructive: the base mesh is untouched until `apply_modifier`.
- Set values through `geonodes_set_input` on the modifier, not by editing the graph, once a value is exposed as a group input.

```python
add_geonodes_modifier(group="Scatter", object="Terrain", name="Scatter", create_if_missing=True)
```

### geonodes_add_node

Add a node to a geometry node group.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `group` | string | vertex group name; omit for the active group | — | yes |
| `type` | string | the node's `bl_idname`, e.g. `GeometryNodeTransform` | — | yes |
| `location` | array | `[x, y]` node-editor units — space nodes ~200 apart | none | no |
| `name` | string | node name; Blender may uniquify it | none | no |
| `props` | object | node attribute or input-socket name -> value | none | no |

Returns: `{group, node, type, location, inputs, outputs, applied_props, failed_props}`. Always read `failed_props`.

Gotchas:

- `type` is the `bl_idname`. Find the exact name with `search_python_api`.
- Anything in `props` that does not apply comes back in `failed_props` instead of raising — always check that field.

```python
geonodes_add_node(group="Scatter", type="GeometryNodeDistributePointsOnFaces", location=[-200, 0], props={"Density": 12.0})
```

### geonodes_add_socket

Add an input or output socket to a node group's interface.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `group` | string | vertex group name; omit for the active group | — | yes |
| `name` | string | socket display name | — | yes |
| `socket_type` | string | `NodeSocketFloat`, `NodeSocketVector`, `NodeSocketGeometry`, … | `NodeSocketFloat` | no |
| `in_out` | string | `INPUT` or `OUTPUT` | `INPUT` | no |
| `default` | any | default value for the socket | none | no |
| `min_value` | number | socket minimum | none | no |
| `max_value` | number | socket maximum | none | no |
| `description` | string | tooltip text | none | no |

Returns: `{group, name, identifier, in_out, socket_type}` — keep the `identifier`.

Gotchas:

- Blender 4.0 removed `node_tree.inputs`; this uses `interface.new_socket`.
- `default` / `min_value` / `max_value` are ignored for socket types with no value (geometry).

```python
geonodes_add_socket(group="Scatter", name="Density", socket_type="NodeSocketFloat", in_out="INPUT", default=12.0, min_value=0.0, max_value=200.0)
```

### geonodes_create_group

Create a Geometry Nodes group, pre-wired Group Input -> Group Output.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `name` | string | node group name | `Geometry Nodes` | no |
| `with_default_io` | boolean | pre-wire Group Input -> Group Output | `true` | no |

Returns: `{group, nodes, sockets}`; each socket carries the `identifier` (`Socket_0`, …) the modifier addresses it by.

Gotchas:

- Keep the returned socket `identifier`s (`Socket_0`, `Socket_1`, …) — the modifier addresses inputs by identifier, not by display name.

```python
geonodes_create_group(name="Scatter", with_default_io=True)
```

### geonodes_get_graph

Read a node group's whole graph: interface sockets, nodes and links.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `group` | string | vertex group name; omit for the active group | — | yes |
| `limit` | integer | cap on the returned list (counts stay exact) | `200` | no |

Returns: `{group, type, interface, nodes, links, node_count, link_count, truncated}`.

Gotchas:

- Read this before modifying an existing setup. Unlinked input sockets report their `default_value`; linked ones report `linked: true`, and setting those does nothing.

```python
geonodes_get_graph(group="Scatter", limit=200)
```

### geonodes_link

Connect one node's output to another node's input.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `group` | string | vertex group name; omit for the active group | — | yes |
| `from_node` | string | source node name | — | yes |
| `to_node` | string | destination node name | — | yes |
| `from_socket` | string|integer | output socket name, or 0-based index | `0` | no |
| `to_socket` | string|integer | input socket name, or 0-based index | `0` | no |

Returns: `{group, linked, link_count}` — check `link_count` rose.

Gotchas:

- Linking an incompatible pair silently does NOTHING in Blender rather than erroring. Verify `link_count` rose.
- Index 0 is usually the geometry socket, which makes `0 -> 0` the normal way to chain geometry operations.

```python
geonodes_link(group="Scatter", from_node="Group Input", to_node="Distribute Points on Faces", from_socket=0, to_socket=0)
```

### geonodes_list_inputs

List a Geometry Nodes modifier's inputs with their current values.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `modifier` | string | modifier name, or stack index as a number | — | yes |
| `object` | string | object name; omit for the active object | none | no |

Returns: `{object, modifier, group, inputs}` with each socket's name, `identifier`, type and current value.

Gotchas:

- Call this before `geonodes_set_input` to learn each socket's `identifier`. Geometry sockets report no value — they are wired in the graph, not set on the modifier.

```python
geonodes_list_inputs(modifier="Scatter", object="Terrain")
```

### geonodes_remove_node

Remove a node from a group. Its links go with it.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `group` | string | vertex group name; omit for the active group | — | yes |
| `node` | string | node name from the graph dump | — | yes |

Returns: `{group, removed, node_count}`.

Gotchas:

- Its links go with it and nothing is re-routed — a node removed mid-chain breaks the geometry flow until you re-link.

```python
geonodes_remove_node(group="Scatter", node="Distribute Points on Faces")
```

### geonodes_set_input

Set a Geometry Nodes modifier input value.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `modifier` | string | modifier name, or stack index as a number | — | yes |
| `input` | string | socket display name (`Scale`) or identifier (`Socket_2`) | — | yes |
| `value` | any | number, bool, string, or a list for vector/color sockets | — | yes |
| `object` | string | object name; omit for the active object | none | no |

Returns: `{object, modifier, input, identifier, value}`.

Gotchas:

- An ambiguous display name returns the candidate identifiers so you can disambiguate.

```python
geonodes_set_input(modifier="Scatter", input="Density", value=24.0, object="Terrain")
```

---

## io

6 tools. Import, export and .blend file operations. `open_blend` and `save_blend` over an existing file need `confirm: true` AND the user's explicit OK.

### append_from_blend

Append or link datablocks from another .blend file.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `path` | string | absolute filesystem path | — | yes |
| `datablock_type` | string | objects, materials, meshes, collections, actions, node_groups, images, worlds, armatures, brushes | `objects` | no |
| `names` | array | datablock names to bring in; omit for all of that type | none | no |
| `link` | boolean | `false` appends an independent copy; `true` links a live read-only reference | `false` | no |

Returns: `{path, datablock_type, appended, linked_into_scene, link_mode}`.

Gotchas:

- `link=true` gives a live read-only reference that updates when the source changes; `false` appends an independent copy.
- An unknown name is refused WITH the list of what is actually there — call `list_blend_contents` first to avoid the round trip.

```python
append_from_blend(path="/assets/props.blend", datablock_type="objects", names=["Crate"], link=False)
```

### export_model

Export the scene, or just the selection, to a 3D model file.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `path` | string | absolute filesystem path | — | yes |
| `format` | string | `OBJ`, `STL`, `PLY`, `FBX`, `GLTF`, `USD`, `ABC`, or `auto` (from the extension) | `auto` | no |
| `selected_only` | boolean | export only selected objects; fails fast when nothing is selected | `false` | no |
| `scale` | number | uniform export scale; IGNORED by GLTF and USD | none | no |
| `forward_axis` | string | source/target forward axis convention | none | no |
| `up_axis` | string | source/target up axis convention | none | no |
| `apply_modifiers` | boolean | evaluate modifiers before export (not available for ABC or USD) | none | no |
| `options` | object | raw operator kwargs passed straight through | none | no |
| `timeout` | number | seconds to wait for the bridge call | `180.0` | no |

Returns: `{format, path, bytes, applied_options, ignored_options}`.

Gotchas:

- `scale` is IGNORED by GLTF and USD, which are fixed-unit formats — see `ignored_options`.
- `apply_modifiers` is not available for Alembic or USD, which carry their own evaluation mode.
- `.glb` writes the single-binary variant automatically and is already Y-up for game engines; `.gltf` writes separate files.
- `selected_only` with nothing selected fails fast rather than writing an empty file.
- In 5.2 `wm.fbx_export` does not exist (only `wm.fbx_import`); FBX export goes through `export_scene.fbx`. The tool routes this for you.

```python
export_model(path="/exports/body.glb", format="GLTF", selected_only=True, apply_modifiers=True, timeout=180.0)
```

### import_model

Import a 3D model file into the current scene.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `path` | string | absolute filesystem path | — | yes |
| `format` | string | `OBJ`, `STL`, `PLY`, `FBX`, `GLTF`, `USD`, `ABC`, or `auto` (from the extension) | `auto` | no |
| `scale` | number | uniform import scale; unsupported by some formats — check `ignored_options` | none | no |
| `forward_axis` | string | source/target forward axis convention | none | no |
| `up_axis` | string | source/target up axis convention | none | no |
| `options` | object | raw operator kwargs passed straight through | none | no |
| `timeout` | number | seconds to wait for the bridge call | `180.0` | no |

Returns: `{format, path, created_objects, created_count, applied_options, ignored_options}` — `created_objects` is computed by diffing the scene, so the names are real even after a rename-on-collision.

Gotchas:

- `forward_axis` / `up_axis` are only accepted by OBJ, STL and PLY on import.
- Options the operator does not have come back in `ignored_options` rather than erroring — check that field.
- `created_objects` is diffed from the scene, so the names are real even when Blender renames on collision.

```python
import_model(path="/assets/scan.obj", format="OBJ", scale=0.01, forward_axis="NEGATIVE_Z", up_axis="Y")
```

### list_blend_contents

List the datablocks inside another .blend file, without opening it.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `path` | string | absolute filesystem path | — | yes |
| `datablock_type` | string | objects, materials, meshes, collections, actions, node_groups, images, worlds, armatures, brushes | none | no |

Returns: `{path, contents, counts}`.

Gotchas:

- Cannot inspect the .blend that is currently open — `bpy.data.libraries.load()` refuses it with "Cannot load from the current blend file". Its contents are already addressable by name.

```python
list_blend_contents(path="/assets/props.blend", datablock_type="objects")
```

### open_blend

Open a .blend file, replacing the current scene. Destructive.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `path` | string | absolute filesystem path | — | yes |
| `confirm` | boolean | must be `true` — opening discards all unsaved work | `false` | no |

Returns: `{path, discarded_unsaved_changes, objects}`.

Gotchas:

- DESTRUCTIVE: everything unsaved in the current session is discarded. Always requires `confirm=true` AND the user's explicit OK.
- The refusal message tells you whether the current scene actually has unsaved changes, so you can offer `save_blend` first.

```python
open_blend(path="/projects/character.blend", confirm=True)
```

### save_blend

Save the .blend file.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `path` | string | absolute filesystem path | none | no |
| `confirm` | boolean | required only when `path` names a DIFFERENT existing file | `false` | no |
| `compress` | boolean | write a compressed .blend | `false` | no |

Returns: `{path, bytes}`.

Gotchas:

- `confirm` is required only when `path` names an existing file that is NOT the one currently open.
- Omitting `path` saves over the current file and fails if the scene has never been saved.

```python
save_blend(path="/projects/character.blend", confirm=True, compress=True)
```

---

## docs

5 tools. Version-correct documentation search and retrieval. For an operator's exact parameters use `describe_api` instead — it reads the running build.

### docs_cache_info

Report or clear the documentation cache under ~/.cache/blender-agent-mcp.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `clear` | boolean | empty the cache under `~/.cache/blender-agent-mcp` | `false` | no |

Returns: `{path, entries, bytes, cleared}`.

Gotchas:

- Clear it after a Blender upgrade if search results still point at the previous version.

```python
docs_cache_info(clear=False)
```

### find_tutorials

Find tutorials and community answers about a Blender topic.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `topic` | string | free-text topic | — | yes |
| `level` | string | `beginner` / `intermediate` / `advanced` | none | no |
| `limit` | integer | cap on the returned list (counts stay exact) | `8` | no |

Returns: `{topic, count, results}`, or an empty list plus a `note` when the network is unavailable.

Gotchas:

- Needs network access; returns an empty list with a `note` rather than failing when unavailable.

```python
find_tutorials(topic="weight painting a shoulder", level="intermediate", limit=8)
```

### get_doc_page

Fetch a Blender documentation page and return it as readable markdown.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `url` | string | documentation URL from a search tool | — | yes |
| `max_chars` | integer | cap on returned characters | `12000` | no |

Returns: `{url, title, markdown, anchors, truncated, total_chars, cached}`.

Gotchas:

- Restricted to Blender documentation and community hosts — it is a documentation reader, not a general web fetcher.
- Pages are cached for 6 hours; `truncated` and `total_chars` tell you when content was cut.

```python
get_doc_page(url="https://docs.blender.org/manual/en/latest/sculpt_paint/sculpting/tool_settings/remesh.html", max_chars=12000)
```

### search_blender_manual

Search the official Blender user manual for concepts and workflows.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `query` | string | free-text search terms | — | yes |
| `limit` | integer | cap on the returned list (counts stay exact) | `12` | no |

Returns: `{query, manual_version, count, results}`; each result is `{symbol, role, url, dispname, score}`, best first.

Gotchas:

- Searches the manual's Sphinx inventory, so every result is a real version-correct deep link rather than a guessed URL.
- For an operator's exact parameters use `describe_api` — it reads the running build and cannot be stale.

```python
search_blender_manual(query="voxel remesh", limit=12)
```

### search_python_api

Search the Blender Python API reference for a class, operator or property.

| param | type | units / meaning | default | required |
| --- | --- | --- | --- | --- |
| `query` | string | free-text search terms | — | yes |
| `limit` | integer | cap on the returned list (counts stay exact) | `12` | no |

Returns: `{query, count, results}` of the same shape.

Gotchas:

- Good for finding WHICH symbol you want. Once you know it, `describe_api` gives the authoritative live signature.

```python
search_python_api(query="quadriflow remesh", limit=12)
```

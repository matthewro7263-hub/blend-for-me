# Recipes

Eight end-to-end sequences, written as the literal calls to make. Every argument
below is a real starting value, not a law — adjust for the model in front of you
and say what you changed.

Read the units on every number: **world units** (metres by default), **screen
pixels**, **radians**, or a **0–1 factor**. Brush `size_px` is pixels. Every
angle is radians. Every strength and weight is 0–1.

Before recipe step 1, always run the session-start protocol from `SKILL.md`
(`health` → `get_blender_version` → `get_scene_info` → `viewport_screenshot`) and
use the object names it returns. Names below (`Creature`, `Housing`, `Body`,
`Rig`, `Prop`) are placeholders.

| # | Recipe | GUI required? | Rough wall time |
| --- | --- | --- | --- |
| 1 | Sphere → sculpted creature blockout | **Yes** (strokes, masks, filters) | 3–8 min |
| 2 | Hard-surface prop: boolean → bevel → subsurf | No | 1–3 min |
| 3 | Rig → auto-weight → cleanup chain → pose test | Heatmap step only | 2–6 min |
| 4 | "Fix these bad deformations" triage | Heatmap step only | 2–5 min |
| 5 | PBR material + textures + Cycles/Metal render | No (screenshot step only) | 1–15 min |
| 6 | UV unwrap → GLB for a game engine | No | 1–4 min |
| 7 | Keyframe animation + playblast review | **Yes** (playblast) | 1–3 min |
| 8 | Research first: unknown technique | No | 1–3 min |

---

## 1. Sphere → sculpted creature blockout

**Goal.** A recognisable creature silhouette from a sphere: body mass, a spine
ridge, one inflated limb, then a unifying smooth pass.

**Prerequisites.** GUI Blender. `health` must report a viewport. Sculpt strokes,
masks and mesh filters all segfault under `--background`, which is why the
handler refuses before invoking the operator. If `has_view3d` is false, stop and
tell the user — do not substitute `execute_python`.

**1. Checkpoint.** Name it for what it precedes.

```
undo_checkpoint(label="before creature blockout")
```

**2. Base mass.** For `uv_sphere`, `size` is the **radius** in world units, so
this is a 2-unit-tall ball sitting on the floor.

```
create_primitive(kind="uv_sphere", size=1.0, location=[0, 0, 1.0], segments=32, ring_count=16, name="Creature")
```

**3. Bake the transform** before remeshing — voxel size is measured in world
units and a scaled object gives you a voxel grid you did not ask for.

```
apply_transforms(names=["Creature"], rotation=true, scale=true)
```

**4. Uniform sculptable density.** `voxel_size` is world units and cost scales
roughly cubically. On this 2-unit object: 0.03 ≈ 14k verts (~2 s), 0.02 ≈ 31k
(~5 s), 0.01 ≈ 125k (~30 s and rarely needed for a blockout).

```
voxel_remesh(object="Creature", voxel_size=0.03, preserve_volume=true)
mesh_stats(name="Creature")
```

Check `counts.vertices` landed near 14k and `diagnostics.is_watertight` is true.
A watertight result with a negative `volume_signed` means inverted normals — fix
with `mesh_recalculate_normals(name="Creature")` before sculpting.

**5. Enter sculpt and set symmetry.** Symmetry mirrors about the object's
**local origin**, which step 2 left at the sphere centre.

```
enter_sculpt(object="Creature")
sculpt_symmetry(x=true, y=false, z=false)
get_sculpt_state()
```

Read `get_sculpt_state` for whether *unified* size/strength is on. That decides
where `sculpt_set_brush` has to write; it reports which field it used.

**6. Body pass — Clay Strips.** `size_px` is **screen pixels**, so its world
footprint depends on zoom. 40–80 is the normal working range.

```
sculpt_set_brush(name="Clay Strips", size_px=60, strength=0.5, direction="ADD")
stroke_curve(control_points=[[0, -0.55, 0.35], [0, -0.2, 0.6], [0, 0.25, 0.5], [0, 0.6, 0.2]], steps=20, size_px=65)
```

Stroke tools return a screenshot by default (`return_screenshot=true`). **What
it should show:** a raised ridge running front-to-back along the top of the
sphere, thicker in the middle, both sides equal because X symmetry is on. If it
shows nothing, check `dropped_points` — points projecting behind the camera are
reported there, not silently skipped.

**7. Rough a limb.** Straight line, smaller brush, fewer steps.

```
stroke_line(a=[0.35, -0.15, 0.1], b=[0.8, -0.1, -0.6], steps=14, size_px=45)
viewport_screenshot(max_size=1024)
```

**What it should show:** a stub protruding down-and-out on both sides. Below ~8
steps a stroke reads as separate dabs rather than a continuous limb.

**8. Mask the limb, then inflate only it.** `mask_from_selection` is the one
mask tool that works headless because it writes the mask attribute directly.
`invert=true` masks the *unselected* vertices, i.e. protects everything except
your box.

```
mesh_select_geometry(name="Creature", domain="VERT", box_min=[0.3, -0.6, -0.9], box_max=[1.2, 0.4, 0.3], space="OBJECT")
mask_from_selection(object="Creature", invert=true)
sculpt_mesh_filter(type="INFLATE", strength=0.35, iterations=2, return_screenshot=true)
```

`sculpt_mesh_filter` defaults to `return_screenshot=false` — pass `true`.
**What it should show:** the limb thickened, the body untouched. Strength above
~1 with several iterations blows the mesh apart; step up from 0.35 and look.

**9. Clear the mask** before the next global operation, or the filter only ever
touches one region and you will chase the reason for ten minutes.

```
clear_mask(object="Creature")
```

**10. Unifying smooth pass.**

```
sculpt_mesh_filter(type="SMOOTH", strength=0.5, iterations=2, return_screenshot=true)
```

**11. Re-remesh** once strokes have stretched the topology. Tighter voxels now
that the silhouette is real.

```
undo_checkpoint(label="before second remesh")
voxel_remesh(object="Creature", voxel_size=0.02, preserve_volume=true)
mesh_stats(name="Creature")
viewport_screenshot(max_size=1024)
```

Remesh **destroys UVs and vertex groups**. Do all remeshing before unwrapping
(recipe 6) or weighting (recipe 3).

**Radial features** (spikes, a frill, a crown) — radial symmetry was removed in
Blender 5.x; `radial_strokes` is the replacement. `radius` here is **world
units**, unlike `size_px`.

```
sculpt_set_brush(name="Crease", size_px=35, strength=0.7)
radial_strokes(center=[0, 0.55, 0.7], radius=0.4, count=8, steps=10, axis="Z", inward=false)
```

**Honest edge.** Nothing here judges whether the proportions read correctly.
Screenshot and assess, or ask the user. Three "successful" strokes on a shape
that already looked wrong are still three wasted strokes.

---

## 2. Hard-surface prop: primitives → boolean → bevel → subsurf

**Goal.** A bored-out housing block with edges that catch light and a clean
subdivided surface.

**Prerequisites.** None — works headless. Only the verification screenshots need
a GUI.

**Why bevel before subsurf.** Subsurf rounds a corner by how much geometry sits
near it. A bevel adds the supporting loops that hold the corner tight, so
Bevel at stack index 0 and Subsurf below it gives crisp edges on a smooth body.
Reverse them and the bevel runs on the already-quadrupled cage: you get a
hairline bevel on every subdivided edge and a mushy corner. Same rule as Mirror
before Subsurf.

**1. Checkpoint and blocks.** For `cube`, `size` is the **full edge length**.
For `cylinder`, `size` is the **radius** and `depth` is the height. `rotation`
is radians — 1.5708 is 90°.

```
undo_checkpoint(label="before boolean prop")
create_primitive(kind="cube", size=1.0, location=[0, 0, 0.5], name="Housing")
create_primitive(kind="cylinder", size=0.28, depth=1.6, vertices=32, location=[0, 0, 0.5], rotation=[1.5708, 0, 0], name="Bore")
```

**2. Bake transforms on both.** Bevel, Solidify and Remesh all measure in world
units; a non-uniform object scale makes them visibly wrong.

```
apply_transforms(names=["Housing", "Bore"], rotation=true, scale=true)
```

**3. Cut.** `EXACT` is Blender's robust solver. `FLOAT` is the fast approximate
one — it was called `FAST` before 5.x and `"FAST"` is still accepted and
translated.

```
add_boolean(object="Housing", target="Bore", operation="DIFFERENCE", solver="EXACT")
apply_modifier(object="Housing", modifier="Boolean", timeout=120)
delete_objects(names=["Bore"])
```

The cutter is not hidden or deleted for you — delete it or it still renders.

**4. Repair before you build on it.** Booleans leave doubles and occasionally
flipped winding.

```
mesh_merge_by_distance(name="Housing", threshold=0.0001)
mesh_recalculate_normals(name="Housing")
mesh_stats(name="Housing")
```

Check `diagnostics.non_manifold_edges` is 0 and `faces_by_kind.ngons` is what
you expect (a boolean cut through a flat face legitimately produces n-gons).

**5. Bevel as a modifier**, so the width stays tweakable. Read the property
names from your build rather than trusting this list.

```
add_modifier(object="Housing", type="BEVEL", name="Bevel", settings={"width": 0.012, "segments": 2, "limit_method": "ANGLE", "angle_limit": 0.5236, "profile": 0.5, "harden_normals": false})
list_modifiers(object="Housing", include_properties=true)
```

`width` is world units — 0.005–0.05 is the hard-surface range. `angle_limit` is
**radians** (0.5236 = 30°). `segments` 1 is a chamfer, 2–4 reads as rounded,
above ~6 rarely pays for itself.

If you want the bevel welded into the mesh instead, `mesh_bevel` is the
destructive path and it needs an **EDGE** selection:

```
mesh_select_geometry(name="Housing", domain="EDGE", select_all=true)
mesh_bevel(name="Housing", width=0.012, segments=2, profile=0.5, clamp_overlap=true)
```

**6. Subsurf under it.** Each level quadruples faces — keep `levels` at 1–2
while working, let `render_levels` carry the quality.

```
add_subsurf(object="Housing", levels=2, render_levels=3)
list_modifiers(object="Housing", include_properties=false)
```

**7. Fix stack order if needed.** Index 0 = top = evaluated first.

```
reorder_modifier(object="Housing", modifier="Bevel", index=0)
```

**8. Shading.** `angle` is **radians**, not degrees — passing 30 smooths
everything. Requires Object Mode.

```
set_mode(mode="OBJECT", object="Housing")
mesh_shade_auto_smooth(name="Housing", angle=0.5236)
viewport_screenshot(shading_mode="MATERIAL")
```

**What it should show:** flat faces flat, the bore's cylindrical wall smooth, a
bright highlight line along every hard edge. Faceted curves mean the angle was
too tight; a mushy corner means Subsurf is above Bevel.

**Panel detail**, if wanted — inset then extrude, no re-selection needed because
inset leaves its faces selected:

```
mesh_select_geometry(name="Housing", domain="FACE", normal=[0, 0, 1], normal_angle=0.1)
mesh_inset(name="Housing", thickness=0.06, depth=0.0)
mesh_extrude(name="Housing", normal_offset=-0.015)
```

---

## 3. Rig → auto-weight → full cleanup chain → pose test

**Goal.** A bound, export-clean skin with no unweighted vertices and at most 4
influences per vertex.

**Prerequisites.** A mesh named `Body` in Object Mode with transforms applied.
Everything except the heatmap works headless.

**1. Checkpoint and build the skeleton.** `head`/`tail` are in
**armature-object local** space; `roll` is radians. Name the left side `.L` or
`symmetrize_bones` produces nothing and still succeeds.

```
undo_checkpoint(label="before rig build")
create_armature(name="Rig", location=[0, 0, 0], bone_tree=[
  {"name": "root",        "head": [0, 0, 0.0],   "tail": [0, 0, 0.2],  "use_deform": false},
  {"name": "spine",       "head": [0, 0, 0.9],   "tail": [0, 0, 1.25], "parent": "root"},
  {"name": "chest",       "head": [0, 0, 1.25],  "tail": [0, 0, 1.55], "parent": "spine", "connect": true},
  {"name": "upper_arm.L", "head": [0.18, 0, 1.5],"tail": [0.55, 0, 1.42], "parent": "chest"},
  {"name": "forearm.L",   "head": [0.55, 0, 1.42],"tail": [0.9, 0, 1.3], "parent": "upper_arm.L", "connect": true},
  {"name": "hand.L",      "head": [0.9, 0, 1.3], "tail": [1.02, 0, 1.26], "parent": "forearm.L", "connect": true}
])
```

**2. Mirror the side.** `NEGATIVE_X` copies +X bones to −X.

```
symmetrize_bones(armature="Rig", direction="NEGATIVE_X")
list_bones(armature="Rig", space="DATA", limit=100)
```

Read the returned names — collisions get `.001` appended silently.

**3. Bind.** `AUTOMATIC` is bone-heat. It fails outright on non-manifold
geometry or bones outside the mesh volume; `ENVELOPE` is the crude fallback.
Allow real time on a dense mesh.

```
auto_weights(mesh="Body", armature="Rig", method="AUTOMATIC", xmirror=true, timeout=180)
```

Check `bones_without_group` first — a non-empty list means those bones deform
nothing.

**4. Diagnose the bind** before touching anything.

```
per_bone_weight_summary(mesh="Body")
report_unweighted_verts(mesh="Body", threshold=0.001, limit=200)
report_over_influenced(mesh="Body", max_influences=4)
```

`empty_bones` is the classic symptom of a heat solve that quietly failed on part
of the mesh. Each unweighted vertex comes back with its object-space `co`, which
feeds straight into a bounding-box `verts_spec`.

**5. Patch stranded vertices** using the coordinates the report gave you.

```
vgroup_create(mesh="Body", name="chest")
assign_weights(mesh="Body", group="chest", verts_spec={"min": [-0.12, -0.1, 1.44], "max": [0.12, 0.1, 1.6], "space": "LOCAL"}, weight=1.0, mode="REPLACE")
```

**6. The cleanup chain, in this order.** Order matters: clean removes the dust
that would otherwise occupy a top-4 slot; smoothing *adds* influences, so
`limit_total` must come after it; `normalize_all` must come last because
dropping influences leaves the remainder summing below 1.

```
undo_checkpoint(label="before weight cleanup chain")
clean_weights(mesh="Body", threshold=0.01, group_select_mode="ALL", keep_single=true)
smooth_weights(mesh="Body", factor=0.5, iterations=3, group_select_mode="BONE_DEFORM", timeout=180)
limit_total(mesh="Body", max_influences=4, group_select_mode="BONE_DEFORM")
normalize_all(mesh="Body", lock_active=false, group_select_mode="BONE_DEFORM")
```

`keep_single=true` is not optional when cleaning `ALL` on a rigged mesh —
without it you strand vertices at the origin.

**7. Re-verify.** `influence_histogram` should now top out at 4.

```
report_over_influenced(mesh="Body", max_influences=4)
report_unweighted_verts(mesh="Body", threshold=0.001)
```

**8. Pose test.** Rotation is radians; −1.2 rad ≈ −69° of elbow bend.

```
pose_bone(armature="Rig", bone="forearm.L", rotation_euler=[0, 0, -1.2], space="LOCAL")
viewport_screenshot(max_size=1024)
weight_heatmap(mesh="Body", group="forearm.L", show_contours=true)
```

**What the heatmap should show:** red down the forearm, a smooth green band
across the elbow roughly 1.5–2 vertex rings wide, blue on the upper arm, no
black patches inside the deforming region (black = not in the group at all). A
hard blue-to-red step across one edge loop is the crease you will see in the
pose.

**9. Reset.** This clears pose channels only — it removes no keyframes and
disables no constraints.

```
reset_pose(armature="Rig")
```

**Optional IK.** `chain_tip` is the **last bone the solver owns**, so for
upper_arm → forearm → hand that is `forearm`, not `hand`.

```
setup_ik(armature="Rig", chain_tip="forearm.L", chain_length=2, auto_pole=true, target_type="EMPTY")
viewport_screenshot(max_size=1024)
```

Check `is_valid` in the response, then look. `auto_pole` needs a chain that is
already slightly bent in the rest pose; a perfectly straight one gives it no
plane to work in.

---

## 4. "Fix these bad deformations" triage

**Goal.** Turn a vague complaint into a named cause and a targeted fix, without
re-binding the whole mesh.

**Prerequisites.** An already-bound mesh. Heatmap needs GUI; every diagnostic
below is headless.

**1. Establish ground truth.** Never triage from the user's description alone.

```
get_scene_info(limit=200)
vgroup_list(mesh="Body", armature="Rig")
per_bone_weight_summary(mesh="Body")
```

`is_deform_bone: false` on a group is a typo'd name deforming nothing.
`groups_not_deform_bones` catches the same thing from the other side.

**2. Reproduce the failure and look at it.**

```
pose_bone(armature="Rig", bone="upper_arm.L", rotation_euler=[0, 0.9, 0], space="LOCAL")
viewport_screenshot(max_size=1024)
weight_heatmap(mesh="Body", group="upper_arm.L", show_contours=true)
```

**3. Match symptom to cause.**

| What you see | Likely cause | Diagnostic that confirms it | Fix |
| --- | --- | --- | --- |
| Mesh tears; a patch stays pinned | Unweighted vertices | `report_unweighted_verts(threshold=0.001)` | `assign_weights` on the reported `co` box, then `normalize_all` |
| Joint creases into a hard fold | Weights step across one loop | `weight_heatmap(show_contours=true)` — contours bunched | `smooth_weights(factor=0.5, iterations=3, only_selected=true)` |
| Limb drags unrelated geometry | Bleed from a nearby bone | `select_verts_by_weight(min=0.01, max=0.25)` | flatten the strays to 0 with `assign_weights` |
| Deformation collapses toward origin | Weights sum below 1 | `per_bone_weight_summary` — low `mean_weight` | `normalize_all(group_select_mode="BONE_DEFORM")` |
| Fine in Blender, broken in the engine | >4 influences per vertex | `report_over_influenced(max_influences=4)` | `clean_weights` → `limit_total(4)` → `normalize_all` |
| One side good, one side wrong | Asymmetric bind | compare `weight_heatmap` per side | `mirror_weights(all_groups=true)` |

**4. Targeted repair — bleed.** Select the weak stray influences and flatten
them. `assign_weights(weight=0.0)` still leaves the vertex *assigned*; follow
with `clean_weights` to truly unassign.

```
undo_checkpoint(label="before upper_arm.L bleed fix")
select_verts_by_weight(mesh="Body", group="upper_arm.L", min=0.01, max=0.25, limit=2000)
assign_weights(mesh="Body", group="upper_arm.L", verts_spec="SELECTED", weight=0.0, mode="REPLACE")
clean_weights(mesh="Body", threshold=0.01, group="upper_arm.L", keep_single=true)
normalize_all(mesh="Body", lock_active=false, group_select_mode="BONE_DEFORM")
```

**5. Targeted repair — crease.** Smooth only the joint, not the whole group.

```
select_verts_by_weight(mesh="Body", group="forearm.L", min=0.1, max=0.9, limit=4000)
smooth_weights(mesh="Body", factor=0.4, iterations=4, group="forearm.L", only_selected=true, timeout=120)
```

**6. Rebuild the good side onto the bad.** Local axis, not world — a rotated
object's local X is not world X.

```
mirror_weights(mesh="Body", axis="X", all_groups=true, flip_group_names=true, use_topology=true, timeout=180)
```

`use_topology` is **X-axis only**; on Y/Z the tool falls back to KD-tree pairing
and reports `method: "kdtree"` plus `unpaired_vertices`.

**7. Re-verify in the failing pose, then reset.**

```
weight_heatmap(mesh="Body", group="upper_arm.L", show_contours=true)
viewport_screenshot(max_size=1024)
reset_pose(armature="Rig")
```

**When to give up on repair.** If `per_bone_weight_summary` shows several
`empty_bones`, the heat solve failed structurally — re-run
`auto_weights(reuse_binding=true)` after fixing the geometry
(`mesh_merge_by_distance`, `mesh_recalculate_normals`) rather than hand-patching
group after group.

---

## 5. PBR material + image textures + Cycles-on-Metal test render

**Goal.** A textured object and a real render, cheaply iterated.

**Prerequisites.** Texture files on the machine running Blender, at absolute
paths. Everything works headless except the viewport screenshot.

**1. Base material.** All colour components are **0–1**, not 0–255. Metallic is
0 or 1; values between are physically meaningless.

```
create_material(name="PaintedMetal", base_color=[0.18, 0.2, 0.24, 1.0], metallic=1.0, roughness=0.35, ior=1.45)
assign_material(object="Housing", material="PaintedMetal")
```

Check `missing_sockets` in the response — Blender 4.x/5.x renamed sockets
(`Emission Color`, `Specular IOR Level`), and anything absent is reported there
rather than failing the call.

**2. Hook up maps.** `hook_to` picks the colour space for you: colour maps get
sRGB, data maps get Non-Color. `hook_to="Normal"` also inserts a **Normal Map**
node, which is the difference between "subtly wrong" and correct.

```
load_image_texture(material="PaintedMetal", path="/Users/you/tex/housing_basecolor.png", hook_to="Base Color")
load_image_texture(material="PaintedMetal", path="/Users/you/tex/housing_roughness.png", hook_to="Roughness")
load_image_texture(material="PaintedMetal", path="/Users/you/tex/housing_normal.png", hook_to="Normal")
```

**3. Read the real node names** before editing anything. Blender auto-numbers
duplicates, so `Image Texture.002` is a real name you cannot predict.

```
get_node_graph(material="PaintedMetal", limit=200)
```

**4. Add tiling control.** `add_node` takes the `bl_idname`, not the UI label.

```
add_node(material="PaintedMetal", type="ShaderNodeTexCoord", location=[-1200, 0], label="UVs")
add_node(material="PaintedMetal", type="ShaderNodeMapping", location=[-950, 0], label="Tiling")
link_nodes(material="PaintedMetal", from_node="Texture Coordinate", from_socket="UV", to_node="Mapping", to_socket="Vector")
link_nodes(material="PaintedMetal", from_node="Mapping", from_socket="Vector", to_node="Image Texture", to_socket="Vector")
set_node_prop(material="PaintedMetal", node="Mapping", prop="Scale", value=[2.0, 2.0, 1.0])
```

Use the exact `node` names `get_node_graph` returned, not the ones above. If a
socket comes back `linked: true` after `set_node_prop`, you just set a value
nothing reads — the link wins.

**5. Light it.** Units differ per light type and this is the most common
mistake: POINT/SPOT/AREA `energy` is **watts**; SUN `energy` is irradiance in
**W/m²** where 1–10 is sensible. 1000 on a SUN blows the frame out.

```
add_light(type="AREA", energy=250, size=2.0, location=[2.2, -2.4, 2.8], rotation=[0.9, 0, 0.75], shape="SQUARE", name="Key")
add_light(type="SUN", energy=2.5, rotation=[0.6, 0.2, -0.9], size=0.02, name="Fill")
```

For SUN only the **rotation** matters, not the location.

**6. Camera.** `rotation` is radians. A camera at zero rotation looks straight
down; `[1.5708, 0, 0]` looks horizontally along −Y. `lens` is millimetres.

```
add_camera(location=[2.6, -3.4, 1.7], rotation=[1.15, 0, 0.65], lens=50, make_active=true, name="Cam")
```

Nothing aims the camera for you — read `object_bounds(name="Housing")` and
compute, or add a Track To constraint via `execute_python` and say why.

**7. Iterate in the viewport, not the renderer.**

```
viewport_screenshot(shading_mode="MATERIAL", max_size=1024)
```

**8. Probe render small, then commit.** `render_frame(engine="CYCLES")` enables
the Cycles add-on (it is not in the engine list until then) and selects Metal
GPU compute on macOS when available. Scene settings are restored afterwards.

```
render_frame(engine="CYCLES", resolution=[640, 480], samples=48, timeout=300)
```

**What the probe should show:** correct framing, roughly correct exposure, no
black object. Only then:

```
render_frame(engine="CYCLES", resolution=[1600, 1200], samples=256, timeout=900)
```

Rough timings on an M-series Mac at 1600×1200: 48 samples ≈ 10–25 s, 256 samples
≈ 1–3 min. Raise `timeout` before you raise samples, not after the call fails.

**9. Optional: bake detail to a texture.** Cycles-only, slow, and it needs real
UVs — do recipe 6 first. Probe at 256 before committing to 2048.

```
bake(object="Housing", type="AO", size=256, margin=8, samples=32, return_image=true, timeout=300)
```

A black bake almost always means overlapping or absent UVs, not a broken tool.

---

## 6. UV unwrap → GLB export for a game engine

**Goal.** A `.glb` that imports into Unity/Unreal/three.js with correct UVs,
materials and (if skinned) ≤4 influences per vertex.

**Prerequisites.** All remeshing already done — `voxel_remesh` destroys UVs and
vertex groups, so remesh **before** this recipe, never after.

**1. Checkpoint, then UVs.** Two routes:

*Authored seams* — better for hand-painting. `angle` is radians.

```
mark_seams(edges="SHARP", object="Prop", angle=0.5236)
unwrap(object="Prop", method="ANGLE_BASED", margin=0.02)
```

`unwrap` warns when the seam count is zero; that is the usual cause of one
stretched island.

*Automatic* — the right first choice after sculpting or remeshing.
`angle_limit` is radians (1.152 ≈ 66°, Blender's default).

```
smart_uv_project(object="Prop", angle_limit=1.152, island_margin=0.02)
```

**2. Repack and diagnose.**

```
pack_islands(object="Prop", margin=0.02, rotate=true, shape_method="CONCAVE")
uv_stats(object="Prop")
uv_layer_list(object="Prop")
```

Read `uv_stats` like this: `coverage_percent` well under 100 means wasted texture
space — repack; `overlap_likely: true` means a bake will produce artefacts;
`loops_outside_0_1` above zero is intentional only for UDIMs. In
`uv_layer_list`, `active` and `active_render` differing is a classic cause of a
bake landing on the wrong layer.

**3. Pre-export checklist.** Run all of it; each item is a bug an engine
surfaces and Blender does not.

| Check | Call | Pass condition |
| --- | --- | --- |
| Transforms baked | `apply_transforms(names=["Prop"], rotation=true, scale=true)` | scale reads 1,1,1 |
| Origin sane | `set_origin(names=["Prop"], type="ORIGIN_GEOMETRY", center="BOUNDS")` | pivot where the engine expects |
| Geometry sound | `mesh_stats(name="Prop")` | `non_manifold_edges` 0, no unexpected n-gons |
| Normals outward | `mesh_recalculate_normals(name="Prop")` | `volume_signed` positive on a watertight mesh |
| UVs present | `uv_stats(object="Prop")` | `overlap_likely` false |
| Influences (skinned) | `report_over_influenced(mesh="Prop", max_influences=4)` | histogram tops out at 4 |
| Weights normalized (skinned) | `normalize_all(mesh="Prop", group_select_mode="BONE_DEFORM")` | — |
| Triangulated **last** | `mesh_triangulate(name="Prop", quad_method="BEAUTY")` | do this after all modelling |

Triangulate last: triangles break edge loops, so bevels and subdivision misbehave
afterwards. Many engines triangulate on import anyway — skip it if unsure.

**4. Select what you are exporting.** `selected_only=true` with nothing selected
fails fast rather than writing an empty file.

```
select_objects(names=["Prop"], mode="SET", active="Prop")
```

**5. Export.** `.glb` writes the single-binary variant automatically; `.gltf`
writes the separate-files variant.

```
export_model(path="/Users/you/exports/prop.glb", format="GLTF", selected_only=true, apply_modifiers=true, options={"export_yup": true, "export_animations": false, "export_materials": "EXPORT", "export_normals": true, "export_tangents": true, "export_skins": true, "export_influence_nb": 4, "export_all_influences": false, "export_extras": false, "export_cameras": false, "export_lights": false, "export_image_format": "AUTO"}, timeout=180)
```

**Which options actually apply to GLTF.** The generic arguments are mapped
per-format; the ones glTF has no equivalent for come back in `ignored_options`
rather than silently doing nothing. Always read that field.

| Generic argument | glTF operator property | Effect |
| --- | --- | --- |
| `selected_only` | `use_selection` | applies |
| `apply_modifiers` | `export_apply` | applies |
| `scale` | — | **ignored** — glTF is a fixed-unit format |
| `forward_axis` | — | **ignored** — pass `options={"export_yup": true}` instead |
| `up_axis` | — | **ignored** — same |

Everything else goes through `options` verbatim under its real operator name:
`export_animations`, `export_skins`, `export_def_bones`, `export_influence_nb`,
`export_all_influences`, `export_morph`, `export_materials`, `export_image_format`,
`export_normals`, `export_tangents`, `export_extras`, `export_cameras`,
`export_lights`, `export_yup`. Misspell one and it appears in
`ignored_options` — check, do not assume.

**6. Round-trip verify.** The only honest check that the file is what you think.

```
import_model(path="/Users/you/exports/prop.glb", format="GLTF")
get_scene_info(limit=200)
```

Compare `created_objects` against the source: object count, `mesh_stats` triangle
count, `vgroup_list` group names. Then delete the imported copy:

```
delete_objects(names=["prop"], purge_orphan_data=false)
```

**Honest edge.** FBX export goes through `export_scene.fbx` (there is no
`wm.fbx_export` in 5.2), and its option names differ entirely from glTF's — call
`describe_api(path="bpy.ops.export_scene.fbx")` before passing `options` for FBX.

---

## 7. Simple keyframe animation + playblast review

**Goal.** A short looping motion you can actually watch, reviewed as a video.

**Prerequisites.** `playblast` needs **GUI Blender**. Keying, interpolation and
frame-range work headless.

**1. Timing first.** Changing fps does **not** rescale existing keys, so set it
before you key anything. An end before the start is refused outright rather than
silently clamped.

```
set_fps(fps=24)
set_frame_range(start=1, end=48)
```

**2. Name the take.** Keying without an action still works, but a named action
keeps takes separable and pushable into the NLA later.

```
assign_action(action="PropSpin", object="Prop", create_if_missing=true)
```

**3. Key the rotation.** Euler values are **radians** — 6.28319 is one full
turn. `index=-1` keys all three components. `value` sets the property before
keying, so the key records what you meant rather than whatever was there.

```
insert_keyframe(object="Prop", data_path="rotation_euler", frame=1, value=[0, 0, 0], index=-1)
insert_keyframe(object="Prop", data_path="rotation_euler", frame=48, value=[0, 0, 6.28319], index=-1)
```

**4. Key a bounce** on top.

```
insert_keyframe(object="Prop", data_path="location", frame=1,  value=[0, 0, 0.5], index=-1)
insert_keyframe(object="Prop", data_path="location", frame=24, value=[0, 0, 0.95], index=-1)
insert_keyframe(object="Prop", data_path="location", frame=48, value=[0, 0, 0.5], index=-1)
```

**5. Curves.** A constant-speed spin needs LINEAR or it eases at both ends and
the loop stutters. The bounce wants easing.

```
set_interpolation(object="Prop", data_path="rotation_euler", interpolation="LINEAR")
set_interpolation(object="Prop", data_path="location", interpolation="BEZIER", easing="EASE_IN_OUT")
```

`set_interpolation` only edits keys that already exist; it creates none.

**6. Verify the channels before rendering anything.**

```
list_keyframes(object="Prop", limit=100)
set_frame(frame=24)
viewport_screenshot(max_size=1024)
```

**What the screenshot should show:** the prop at the top of its arc, rotated
half a turn. If it has not moved, the object may be on QUATERNION rotation mode —
keying `rotation_euler` on a quaternion object animates nothing. Check with
`get_object_info(name="Prop")`.

**7. Playblast.** `percentage=50` halves the resolution for a fast look. For
`format="PNG"` the path is a **prefix** and Blender appends frame numbers; for
MP4 it is the movie file. It overwrites without asking.

```
playblast(out_path="/Users/you/previews/spin.mp4", frame_start=1, frame_end=48, format="MP4", fps=24, percentage=50, timeout=600)
```

48 frames at 50% typically takes 5–20 s. The tool returns the path and frame
count, never the video bytes — tell the user where to find it.

**Animating a rig** instead of an object: pose, then record. `keyframe_pose`
records the current state; it does not set one. `channels="ROT"` resolves per
bone to whichever rotation channel that bone's `rotation_mode` actually uses,
which is why it beats keying `rotation_quaternion` blindly.

```
set_frame(frame=1)
reset_pose(armature="Rig")
keyframe_pose(armature="Rig", bones=["upper_arm.L", "forearm.L"], frame=1, channels="LOCROT")
pose_bone(armature="Rig", bone="forearm.L", rotation_euler=[0, 0, -1.2], space="LOCAL")
keyframe_pose(armature="Rig", bones=["forearm.L"], frame=12, channels="ROT")
```

---

## 8. "Research first": unknown technique → attempt → verify

**Goal.** Handle a technique you do not already know without burning calls on
guesses. Use this whenever you catch yourself about to invent a parameter name.

Worked example: *"shrinkwrap my retopo cage onto the sculpt."*

**1. Concept before syntax.** The manual answers "how does this work" and "what
is the right workflow"; it does not give signatures.

```
search_blender_manual(query="shrinkwrap modifier project mode snap", limit=8)
```

Every result is a real deep link from the manual's Sphinx inventory, versioned
against the connected Blender — not a guessed URL.

**2. Read one, not five.**

```
get_doc_page(url="<url from step 1>", max_chars=8000)
```

**3. Worked examples, when the manual documents the mechanism but not the
practice.**

```
find_tutorials(topic="retopology with shrinkwrap onto a sculpt", level="intermediate", limit=6)
```

Needs network access; returns an empty list with a `note` rather than failing.

**4. Find the symbol, then get its live signature.** These answer different
questions — reaching for the wrong one wastes a round trip.

```
search_python_api(query="ShrinkwrapModifier wrap_method project", limit=8)
describe_api(path="bpy.types.ShrinkwrapModifier")
```

`describe_api` reads RNA from the **running** Blender, so it can never be
version-wrong. This is what catches the removals: `paint.brush_select` and
`object.vertex_group_transfer_weight` are gone in 5.x, `wm.fbx_export` never
existed, and `Brush.sculpt_tool` does not exist because brushes are assets now.

**5. Checkpoint, then attempt with a dedicated tool.**

```
undo_checkpoint(label="before shrinkwrap attempt")
add_shrinkwrap(object="Retopo", target="Sculpt", wrap_method="PROJECT", offset=0.002, wrap_mode="ON_SURFACE")
```

**6. Discover the remaining properties from the live object** rather than
guessing a second time. `list_modifiers` returns every settable property with its
current value, its type, and the exact enum options in this build.

```
list_modifiers(object="Retopo", include_properties=true)
set_modifier_prop(object="Retopo", modifier="Shrinkwrap", settings={"use_negative_direction": true, "use_positive_direction": true})
```

An unknown property name raises with the full list of settable ones, so a
near-miss self-corrects in one retry.

**7. Verify.**

```
mesh_stats(name="Retopo")
viewport_screenshot(max_size=1024)
```

**What it should show:** the cage lying on the sculpt surface with no
interpenetration. Spikes mean the projection axis is wrong; a floating cage means
`offset` is too large.

**8. Roll back cleanly if it was wrong.** This is what step 5's checkpoint was
for — tell the user exactly what the rollback undoes.

```
undo()
```

**9. Only if no tool covers it.** Say so, explain why, and keep the code short.

```
describe_api(path="bpy.ops.object.shrinkwrap")
execute_python(code="import bpy\nm = bpy.data.objects['Retopo'].modifiers['Shrinkwrap']\nm.subsurf_levels = 1\nprint(m.subsurf_levels)", timeout=30)
```

`execute_python` returns stdout, the repr of a trailing expression, and the full
traceback on failure — the call itself does not raise, so you always get the
diagnosis. It pushes no undo step and validates nothing, which is exactly why it
is the last resort.

**10. If a tool you expect is missing entirely**, the MCP server and the
installed extension may be out of step:

```
list_bridge_commands()
get_blender_version()
```

---

## Timing reference

Rough, on an Apple-silicon Mac. Use these to pick a `timeout`, not to promise a
duration.

| Operation | Typical | Raise timeout when |
| --- | --- | --- |
| `voxel_remesh` @ 0.03 on a 2-unit object | 1–3 s | voxel_size ≤ 0.01 |
| `voxel_remesh` @ 0.01 | 20–60 s | always — it can hit millions of verts |
| `quadriflow_remesh` @ 5000 faces | 30 s – 3 min | any dense input; allow minutes |
| `apply_modifier` on Subsurf level 3 | 2–20 s | dense mesh or stacked booleans |
| `auto_weights` AUTOMATIC, 30k verts | 5–30 s | dense mesh — default is 180 s |
| `smooth_weights`, 4 iterations, 30k verts | 3–15 s | many iterations — default 120 s |
| `render_frame` CYCLES 640×480 @ 48 spp | 10–25 s | — |
| `render_frame` CYCLES 1600×1200 @ 256 spp | 1–3 min | default 300 s is tight |
| `bake` AO 1024 @ 32 spp | 20–60 s | size 2048 or samples > 64 |
| `playblast` 48 frames @ 50% | 5–20 s | long ranges — default 600 s |
| `export_model` GLB, one prop | < 1 s | large scenes with textures |

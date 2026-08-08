# Weight painting

Weights are numbers. Set them, do not paint them. `assign_weights` and
`set_weights` are exact, headless, reproducible and instant; `brush_stroke` and
`weight_gradient` depend on the viewport camera, occlusion and brush falloff.
Reach for a brush only when you genuinely want painterly falloff along a visible
surface.

Units, every time: weights are **0.0–1.0** (never 0–100). `radius_px` in
`brush_stroke` is **screen pixels**. Bounding boxes in `verts_spec` are **world
or object units**, never pixels. `tolerance` in `mirror_weights` is **object-space
units**. Nothing here takes an angle.

GUI Blender is required for exactly three tools in this module: `weight_heatmap`,
`weight_gradient`, `brush_stroke`. Everything else — including `auto_weights`,
`smooth_weights`, `mirror_weights` and all three diagnostics — runs headless.

**Order the pipeline correctly.** `voxel_remesh` destroys UVs *and* vertex
groups. Remesh, then unwrap, then weight. Weighting before a remesh throws the
work away silently — the tool returns `ok` and the groups are simply gone.

---

## 1. The standard chain after auto_weights


> **This section is the single authority for the cleanup chain.** `recipes.md`
> and `rigging-animation.md` abbreviate it inline for readability; where they
> differ in order or in `group_select_mode`, follow the chain here. Copy group
> and bone names from `vgroup_list` / `list_bones`, never from an example — the
> names in these files are illustrative and lookups are literal string matches.

Run this in order. Checkpoint first, because steps 3–5 are destructive and you
cannot un-drop an influence.

```
undo_checkpoint(label="before weight cleanup on Body")
```

### Step 0 — bind

```
auto_weights(mesh="Body", armature="Rig", method="AUTOMATIC", timeout=180.0)
```

Read `bones_without_group` in the result before anything else. A non-empty list
means those deform bones got no group at all and will deform nothing. If the heat
solve errors ("Bone Heat Weighting: failed to find solution"), the mesh is
non-manifold or self-intersecting — check `mesh_stats(name="Body")` for
`non_manifold_edges`, or fall back to `method="ENVELOPE"` and expect to fix a lot
by hand.

Take a baseline immediately, before any cleanup rewrites the evidence:

```
report_unweighted_verts(mesh="Body", armature="Rig", threshold=0.0)
report_over_influenced(mesh="Body", max_influences=4, armature="Rig")
per_bone_weight_summary(mesh="Body", armature="Rig")
```

### Step 1 — clean

```
clean_weights(mesh="Body", threshold=0.01, group_select_mode="ALL", keep_single=true)
```

`group_select_mode` defaults to `ACTIVE`, which would clean exactly one group —
pass `"ALL"` explicitly. `keep_single=true` is not optional on a rigged mesh: it
guarantees every vertex keeps its single strongest influence even if that
influence is below the threshold, so cleaning cannot strand geometry at the
object origin.

**Why:** the bone-heat solve leaves thousands of influences in the 0.001–0.01
range. They are visually irrelevant and they are *ranked*, so they occupy top-4
slots that a real 0.3 influence needs.

**Skip it and:** step 2 keeps dust and drops real weights. Exports bloat with
junk influence entries. Deformation changes for no visible reason.

**Threshold guidance:** 0.01 is the default and right for characters. Use 0.001
when a long thin chain (a tail, a rope, hair) legitimately carries faint
influences you need. Use 0.05 only when speckle in the heatmap tells you the
solve was noisy.

### Step 2 — limit total influences

```
limit_total(mesh="Body", max_influences=4, group_select_mode="ALL")
```

**Why:** most real-time engines take 4 bone influences per vertex. glTF and FBX
both store 4 per set. Blender itself is unbounded, so a mesh that deforms
perfectly in the viewport can deform visibly differently in the engine.

**Skip it and:** the exporter or engine truncates for you — often by group order
rather than by weight — so the influence that gets dropped is arbitrary. That is
the classic "it looked fine in Blender" bug.

Use `max_influences=8` only if you have confirmed the target engine supports 8,
and say so to the user.

### Step 3 — normalize

```
normalize_all(mesh="Body", lock_active=false, group_select_mode="ALL")
```

`lock_active` defaults to `true`, which pins whatever group happens to be active
and redistributes everything else around it. After `limit_total` the active group
is arbitrary, so pinning it is meaningless and skews the result — pass
`lock_active=false` here. Save `lock_active=true` for the case it exists for:
protecting one group you just hand-tuned.

**Why:** `limit_total` drops influences without redistributing them, so a vertex
that had 0.5/0.3/0.15/0.04/0.01 now sums to 0.95.

**Skip it and:** a vertex whose deform weights sum to less than 1 is blended
toward the object origin — the mesh collapses inward at exactly those vertices.
Sums above 1 push it outward. Both read as random spikes when the rig moves.

If this fails with "All groups are locked", `vgroup_lock` locked everything:
`vgroup_lock(mesh="Body", locked=false)` unlocks all groups.

### Step 4 — targeted smoothing

Smooth one group at a time, at the joints that need it. Never
`group_select_mode="ALL"` — that blurs the entire rig at once and destroys
deliberately tight weights (jaw, fingers, eyelids).

```
smooth_weights(mesh="Body", group="UpperArm.L", factor=0.5, iterations=3, expand=0.0, group_select_mode="ACTIVE", timeout=120.0)
smooth_weights(mesh="Body", group="Forearm.L", factor=0.5, iterations=3, expand=0.0, group_select_mode="ACTIVE", timeout=120.0)
```

Prefer several passes at `factor=0.5` over one pass at `factor=1.0` — one hard
pass collapses the group toward its neighbour average and loses the shape.
Starting points: 3 iterations for shoulders/hips, 2 for elbows/knees, 1 for
wrists and ankles, 5+ only if the heatmap still bands.

`expand` grows (positive) or shrinks (negative) the weighted region into
unweighted neighbours. Leave it at `0.0` unless you are deliberately reaching one
loop further: `expand=0.15` grows, `expand=-0.15` pulls a bleeding group back.

The tool enters Weight Paint mode because `object.vertex_group_smooth` refuses to
run in Object mode, and restores your previous mode afterwards. It still works
headless.

**Skip it and:** joints crease into hard faceted folds and shoulders show the
candy-wrapper collapse.

**Smoothing denormalizes.** It rewrites one group without touching the others, so
per-vertex sums drift off 1.0. Re-run step 3 after your smoothing passes. If you
used `expand > 0`, also re-run step 2 — expansion can add a fifth influence:

```
limit_total(mesh="Body", max_influences=4, group_select_mode="ALL")
normalize_all(mesh="Body", lock_active=false, group_select_mode="ALL")
```

### Step 5 — mirror

```
mirror_weights(mesh="Body", axis="X", all_groups=true, flip_group_names=true, use_topology=false, tolerance=0.0001, timeout=120.0)
```

**Why:** the heat solve is not symmetric even on a perfectly symmetric mesh, and
neither is your hand-tuning. Mirroring makes the left match the right exactly.

Use `all_groups=true`. Mirroring a single group moves weight across the body
without moving the complementary groups, which breaks normalization on both
sides.

Facts that change what you call:

| Fact | Consequence |
| --- | --- |
| `axis` is the object's **LOCAL** axis | A rotated object's local X is not world X. Check `get_object_info(name="Body")` first. |
| Blender 5.2's `object.vertex_group_mirror` is **X-only** | `axis="X"` uses it (full name flipping, `use_topology` supported). `axis="Y"`/`"Z"` fall back to a KD-tree pairing; the result reports `method: "kdtree"` and **ignores `use_topology`**. |
| `tolerance` (0.0001 default) is object-space units, Y/Z path only | Raise to 0.001 on a slightly asymmetric mesh. Read `unpaired_vertices` in the result to see whether it was enough. |
| `use_topology=true` pairs by mesh structure, not position | Use it when the mesh is structurally symmetric but the coordinates drifted (a sculpt). X only. |
| Name flipping on the KD-tree path handles `.L/.R`, `_L/_R`, `Left/Right` only | Non-standard bone naming mirrors onto itself. Check `vgroup_list(mesh="Body")` names first. |

Then normalize once more, because mirroring on an imperfectly symmetric mesh
leaves a few vertices short:

```
normalize_all(mesh="Body", lock_active=false, group_select_mode="ALL")
```

### Step 6 — verify per major bone

Headless: re-run all three diagnostics and compare against the baseline from
step 0. GUI: look at the heatmap for every bone that carries a joint. Do not
sample one bone and declare the rig good.

```
weight_heatmap(mesh="Body", group="UpperArm.L", show_contours=true, max_size=1024)
weight_heatmap(mesh="Body", group="Forearm.L", show_contours=true, max_size=1024)
weight_heatmap(mesh="Body", group="Thigh.L", show_contours=true, max_size=1024)
weight_heatmap(mesh="Body", group="Spine.002", show_contours=true, max_size=1024)
```

Then test the deformation, not just the numbers:

```
pose_bone(armature="Rig", bone="Forearm.L", rotation_euler=[0, 0, -1.5708], space="LOCAL")
viewport_screenshot(shading_mode="SOLID", max_size=1024)
reset_pose(armature="Rig", bones=["Forearm.L"])
```

`rotation_euler` is **radians** — 1.5708 is 90 degrees.

---

## 2. Reading a weight_heatmap

`weight_heatmap` returns an image of one group at a time. The ramp:

| Colour | Weight | Meaning |
| --- | --- | --- |
| Black | — | Vertex is **not in the group at all**. Different from blue. |
| Deep blue | 0.0 | In the group, zero influence. |
| Cyan | ~0.25 | Fringe of the falloff. |
| Green | ~0.5 | The 50/50 line. This is where the joint pivot should sit. |
| Yellow/orange | ~0.75 | Core edge. |
| Red | 1.0 | Fully owned by this bone. |

Always pass `show_contours=true` when judging a gradient. Iso-weight lines make
the spread countable in edge loops; colour alone is guesswork at 1024px.

**Good falloff at a joint** — red solid over the bone's own segment; a gradient
band spread across **2–3 edge loops** either side of the joint; the green (~0.5)
contour landing on or within one loop of the joint pivot; fully blue/black by
3–4 loops into the neighbouring bone's territory. Contour lines are roughly
parallel and evenly spaced.

**Red bleeding across a joint** — red continues past the pivot well into the next
bone's segment. That bone drags geometry it should not own; the elbow will pull
the upper arm, the hand will pull the forearm. Fix: shrink the group with
`smooth_weights(..., expand=-0.15)`, or zero the offending region outright with a
bounding-box `assign_weights` (section 4) and re-normalize.

**Hard banding** — red directly adjacent to blue with no green between them, the
transition compressed into 0–1 loops. Contours are bunched into a single line.
This is the crease/candy-wrapper source. Fix: `smooth_weights(mesh=..., group=...,
factor=0.5, iterations=3)`, re-heatmap, repeat up to ~5 iterations before
concluding the mesh simply lacks loops at that joint.

**Speckle** — isolated red or green dots scattered through a blue field, or lone
blue dots inside red. Heat-solve noise, or stray weights from a bad transfer.
Individual vertices will spike when the bone moves. Fix:
`clean_weights(mesh=..., group=..., threshold=0.05, group_select_mode="ACTIVE")`
then one smoothing pass.

**Large black patches over geometry that must deform** — that geometry is not in
this group. Normal if another bone owns it; a bug if it is black in *every*
deform group. Confirm with `report_unweighted_verts`, do not judge by eye.

Two capture gotchas:

- The heatmap shows **the current viewport camera**. Back-facing and occluded
  geometry is invisible. There is no orbit tool — `add_camera` at the angle you need and screenshot with `camera_view=True`, or ask the user to orbit, before capturing an armpit or an inner
  thigh, and take a second shot from the other side.
- `use_render=true` runs `render.opengl`, which **omits the weight colours
  entirely** — the overlay engine draws them. Leave it `false` (the default).
  The tool tells you this in `note` if you get it wrong.

---

## 3. Diagnostics — what the numbers mean

### `report_unweighted_verts(mesh, armature, threshold, limit, timeout)`

Returns `vertex_count`, `deform_groups`, `unweighted_count`, and `vertices[]`
where each entry carries `index`, `total_weight` and object-space `co`.

| Result | Means | Fix |
| --- | --- | --- |
| `unweighted_count: 0` | Pass. Every vertex follows the rig. | — |
| Small count, `co` values clustered in one region | Heat solve missed a pocket — inside the mouth, armpit, crotch, between fingers. | Feed the cluster's bounds straight into a bbox `assign_weights` (section 4), or `transfer_weights` from a working proxy. |
| Count scattered across the whole mesh | Bind partially failed. | Re-run `auto_weights(..., method="ENVELOPE")`, or `reuse_binding=true` after fixing bone placement. |
| Count == `vertex_count` | Nothing bound. | `per_bone_weight_summary` will show every bone empty. Re-bind. |
| 0 at `threshold=0.0` but non-zero at `threshold=0.001` | Dust-level influences only. Technically bound, practically not. | `clean_weights(threshold=0.01, group_select_mode="ALL", keep_single=true)` then `normalize_all`. |
| Tool raises "has no vertex group matching a deform bone" | The bind never created groups, or every group name is misspelled. | `vgroup_list(mesh=..., armature=...)` and check `is_deform_bone`. |

The reported `co` is **object space**. Convert with `get_object_info`'s
`matrix_world` if you want to feed it a `"space": "WORLD"` bbox.

### `report_over_influenced(mesh, max_influences, armature, threshold, limit, timeout)`

Returns `over_influenced_count`, an `influence_histogram` mapping influence count
to vertex count, and per-vertex `groups[]` sorted strongest first.

| Result | Means | Fix |
| --- | --- | --- |
| Histogram keys all `"1"`–`"4"`, `over_influenced_count: 0` | Export-clean for a 4-influence engine. | — |
| Histogram has a long tail at 6–12 | Raw auto-weight dust. Normal before cleanup. | `clean_weights` → `limit_total(4)` → `normalize_all`. |
| Tail persists *after* `clean_weights` | Real influences, not dust — usually too many bones crossing one region. | `limit_total(4)` and accept the loss, or reduce the bone count in that area. |
| A vertex whose `groups[]` names a bone on the other side of the body | Wrong-bone assignment. See below. | Zero that group over the region, then re-normalize. |
| `armature: null` in the result | No armature modifier found, so **every** vertex group was counted, including non-deform helper groups. | Pass `armature="Rig"` explicitly for a meaningful count. |

`threshold=0.0` (the default) counts an explicit zero as an influence — which is
what an exporter does too, so leave it there for pre-flight.

### `per_bone_weight_summary(mesh, armature, limit, timeout)`

One cheap call for the whole rig. Prefer it over paging `get_weights`. Three
fields do the diagnosing:

| Field | Means | Fix |
| --- | --- | --- |
| `bones_with_no_group: []` | Every deform bone has a group. Pass. | — |
| `bones_with_no_group` non-empty | Those bones deform nothing. Either they gained `use_deform` after the bind, or the group was renamed. | `vgroup_create(mesh="Body", name="Toe.L")` then weight it, or re-run `auto_weights(..., reuse_binding=true)`. |
| `empty_bones` non-empty | Group exists, holds zero weight. The heat solve quietly failed on that limb — usually the bone sits outside the mesh volume. | Move the bone inside the volume with `edit_bone`, then `auto_weights(..., reuse_binding=true)`. |
| `groups_not_deform_bones` non-empty | Groups matching no deform bone: either deliberate masking groups, or a typo. | Typo → `vgroup_rename(mesh="Body", name="UpperArm.l", new_name="UpperArm.L")`. Junk → `vgroup_delete(mesh="Body", name="Group")`. |
| A bone's `max` well below 1.0 | Nothing is fully owned by it; its influence is always diluted. | Usually harmless after `normalize_all`. If the limb looks mushy, `levels(mesh=..., group=..., gain=1.4)` then re-normalize. |
| A bone's `vertices` wildly out of proportion (a finger owning 8% of the body) | Wrong-bone assignment. | See below. |

### The wrong-bone case

A vertex weighted to a bone that has no business owning it — a thigh vertex
carrying `Hand.R`, a chest vertex carrying `Foot.L`. The heat solve does this
where two limbs touch in the rest pose, or where the mesh self-intersects.

It does **not** show up as unweighted, and it may not show up as over-influenced.
Three ways to catch it:

1. `per_bone_weight_summary` — the bone's `vertices` count is far larger than its
   size justifies.
2. `report_over_influenced` — read the `groups[]` names, not just the counts. A
   distant bone in the list is the signal.
3. Pose the suspect bone hard and screenshot. Stray geometry flies with it.

```
pose_bone(armature="Rig", bone="Hand.R", location=[0, 0, 0.4], space="LOCAL")
viewport_screenshot(shading_mode="SOLID", max_size=1024)
reset_pose(armature="Rig", bones=["Hand.R"])
```

Fix — zero the group over the offending region, truly unassign, then re-normalize:

```
assign_weights(mesh="Body", group="Hand.R", verts_spec={"min": [-0.35, -0.25, 0.05], "max": [0.35, 0.25, 0.95], "space": "WORLD"}, weight=0.0, mode="REPLACE")
clean_weights(mesh="Body", group="Hand.R", threshold=0.001, group_select_mode="ACTIVE", keep_single=false)
normalize_all(mesh="Body", lock_active=false, group_select_mode="ALL")
```

`assign_weights(weight=0.0)` leaves the vertex **assigned at zero**, which still
counts as an influence to `limit_total` and to most exporters. The `clean_weights`
call is what actually unassigns it. `set_weights(..., remove_zero=true)` does the
same thing per-vertex.

---

## 4. Manual fixes

### Select by weight, then assign

`select_verts_by_weight` writes the selection onto the mesh data (it briefly drops
to Object mode and restores your mode), and `assign_weights(verts_spec="SELECTED")`
consumes it. Flatten a faint fringe:

```
select_verts_by_weight(mesh="Body", group="Head", min=0.001, max=0.15, include_unassigned=false, extend=false, limit=1000)
assign_weights(mesh="Body", group="Head", verts_spec="SELECTED", weight=0.0, mode="REPLACE")
clean_weights(mesh="Body", group="Head", threshold=0.001, group_select_mode="ACTIVE")
normalize_all(mesh="Body", lock_active=false, group_select_mode="ALL")
```

Read `selected` in the result before assigning — it is the true count, while
`vertices` is capped at `limit`. If `selected` is 0, `assign_weights` with
`verts_spec="SELECTED"` raises rather than silently doing nothing.

Push a region to full influence, additively, without touching anything else:

```
assign_weights(mesh="Body", group="Jaw", verts_spec={"min": [-0.12, 0.02, 1.55], "max": [0.12, 0.20, 1.72], "space": "WORLD"}, weight=0.6, mode="ADD")
```

`mode` is `REPLACE`, `ADD` or `SUBTRACT`. `ADD`/`SUBTRACT` clamp into 0–1.
`verts_spec` accepts `"ALL"`, `"SELECTED"`, an explicit index list like
`[0, 5, 12]`, or a bbox `{"min": [...], "max": [...], "space": "LOCAL"|"WORLD"}` —
`LOCAL` is the default when `space` is omitted.

### Computed falloff with set_weights

When you can work the falloff out yourself, do — it is exact, headless, and does
not care where the camera points. Vertex indices come from
`report_unweighted_verts`, `select_verts_by_weight` or `mesh_select_geometry`:

```
set_weights(mesh="Tail", group="Tail.003", weights={"412": 1.0, "413": 0.85, "414": 0.6, "415": 0.35, "416": 0.12, "417": 0.0}, remove_zero=true)
```

`remove_zero=true` unassigns rather than storing a zero — the difference between
"this bone has no influence here" and "an influence of exactly nothing", which
matters to `limit_total` and to every exporter. Keep batches to a few thousand
entries; split larger writes. Out-of-range indices raise, they are not skipped.

### weight_gradient for tails, capes, skirts — GUI only

`paint.weight_gradient` takes **integer screen coordinates**; this tool projects
your 3D points into region pixels for you, which is why it needs a real 3D
viewport and fails under `--background`. It only paints what is **visible and
front-facing** in the current view.

```
weight_gradient(mesh="Cape", start=[0.0, 0.14, 1.62], end=[0.0, 0.34, 0.18], group="Spine.003", type="LINEAR", weight=1.0, space="WORLD", flip=false)
```

- `type="LINEAR"` is a band perpendicular to start→end; `"RADIAL"` is concentric
  around `start`, reaching 0 at `end`.
- Both endpoints must project **in front of** the viewport camera or the call
  fails with an explanation. You cannot orbit: place a camera with `add_camera` and screenshot with `camera_view=True`, or ask the user.
- `space` is `WORLD` (default) or `LOCAL`.
- The result reports `start_region_px`, `end_region_px` and `region_size` — check
  them; a gradient that lands mostly off-screen produces near-uniform weights.

For a skirt or cape, do one gradient per deform bone from top to bottom, then
`normalize_all(lock_active=false)` so the overlapping bands sum to 1:

```
weight_gradient(mesh="Skirt", start=[0.0, 0.0, 1.02], end=[0.0, 0.0, 0.62], group="Skirt.001", type="LINEAR", weight=1.0)
weight_gradient(mesh="Skirt", start=[0.0, 0.0, 0.62], end=[0.0, 0.0, 0.22], group="Skirt.002", type="LINEAR", weight=1.0)
normalize_all(mesh="Skirt", lock_active=false, group_select_mode="ALL")
weight_heatmap(mesh="Skirt", group="Skirt.002", show_contours=true)
```

Prefer `set_weights` whenever you can compute the falloff from vertex Z — it
reaches occluded geometry the gradient cannot, and it repeats identically.

### brush_stroke — last resort, GUI only

```
brush_stroke(mesh="Body", points=[[0.28, 0.0, 1.34], [0.42, 0.0, 1.31], [0.55, 0.0, 1.27]], group="UpperArm.L", weight=1.0, radius_px=45.0, strength=0.6, mode="NORMAL", space="WORLD", pressure=1.0)
```

`radius_px` is **screen pixels**, so its world footprint changes with camera
distance — the same call at a different zoom paints a different amount. Only
front-facing geometry is affected. `mode="INVERT"` paints away from `weight`.
Verify with `weight_heatmap`, never assume.

Blender 5.x weight-paint brushes are assets and there are only four: `Paint`,
`Blur`, `Average`, `Smear`. The tool paints with whichever is active and writes
the paint weight through `unified_paint_settings` when `use_unified_weight` is on
(writing `brush.weight` is silently ignored in that case) — you do not control
which brush is selected from here.

### transfer_weights — clothing onto a body

`object.vertex_group_transfer_weight` was removed in Blender 5.x. This runs
`object.data_transfer(data_type='VGROUP_WEIGHTS')`.

```
transfer_weights(source="Body", target="Jacket", method="POLYINTERP_VNORPROJ", layers_select_src="BONE_DEFORM", name_matching=true, use_create=true, max_distance=0.05, mix_mode="REPLACE", timeout=180.0)
```

| Situation | `method` | Why |
| --- | --- | --- |
| Clothing offset from the body surface | `POLYINTERP_VNORPROJ` | Projects along the target's normal, so it reads the body *under* the garment rather than the nearest point sideways. |
| Retopologised mesh, same silhouette | `POLYINTERP_NEAREST` (default) | Interpolates across the nearest face. The right general choice for differing topology. |
| Identical vertex order (a duplicate) | `TOPOLOGY` | Exact, index for index. |
| Anything else | `NEAREST` | Blocky. Only when the others fail. |

- `layers_select_src="BONE_DEFORM"` sends only groups named after a deform bone —
  use it to avoid dragging masking groups onto the garment. `"ALL"` is the default.
- `max_distance` is in **Blender units, world space** when
  `use_object_transform=true` (the default). 0.05 on a human-scale character stops
  a nearby forearm bleeding onto a torso panel. Omit for no limit.
- `name_matching=true` matches destination groups by name and creates missing
  ones. `false` matches by **index**, which scrambles weights unless both meshes
  have identical group ordering — leave it true.
- Read `groups_created` in the result. An empty list when you expected groups
  means nothing matched.
- Both objects are left selected with `source` active, because the operator
  requires it.

The garment still needs its own modifier and its own cleanup:

```
add_armature(object="Jacket", armature="Rig", use_deform_preserve_volume=true)
clean_weights(mesh="Jacket", threshold=0.01, group_select_mode="ALL", keep_single=true)
limit_total(mesh="Jacket", max_influences=4, group_select_mode="ALL")
normalize_all(mesh="Jacket", lock_active=false, group_select_mode="ALL")
report_unweighted_verts(mesh="Jacket", armature="Rig", threshold=0.0)
```

`use_deform_preserve_volume=true` switches to dual-quaternion skinning, which
stops the candy-wrapper collapse at twisted shoulders and wrists. It is off by
default in Blender and usually worth turning on for characters.

---

## 5. Game-export pre-flight

Run all four checks. Report the actual numbers to the user, not "looks fine".

**1. Max 4 influences per vertex.**

```
report_over_influenced(mesh="Body", max_influences=4, armature="Rig", threshold=0.0)
```

Passing looks like `over_influenced_count: 0` and an `influence_histogram` whose
keys stop at `"4"` — e.g. `{"1": 2104, "2": 3890, "3": 1502, "4": 388}`. Any key
of `"5"` or higher fails. Fix: `clean_weights` → `limit_total(4)` →
`normalize_all`.

**2. No zero-weight vertices.**

```
report_unweighted_verts(mesh="Body", armature="Rig", threshold=0.0, limit=1000)
```

Passing is `unweighted_count: 0`. Then repeat at `threshold=0.001` — vertices that
only pass at 0.0 are held by dust and will look unbound in the engine.

**3. No weights on non-deform bones, no empty bones.**

```
per_bone_weight_summary(mesh="Body", armature="Rig", limit=1000)
```

Passing is `bones_with_no_group: []`, `empty_bones: []`, and
`groups_not_deform_bones` containing only groups you deliberately created for
masking. Anything else there is a typo or junk — `vgroup_rename` or
`vgroup_delete` it. Exporters write every vertex group; a stray group becomes a
stray influence in the engine.

**4. Normalized.**

No tool reports per-vertex weight sums, so there is no direct assertion for this.
Make `normalize_all(lock_active=false, group_select_mode="ALL")` the **last write
in the chain** — after every smooth, mirror and manual assign — and confirm check
2 still passes afterwards. When you need the sums proven, this is one of the few
legitimate `execute_python` cases; say why you fell back to it:

```
execute_python(code="import bpy\no=bpy.data.objects['Body']\nrig=next(m.object for m in o.modifiers if m.type=='ARMATURE')\nd={g.index for g in o.vertex_groups if g.name in {b.name for b in rig.data.bones if b.use_deform}}\nbad=[v.index for v in o.data.vertices if abs(sum(e.weight for e in v.groups if e.group in d)-1.0)>1e-3]\n{'unnormalized': len(bad), 'sample': bad[:10]}", timeout=60.0)
```

Passing is `{'unnormalized': 0, 'sample': []}`.

**Then export.**

```
export_model(path="/Users/you/exports/character.glb", format="GLTF", selected_only=true, apply_modifiers=true, timeout=180.0)
```

`.glb` already writes Y-up, so leave `forward_axis`/`up_axis` alone for Unity and
Unreal unless the user says otherwise. `selected_only=true` fails fast with a
clear message if nothing is selected rather than writing an empty file — select
the mesh and the armature first with `select_objects`.

---

## Honest edges

- **No per-vertex normalization report.** Section 5 check 4 is the workaround.
- **No live deformation quality metric.** Nothing scores a joint. You pose, you
  screenshot, you judge — or you ask the user.
- **`weight_heatmap` shows one group at a time, front-facing only.** Checking a
  20-bone rig costs 20+ images. Lead with the headless diagnostics and spend
  images only where they point.
- **Radial symmetry does not exist in Blender 5.x.** `mirror_weights` is the only
  symmetry tool for weights, and Blender's own operator handles X only — Y and Z
  go through a KD-tree fallback that ignores `use_topology` and understands fewer
  name conventions.
- **`brush_stroke` is not reproducible.** Same call, different camera, different
  result. Treat any brush result as provisional until a heatmap confirms it.
- **`limit_total` is lossy and there is no undo inside the chain.** Once an
  influence is dropped, only `undo` or `undo_checkpoint` gets it back. Checkpoint
  before step 1.

# Sculpting in Blender 5.2 through blender-agent-mcp

Read this before the first `enter_sculpt`. It is doctrine, not a menu.

Units, every time: `size_px` and `radius_px` are **screen pixels**. `radius` in
`radial_strokes`, `voxel_size`, `a`/`b`/`control_points`/`location` are **world or
object units**. All angles are **radians**. All strengths, hardness, `auto_smooth`,
`normal_radius` and `mix_factor` are **0–1**. Mask values are 0–1 where **1 =
protected**.

## GUI gate — check this before planning a session

Sculpt-session operators walk the PBVH and **segfault** under `blender --background`;
they do not merely fail. The handler refuses them before invoking the operator, so
you get an exception instead of a dead Blender. Confirm with `health` /
`get_blender_version` (`has_view3d`) at session start.

| Works headless | GUI Blender required |
| --- | --- |
| `enter_sculpt`, `get_sculpt_state`, `sculpt_list_brushes`, `sculpt_set_brush`, `sculpt_symmetry` | every stroke tool: `sculpt_stroke`, `stroke_line`, `stroke_curve`, `stroke_on_surface`, `radial_strokes` |
| `voxel_remesh`, `quadriflow_remesh`, `dyntopo_enable`, `dyntopo_disable` | `dyntopo_flood_fill`, `sculpt_mesh_filter` |
| `mask_from_selection` (writes the `.sculpt_mask` attribute directly) | `mask_box`, `mask_by_cavity`, `mask_filter`, `invert_mask`, `clear_mask` |
| `multires_set_level`, `add_multires`, `multires_subdivide`, `mesh_stats` | `face_sets_init`, `face_sets_create`, `face_set_visibility`, `reveal_all`, `viewport_screenshot` |

Headless, you can still remesh, subdivide, mask from a selection and drive Multires.
You cannot sculpt. Say so up front rather than planning strokes you cannot run.

---

## 1. Pass structure

Three passes. **Never detail before the silhouette reads.** If you catch yourself
setting `size_px` below 30 while you are still unsure of proportions, stop, undo,
and go back to the primary pass.

### Primary — silhouette

Brushes: `Grab`, `Elastic Grab`, `Snake Hook`, `Inflate/Deflate`, `Pose`.
Density: coarse. `voxel_remesh(voxel_size = 4–5% of bounding-box height)`, target
5k–40k vertices. Detail here is wasted work; you will remesh it away.

Work large: `size_px` 70–110, `strength` 0.6–1.0. One decisive stroke beats six
timid ones — `Grab` and `Elastic Grab` are single-motion brushes, so a stroke that
doubles back on itself just fights itself.

**Leave the primary pass only when all three hold:**

1. `viewport_screenshot(shading_mode="SOLID")` from front, side **and** three-quarter
   reads as the subject with shading ignored — judge the outline, not the shading.
2. `object_bounds(name="Body")` `world.size` matches the brief within ~5%.
3. `mesh_stats(name="Body")` shows `is_watertight: true` and
   `non_manifold_edges: 0`. Snake Hook pulls routinely puncture a mesh; a voxel
   remesh heals it.

### Secondary — planes and masses

Brushes: `Clay Strips` (the workhorse), `Clay`, `Crease Sharp`, `Flatten/Contrast`,
`Scrape/Fill`, `Fill/Deepen`.
Density: `voxel_remesh(voxel_size ≈ 2% of bbox height)`, target 100k–400k vertices.

`size_px` 40–60, `strength` 0.4–0.55. Build in overlapping flat dabs — think planes
meeting at edges, not smooth blends. Where two masses meet, cut the transition with
`Crease Sharp` at `size_px=28`, then soften it back with one `Smooth` pass at
`strength=0.3`.

**Leave the secondary pass only when:**

1. A matcap screenshot (`viewport_screenshot(shading_mode="SOLID", max_size=1024)`)
   shows distinct plane changes, not one continuous blur.
2. No individual stroke reads as a stripe. A visible stripe means `strength` was too
   high or `steps` too low — fix it here, because the tertiary pass amplifies it.
3. You have done your **last** `voxel_remesh`. Remeshing after this point re-averages
   the surface and eats the plane breaks you just built.

### Tertiary — detail

Brushes: `Draw Sharp`, `Pinch/Magnify`, small `Crease Sharp`, `Sharpen`.
Density: `dyntopo_enable` for organic noise you place freehand, or Multires
(`add_multires` → `multires_subdivide`) when you need the low-frequency form to stay
editable and will bake a normal map later.

`size_px` 15–32, `strength` 0.35–0.6. Verify with a zoomed
`viewport_screenshot(max_size=1600)` — nothing larger; big images burn context fast.

**Hard ordering rule:** `voxel_remesh` and `quadriflow_remesh` destroy UVs and vertex
groups. Sculpt → remesh → *then* `smart_uv_project` / `unwrap` / `auto_weights`.
Never the other way round.

---

## 2. Brush selection

Blender 5.x brushes are **assets**. `Brush.sculpt_tool` no longer exists; there is no
enum to pick from. `sculpt_set_brush(name=...)` resolves friendly names to the real
compound asset names. An unmatched name returns the full valid list — read it rather
than guessing twice. `sculpt_list_brushes` is authoritative.

Starting `size_px` below assumes the model fills roughly 60% of a 1024 px viewport.
Zoom in and the same number covers less surface — brush size is screen-relative, so
**re-check it after every viewport change**.

| Asset name (alias accepted) | What it is for | size_px | strength | Direction | Do NOT use it when |
| --- | --- | --- | --- | --- | --- |
| `Draw` | Soft general build-up of mass | 60 | 0.35 | `ADD` builds, `SUBTRACT` dents | You need a crisp edge — it stays mushy at any strength |
| `Draw Sharp` | Wrinkles, panel lines, hard creases | 25 | 0.60 | `SUBTRACT` cuts in | Making volume — it displaces along the *original* normal and tears on big moves |
| `Clay` | Packing volume in like real clay | 55 | 0.50 | `ADD` | Final surface polish; it leaves a lumpy signature |
| `Clay Strips` | **The** secondary-form brush: flat square dabs that build planes | 50 | 0.50 | `ADD` / `SUBTRACT` both useful | The mesh is coarse — needs ~5+ vertices across the brush footprint or it faceted-steps |
| `Crease Sharp` (`Crease`) | Lips, eyelids, folds — pinches while it carves | 28 | 0.50 | `SUBTRACT` carves, `ADD` ridges | You want a *wide* groove — it bunches topology into a spike |
| `Blob` | Spherical bulges: knuckles, muscle bellies, warts | 45 | 0.50 | `ADD` | Anything that should read flat |
| `Inflate/Deflate` (`Inflate`, `Deflate`) | Thicken or thin along normals | 60 | 0.35 | `SUBTRACT` deflates | Thin geometry — it self-intersects within two strokes |
| `Grab` | Move a chunk of surface bodily; proportions | 90 | 1.00 | n/a | Detail work. Stretches topology badly — `voxel_remesh` after |
| `Elastic Grab` (`Elastic Deform`, `Elastic`) | Volume-preserving pull that falls off through the whole form; best proportion tool | 100 | 0.80 | n/a | Dense meshes (it is the slowest brush here) or local detail |
| `Snake Hook` | Pull out tubes: limbs, horns, tentacles | 70 | 0.90 | n/a | The mesh is not dense and dyntopo is off — you get a spike of stretched polygons |
| `Smooth` | Kill noise, relax stretched topology | 60 | 0.40 | n/a | More than 2 passes — it eats form. Re-establish with `Clay Strips` instead |
| `Flatten/Contrast` (`Flatten`, `Contrast`) | Flatten toward the local average plane | 60 | 0.40 | `ADD` flattens, `SUBTRACT` adds contrast | Curved organic silhouettes — it reads as a facet |
| `Scrape/Fill` (`Scrape`) | Cut material away down to the plane: cheekbones, hard edges, wear | 55 | 0.50 | `SUBTRACT` scrapes | Soft forms |
| `Fill/Deepen` (`Fill`, `Deepen`) | Fill concavities up to the plane; remove pits between forms | 55 | 0.45 | `ADD` fills, `SUBTRACT` deepens | Convex surfaces — it does nothing there |
| `Pinch/Magnify` (`Pinch`, `Magnify`) | Pull vertices toward the stroke centre line to sharpen an existing ridge | 30 | 0.35 | `SUBTRACT` spreads instead | As a *first* pass. It only sharpens what already exists, and it bunches topology — remesh after |
| `Pose` | Pivot a whole limb about an inferred joint, no rig needed | 80 | 1.00 | n/a | Surface work — it moves everything past the pivot |
| `Mask` | Paint the mask with a brush stroke | 70 | 1.00 | `SUBTRACT` unmasks | You can express the region geometrically — `mask_from_selection` is deterministic and works headless |

Other assets present in 5.2 and reachable by exact name: `Clay Thumb`, `Grab 2D`,
`Elastic Snake Hook`, `Nudge`, `Thumb`, `Twist`, `Layer`, `Boundary`, `Sharpen`,
`Blur`, `Density`, `Airbrush`, `Trim`, `Pull`, `Plateau`, `Smear`, `Relax Slide`
(alias `relax`), `Relax Pinch`, `Scene Project`, `Grab Silhouette`, `Face Set Paint`,
`Crease Polish`.

Two traps:

- `"clay"` resolves to `Clay`, **not** `Clay Strips`. Exact matches win; ask for
  `"Clay Strips"` when you mean it.
- If unified paint settings are on, writing `brush.size` is silently ignored. The
  tool detects this and writes `unified_paint_settings.size` instead, and reports
  which field it used in `size_written_to`. If a size change appears to do nothing,
  read `get_sculpt_state` — it reports `use_unified_size` and `use_unified_strength`.

```
sculpt_set_brush(name="Clay Strips", size_px=50, strength=0.5, direction="ADD", auto_smooth=0.15)
sculpt_set_brush(name="Crease", size_px=28, strength=0.5, direction="SUBTRACT", hardness=0.5)
sculpt_set_brush(name="Inflate", size_px=60, strength=0.35, direction="SUBTRACT")
```

---

## 3. Detail management — which tool, and how dense

| Situation | Use | Why |
| --- | --- | --- |
| Form changed a lot, topology stretched, parts need welding into one surface | `voxel_remesh` | Uniform density, heals non-manifold geometry, welds intersecting blobs. Works headless |
| Pulling new geometry out of nothing (horns, tentacles) and you cannot predict where detail goes | `dyntopo_enable` | Subdivides only under the brush |
| You need the low-frequency form to stay editable, and will bake a normal map | `add_multires` → `multires_subdivide` | Detail lives per level; drop to level 1, change the big shape, fine detail rides along |
| Final production asset, sculpt is finished | `quadriflow_remesh` | Even quad flow. Tens of seconds to minutes |

These do not mix. Dyntopo discards vertex colours and **cannot coexist with a
Multires modifier**. `voxel_remesh` wipes Multires levels. Pick one per phase.

### Voxel sizes, relative to object scale

Read the object first — never guess from the name:

```
object_bounds(name="Body")
```

Take `world.size[2]` (height, world units) and start at **2–5% of it**.

| Bounding-box height | Blockout (primary) | Working (secondary) | Dense (tertiary) | Floor — check `mesh_stats` first |
| --- | --- | --- | --- | --- |
| 0.5 u | 0.020 | 0.012 | 0.006 | 0.003 |
| **2 u** | **0.080** | **0.050** | **0.020** | **0.010** |
| 5 u | 0.20 | 0.12 | 0.05 | 0.025 |
| 10 u | 0.40 | 0.25 | 0.10 | 0.050 |

**Worked example — a 2-unit-tall object.** Start at `voxel_size=0.05`. Go dense at
`0.02`. Do not go below `~0.01` without reading `mesh_stats` first and predicting the
cost.

```
voxel_remesh(object="Body", voxel_size=0.05, preserve_volume=True)
mesh_stats(name="Body")
```

Predict before you commit: the output is a *surface*, so
**vertices ≈ surface_area / voxel_size²**. `mesh_stats` returns `surface_area` in
object-space units. A 2-unit sphere has `surface_area ≈ 12.6`, so 0.05 → ~5k,
0.02 → ~31k, 0.01 → ~126k, 0.005 → ~500k. Halving `voxel_size` roughly **quadruples
vertices** and **octuples** the volumetric grid the remesher builds — which is why
the time cost feels cubic even though the vertex count is not.

Calibrate after the first remesh: the tool returns `vertices_before` /
`vertices_after` / `faces_before` / `faces_after`. Use the real ratio for the next call.

### Where things get slow

| Vertices | What to expect |
| --- | --- |
| < 50k | Everything snappy. Strokes < 1 s |
| 50k–200k | Strokes 1–3 s. Comfortable working range for secondary forms |
| 200k–500k | `voxel_remesh` 10–60 s. `Elastic Grab` and `Pose` become laggy. `mesh_stats` slows noticeably |
| 500k–1M | `quadriflow_remesh` can exceed its 600 s timeout. Strokes approach the 90 s stroke timeout |
| > 1M | Expect timeouts. Drop to a lower Multires level or `mesh_decimate` before continuing |

Tool timeouts, so you can size the work to fit: strokes **90 s**, `radial_strokes`
**180 s**, `sculpt_mesh_filter` **180 s**, `voxel_remesh` and `dyntopo_flood_fill`
and `multires_subdivide` **300 s**, `quadriflow_remesh` **600 s**, `face_sets_init`
**120 s**, mask filters and dyntopo toggles **60 s**.

### Dyntopo settings

```
dyntopo_enable(object="Body", mode="RELATIVE", detail=10, refine_method="SUBDIVIDE_COLLAPSE")
```

- `mode="RELATIVE"` — `detail` is a screen-space edge length in **pixels**
  (Blender's default 12; 8 fine, 20 coarse). Zoom changes the effective density.
- `mode="CONSTANT"` — `detail` is a world resolution figure (Blender's default 3;
  higher = finer). Zoom-independent, so prefer it when you will orbit a lot.
- `mode="BRUSH"` — `detail` is a **percentage** of brush size (default 25).
- `refine_method="SUBDIVIDE_COLLAPSE"` both adds and removes; `SUBDIVIDE` only grows
  the mesh, which is how a session ends up at 3M vertices without you noticing.

Confirm exact ranges with `describe_api(path="bpy.types.Sculpt")` if it matters.
`dyntopo_flood_fill` applies the current detail level to the **whole** mesh at once —
check `mesh_stats` immediately after; it is the single easiest way to explode a scene.

### Multires

Each level multiplies faces by 4. On a 5k-face quadriflow base:

| Level | Faces | Verdict |
| --- | --- | --- |
| 1 | 20k | Broad form |
| 2 | 80k | Secondary forms |
| 3 | 320k | Working ceiling on most machines |
| 4 | 1.28M | Starts to stall |
| 5+ | 5M+ | Will stall. Don't |

```
quadriflow_remesh(object="Head", target_faces=5000, preserve_sharp=True)
enter_sculpt(object="Head")
add_multires(object="Head")
multires_subdivide(object="Head", levels=1, mode="CATMULL_CLARK")
multires_set_level(object="Head", sculpt_levels=2, levels=2, render_levels=3)
```

`quadriflow_remesh` leaves you in **Object Mode** and does not restore Sculpt Mode —
call `enter_sculpt` after it. `voxel_remesh` drops to Object Mode internally (the
sculpt-undo path crashes headless) and *does* restore Sculpt Mode if you were in it.
`mask_from_selection` also leaves you in Object Mode; the stroke tools re-enter
Sculpt Mode themselves, so it self-heals — but `get_sculpt_state` in between will
report `OBJECT`, which is not a bug.

Subdivide **one level per call** and check `mesh_stats` between. `multires_set_level`
refuses a level above `total_levels` rather than silently clamping.

---

## 4. Symmetry and masking

### Symmetry

```
sculpt_symmetry(x=True, y=False, z=False, feather=True)
```

Mirroring is about the object's **local origin**, not the world origin and not the
mesh centre. Verify before you rely on it:

```
object_bounds(name="Body")
```

Compare `world.center` against the object's `location` (from `get_object_info`). If
they differ along X, every mirrored stroke lands in the wrong place — fix the origin
with `set_origin` first. To rescue a sculpt that already drifted asymmetric, use
`mesh_symmetrize(name="Body", axis="-X", threshold=0.0005)` in Object Mode, then
`mesh_merge_by_distance(name="Body", threshold=0.0005)` if a centre seam survives.

**Radial symmetry was removed in Blender 5.x.** `sculpt_symmetry(radial_counts=8)` is
accepted but applies nothing and reports `radial_supported: false`. Use
`radial_strokes` instead.

```
sculpt_set_brush(name="Snake Hook", size_px=45, strength=0.85)
radial_strokes(object="Body", center=[0, 0, 0.1], radius=1.7, count=10, steps=12, axis="Z")
```

`radius` is **world/object units**, unlike `size_px`. The stroke starts at `center`
and travels outward, so on a solid form the inner samples sit inside the volume and
do nothing useful — overshoot `radius` past the surface (1.7 on a unit sphere) so
the working half of each stroke crosses the skin. Set `inward=True` with `Grab` to
fold a rim toward the hub instead (frills, petals). This runs `count` separate
operator calls under one 180 s timeout — keep `count × steps ≤ ~200`.

### Grow a limb: mask → invert → inflate

This is the literal sequence. `mask_from_selection(invert=True)` masks everything
**outside** your selection, so only the selection stays sculptable.

```
undo_checkpoint(label="before shoulder mass")
mesh_select_geometry(name="Body", domain="VERT", box_min=[0.55, -0.6, -0.2], box_max=[1.2, 0.6, 0.9])
mask_from_selection(object="Body", value=1.0, invert=True)
enter_sculpt(object="Body")
mask_filter(object="Body", filter_type="SMOOTH", iterations=3)
sculpt_mesh_filter(object="Body", type="INFLATE", strength=0.30, iterations=2, return_screenshot=True)
clear_mask(object="Body")
voxel_remesh(object="Body", voxel_size=0.05)
mesh_stats(name="Body")
```

The `mask_filter(filter_type="SMOOTH", iterations=3)` step is not optional — a hard
mask border leaves a visible step where the inflate stops. `GROW` with
`iterations=5` spreads much further than `iterations=1`; use it to feather a mask
outward before a broad filter.

`sculpt_mesh_filter` respects the mask, which is exactly what makes this work.
Keep `strength` in the 0.2–0.4 band and add `iterations` rather than pushing
`strength` above 1.0 — above 1.0 with several iterations it can turn the mesh
inside out. Negative strength reverses the filter (`INFLATE` at `-0.3` deflates).

### Cavity masks for a wear pass

```
mask_by_cavity(object="Helmet", mix_factor=1.0)
sculpt_set_brush(name="Scrape", size_px=45, strength=0.30, direction="SUBTRACT")
stroke_on_surface(object="Helmet", view_path_2d=[[0.30, 0.62], [0.45, 0.66], [0.60, 0.63]], normalized=True)
clear_mask(object="Helmet")
```

`mask_by_cavity` masks the crevices, so with the mask as-is you sculpt only the
**raised** areas — which is what a wear/chipping pass wants. Call
`invert_mask(object="Helmet")` in between to work in the cavities instead (grime,
deepened seams).

### Face sets to isolate a region

```
face_sets_init(object="Creature", mode="LOOSE_PARTS")
face_set_visibility(object="Creature", mode="HIDE_ACTIVE")
... strokes ...
reveal_all(object="Creature")
```

`LOOSE_PARTS` is the right mode straight after `join_objects`. You can also promote
the current mask to a region with `face_sets_create(object="Creature", mode="MASKED")`.

**Always `reveal_all` when you are done.** Hidden geometry is excluded from sculpting
entirely, and the failure mode — strokes that report success and change nothing — is
easy to misdiagnose as a broken brush.

---

## 5. Stroke planning

Every stroke tool takes **object-space** coordinates by default (`space="OBJECT"`).
Pass `space="WORLD"` only when you have world coordinates in hand; convert using
`matrix_world` from `get_object_info`.

Points do not have to sit exactly on the surface — the brush works within its radius
of each sample — but they must sit close, and they must be **in front of the
viewport camera**. Points that project behind the view come back in
`dropped_points`; if you see any, orbit the view to face the work and re-run. If
fewer than 2 points survive, the call raises instead of half-applying.

### Choosing `steps`

One sample per **0.05–0.08 world units** of stroke length. Floor 8, cap ~60.

| Stroke | Length | `steps` |
| --- | --- | --- |
| Short ridge, brow, knuckle | < 0.5 u | 8–16 |
| Muscle form, jawline | 0.5–1.5 u | 16–24 |
| Long curve: spine, tail, tentacle | > 1.5 u | 24–40 |

Below 8 steps the dabs read as separate blobs. Above ~60 you are paying stroke time
for nothing — the brush spacing dominates.

### Placing points on a sphere

To put a point on a sphere of radius `r` at height `z`, the ring radius is
`sqrt(r² − z²)`. On a unit sphere at `z = 0.35` the ring radius is `0.937`, so
`[0, -0.937, 0.35]` sits on the front of the surface (Blender front view looks along
`+Y`, so the front of an object is `−Y`). Land a hair *inside* the surface —
`0.98 × r` — so the brush bites rather than skimming.

### Arcs and curves

`stroke_curve` fits a Catmull-Rom spline that passes **through** every control point.
Three to six controls is plenty; more controls make the path wobble, not smooth.
`stroke_line` is a straight lerp from `a` to `b`.

### Pressure ramps

Only `sculpt_stroke` accepts per-point `pressure` (0–1) and per-point `size` (px).
Taper both ends so the stroke does not start and end in a crater:

```
sculpt_stroke(object="Body", size_px=40, points=[
  {"location": [0.00, -0.93, 0.34], "pressure": 0.15},
  {"location": [0.14, -0.92, 0.37], "pressure": 0.55},
  {"location": [0.28, -0.88, 0.38], "pressure": 1.00},
  {"location": [0.42, -0.84, 0.34], "pressure": 0.75},
  {"location": [0.54, -0.79, 0.25], "pressure": 0.20}
])
```

`mode="INVERT"` flips the brush direction for that one stroke without touching the
brush settings — cheaper than two `sculpt_set_brush` calls when you are alternating.

---

### Worked example A — brow ridge on a unit sphere at the origin

Sphere radius 1, centre `[0, 0, 0]`. Symmetry does the left side for you.

```
undo_checkpoint(label="before brow ridge")
sculpt_symmetry(x=True, y=False, z=False, feather=True)
sculpt_set_brush(name="Clay Strips", size_px=45, strength=0.45, direction="ADD", auto_smooth=0.15)
stroke_curve(object="Sphere", steps=16, control_points=[
  [0.02, -0.93, 0.34],
  [0.20, -0.90, 0.38],
  [0.38, -0.85, 0.36],
  [0.52, -0.80, 0.26]
])
```

Expected: a raised band arcing from just off centre out and slightly down toward the
temple, mirrored to `−X`, sitting about a third of the way up the front of the
sphere. It should read as one continuous ridge with a soft leading edge, not four
lumps. Follow with `Crease Sharp` under it to define the socket:

```
sculpt_set_brush(name="Crease", size_px=26, strength=0.45, direction="SUBTRACT")
stroke_curve(object="Sphere", steps=14, control_points=[
  [0.05, -0.95, 0.18], [0.24, -0.92, 0.20], [0.44, -0.85, 0.19]
])
```

### Worked example B — spine ridge down the back

Same unit sphere. The back is `+Y`. Points follow `y = sqrt(1 − z²)`, pulled to ~0.98 r.

```
sculpt_set_brush(name="Draw Sharp", size_px=32, strength=0.50, direction="ADD")
stroke_curve(object="Sphere", steps=32, control_points=[
  [0.0,  0.69,  0.72],
  [0.0,  0.90,  0.40],
  [0.0,  0.97,  0.02],
  [0.0,  0.90, -0.40],
  [0.0,  0.70, -0.70]
])
sculpt_set_brush(name="Pinch", size_px=26, strength=0.30)
stroke_curve(object="Sphere", steps=32, control_points=[
  [0.0,  0.69,  0.72], [0.0, 0.90, 0.40], [0.0, 0.97, 0.02], [0.0, 0.90, -0.40], [0.0, 0.70, -0.70]
])
```

Expected: a raised spine running pole to pole down the back, sharpened by the second
pass into a defined crest rather than a soft swell. 32 steps for a ~2.9 u path is one
sample per 0.09 u — at the coarse end, which is fine because the curve is smooth. Drop
to 40 if you see beading.

Because the ridge line lies on `x = 0`, symmetry adds nothing here; leave
`sculpt_symmetry` alone or the mirrored stroke doubles the displacement on the seam.

### Worked example C — pull a limb with Snake Hook

Same unit sphere. Pull out along `+X`. Snake Hook stretches topology by design, so
this is a remesh-per-pull operation.

```
undo_checkpoint(label="before right limb pull")
dyntopo_enable(object="Sphere", mode="CONSTANT", detail=6, refine_method="SUBDIVIDE_COLLAPSE")
sculpt_set_brush(name="Snake Hook", size_px=70, strength=0.90)
stroke_line(object="Sphere", a=[0.90, 0.0, 0.28], b=[1.85, -0.20, 0.55], steps=18)
mesh_stats(name="Sphere")
dyntopo_disable(object="Sphere")
voxel_remesh(object="Sphere", voxel_size=0.04, preserve_volume=True)
mesh_stats(name="Sphere")
```

Expected: a tapering tube leaving the sphere near its `+X` equator and rising forward
and up, roughly 1 unit long. 18 steps over a ~1.0 u path is one sample per 0.055 u —
tight enough that the tube is continuous instead of segmented.

Check `mesh_stats` for `non_manifold_edges > 0` after the pull; the voxel remesh at
0.04 heals it and hands back an even surface for the secondary pass. Without dyntopo
or a dense base you get a spike of stretched polygons instead of a limb — that is the
single most common Snake Hook failure.

---

## 6. Verification budget

| After | Check | Looking for |
| --- | --- | --- |
| Every stroke pass | `viewport_screenshot` (stroke tools return one by default — leave `return_screenshot=True`) | The stroke landed where you aimed, at the intensity you wanted |
| Every remesh, flood fill or Multires subdivide | `mesh_stats(name=...)` | `vertices`, `is_watertight`, `non_manifold_edges`, `faces_by_kind` |
| Every mask or face-set change | `get_sculpt_state` | `has_mask`, `has_face_sets`, current brush, unified flags |
| Before any multi-step experiment | `undo_checkpoint(label="...")` | Name it for what it precedes, so you can tell the user what a rollback undoes |
| End of each pass | `object_bounds(name=...)` | Proportions still match the brief |

Rough budget for a head-scale sculpt: 40–80 strokes, ~30 screenshots at the default
`max_size=1024`, 3–5 remeshes. Do not chain more than 2–3 strokes without looking —
a stroke that landed in empty space still returns `ok`.

### When it looks wrong

| Symptom | Cause | Fix |
| --- | --- | --- |
| Spiky shards along the stroke | Mesh too coarse for `size_px`; the brush is pulling individual vertices | `undo`, `voxel_remesh` one step finer, redo |
| Stroke barely visible | `strength` too low, or the area is masked | `get_sculpt_state` → check `has_mask`; `clear_mask` |
| Stroke reports success, nothing changed at all | Geometry is hidden by a face set | `reveal_all` |
| Stroke landed in the wrong place | Wrong `space`, or the object has a non-identity transform | `get_object_info` → read `matrix_world`; use `space="WORLD"` or convert |
| `dropped_points` in the result | Those samples project behind the view | Orbit to face the work and re-run; do not compensate by moving the points |
| `missed_rays` from `stroke_on_surface` | Those 2D points hit nothing | `viewport_screenshot` first and re-aim, or switch to 3D coordinates |
| Mirrored strokes land off-centre | Object origin is not on the symmetry plane | `object_bounds` → compare `world.center` to `location`; `set_origin` |
| Mesh ballooned to millions of vertices | `voxel_size` too small, or `refine_method="SUBDIVIDE"` on dyntopo | `undo`; recompute `voxel_size` from `object_bounds` |
| Inflate blew the mesh apart | `sculpt_mesh_filter` `strength` > 1 with several iterations | `undo`; drop to `strength=0.3` and raise `iterations` |
| Stroke tool timed out at 90 s | Mesh above ~500k vertices | Lower Multires `sculpt_levels`, or `mesh_decimate` before continuing |
| Brush size change had no effect | Unified paint settings are on | Read `size_written_to` in the `sculpt_set_brush` result; `get_sculpt_state` confirms `use_unified_size` |
| Brush name rejected | 5.x asset name differs from the tutorial name | The error lists every valid brush; `sculpt_list_brushes` for the alias map |

### What this toolset genuinely cannot do

- **Judge whether it looks right.** Nothing scores proportion or appeal. Screenshot,
  assess against reference, or ask the user. Never assume a stroke read well.
- **Sculpt headless.** Strokes, masks, face sets and mesh filters need a live sculpt
  session. Headless you get remeshing, `mask_from_selection`, Multires and the
  data API.
- **Radial symmetry.** Removed in 5.x. `radial_strokes` approximates it and will not
  be pixel-identical between passes.
- **Manual retopology.** `quadriflow_remesh` is automatic and takes no guidance
  beyond `preserve_sharp` / `preserve_boundary`. There is no edge-flow authoring.
- **Undo a stroke you did not checkpoint.** `undo` steps back one level; deep
  recovery needs a `undo_checkpoint` you placed beforehand.

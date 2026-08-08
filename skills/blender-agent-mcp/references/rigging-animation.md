# Rigging and animation

Armatures, IK, constraints, pose testing, keyframes, playblasts.

Everything in `rig` and `anim` works **headless** except `playblast`. You still
need a GUI for the tools you verify with — `viewport_screenshot` and
`weight_heatmap` — so a rig built headless is a rig you cannot look at. Say that
to the user before you start.

**Units, every time:** bone `head`/`tail` are world units in **armature-object
local space**; `roll`, `rotation_euler`, `pole_angle` and every constraint angle
are **radians**; `influence`, `mix_factor` and weights are **0–1**; frames are
integers.

---

## 1. The bone-tree spec

`create_armature(name=..., bone_tree=[...], location=[x,y,z])` builds the whole
hierarchy in one EDIT-mode session. Use it to build from nothing; use `add_bones`
with the identical dict shape to extend an existing armature.

Each entry in `bone_tree`:

| Key | Type | Default | Meaning |
| --- | --- | --- | --- |
| `name` | string | **required** | Errors if missing. Collides silently — Blender appends `.001`. |
| `head` | `[x,y,z]` | `[0,0,0]` | Joint start, armature-local world units. |
| `tail` | `[x,y,z]` | `[0,0,1]` | Joint end. `head == tail` is **rejected** — Blender deletes zero-length bones on leaving edit mode. |
| `roll` | number | `0.0` | Twist about the head→tail axis, **radians**. |
| `parent` | string | none | Another bone, in this list or already in the armature. Forward references are fine — parenting is a second pass. |
| `connect` | bool | `false` | Glues this head onto the parent's tail, **moving the head** to get there. Needs a parent. `use_connect` is accepted as an alias. |
| `use_deform` | bool | `true` | Whether binding creates a vertex group for it. **Set `false` on every control, IK target and pole bone.** |

`location` is the armature object's origin in **world** space. Head/tail equal
world coordinates only while `location` is `[0,0,0]` and the object is unrotated
and unscaled — keep it at the origin unless you have a reason not to.

Read `bones[].name` out of the response. That is what was actually created; your
requested name is echoed back as `requested`.

### Worked example — a biped leg

Character 1.75 m tall, facing **-Y**, Z up, armature at the world origin. The
knee sits 4 cm forward of the hip→ankle line: that pre-bend is what makes IK and
auto-pole placement work later.

```
create_armature(
    name="Rig",
    location=[0, 0, 0],
    bone_tree=[
        {"name": "hips",    "head": [0.00,  0.00, 0.98], "tail": [0.00,  0.00, 1.06]},
        {"name": "thigh.L", "head": [0.10,  0.00, 0.96], "tail": [0.11, -0.04, 0.53],
         "parent": "hips",    "connect": False},
        {"name": "shin.L",  "head": [0.11, -0.04, 0.53], "tail": [0.11,  0.00, 0.10],
         "parent": "thigh.L", "connect": True},
        {"name": "foot.L",  "head": [0.11,  0.00, 0.10], "tail": [0.11, -0.14, 0.02],
         "parent": "shin.L",  "connect": True},
        {"name": "toe.L",   "head": [0.11, -0.14, 0.02], "tail": [0.11, -0.24, 0.02],
         "parent": "foot.L",  "connect": True},
    ],
)
```

Resulting lengths: thigh 0.432, shin 0.432, foot 0.161, toe 0.100 world units.
Hip-to-ankle is 0.86 — remember that number, IK wants it.

`thigh.L` deliberately has `connect: False`: the hip joint sits 2 cm below and
10 cm out from the pelvis tail, and connecting would snap it onto the tail and
destroy the offset. Connect only where the joint really is shared.

### Naming so the mirroring tools work

Build **one side only**, at **+X**, suffixed `.L`, then mirror:

```
symmetrize_bones(armature="Rig", direction="NEGATIVE_X")
```

`NEGATIVE_X` (the default) means +X bones are copied to -X. This is the only
place the naming matters and it fails **silently**: a bone named `thigh`
produces nothing and the call still returns success. Check the `created` array —
empty means no bone carried a side suffix. Recognised suffixes are `.L`/`.R`,
`_L`/`_R`, `left`/`right`. Re-running overwrites the mirrored side rather than
duplicating it, so it is safe to iterate on the left and re-symmetrize.

The same suffixes are what `mirror_weights(mesh="Body", flip_group_names=True)`
uses to flip vertex-group names. One naming convention, two tools. Adopt `.L`/`.R`
even for a rig you think will never be mirrored.

Renaming after binding breaks skinning: `edit_bone(name=...)` does **not** rename
the matching vertex groups. Name it right before `parent_mesh_to_armature`.

---

## 2. IK setup

`setup_ik` creates the constraint **and** the objects it needs, then solves the
pole angle numerically. One call.

### Choosing `chain_tip` and `chain_length`

The constraint lives on the **last bone the solver owns**, which is the bone
above the end effector — not the hand or the foot.

| Limb | `chain_tip` | `chain_length` |
| --- | --- | --- |
| Leg (thigh → shin → foot) | `shin.L` | `2` |
| Arm (upper_arm → forearm → hand) | `forearm.L` | `2` |
| Three-segment digitigrade leg | last shin bone | `3` |
| Tentacle / spine reach | tip bone | `4`–`6` |

`chain_length=0` means "all the way to the root" and will swing the entire spine
off the hips. Never use it on a limb.

### Pole placement rule of thumb

The pole sits **in front of the knee/elbow, on the plane the joint already bends
in, at roughly the limb's length away**. `auto_pole=True` derives that direction
for you: it projects the middle joint off the root→tip axis, and the leftover
perpendicular *is* the bend direction. A pre-bent rest pose therefore gives the
right answer; a perfectly straight chain does not, and the code falls back to an
arbitrary world axis. **Bend the chain 2–5 cm before you run IK.**

`pole_distance` defaults to the chain's length **but never below 1.0** armature
units. For the 0.86-unit leg above that floor pushes the pole further out than
you want, so pass it:

```
setup_ik(armature="Rig", chain_tip="shin.L", chain_length=2,
         auto_pole=True, pole_distance=0.85)
```

That creates:

| Created | Kind | Where |
| --- | --- | --- |
| `shin.L_IK` | `PLAIN_AXES` empty | Exactly on the shin's current tail (the ankle), so the chain does not jump |
| `shin.L_pole` | `SPHERE` empty | Knee head + bend direction × 0.85, i.e. ~0.85 units in front of the knee |
| `IK` | constraint on `shin.L` | `chain_count=2`, `use_tail=true`, `use_stretch=false` |

`pole_angle` is **solved**, not guessed, whenever you supply `auto_pole` and omit
it: the target sits where the tip already is, so the correct angle is the one
that leaves the chain unmoved. The solver sweeps -180°…180° in 5° steps then
refines in 0.25° steps — 113 depsgraph evaluations, roughly a second on a small
rig. This is what removes the classic "the leg flipped 90 degrees" failure.
Only pass `pole_angle` explicitly to override it.

Use `target_type="BONE"` instead of the default `"EMPTY"` when you want the
controls to live inside the armature (game rigs, single-object export). The
created bones get `use_deform=False` automatically.

### Verifying it works

Never trust `setup_ik`'s success alone. Four steps:

```
list_bones(armature="Rig", space="POSE")
transform_object(name="shin.L_IK", location=[0.11, -0.30, 0.34])
viewport_screenshot(max_size=1024)
transform_object(name="shin.L_IK", location=[0.11, 0.00, 0.10])
```

1. `is_valid` in the `setup_ik` response — false almost always means a missing
   `subtarget` on an armature target.
2. `list_bones(space="POSE")` shows the constraint list per bone; confirm the IK
   is on `shin.L` and nothing stray landed on `thigh.L`.
3. Moving the target 0.3 units forward must bend the knee **forward**. Sideways
   or backwards means the pole is on the wrong side — re-run with
   `pole_angle` offset by `3.1416` radians, or fix the rest-pose pre-bend and
   re-run `auto_pole`.
4. Put the target back at the ankle and screenshot again; the leg must return to
   the rest silhouette.

### Making the foot follow the control

The IK chain stops at the shin, so the foot dangles. Attach it:

```
add_bone_constraint(armature="Rig", bone="foot.L", type="COPY_ROTATION",
                    settings={"target": "shin.L_IK",
                              "target_space": "WORLD", "owner_space": "WORLD"})
```

---

## 3. Constraint patterns

`add_bone_constraint(armature=, bone=, type=, settings={}, name=)`. Pointer
settings (`target`, `pole_target`, `space_object`) take an object **name string**;
`subtarget` is a **bone name string** and is required whenever `target` is an
armature. Angles are radians, `influence` is 0–1 (not 0–100).

**Discovery trick:** call it once with `settings` omitted. The response's
`writable_settings` lists every property this constraint type accepts on this
Blender build. A wrong key raises an error that prints the same list, and a wrong
`type` prints every valid constraint type. Never guess a property name.

| Type | Use it for | Watch out for |
| --- | --- | --- |
| `COPY_ROTATION` | Foot follows an IK control; head follows a neck control; twist-bone distribution | Set both `target_space` and `owner_space` deliberately — `LOCAL` copies channel values, `WORLD` copies orientation |
| `DAMPED_TRACK` | Eyes, gun barrels, a tail tip aiming at something. Single-axis aim, no roll flipping | `track_axis="TRACK_Y"` is the bone's own head→tail axis, which is what you want almost always |
| `CHILD_OF` | A weapon socket bone following a hand; temporary parenting you want to animate off | Adding it can snap the bone, because the inverse matrix starts as identity. Check `writable_settings` for `set_inverse_pending` and set it `true` |
| `LIMIT_ROTATION` | Stopping a knee hyperextending or an elbow inverting during IK | Limits are radians. `owner_space="LOCAL"` is nearly always what you mean |
| `STRETCH_TO` | Squash-and-stretch limbs, rubber-hose arms | Records a rest length at creation; re-add it if you rescale the rig |

Real calls:

```
add_bone_constraint(armature="Rig", bone="eye.L", type="DAMPED_TRACK",
                    settings={"target": "LookAt_CTRL", "track_axis": "TRACK_Y",
                              "influence": 1.0})
```

```
add_bone_constraint(armature="Rig", bone="forearm_twist.L", type="COPY_ROTATION",
                    settings={"target": "Rig", "subtarget": "hand.L",
                              "use_x": False, "use_z": False,
                              "target_space": "LOCAL", "owner_space": "LOCAL",
                              "mix_mode": "REPLACE", "influence": 0.5})
```

```
add_bone_constraint(armature="Rig", bone="shin.L", type="LIMIT_ROTATION",
                    settings={"use_limit_x": True, "min_x": 0.0, "max_x": 2.3562,
                              "owner_space": "LOCAL"})
```

```
add_bone_constraint(armature="Rig", bone="prop_socket.R", type="CHILD_OF",
                    settings={"target": "Rig", "subtarget": "hand.R"})
```

**There is no object-level constraint tool.** To attach a prop *object* to a
bone, use `parent_objects(child="Sword", parent="Rig", type="BONE", bone="hand.R",
keep_transform=True)`. Anything else object-level needs `execute_python` — say so
when you fall back to it.

Keep controls out of the deform skeleton with bone collections:

```
bone_collection_create(armature="Rig", name="CTRL",
                       bones=["shin.L_IK", "shin.L_pole"])
bone_collection_visibility(armature="Rig", collection="CTRL", is_visible=True)
```

If a collection stays hidden after you show it, something else is soloed —
`bone_collections_list` reports `is_visible` next to `is_visible_effectively`.

---

## 4. Pose-test protocol

Rigs fail at extremes, not at rest. Test each joint at the pose that hurts,
before you animate anything.

The loop, literally:

> Abbreviated. `weight-painting.md` §1 is authoritative for this chain's order and `group_select_mode` arguments — follow it if they differ.

```
undo_checkpoint(label="before knee pose test")
pose_bone(armature="Rig", bone="shin.L", rotation_euler=[1.5708, 0, 0], space="LOCAL")
viewport_screenshot(max_size=1024)
weight_heatmap(mesh="Body", group="shin.L", show_contours=True)
select_verts_by_weight(mesh="Body", group="shin.L", min=0.05, max=0.95)
smooth_weights(mesh="Body", group="shin.L", factor=0.5, iterations=2,
               only_selected=True, group_select_mode="ACTIVE")
normalize_all(mesh="Body", lock_active=False, group_select_mode="BONE_DEFORM")
viewport_screenshot(max_size=1024)
reset_pose(armature="Rig", bones=["shin.L"])
```

Iterate the middle five calls until the silhouette holds. `factor=0.5` with
`iterations=2` is a starting point; raise `iterations` to 4–6 for a candy-wrapper
collapse, drop `factor` to 0.2 when smoothing is bleeding weight into the wrong
limb.

### Extremes to test, in radians

| Joint | Bone | Channel | Test pose | Radians |
| --- | --- | --- | --- | --- |
| Knee | `shin.L` | local X | 90° flex | `1.5708` |
| Knee | `shin.L` | local X | 135° deep kneel | `2.3562` |
| Hip | `thigh.L` | local X | 90° raise | `1.5708` |
| Hip | `thigh.L` | local Z | 45° abduction (splits) | `0.7854` |
| Ankle | `foot.L` | local X | 40° point / 30° flex | `0.6981` / `-0.5236` |
| Elbow | `forearm.L` | local X | 140° full bend | `2.4435` |
| Shoulder | `upper_arm.L` | local Z | 150°, then 180° overhead | `2.6180`, `3.1416` |
| Wrist | `hand.L` | local Z | 60° deviation | `1.0472` |
| Spine | `spine` | local Y | 45° twist | `0.7854` |
| Neck | `head` | local Z | 70° turn | `1.2217` |

Conversions you will keep needing: 15° `0.2618`, 30° `0.5236`, 45° `0.7854`,
60° `1.0472`, 90° `1.5708`, 120° `2.0944`, 180° `3.1416`.

**Sign depends on the bone's roll.** Pose, screenshot, and if the knee bends
forwards instead of backwards, negate the value. Do not reason about it — look.

### Three honest gotchas

- **`reset_pose` does not defeat a constraint.** It clears location, rotation and
  scale channels only. An IK-driven bone snaps straight back to wherever the
  target is. On an IK limb, test by moving the target with `transform_object`,
  not by posing the shin.
- **`reset_pose` does not delete keyframes.** On an animated rig the pose returns
  the instant the frame changes. Use `remove_keyframe` if you meant to delete.
- **`apply_pose_as_rest` is destructive.** Bound meshes are *not* re-fitted —
  they keep their current vertex positions, so the result is only correct if the
  mesh was already deformed into that pose. Blender skips meshes with shape keys
  entirely, and every existing keyframe is now measured against a different rest
  pose. Screenshot before and after, and read `deformed_meshes` in the response.
  Safe use is fixing a bad rest pose on a rig that is not yet bound or animated.

---

## 5. Keyframing

### Objects

```
insert_keyframe(object="Sword", data_path="location", frame=1, value=[0.0, 0.0, 0.0])
insert_keyframe(object="Sword", data_path="rotation_euler", frame=24, value=[0.0, 0.0, 3.1416])
```

`value` must be a **list**, even for one component. Passing it sets the property
first so the key records what you intended rather than whatever happened to be
there. `index=-1` (default) keys all components; `index=2` keys Z only.

### Pose bones

Two routes. Prefer the second.

```
insert_keyframe(object="Rig", bone="shin.L", data_path="rotation_quaternion",
                frame=12, value=[0.7071, 0.7071, 0.0, 0.0])
```

`bone=` builds `pose.bones["shin.L"].rotation_quaternion` for you — pass the
**short** `data_path`. Quaternions are `[w, x, y, z]`, w first, which is
Blender's order and the reverse of many other tools. `[0.7071, 0.7071, 0, 0]` is
90° about local X.

```
pose_bone(armature="Rig", bone="shin.L", rotation_euler=[1.5708, 0, 0], space="LOCAL")
keyframe_pose(armature="Rig", bones=["shin.L"], frame=12, channels=["ROT"])
```

`pose_bone` sets nothing permanent — it is lost the moment the frame changes on
an animated bone. `keyframe_pose` records the current state; it does not set one.
Always that order.

### The rotation_mode gotcha

New pose bones default to **QUATERNION**. Keying `rotation_euler` on one writes
an fcurve Blender then ignores: the key exists, the bone does not move, and
nothing errors.

| Tool | Behaviour |
| --- | --- |
| `keyframe_pose(channels=["ROT"])` | Resolves **per bone** to whichever rotation channel that bone's `rotation_mode` actually uses. Safe. |
| `pose_bone(rotation_euler=...)` | Auto-switches a quaternion bone to `XYZ`, because the euler channels are otherwise dead. The response reports the mode it ended on. |
| `insert_keyframe(bone=..., data_path="rotation_euler")` | Does **no** such check. This is where the silent failure comes from. |

Check with `list_bones(armature="Rig", space="POSE")` — it reports
`rotation_mode` per bone. Or force it once, up front:
`pose_bone(armature="Rig", bone="shin.L", rotation_mode="XYZ")`.

Reach for `insert_keyframe(bone=...)` only when you need a single component via
`index=`, or a non-transform property. Use `keyframe_pose` for everything else.

`channels` shorthands: `LOC`, `ROT`, `SCALE`, `LOCROT`, `LOCROTSCALE` (default),
`ALL`. Exact property names such as `"location"` also work. Keys land in a group
named after the bone.

### Actions

Start a named clip before keying so takes stay separable:

```
assign_action(action="walk_cycle", object="Rig", create_if_missing=True)
```

Bank it and clear the slot for the next take:

```
nla_push_down(object="Rig", track_name="Locomotion")
```

Shape-key animation lives on the Key datablock, **not** the object — it is a
separate action. Use `shapekey_keyframe(object="Head", keys=["smile"], frame=30,
value=1.0)`, not `insert_keyframe`. For a corrective shape that fires off a joint
angle, use `add_driver(object="Body", host="SHAPE_KEYS",
data_path='key_blocks["elbow_bulge"].value', ...)` rather than keyframes.

### Interpolation and easing

`set_interpolation` edits keys that already exist; it never creates any.

| Motion | `interpolation` | `easing` |
| --- | --- | --- |
| Blocking passes, stepped holds, mechanical/robotic | `CONSTANT` | — |
| Constant-speed travel, spins, conveyors | `LINEAR` | — |
| Default organic motion | `BEZIER` | `AUTO` |
| Anticipation into a fast action | `CUBIC` | `EASE_IN` |
| Settle, cushion, arrival | `QUART` | `EASE_OUT` |
| Breathing, floating, idle cycles | `SINE` | `EASE_IN_OUT` |
| Overshoot, whip, snap-back | `BACK` | `EASE_OUT` |
| Ball landing, foot impact | `BOUNCE` | `EASE_OUT` |
| Springy secondary — antennae, tails, jiggle | `ELASTIC` | `EASE_OUT` |
| Hard pop, instant snap with a tail | `EXPO` | `EASE_OUT` |

`easing` is meaningless on `LINEAR` and `CONSTANT`.

```
set_interpolation(object="Rig", interpolation="BEZIER", easing="AUTO")
set_interpolation(object="Rig", data_path='pose.bones["foot.L"].location',
                  interpolation="BOUNCE", easing="EASE_OUT", frame_range=[24, 36])
```

**`data_path` here is the raw fcurve path**, not the short bone form —
`pose.bones["foot.L"].location`, with the quotes. There is no `bone=` helper on
this tool. Run `list_keyframes(object="Rig")` first and copy the exact
`data_path` strings out of the response.

---

## 6. Playblast review loop

**GUI Blender only.** Fails under `blender --background`.

```
set_fps(fps=24)
set_frame_range(start=1, end=48)
playblast(out_path="/tmp/walk_v01.mp4", frame_start=1, frame_end=48,
          format="MP4", percentage=50, timeout=600)
```

Then inspect, because **you cannot see the video** — `playblast` returns
`{path, exists, bytes, frames, ...}`, never pixels. The file is for the human.
What you can actually look at is frames:

```
set_frame(frame=1);  viewport_screenshot(max_size=1024)
set_frame(frame=12); viewport_screenshot(max_size=1024)
set_frame(frame=24); viewport_screenshot(max_size=1024)
set_frame(frame=36); viewport_screenshot(max_size=1024)
```

Four frames on a 48-frame cycle — contact, down, passing, contact — is enough to
catch a broken pose. Adjust with `pose_bone` + `keyframe_pose`, then re-blast.

Facts that bite:

- **It overwrites `out_path` without asking.** Version the filename —
  `walk_v01.mp4`, `walk_v02.mp4` — so a re-blast never destroys the take the
  user is looking at.
- **Read `path` from the response.** For MP4 Blender may append the frame range
  to the filename; the handler hunts for the real file and reports it. Do not
  assume the path you passed.
- For `format="PNG"`, `out_path` is a **filename prefix** and Blender appends
  frame numbers.
- `percentage=50` halves resolution and roughly quarters the time. Use it for
  every check pass; only go to 100 for the take you hand over.
- `timeout` defaults to **600 s**. A 250-frame blast at full resolution can
  exceed it. Drop `percentage` first, raise `timeout` second.
- Scene render settings (`filepath`, format, frame range, resolution, fps,
  ffmpeg codec) are all saved and restored afterwards, so a playblast cannot
  quietly reconfigure the user's render.
- `set_fps` does **not** rescale existing keyframes. A 24 fps animation played at
  48 simply runs twice as fast.
- `set_frame_range` refuses an end before the start rather than letting Blender
  clamp the two against each other and silently accept a range you did not ask
  for.

---

## Order of operations

Get this wrong and you redo the rig.

1. Model, then **remesh** — `voxel_remesh` destroys UVs and vertex groups, so it
   must happen before anything that creates them.
2. Build the armature at +X only, `.L`-suffixed, controls flagged
   `use_deform: false`.
3. Pre-bend knees and elbows 2–5 cm.
4. `symmetrize_bones` — check `created` is non-empty.
5. Rename anything you dislike **now**, before binding.
6. `parent_mesh_to_armature(mesh="Body", armature="Rig", mode="AUTOMATIC")`.
   Only `use_deform` bones get a vertex group; that is why the controls were
   flagged.
7. `setup_ik` and constraints.
8. Pose-test every joint at its extreme (§4), fixing weights as you go.
9. Keyframe, then set interpolation, then playblast.

`undo_checkpoint(label="rig built, before weights")` between stages 6 and 7 —
name the checkpoint for what it precedes so a rollback is describable.

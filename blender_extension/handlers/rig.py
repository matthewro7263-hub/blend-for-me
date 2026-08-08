"""Armatures, bones, posing, constraints, shape keys and drivers.

Everything here goes through the data API. The three exceptions —
``armature.symmetrize``, ``pose.armature_apply`` and ``object.parent_set`` — have
no data-API equivalent, and all three were verified to run under
``blender --background``, so no command in this module needs a GUI.
"""

from __future__ import annotations

import contextlib
import math
from typing import Iterator, List, Optional

import bpy
from mathutils import Matrix, Quaternion, Vector

from ..registry import command

# Pointer properties on constraints are addressed by name; map the RNA type of
# the pointer onto the bpy.data collection a name should be looked up in.
_ID_COLLECTIONS = {
    "Object": "objects",
    "Action": "actions",
    "Collection": "collections",
    "Image": "images",
    "Material": "materials",
    "Mesh": "meshes",
    "MovieClip": "movieclips",
    "Scene": "scenes",
    "Text": "texts",
    "Texture": "textures",
}

#: Where a driver may live relative to the object that owns it.
_DRIVER_HOSTS = ("OBJECT", "DATA", "SHAPE_KEYS")

_DRIVER_ID_COLLECTIONS = {
    "OBJECT": "objects",
    "ARMATURE": "armatures",
    "MESH": "meshes",
    "KEY": "shape_keys",
    "SCENE": "scenes",
    "MATERIAL": "materials",
    "ACTION": "actions",
    "COLLECTION": "collections",
    "CAMERA": "cameras",
    "LIGHT": "lights",
    "NODETREE": "node_groups",
    "TEXTURE": "textures",
    "WORLD": "worlds",
}


# ---------------------------------------------------------------------------
# lookup / conversion helpers
# ---------------------------------------------------------------------------

def _object(name: str, expect: Optional[str] = None):
    obj = bpy.data.objects.get(name)
    if obj is None:
        raise KeyError(
            f"no object named {name!r}; call get_scene_info to see the real names"
        )
    if expect and obj.type != expect:
        raise TypeError(f"{name!r} is a {obj.type}, expected {expect}")
    return obj


def _armature(name: str):
    return _object(name, "ARMATURE")


def _bone_names(arm_obj) -> list:
    data = arm_obj.data
    if data.is_editmode:
        return [b.name for b in data.edit_bones]
    return [b.name for b in data.bones]


def _pose(arm_obj):
    """The armature's pose, forcing a depsgraph sync if it has not been built yet.

    A freshly linked armature object has ``pose is None`` until the view layer
    resyncs, which is easy to hit right after ``rig.create_armature``.
    """
    if arm_obj.pose is None:
        bpy.context.view_layer.update()
    if arm_obj.pose is None:
        raise RuntimeError(
            f"{arm_obj.name!r} has no pose data. It is probably not linked into "
            f"the current scene, so Blender never evaluated it."
        )
    return arm_obj.pose


def _pose_bone(arm_obj, bone: str):
    pb = _pose(arm_obj).bones.get(bone)
    if pb is None:
        raise KeyError(
            f"armature {arm_obj.name!r} has no bone {bone!r}; "
            f"bones are: {_bone_names(arm_obj)}"
        )
    return pb


def _edit_bone(arm_obj, bone: str):
    eb = arm_obj.data.edit_bones.get(bone)
    if eb is None:
        raise KeyError(
            f"armature {arm_obj.name!r} has no bone {bone!r}; "
            f"bones are: {[b.name for b in arm_obj.data.edit_bones]}"
        )
    return eb


def _v3(value, what: str) -> Vector:
    seq = list(value)
    if len(seq) != 3:
        raise ValueError(f"{what} must be 3 numbers, got {seq!r}")
    return Vector([float(x) for x in seq])


def _m(matrix) -> list:
    return [list(row) for row in matrix]


def _enum_ids(rna_prop) -> list:
    return [i.identifier for i in rna_prop.enum_items]


# ---------------------------------------------------------------------------
# mode handling
# ---------------------------------------------------------------------------

def _mode_set(obj, mode: str) -> None:
    try:
        bpy.ops.object.mode_set(mode=mode)
    except RuntimeError as exc:
        raise RuntimeError(
            f"cannot put {obj.name!r} into {mode} mode ({exc}). Usually the object "
            f"is hidden, unselectable, or in a collection excluded from the view "
            f"layer — make it visible and selectable, then retry."
        ) from exc


@contextlib.contextmanager
def _mode(obj, mode: str) -> Iterator[None]:
    """Put ``obj`` into ``mode`` for the block, then restore what was there.

    ``bpy.ops.object.mode_set`` acts on the *active* object, so the active object
    and any mode another object is stuck in have to be dealt with first.
    """
    # A just-linked object is absent from view_layer.objects until the layer
    # resyncs, so sync before believing the membership checks below.
    bpy.context.view_layer.update()
    scene = bpy.context.scene
    if obj.name not in scene.objects:
        raise RuntimeError(
            f"{obj.name!r} is not in scene {scene.name!r}, so its mode cannot be "
            f"changed. Link it into a collection in this scene first."
        )
    view_layer = bpy.context.view_layer
    if obj.name not in view_layer.objects:
        raise RuntimeError(
            f"{obj.name!r} is in the scene but not in view layer "
            f"{view_layer.name!r}, so its mode cannot be changed. Its collection is "
            f"excluded from this view layer — re-enable it."
        )

    prev_active = view_layer.objects.active
    prev_mode = obj.mode
    if prev_active is not None and prev_active is not obj and prev_active.mode != "OBJECT":
        _mode_set(prev_active, "OBJECT")
    view_layer.objects.active = obj
    try:
        if obj.mode != mode:
            _mode_set(obj, mode)
        yield
    finally:
        if obj.mode != prev_mode:
            with contextlib.suppress(RuntimeError):
                bpy.ops.object.mode_set(mode=prev_mode)
        if prev_active is not None and prev_active.name in view_layer.objects:
            view_layer.objects.active = prev_active


def _refresh() -> None:
    """Re-evaluate the dependency graph so posed matrices read back correctly."""
    bpy.context.view_layer.update()


# ---------------------------------------------------------------------------
# armature creation and edit-mode bone editing
# ---------------------------------------------------------------------------

def _build_bones(arm_data, entries: list) -> list:
    """Create edit bones from ``entries`` in two passes so parents can be forward refs.

    Returns one record per entry with the name Blender actually assigned (it
    appends ``.001`` on a collision).
    """
    edit_bones = arm_data.edit_bones
    created = []
    for entry in entries:
        name = entry.get("name")
        if not name:
            raise ValueError(f"every bone_tree entry needs a 'name': {entry!r}")
        bone = edit_bones.new(name)
        # head/tail must be set before parenting: use_connect snaps head to the
        # parent's tail, and roll is only meaningful once the axis exists.
        bone.head = _v3(entry.get("head", (0.0, 0.0, 0.0)), f"bone {name!r} head")
        bone.tail = _v3(entry.get("tail", (0.0, 0.0, 1.0)), f"bone {name!r} tail")
        if bone.length == 0.0:
            raise ValueError(
                f"bone {name!r} has head == tail; Blender deletes zero-length bones "
                f"on leaving edit mode"
            )
        bone.roll = float(entry.get("roll", 0.0))
        bone.use_deform = bool(entry.get("use_deform", True))
        created.append({"requested": name, "name": bone.name})

    for entry, record in zip(entries, created):
        bone = edit_bones[record["name"]]
        parent = entry.get("parent")
        if parent:
            resolved = next(
                (r["name"] for r in created if r["requested"] == parent), parent
            )
            parent_bone = edit_bones.get(resolved)
            if parent_bone is None:
                raise KeyError(
                    f"bone {record['name']!r} names parent {parent!r}, which is not "
                    f"in this armature or in bone_tree"
                )
            bone.parent = parent_bone
            # use_connect only means anything with a parent, and it moves head.
            connect = entry.get("connect", entry.get("use_connect", False))
            bone.use_connect = bool(connect)
        record["parent"] = bone.parent.name if bone.parent else None
        record["head"] = list(bone.head)
        record["tail"] = list(bone.tail)
        record["roll"] = bone.roll
        record["use_connect"] = bone.use_connect
        record["use_deform"] = bone.use_deform
    return created


@command("rig.create_armature", mutates=True)
def create_armature(params: dict) -> dict:
    """Create an armature object and build its bones in EDIT mode."""
    name = params.get("name") or "Armature"
    location = _v3(params.get("location", (0.0, 0.0, 0.0)), "location")
    bone_tree = params.get("bone_tree") or [
        {"name": "Bone", "head": [0, 0, 0], "tail": [0, 0, 1]}
    ]

    arm_data = bpy.data.armatures.new(name)
    arm_obj = bpy.data.objects.new(name, arm_data)
    bpy.context.collection.objects.link(arm_obj)
    arm_obj.location = location
    _refresh()

    with _mode(arm_obj, "EDIT"):
        created = _build_bones(arm_data, bone_tree)

    return {
        "object": arm_obj.name,
        "armature_data": arm_data.name,
        "location": list(arm_obj.location),
        "bones": created,
        "bone_count": len(arm_data.bones),
        "mode": arm_obj.mode,
    }


@command("rig.edit_bones_add", mutates=True)
def edit_bones_add(params: dict) -> dict:
    """Add bones to an existing armature (enters EDIT mode and returns)."""
    arm_obj = _armature(params["armature"])
    entries = params.get("bones") or []
    if not entries:
        raise ValueError("'bones' must be a non-empty list of bone definitions")

    with _mode(arm_obj, "EDIT"):
        created = _build_bones(arm_obj.data, entries)

    return {"armature": arm_obj.name, "added": created,
            "bone_count": len(arm_obj.data.bones)}


@command("rig.edit_bones_remove", mutates=True)
def edit_bones_remove(params: dict) -> dict:
    """Delete bones from an armature; children are re-parented to the grandparent."""
    arm_obj = _armature(params["armature"])
    names = params.get("bones")
    if isinstance(names, str):
        names = [names]
    if not names:
        raise ValueError("'bones' must be a bone name or a list of bone names")

    removed, reparented = [], []
    with _mode(arm_obj, "EDIT"):
        edit_bones = arm_obj.data.edit_bones
        for name in names:
            bone = _edit_bone(arm_obj, name)
            grandparent = bone.parent
            for child in list(bone.children):
                child.use_connect = False
                child.parent = grandparent
                reparented.append({"bone": child.name,
                                   "new_parent": grandparent.name if grandparent else None})
            edit_bones.remove(bone)
            removed.append(name)

    return {"armature": arm_obj.name, "removed": removed, "reparented": reparented,
            "bone_count": len(arm_obj.data.bones)}


@command("rig.edit_bone_set", mutates=True)
def edit_bone_set(params: dict) -> dict:
    """Edit one bone's rest geometry: head, tail, roll, parent, connect, deform."""
    arm_obj = _armature(params["armature"])
    name = params["bone"]
    changed = {}

    with _mode(arm_obj, "EDIT"):
        bone = _edit_bone(arm_obj, name)

        if "head" in params:
            bone.head = _v3(params["head"], "head")
            changed["head"] = list(bone.head)
        if "tail" in params:
            bone.tail = _v3(params["tail"], "tail")
            changed["tail"] = list(bone.tail)
        if bone.length == 0.0:
            raise ValueError(
                f"bone {bone.name!r} would have zero length (head == tail); "
                f"Blender deletes such bones on leaving edit mode"
            )
        if "roll" in params:
            bone.roll = float(params["roll"])
            changed["roll"] = bone.roll

        if "parent" in params:
            parent = params["parent"]
            if parent in (None, ""):
                bone.use_connect = False
                bone.parent = None
            else:
                if parent == bone.name:
                    raise ValueError(f"bone {bone.name!r} cannot parent to itself")
                bone.parent = _edit_bone(arm_obj, parent)
            changed["parent"] = bone.parent.name if bone.parent else None

        if "use_connect" in params:
            if params["use_connect"] and bone.parent is None:
                raise ValueError(
                    f"use_connect needs a parent; bone {bone.name!r} has none"
                )
            bone.use_connect = bool(params["use_connect"])
            changed["use_connect"] = bone.use_connect
            changed["head"] = list(bone.head)  # connecting snaps head to parent tail

        if "use_deform" in params:
            bone.use_deform = bool(params["use_deform"])
            changed["use_deform"] = bone.use_deform

        if "name" in params and params["name"] != bone.name:
            bone.name = params["name"]
            changed["name"] = bone.name

        result = {
            "head": list(bone.head), "tail": list(bone.tail), "roll": bone.roll,
            "length": bone.length,
            "parent": bone.parent.name if bone.parent else None,
            "use_connect": bone.use_connect, "use_deform": bone.use_deform,
            "name": bone.name,
        }

    return {"armature": arm_obj.name, "bone": result, "changed": changed}


@command("rig.list_bones")
def list_bones(params: dict) -> dict:
    """List an armature's bones with parents, heads and tails."""
    arm_obj = _armature(params["armature"])
    limit = int(params.get("limit", 1000))
    space = str(params.get("space", "AUTO")).upper()
    if space not in {"AUTO", "DATA", "EDIT", "POSE"}:
        raise ValueError(f"space must be AUTO, DATA, EDIT or POSE, got {space!r}")
    if space == "AUTO":
        space = {"EDIT_ARMATURE": "EDIT", "POSE": "POSE"}.get(arm_obj.mode, "DATA")

    bones: List[dict] = []

    if space == "EDIT":
        with _mode(arm_obj, "EDIT"):
            total = len(arm_obj.data.edit_bones)
            for bone in list(arm_obj.data.edit_bones)[:limit]:
                bones.append({
                    "name": bone.name,
                    "parent": bone.parent.name if bone.parent else None,
                    "head": list(bone.head),
                    "tail": list(bone.tail),
                    "roll": bone.roll,
                    "length": bone.length,
                    "use_connect": bone.use_connect,
                    "use_deform": bone.use_deform,
                    "collections": [c.name for c in bone.collections],
                })
    elif space == "POSE":
        pose_bones = _pose(arm_obj).bones
        total = len(pose_bones)
        for pb in list(pose_bones)[:limit]:
            bones.append({
                "name": pb.name,
                "parent": pb.parent.name if pb.parent else None,
                "head": list(pb.head),
                "tail": list(pb.tail),
                "length": pb.length,
                "location": list(pb.location),
                "rotation_mode": pb.rotation_mode,
                "rotation_quaternion": list(pb.rotation_quaternion),
                "rotation_euler": list(pb.rotation_euler),
                "scale": list(pb.scale),
                "constraints": [{"name": c.name, "type": c.type, "mute": c.mute,
                                 "influence": c.influence} for c in pb.constraints],
                "collections": [c.name for c in pb.bone.collections],
            })
    else:
        data_bones = arm_obj.data.bones
        total = len(data_bones)
        for bone in list(data_bones)[:limit]:
            bones.append({
                "name": bone.name,
                "parent": bone.parent.name if bone.parent else None,
                "head": list(bone.head_local),
                "tail": list(bone.tail_local),
                "length": bone.length,
                "use_connect": bone.use_connect,
                "use_deform": bone.use_deform,
                "children": [c.name for c in bone.children],
                "collections": [c.name for c in bone.collections],
            })

    return {"armature": arm_obj.name, "space": space, "count": total,
            "bones": bones, "truncated": total > limit, "mode": arm_obj.mode}


@command("rig.symmetrize_bones", mutates=True)
def symmetrize_bones(params: dict) -> dict:
    """Mirror side-suffixed bones across X with ``armature.symmetrize``."""
    arm_obj = _armature(params["armature"])
    direction = str(params.get("direction", "NEGATIVE_X")).upper()
    valid = _enum_ids(
        bpy.ops.armature.symmetrize.get_rna_type().properties["direction"]
    )
    if direction not in valid:
        raise ValueError(f"direction must be one of {valid}, got {direction!r}")

    names = params.get("bones")
    if isinstance(names, str):
        names = [names]

    with _mode(arm_obj, "EDIT"):
        edit_bones = arm_obj.data.edit_bones
        before = {b.name for b in edit_bones}
        if names:
            for name in names:
                _edit_bone(arm_obj, name)
            wanted = set(names)
        else:
            wanted = before
        for bone in edit_bones:
            bone.select = bone.select_head = bone.select_tail = bone.name in wanted
        bpy.ops.armature.symmetrize(direction=direction)
        after = {b.name for b in edit_bones}

    created = sorted(after - before)
    return {
        "armature": arm_obj.name,
        "direction": direction,
        "selected": sorted(wanted),
        "created": created,
        "bone_count": len(arm_obj.data.bones),
        "note": (
            "symmetrize only mirrors bones whose names carry a side suffix "
            "(.L/.R, _L/_R, left/right). Bones without one are left alone, which "
            "is why 'created' can be empty."
            if not created else ""
        ),
    }


# ---------------------------------------------------------------------------
# posing
# ---------------------------------------------------------------------------

def _rotation_channel(pose_bone) -> str:
    if pose_bone.rotation_mode == "QUATERNION":
        return "rotation_quaternion"
    if pose_bone.rotation_mode == "AXIS_ANGLE":
        return "rotation_axis_angle"
    return "rotation_euler"


def _quat_from(params: dict, pose_bone) -> Optional[Quaternion]:
    if "rotation_quaternion" in params:
        seq = list(params["rotation_quaternion"])
        if len(seq) != 4:
            raise ValueError("rotation_quaternion must be 4 numbers, [w, x, y, z]")
        return Quaternion([float(x) for x in seq])
    if "rotation_euler" in params:
        vec = _v3(params["rotation_euler"], "rotation_euler")
        order = params.get("rotation_mode", pose_bone.rotation_mode)
        if order in {"QUATERNION", "AXIS_ANGLE"}:
            order = "XYZ"
        from mathutils import Euler
        return Euler(vec, order).to_quaternion()
    return None


@command("rig.pose_bone_transform", mutates=True)
def pose_bone_transform(params: dict) -> dict:
    """Move/rotate/scale a pose bone in LOCAL, POSE or WORLD space."""
    arm_obj = _armature(params["armature"])
    pb = _pose_bone(arm_obj, params["bone"])
    space = str(params.get("space", "LOCAL")).upper()
    mode = str(params.get("mode", "absolute")).lower()
    if space not in {"LOCAL", "POSE", "WORLD"}:
        raise ValueError(f"space must be LOCAL, POSE or WORLD, got {space!r}")
    if mode not in {"absolute", "delta"}:
        raise ValueError(f"mode must be 'absolute' or 'delta', got {mode!r}")

    if "rotation_mode" in params:
        valid = _enum_ids(bpy.types.PoseBone.bl_rna.properties["rotation_mode"])
        wanted = str(params["rotation_mode"]).upper()
        if wanted not in valid:
            raise ValueError(f"rotation_mode must be one of {valid}, got {wanted!r}")
        pb.rotation_mode = wanted
    elif "rotation_euler" in params and pb.rotation_mode == "QUATERNION":
        # The euler channels are ignored while the bone is in quaternion mode.
        pb.rotation_mode = "XYZ"

    quat = _quat_from(params, pb)
    loc = _v3(params["location"], "location") if "location" in params else None
    scale = _v3(params["scale"], "scale") if "scale" in params else None

    if space == "LOCAL":
        if loc is not None:
            pb.location = pb.location + loc if mode == "delta" else loc
        if scale is not None:
            pb.scale = (Vector(pb.scale) * Vector(scale) if mode == "delta" else scale)
        if quat is not None:
            channel = _rotation_channel(pb)
            if channel == "rotation_quaternion":
                current = Quaternion(pb.rotation_quaternion)
                pb.rotation_quaternion = (current @ quat) if mode == "delta" else quat
            elif channel == "rotation_axis_angle":
                axis, angle = quat.to_axis_angle()
                if mode == "delta":
                    current = Quaternion(
                        Vector(pb.rotation_axis_angle[1:]), pb.rotation_axis_angle[0]
                    )
                    axis, angle = (current @ quat).to_axis_angle()
                pb.rotation_axis_angle = (angle, axis.x, axis.y, axis.z)
            else:
                euler = quat.to_euler(pb.rotation_mode)
                if mode == "delta":
                    combined = Quaternion(pb.rotation_euler.to_quaternion()) @ quat
                    euler = combined.to_euler(pb.rotation_mode)
                pb.rotation_euler = euler
    else:
        _refresh()
        # pb.matrix is armature ("pose") space; world space is that pre-multiplied
        # by the armature object's own world matrix.
        to_pose = (arm_obj.matrix_world.inverted() if space == "WORLD"
                   else Matrix.Identity(4))
        from_pose = arm_obj.matrix_world if space == "WORLD" else Matrix.Identity(4)
        current = from_pose @ pb.matrix
        cur_loc, cur_quat, cur_scale = current.decompose()

        new_loc = cur_loc if loc is None else (cur_loc + loc if mode == "delta" else loc)
        new_quat = cur_quat if quat is None else (
            quat @ cur_quat if mode == "delta" else quat)
        new_scale = cur_scale if scale is None else (
            Vector(cur_scale) * Vector(scale) if mode == "delta" else scale)

        pb.matrix = to_pose @ Matrix.LocRotScale(new_loc, new_quat, new_scale)

    _refresh()
    return {
        "armature": arm_obj.name,
        "bone": pb.name,
        "space": space,
        "mode": mode,
        "rotation_mode": pb.rotation_mode,
        "location": list(pb.location),
        "rotation_quaternion": list(pb.rotation_quaternion),
        "rotation_euler": list(pb.rotation_euler),
        "scale": list(pb.scale),
        "matrix_pose": _m(pb.matrix),
        "head_pose": list(pb.head),
        "tail_pose": list(pb.tail),
    }


_CHANNEL_GROUPS = {
    "LOC": ("location",),
    "LOCATION": ("location",),
    "ROT": ("rotation",),
    "ROTATION": ("rotation",),
    "SCALE": ("scale",),
    "LOCROT": ("location", "rotation"),
    "LOCROTSCALE": ("location", "rotation", "scale"),
    "LOCSCALE": ("location", "scale"),
    "ROTSCALE": ("rotation", "scale"),
    "ALL": ("location", "rotation", "scale"),
}


def _resolve_channels(pose_bone, channels) -> list:
    if channels is None:
        channels = ["LOCROTSCALE"]
    if isinstance(channels, str):
        channels = [channels]

    resolved: List[str] = []
    for item in channels:
        parts = _CHANNEL_GROUPS.get(str(item).upper(), (item,))
        for part in parts:
            path = _rotation_channel(pose_bone) if part == "rotation" else part
            if path not in bpy.types.PoseBone.bl_rna.properties:
                raise ValueError(
                    f"{path!r} is not a PoseBone channel. Use LOC, ROT, SCALE, "
                    f"LOCROTSCALE, or an exact property name such as 'location', "
                    f"'rotation_quaternion', 'rotation_euler', 'scale'."
                )
            if path not in resolved:
                resolved.append(path)
    return resolved


@command("rig.pose_bone_keyframe", mutates=True)
def pose_bone_keyframe(params: dict) -> dict:
    """Insert pose keyframes on one or more bones at a frame."""
    arm_obj = _armature(params["armature"])
    bones = params.get("bones", params.get("bone"))
    if isinstance(bones, str):
        bones = [bones]
    if not bones:
        bones = [pb.name for pb in _pose(arm_obj).bones]

    frame = params.get("frame")
    frame = bpy.context.scene.frame_current if frame is None else int(frame)
    channels = params.get("channels")

    inserted = []
    with _mode(arm_obj, "POSE"):
        for name in bones:
            pb = _pose_bone(arm_obj, name)
            paths = _resolve_channels(pb, channels)
            done = []
            for path in paths:
                if pb.keyframe_insert(data_path=path, frame=frame, group=pb.name):
                    done.append(path)
            inserted.append({"bone": pb.name, "channels": done})

    action = arm_obj.animation_data.action if arm_obj.animation_data else None
    return {
        "armature": arm_obj.name,
        "frame": frame,
        "inserted": inserted,
        "action": action.name if action else None,
    }


@command("rig.reset_pose", mutates=True)
def reset_pose(params: dict) -> dict:
    """Clear pose transforms back to rest (location, rotation and scale)."""
    arm_obj = _armature(params["armature"])
    names = params.get("bones")
    if isinstance(names, str):
        names = [names]

    identity = Matrix.Identity(4)
    reset = []
    targets = ([_pose_bone(arm_obj, n) for n in names] if names
               else list(_pose(arm_obj).bones))
    for pb in targets:
        pb.matrix_basis = identity
        reset.append(pb.name)

    _refresh()
    return {"armature": arm_obj.name, "reset": reset, "count": len(reset)}


@command("rig.apply_pose_as_rest", mutates=True)
def apply_pose_as_rest(params: dict) -> dict:
    """Make the current pose the new rest pose (``pose.armature_apply``)."""
    arm_obj = _armature(params["armature"])
    names = params.get("bones")
    if isinstance(names, str):
        names = [names]

    with _mode(arm_obj, "POSE"):
        if names:
            for name in names:
                _pose_bone(arm_obj, name)
            wanted = set(names)
            # Pose-mode selection lives on PoseBone in 5.2; bpy.types.Bone has no
            # `select` property at all.
            for pb in _pose(arm_obj).bones:
                pb.select = pb.name in wanted
        result = bpy.ops.pose.armature_apply(selected=bool(names))

    deformed = [o.name for o in bpy.data.objects
                if any(m.type == "ARMATURE" and m.object is arm_obj for m in o.modifiers)]
    return {
        "armature": arm_obj.name,
        "applied_to": sorted(names) if names else "all bones",
        "result": sorted(result),
        "deformed_meshes": deformed,
        "warning": (
            "Bound meshes keep their current vertex positions, so the new rest pose "
            "only looks right if the meshes were already deformed into it. Meshes "
            "with shape keys are skipped by Blender."
            if deformed else ""
        ),
    }


# ---------------------------------------------------------------------------
# constraints
# ---------------------------------------------------------------------------

def _constraint_types() -> list:
    return _enum_ids(
        bpy.types.PoseBoneConstraints.bl_rna.functions["new"].parameters["type"]
    )


def _coerce_setting(constraint, key: str, value):
    prop = constraint.bl_rna.properties.get(key)
    if prop is None or prop.is_readonly:
        writable = sorted(
            p.identifier for p in constraint.bl_rna.properties
            if not p.is_readonly and p.identifier != "rna_type"
        )
        raise ValueError(
            f"a {constraint.type} constraint has no writable setting {key!r}; "
            f"writable settings are: {writable}"
        )
    if prop.type == "ENUM":
        valid = _enum_ids(prop)
        upper = str(value).upper()
        if upper not in valid:
            raise ValueError(f"{key} must be one of {valid}, got {value!r}")
        return upper
    if prop.type == "POINTER":
        if value in (None, ""):
            return None
        if not isinstance(value, str):
            return value
        target_type = prop.fixed_type.identifier
        collection = _ID_COLLECTIONS.get(target_type)
        if collection is None:
            raise ValueError(
                f"{key} points at a {target_type}, which cannot be addressed by name "
                f"here; settable pointer types are {sorted(_ID_COLLECTIONS)}"
            )
        found = getattr(bpy.data, collection).get(value)
        if found is None:
            raise KeyError(f"{key}: no {target_type} named {value!r}")
        return found
    if prop.type in {"FLOAT", "INT", "BOOLEAN"} and getattr(prop, "array_length", 0):
        return tuple(value)
    return value


def _apply_settings(constraint, settings: Optional[dict]) -> list:
    applied = []
    for key, value in (settings or {}).items():
        setattr(constraint, key, _coerce_setting(constraint, key, value))
        applied.append(key)
    return applied


@command("rig.add_bone_constraint", mutates=True)
def add_bone_constraint(params: dict) -> dict:
    """Add any bone constraint type to a pose bone and apply its settings."""
    arm_obj = _armature(params["armature"])
    pb = _pose_bone(arm_obj, params["bone"])
    con_type = str(params["type"]).upper()
    valid = _constraint_types()
    if con_type not in valid:
        raise ValueError(
            f"unknown bone constraint type {con_type!r}; valid types are: {valid}"
        )

    constraint = pb.constraints.new(type=con_type)
    if params.get("name"):
        constraint.name = params["name"]
    applied = _apply_settings(constraint, params.get("settings"))
    _refresh()

    return {
        "armature": arm_obj.name,
        "bone": pb.name,
        "constraint": constraint.name,
        "type": constraint.type,
        "applied": applied,
        "is_valid": getattr(constraint, "is_valid", None),
        "writable_settings": sorted(
            p.identifier for p in constraint.bl_rna.properties
            if not p.is_readonly and p.identifier != "rna_type"
        ),
    }


def _chain_root(pose_bone, chain_length: int):
    """Walk ``chain_length - 1`` parents up from ``pose_bone`` (0 = to the root)."""
    chain = [pose_bone]
    node = pose_bone
    while node.parent is not None and (chain_length == 0 or len(chain) < chain_length):
        node = node.parent
        chain.append(node)
    return chain


def _elbow_index(chain: list) -> int:
    """Index into a tip-first chain of the bone whose *head* is the middle joint."""
    return max(0, (len(chain) - 1) // 2)


def _pole_position(chain: list) -> Vector:
    """A direction in the chain's bend plane, on the side the elbow already points.

    ``chain`` runs tip-first. Project the elbow joint off the root→tip axis; the
    perpendicular component is exactly the direction the chain bends in.
    """
    root, tip = chain[-1], chain[0]
    axis = tip.tail - root.head
    if axis.length == 0.0:
        raise ValueError("IK chain has zero length; cannot place a pole target")
    offset = chain[_elbow_index(chain)].head - root.head
    perpendicular = offset - offset.project(axis)
    if perpendicular.length < 1e-5:
        # Perfectly straight chain: no bend direction to infer, so pick the axis
        # least aligned with the chain and use that.
        world_axes = [Vector((0, 1, 0)), Vector((0, 0, 1)), Vector((1, 0, 0))]
        perpendicular = min(world_axes, key=lambda a: abs(a.dot(axis.normalized())))
    return perpendicular.normalized()


@command("rig.setup_ik", mutates=True)
def setup_ik(params: dict) -> dict:
    """Wire an IK constraint, creating the target and pole when not supplied."""
    arm_obj = _armature(params["armature"])
    tip_name = params["chain_tip"]
    chain_length = int(params.get("chain_length", 2))
    if chain_length < 0:
        raise ValueError("chain_length must be >= 0 (0 means 'up to the root')")
    target_type = str(params.get("target_type", "EMPTY")).upper()
    if target_type not in {"EMPTY", "BONE"}:
        raise ValueError(f"target_type must be EMPTY or BONE, got {target_type!r}")

    _refresh()
    tip = _pose_bone(arm_obj, tip_name)
    chain = _chain_root(tip, chain_length)
    root = chain[-1]
    created = []

    # -- target ---------------------------------------------------------
    target_name = params.get("target")
    target_bone = params.get("target_bone")
    tip_tail_local = tip.tail.copy()

    if target_name:
        target_obj = _object(target_name)
        if target_obj is arm_obj and not target_bone:
            raise ValueError(
                "when target is the armature itself you must also pass "
                "target_bone naming the control bone"
            )
    elif target_type == "BONE":
        target_obj = arm_obj
        target_bone = f"{tip_name}_IK"
        with _mode(arm_obj, "EDIT"):
            bone = arm_obj.data.edit_bones.new(target_bone)
            bone.head = tip_tail_local
            bone.tail = tip_tail_local + Vector((0.0, 0.0, max(tip.length, 0.1) * 0.5))
            bone.use_deform = False
            target_bone = bone.name
        created.append({"kind": "BONE", "name": target_bone, "role": "target"})
    else:
        target_obj = bpy.data.objects.new(f"{tip_name}_IK", None)
        target_obj.empty_display_type = "PLAIN_AXES"
        target_obj.empty_display_size = max(tip.length, 0.1) * 0.5
        target_obj.matrix_world = arm_obj.matrix_world @ Matrix.Translation(tip_tail_local)
        bpy.context.collection.objects.link(target_obj)
        _refresh()
        created.append({"kind": "EMPTY", "name": target_obj.name, "role": "target"})

    # -- pole -----------------------------------------------------------
    pole_obj = None
    pole_bone = params.get("pole_bone")
    pole_name = params.get("pole_target")
    auto_pole = bool(params.get("auto_pole", False))
    pole_angle = params.get("pole_angle")

    if pole_name:
        pole_obj = _object(pole_name)
    elif auto_pole:
        direction = _pole_position(chain)
        distance = float(params.get("pole_distance",
                                    max((tip.tail - root.head).length, 1.0)))
        pole_local = chain[_elbow_index(chain)].head + direction * distance
        if target_type == "BONE":
            pole_obj = arm_obj
            pole_bone = f"{tip_name}_pole"
            with _mode(arm_obj, "EDIT"):
                bone = arm_obj.data.edit_bones.new(pole_bone)
                bone.head = pole_local
                bone.tail = pole_local + Vector((0.0, 0.0, max(tip.length, 0.1) * 0.5))
                bone.use_deform = False
                pole_bone = bone.name
            created.append({"kind": "BONE", "name": pole_bone, "role": "pole"})
        else:
            pole_obj = bpy.data.objects.new(f"{tip_name}_pole", None)
            pole_obj.empty_display_type = "SPHERE"
            pole_obj.empty_display_size = max(tip.length, 0.1) * 0.35
            pole_obj.matrix_world = arm_obj.matrix_world @ Matrix.Translation(pole_local)
            bpy.context.collection.objects.link(pole_obj)
            _refresh()
            created.append({"kind": "EMPTY", "name": pole_obj.name, "role": "pole"})

    # -- constraint -----------------------------------------------------
    # Every joint except the chain root moves when the pole angle changes; record
    # them now so the solver below can aim for "chain does not move".
    rest_joints = [(b.name, b.head.copy()) for b in chain[:-1]] or [(tip.name, tip.head.copy())]

    constraint = tip.constraints.new(type="IK")
    constraint.name = params.get("name") or "IK"
    constraint.target = target_obj
    if target_bone:
        constraint.subtarget = target_bone
    constraint.chain_count = chain_length
    constraint.use_tail = bool(params.get("use_tail", True))
    constraint.use_stretch = bool(params.get("use_stretch", False))

    solved = None
    if pole_obj is not None:
        constraint.pole_target = pole_obj
        if pole_bone:
            constraint.pole_subtarget = pole_bone
        if pole_angle is None:
            # The target sits exactly on the tip's current tail, so the correct
            # pole angle is the one that leaves the chain where it already is.
            solved = _solve_pole_angle(arm_obj, constraint, rest_joints)
            pole_angle = solved
        constraint.pole_angle = float(pole_angle)
    elif pole_angle is not None:
        constraint.pole_angle = float(pole_angle)

    _refresh()
    return {
        "armature": arm_obj.name,
        "chain_tip": tip.name,
        "chain": [b.name for b in chain],
        "chain_count": constraint.chain_count,
        "constraint": constraint.name,
        "target": target_obj.name,
        "subtarget": constraint.subtarget or None,
        "pole_target": pole_obj.name if pole_obj else None,
        "pole_subtarget": constraint.pole_subtarget or None,
        "pole_angle": constraint.pole_angle,
        "pole_angle_solved": solved is not None,
        "created": created,
        "is_valid": constraint.is_valid,
    }


def _solve_pole_angle(arm_obj, constraint, rest_joints: list) -> float:
    """Pick the pole angle that disturbs the chain least, coarse then fine.

    The IK target is placed exactly where the tip already is, so the right pole
    angle is the one under which the solved chain still matches its rest joints.
    """
    def error(angle: float) -> float:
        constraint.pole_angle = angle
        _refresh()
        depsgraph = bpy.context.evaluated_depsgraph_get()
        posed = arm_obj.evaluated_get(depsgraph).pose
        return sum((posed.bones[name].head - head).length_squared
                   for name, head in rest_joints)

    coarse = min((math.radians(d) for d in range(-180, 180, 5)), key=error)
    fine = min((coarse + math.radians(d / 4.0) for d in range(-20, 21)), key=error)
    error(fine)
    return fine


# ---------------------------------------------------------------------------
# skinning
# ---------------------------------------------------------------------------

_PARENT_MODES = {
    "AUTOMATIC": "ARMATURE_AUTO",
    "ENVELOPE": "ARMATURE_ENVELOPE",
    "EMPTY": "ARMATURE_NAME",
    "ARMATURE_AUTO": "ARMATURE_AUTO",
    "ARMATURE_ENVELOPE": "ARMATURE_ENVELOPE",
    "ARMATURE_NAME": "ARMATURE_NAME",
}


@command("rig.parent_mesh_to_armature", mutates=True)
def parent_mesh_to_armature(params: dict) -> dict:
    """Bind a mesh to an armature with automatic, envelope or empty weights."""
    mesh_obj = _object(params["mesh"])
    arm_obj = _armature(params["armature"])
    mode = str(params.get("mode", "AUTOMATIC")).upper()
    parent_type = _PARENT_MODES.get(mode)
    if parent_type is None:
        raise ValueError(
            f"mode must be one of {sorted(_PARENT_MODES)}, got {mode!r}"
        )

    before = {g.name for g in mesh_obj.vertex_groups}

    with _mode(arm_obj, "OBJECT"):
        bpy.ops.object.select_all(action="DESELECT")
        mesh_obj.select_set(True)
        arm_obj.select_set(True)
        bpy.context.view_layer.objects.active = arm_obj
        result = bpy.ops.object.parent_set(
            type=parent_type, keep_transform=bool(params.get("keep_transform", False))
        )

    after = [g.name for g in mesh_obj.vertex_groups]
    modifiers = [{"name": m.name, "object": m.object.name if m.object else None}
                 for m in mesh_obj.modifiers if m.type == "ARMATURE"]
    return {
        "mesh": mesh_obj.name,
        "armature": arm_obj.name,
        "mode": mode,
        "parent_type": parent_type,
        "result": sorted(result),
        "armature_modifiers": modifiers,
        "vertex_groups": after,
        "vertex_groups_created": sorted(set(after) - before),
        "note": (
            "ARMATURE_NAME creates empty vertex groups only — nothing is weighted "
            "until you paint or transfer weights."
            if parent_type == "ARMATURE_NAME" else ""
        ),
    }


# ---------------------------------------------------------------------------
# shape keys
# ---------------------------------------------------------------------------

def _shape_key_host(name: str):
    obj = _object(name)
    if not hasattr(obj.data, "shape_keys"):
        raise TypeError(
            f"{obj.name!r} is a {obj.type}; only MESH, CURVE, SURFACE and LATTICE "
            f"objects can carry shape keys"
        )
    return obj


def _key_block(obj, name: str):
    keys = obj.data.shape_keys
    if keys is None:
        raise KeyError(f"{obj.name!r} has no shape keys yet; create one first")
    block = keys.key_blocks.get(name)
    if block is None:
        raise KeyError(
            f"{obj.name!r} has no shape key {name!r}; keys are: "
            f"{[k.name for k in keys.key_blocks]}"
        )
    return block


def _describe_key(obj, block, index: int) -> dict:
    keys = obj.data.shape_keys
    return {
        "index": index,
        "name": block.name,
        "value": block.value,
        "slider_min": block.slider_min,
        "slider_max": block.slider_max,
        "mute": block.mute,
        "vertex_group": block.vertex_group or None,
        "relative_key": block.relative_key.name if block.relative_key else None,
        "is_reference": block is keys.reference_key,
    }


@command("rig.shapekey_create", mutates=True)
def shapekey_create(params: dict) -> dict:
    """Add a shape key, optionally captured from the current mix."""
    obj = _shape_key_host(params["object"])
    from_mix = bool(params.get("from_mix", False))
    name = params.get("name") or ("Mix" if from_mix else "Key")

    created_basis = False
    if obj.data.shape_keys is None:
        # The first key becomes the reference ("Basis"); make that explicit rather
        # than silently turning the caller's key into the rest shape.
        obj.shape_key_add(name="Basis", from_mix=False)
        created_basis = True

    block = obj.shape_key_add(name=name, from_mix=from_mix)
    if "value" in params:
        block.value = float(params["value"])

    index = list(obj.data.shape_keys.key_blocks).index(block)
    return {
        "object": obj.name,
        "created_basis": created_basis,
        "key": _describe_key(obj, block, index),
        "key_count": len(obj.data.shape_keys.key_blocks),
    }


@command("rig.shapekey_from_mix", mutates=True)
def shapekey_from_mix(params: dict) -> dict:
    """Snapshot the current blend of all shape keys into a new key."""
    return shapekey_create({**params, "from_mix": True})


@command("rig.shapekey_set_value", mutates=True)
def shapekey_set_value(params: dict) -> dict:
    """Set a shape key's value and, optionally, its slider range and settings."""
    obj = _shape_key_host(params["object"])
    block = _key_block(obj, params["key"])

    # Range first: value is clamped to [slider_min, slider_max] on assignment.
    if "slider_min" in params:
        block.slider_min = float(params["slider_min"])
    if "slider_max" in params:
        block.slider_max = float(params["slider_max"])
    if "value" in params:
        block.value = float(params["value"])
    if "mute" in params:
        block.mute = bool(params["mute"])
    if "vertex_group" in params:
        group = params["vertex_group"] or ""
        if group and group not in obj.vertex_groups:
            raise KeyError(
                f"{obj.name!r} has no vertex group {group!r}; groups are: "
                f"{[g.name for g in obj.vertex_groups]}"
            )
        block.vertex_group = group
    if "relative_key" in params:
        block.relative_key = _key_block(obj, params["relative_key"])

    index = list(obj.data.shape_keys.key_blocks).index(block)
    result = _describe_key(obj, block, index)
    if "value" in params and abs(result["value"] - float(params["value"])) > 1e-6:
        result["clamped"] = (
            f"requested {float(params['value'])}, clamped to the slider range "
            f"[{block.slider_min}, {block.slider_max}] — widen it with slider_min / "
            f"slider_max if you meant to over-drive the key"
        )
    return {"object": obj.name, "key": result}


@command("rig.shapekey_keyframe", mutates=True)
def shapekey_keyframe(params: dict) -> dict:
    """Keyframe shape key values at a frame."""
    obj = _shape_key_host(params["object"])
    keys = obj.data.shape_keys
    if keys is None:
        raise KeyError(f"{obj.name!r} has no shape keys to keyframe")

    names = params.get("keys", params.get("key"))
    if isinstance(names, str):
        names = [names]
    if not names:
        names = [k.name for k in keys.key_blocks if k is not keys.reference_key]

    frame = params.get("frame")
    frame = bpy.context.scene.frame_current if frame is None else int(frame)
    value = params.get("value")

    inserted = []
    for name in names:
        block = _key_block(obj, name)
        if value is not None:
            block.value = float(value)
        # The driver/animation lives on the Key datablock, not the object.
        ok = block.keyframe_insert(data_path="value", frame=frame)
        inserted.append({"key": block.name, "value": block.value, "inserted": bool(ok)})

    action = keys.animation_data.action if keys.animation_data else None
    return {"object": obj.name, "frame": frame, "keyframed": inserted,
            "action": action.name if action else None,
            "note": "shape key animation lives on the Key datablock "
                    f"({keys.name}), not on the object"}


@command("rig.shapekey_list")
def shapekey_list(params: dict) -> dict:
    """List an object's shape keys with values, ranges and relative-to targets."""
    obj = _shape_key_host(params["object"])
    keys = obj.data.shape_keys
    if keys is None:
        return {"object": obj.name, "has_shape_keys": False, "count": 0, "keys": [],
                "truncated": False}

    limit = int(params.get("limit", 1000))
    blocks = list(keys.key_blocks)
    return {
        "object": obj.name,
        "has_shape_keys": True,
        "datablock": keys.name,
        "use_relative": keys.use_relative,
        "reference_key": keys.reference_key.name if keys.reference_key else None,
        "count": len(blocks),
        "keys": [_describe_key(obj, b, i) for i, b in enumerate(blocks[:limit])],
        "truncated": len(blocks) > limit,
    }


# ---------------------------------------------------------------------------
# drivers
# ---------------------------------------------------------------------------

def _driver_host(obj, host: str):
    host = host.upper()
    if host not in _DRIVER_HOSTS:
        raise ValueError(f"host must be one of {list(_DRIVER_HOSTS)}, got {host!r}")
    if host == "OBJECT":
        return obj
    if host == "DATA":
        if obj.data is None:
            raise TypeError(f"{obj.name!r} has no object data to host a driver")
        return obj.data
    keys = getattr(obj.data, "shape_keys", None)
    if keys is None:
        raise KeyError(
            f"{obj.name!r} has no shape keys; create one before driving it"
        )
    return keys


def _resolve_driver_id(spec: dict):
    id_type = str(spec.get("id_type", "OBJECT")).upper()
    collection = _DRIVER_ID_COLLECTIONS.get(id_type)
    if collection is None:
        raise ValueError(
            f"id_type {id_type!r} is not addressable by name here; use one of "
            f"{sorted(_DRIVER_ID_COLLECTIONS)}"
        )
    name = spec.get("id")
    if name in (None, ""):
        return None, id_type
    if not isinstance(name, str):
        return name, id_type
    found = getattr(bpy.data, collection).get(name)
    if found is None:
        raise KeyError(f"no {id_type} datablock named {name!r}")
    return found, id_type


def _apply_driver_target(target, spec: dict, var_type: str) -> None:
    datablock, id_type = _resolve_driver_id(spec)
    if var_type in {"SINGLE_PROP", "CONTEXT_PROP"}:
        # id_type filters what `id` will accept, so it has to be set first.
        target.id_type = id_type
    if datablock is not None:
        target.id = datablock

    if "data_path" in spec:
        target.data_path = spec["data_path"]
    bone = spec.get("bone", spec.get("bone_target"))
    if bone is not None:
        target.bone_target = bone
    for key in ("transform_type", "transform_space", "rotation_mode"):
        if key in spec:
            prop = target.bl_rna.properties[key]
            valid = _enum_ids(prop)
            value = str(spec[key]).upper()
            if value not in valid:
                raise ValueError(f"{key} must be one of {valid}, got {spec[key]!r}")
            setattr(target, key, value)
    if "fallback_value" in spec:
        target.use_fallback_value = True
        target.fallback_value = float(spec["fallback_value"])


@command("rig.add_driver", mutates=True)
def add_driver(params: dict) -> dict:
    """Drive a property from an expression over named variables."""
    obj = _object(params["object"])
    owner = _driver_host(obj, str(params.get("host", "OBJECT")))
    data_path = params["data_path"]
    index = params.get("index")
    index = -1 if index is None else int(index)

    existing = []
    if owner.animation_data:
        existing = [f.data_path for f in owner.animation_data.drivers]

    try:
        curves = owner.driver_add(data_path, index)
    except TypeError as exc:
        raise KeyError(
            f"{data_path!r} is not a drivable property of {owner.bl_rna.identifier} "
            f"{getattr(owner, 'name', '?')!r} ({exc}). Shape key values live at "
            f'host="SHAPE_KEYS", data_path=\'key_blocks["Name"].value\'; pose bone '
            f'channels at data_path=\'pose.bones["Name"].location\' with an index.'
        ) from exc
    curves = curves if isinstance(curves, list) else [curves]

    variables = params.get("variables") or []
    expression = params.get("expression", "var")
    driver_type = str(params.get("driver_type", "SCRIPTED")).upper()
    valid_types = _enum_ids(bpy.types.Driver.bl_rna.properties["type"])
    if driver_type not in valid_types:
        raise ValueError(f"driver_type must be one of {valid_types}, got {driver_type!r}")

    described = []
    for curve in curves:
        driver = curve.driver
        driver.type = driver_type
        # Rebuild variables from scratch so re-running this command is idempotent
        # rather than accumulating duplicates on an existing driver.
        for var in list(driver.variables):
            driver.variables.remove(var)

        for spec in variables:
            var = driver.variables.new()
            var_type = str(spec.get("type", "SINGLE_PROP")).upper()
            valid_var = _enum_ids(bpy.types.DriverVariable.bl_rna.properties["type"])
            if var_type not in valid_var:
                raise ValueError(
                    f"variable type must be one of {valid_var}, got {var_type!r}"
                )
            var.type = var_type
            if spec.get("name"):
                var.name = spec["name"]

            targets = spec.get("targets")
            if targets is None:
                targets = [spec]
            if len(targets) > len(var.targets):
                raise ValueError(
                    f"a {var_type} driver variable takes {len(var.targets)} "
                    f"target(s), got {len(targets)}"
                )
            for target, target_spec in zip(var.targets, targets):
                _apply_driver_target(target, target_spec, var_type)

        if driver_type == "SCRIPTED":
            driver.expression = expression

        described.append({
            "data_path": curve.data_path,
            "array_index": curve.array_index,
            "type": driver.type,
            "expression": driver.expression,
            "is_valid": driver.is_valid,
            "variables": [
                {"name": v.name, "type": v.type,
                 "targets": [{"id": t.id.name if t.id else None,
                              "data_path": t.data_path or None,
                              "bone_target": t.bone_target or None,
                              "transform_type": t.transform_type,
                              "transform_space": t.transform_space}
                             for t in v.targets]}
                for v in driver.variables
            ],
        })

    return {
        "object": obj.name,
        "host": str(params.get("host", "OBJECT")).upper(),
        "id": owner.name,
        "replaced_existing": data_path in existing,
        "drivers": described,
        "count": len(described),
    }


# ---------------------------------------------------------------------------
# bone collections (these replaced bone layers in 4.x)
# ---------------------------------------------------------------------------

def _bone_collection(arm_data, name: str):
    bcoll = arm_data.collections_all.get(name)
    if bcoll is None:
        raise KeyError(
            f"armature {arm_data.name!r} has no bone collection {name!r}; "
            f"collections are: {[c.name for c in arm_data.collections_all]}"
        )
    return bcoll


def _describe_collection(bcoll) -> dict:
    return {
        "name": bcoll.name,
        "index": bcoll.index,
        "parent": bcoll.parent.name if bcoll.parent else None,
        "children": [c.name for c in bcoll.children],
        "is_visible": bcoll.is_visible,
        "is_visible_effectively": bcoll.is_visible_effectively,
        "is_solo": bcoll.is_solo,
        "is_expanded": bcoll.is_expanded,
        "bone_count": len(bcoll.bones),
    }


@command("rig.bone_collection_create", mutates=True)
def bone_collection_create(params: dict) -> dict:
    """Create a bone collection, optionally nested under another."""
    arm_obj = _armature(params["armature"])
    name = params.get("name") or "Bones"
    parent_name = params.get("parent")
    parent = _bone_collection(arm_obj.data, parent_name) if parent_name else None
    bcoll = arm_obj.data.collections.new(name, parent=parent)

    assigned = []
    bones = params.get("bones")
    if isinstance(bones, str):
        bones = [bones]
    for bone_name in bones or []:
        bcoll.assign(_lookup_bone_for_collection(arm_obj, bone_name))
        assigned.append(bone_name)

    return {"armature": arm_obj.name, "collection": _describe_collection(bcoll),
            "assigned": assigned}


def _lookup_bone_for_collection(arm_obj, name: str):
    """Bone collections take whichever bone flavour matches the current mode."""
    data = arm_obj.data
    if data.is_editmode:
        return _edit_bone(arm_obj, name)
    bone = data.bones.get(name)
    if bone is None:
        raise KeyError(
            f"armature {arm_obj.name!r} has no bone {name!r}; "
            f"bones are: {[b.name for b in data.bones]}"
        )
    return bone


@command("rig.bone_collection_assign", mutates=True)
def bone_collection_assign(params: dict) -> dict:
    """Assign (or unassign) bones to a bone collection."""
    arm_obj = _armature(params["armature"])
    bcoll = _bone_collection(arm_obj.data, params["collection"])
    unassign = bool(params.get("unassign", False))

    bones = params.get("bones", params.get("bone"))
    if isinstance(bones, str):
        bones = [bones]
    if not bones:
        raise ValueError("'bones' must be a bone name or a list of bone names")

    changed = []
    for name in bones:
        bone = _lookup_bone_for_collection(arm_obj, name)
        ok = bcoll.unassign(bone) if unassign else bcoll.assign(bone)
        changed.append({"bone": name, "changed": bool(ok)})

    return {
        "armature": arm_obj.name,
        "collection": bcoll.name,
        "operation": "unassign" if unassign else "assign",
        "bones": changed,
        "bone_count": len(bcoll.bones),
    }


@command("rig.bone_collections_list")
def bone_collections_list(params: dict) -> dict:
    """List every bone collection on an armature, with membership."""
    arm_obj = _armature(params["armature"])
    limit = int(params.get("limit", 1000))
    include_bones = bool(params.get("include_bones", True))

    collections = list(arm_obj.data.collections_all)
    described = []
    for bcoll in collections[:limit]:
        entry = _describe_collection(bcoll)
        if include_bones:
            names = [b.name for b in bcoll.bones]
            entry["bones"] = names[:limit]
            entry["bones_truncated"] = len(names) > limit
        described.append(entry)

    return {
        "armature": arm_obj.name,
        "count": len(collections),
        "roots": [c.name for c in arm_obj.data.collections],
        "collections": described,
        "truncated": len(collections) > limit,
        "is_solo_active": arm_obj.data.collections.is_solo_active,
    }


@command("rig.bone_collection_set_visibility", mutates=True)
def bone_collection_set_visibility(params: dict) -> dict:
    """Show, hide or solo a bone collection."""
    arm_obj = _armature(params["armature"])
    bcoll = _bone_collection(arm_obj.data, params["collection"])

    if "is_visible" in params:
        bcoll.is_visible = bool(params["is_visible"])
    elif "toggle" in params and params["toggle"]:
        bcoll.is_visible = not bcoll.is_visible
    if "is_solo" in params:
        bcoll.is_solo = bool(params["is_solo"])
    if "is_expanded" in params:
        bcoll.is_expanded = bool(params["is_expanded"])

    return {"armature": arm_obj.name, "collection": _describe_collection(bcoll),
            "is_solo_active": arm_obj.data.collections.is_solo_active}

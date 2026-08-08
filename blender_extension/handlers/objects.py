"""Object creation, transforms, hierarchy, collections and alignment."""

from __future__ import annotations

import contextlib
import difflib
from typing import Iterable, List, Optional, Sequence

import bpy
from mathutils import Vector

from ..registry import command

_EULER_MODES = {"XYZ", "XZY", "YXZ", "YZX", "ZXY", "ZYX"}


# ---------------------------------------------------------------------------
# lookup / coercion helpers
# ---------------------------------------------------------------------------

def _obj(name: str):
    obj = bpy.data.objects.get(name)
    if obj is None:
        near = difflib.get_close_matches(name, [o.name for o in bpy.data.objects], n=3)
        hint = (f"; closest existing names are {near}" if near
                else "; call list_objects to see the real names")
        raise KeyError(f"no object named {name!r}{hint}")
    return obj


def _names(params: dict, key: str = "names") -> List[str]:
    value = params.get(key)
    if isinstance(value, str):
        return [value]
    if not value:
        raise ValueError(f"{key!r} is required: pass a list of object names")
    return [str(v) for v in value]


def _objs(params: dict, key: str = "names") -> List:
    return [_obj(n) for n in _names(params, key)]


def _vec3(value, label: str) -> List[float]:
    seq = list(value)
    if len(seq) != 3:
        raise ValueError(f"{label} must be 3 numbers [x, y, z], got {value!r}")
    return [float(v) for v in seq]


def _enum(value, valid: Sequence[str], label: str) -> str:
    upper = str(value).upper()
    if upper not in valid:
        raise ValueError(f"{label} must be one of {sorted(valid)}, got {value!r}")
    return upper


def _op_enum(op, prop: str) -> List[str]:
    """Live enum identifiers for an operator property, so validation never drifts."""
    return [i.identifier for i in op.get_rna_type().properties[prop].enum_items]


def _type_enum(rna_type: str, prop: str) -> List[str]:
    return [i.identifier for i in getattr(bpy.types, rna_type).bl_rna.properties[prop].enum_items]


# ---------------------------------------------------------------------------
# context helpers
# ---------------------------------------------------------------------------

@contextlib.contextmanager
def _object_mode():
    """Force OBJECT mode for the duration, then put the user back where they were.

    Operators here poll for OBJECT mode, and they also reassign the active object,
    so the *original* active object is restored before its mode is — otherwise a
    user sculpting object A would be dumped into sculpt mode on newly-created B.
    """
    view_layer = bpy.context.view_layer
    previous_active = view_layer.objects.active
    previous_mode = previous_active.mode if previous_active is not None else "OBJECT"
    if previous_mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    try:
        yield
    finally:
        if previous_mode != "OBJECT":
            still_there = (previous_active is not None
                           and previous_active.name in bpy.data.objects
                           and previous_active.name in view_layer.objects)
            if still_there:
                view_layer.objects.active = previous_active
                with contextlib.suppress(RuntimeError):
                    bpy.ops.object.mode_set(mode=previous_mode)


def _deselect_all() -> None:
    for obj in bpy.context.view_layer.objects:
        obj.select_set(False)


def _select(obj, state: bool = True) -> None:
    """Select via the data API, turning silent no-ops into actionable errors."""
    view_layer = bpy.context.view_layer
    if obj.name not in view_layer.objects:
        raise RuntimeError(
            f"{obj.name!r} is not in the active view layer, so operators cannot reach it. "
            f"Link it into a collection the view layer includes (objects.collection_link)."
        )
    obj.select_set(state)
    if state and not obj.select_get():
        raise RuntimeError(
            f"{obj.name!r} could not be selected because it (or its collection) is hidden. "
            f"Unhide it first, then retry."
        )


def _select_only(objects: Iterable, active=None) -> None:
    _deselect_all()
    for obj in objects:
        _select(obj, True)
    if active is not None:
        bpy.context.view_layer.objects.active = active


def _required_collection(params: dict, create: bool):
    name = params.get("collection")
    if not name:
        raise ValueError("collection is required: pass the name of the target collection")
    return _target_collection(str(name), create=create)


def _target_collection(name: Optional[str], create: bool = False):
    """Named collection, else the active one (matching where the UI would put it)."""
    if name:
        coll = bpy.data.collections.get(name)
        if coll is None:
            if not create:
                raise KeyError(
                    f"no collection named {name!r}; call objects.collection_list to see them "
                    f"or objects.collection_create to make it"
                )
            coll = bpy.data.collections.new(name)
            bpy.context.scene.collection.children.link(coll)
        return coll
    return getattr(bpy.context, "collection", None) or bpy.context.scene.collection


def _link_only_to(obj, coll) -> None:
    for old in list(obj.users_collection):
        if old is not coll:
            old.objects.unlink(obj)
    if obj.name not in coll.objects:
        coll.objects.link(obj)


def _brief(obj) -> dict:
    return {
        "name": obj.name,
        "type": obj.type,
        "location": list(obj.location),
        "rotation_euler": list(obj.rotation_euler),
        "scale": list(obj.scale),
        "dimensions": list(obj.dimensions),
        "collections": [c.name for c in obj.users_collection],
    }


# ---------------------------------------------------------------------------
# bounds
# ---------------------------------------------------------------------------

def _local_corners(obj, evaluated: bool = False) -> List[Vector]:
    """Eight local-space bounding-box corners.

    ``Object.bound_box`` on an *evaluated* object still reports the original cage,
    so evaluated bounds are rebuilt from the evaluated mesh's vertices instead.
    """
    if evaluated:
        depsgraph = bpy.context.evaluated_depsgraph_get()
        verts = getattr(obj.evaluated_get(depsgraph).data, "vertices", None)
        if verts:
            cos = [v.co for v in verts]
            lo = (min(c.x for c in cos), min(c.y for c in cos), min(c.z for c in cos))
            hi = (max(c.x for c in cos), max(c.y for c in cos), max(c.z for c in cos))
            return [Vector((x, y, z)) for x in (lo[0], hi[0])
                    for y in (lo[1], hi[1]) for z in (lo[2], hi[2])]
    return [Vector(c) for c in obj.bound_box]


def _extent(points: Sequence[Vector]) -> dict:
    lo = [min(p[i] for p in points) for i in range(3)]
    hi = [max(p[i] for p in points) for i in range(3)]
    return {
        "min": lo,
        "max": hi,
        "center": [(lo[i] + hi[i]) * 0.5 for i in range(3)],
        "size": [hi[i] - lo[i] for i in range(3)],
        "corners": [list(p) for p in points],
    }


# ---------------------------------------------------------------------------
# creation
# ---------------------------------------------------------------------------

#: kind -> (operator, name of the operator property that ``size`` feeds)
_PRIMITIVES = {
    "cube": (lambda: bpy.ops.mesh.primitive_cube_add, "size"),
    "plane": (lambda: bpy.ops.mesh.primitive_plane_add, "size"),
    "grid": (lambda: bpy.ops.mesh.primitive_grid_add, "size"),
    "monkey": (lambda: bpy.ops.mesh.primitive_monkey_add, "size"),
    "uv_sphere": (lambda: bpy.ops.mesh.primitive_uv_sphere_add, "radius"),
    "ico_sphere": (lambda: bpy.ops.mesh.primitive_ico_sphere_add, "radius"),
    "cylinder": (lambda: bpy.ops.mesh.primitive_cylinder_add, "radius"),
    "cone": (lambda: bpy.ops.mesh.primitive_cone_add, "radius1"),
    "circle": (lambda: bpy.ops.mesh.primitive_circle_add, "radius"),
    "torus": (lambda: bpy.ops.mesh.primitive_torus_add, "major_radius"),
}

_PRIMITIVE_EXTRAS = (
    "segments", "ring_count", "subdivisions", "vertices", "depth", "radius", "radius1",
    "radius2", "major_radius", "minor_radius", "major_segments", "minor_segments",
    "x_subdivisions", "y_subdivisions", "fill_type", "end_fill_type", "calc_uvs",
    "generate_uvs", "mode",
)

#: circle spells the cap style ``fill_type``; cylinder and cone spell it
#: ``end_fill_type``. Accept either and route it to whichever the operator has.
_EXTRA_ALIASES = {"fill_type": "end_fill_type", "end_fill_type": "fill_type"}


@command("objects.create_primitive", mutates=True)
def create_primitive(params: dict) -> dict:
    """Add a mesh primitive and return its final name and dimensions."""
    kind = str(params.get("kind", "cube")).lower()
    if kind not in _PRIMITIVES:
        raise ValueError(f"kind must be one of {sorted(_PRIMITIVES)}, got {params.get('kind')!r}")

    getter, size_prop = _PRIMITIVES[kind]
    op = getter()
    valid = {p.identifier for p in op.get_rna_type().properties}

    kwargs = {"align": "WORLD"}
    if "enter_editmode" in valid:
        kwargs["enter_editmode"] = False
    if params.get("size") is not None:
        kwargs[size_prop] = float(params["size"])
    if params.get("location") is not None:
        kwargs["location"] = _vec3(params["location"], "location")
    if params.get("rotation") is not None:
        kwargs["rotation"] = _vec3(params["rotation"], "rotation")

    for key in _PRIMITIVE_EXTRAS:
        value = params.get(key)
        if value is None:
            continue
        target = key
        if target not in valid:
            target = _EXTRA_ALIASES.get(key, key)
        if target not in valid:
            raise ValueError(
                f"{kind!r} has no {key!r} option; it accepts {sorted(valid - {'rna_type'})}"
            )
        kwargs[target] = value.upper() if isinstance(value, str) else value

    before = set(bpy.data.objects.keys())
    with _object_mode():
        op(**kwargs)
    created = [n for n in bpy.data.objects.keys() if n not in before]
    if not created:
        raise RuntimeError(f"{kind} primitive operator reported success but added no object")
    obj = bpy.data.objects[created[0]]

    # torus takes no `scale` operator property, so scale is always applied here.
    if params.get("scale") is not None:
        obj.scale = _vec3(params["scale"], "scale")
    if params.get("name"):
        obj.name = str(params["name"])
    if params.get("collection"):
        _link_only_to(obj, _target_collection(params["collection"], create=True))

    result = _brief(obj)
    result.update(kind=kind, requested_name=params.get("name"),
                  vertices=len(obj.data.vertices), polygons=len(obj.data.polygons))
    return result


@command("objects.add_empty", mutates=True)
def add_empty(params: dict) -> dict:
    """Create an Empty with the given display type and size."""
    display_type = _enum(params.get("display_type", "PLAIN_AXES"),
                         _type_enum("Object", "empty_display_type"), "display_type")
    obj = bpy.data.objects.new(str(params.get("name") or "Empty"), None)
    obj.empty_display_type = display_type
    obj.empty_display_size = float(params.get("size", 1.0))
    if params.get("location") is not None:
        obj.location = _vec3(params["location"], "location")
    if params.get("rotation") is not None:
        obj.rotation_euler = _vec3(params["rotation"], "rotation")
    _target_collection(params.get("collection"), create=True).objects.link(obj)

    result = _brief(obj)
    result.update(display_type=display_type, display_size=obj.empty_display_size)
    return result


@command("objects.add_camera", mutates=True)
def add_camera(params: dict) -> dict:
    """Create a camera, optionally making it the scene's active camera."""
    cam = bpy.data.cameras.new(str(params.get("name") or "Camera"))
    cam.type = _enum(params.get("type", "PERSP"), _type_enum("Camera", "type"), "type")
    if params.get("lens") is not None:
        cam.lens = float(params["lens"])
    if params.get("clip_start") is not None:
        cam.clip_start = float(params["clip_start"])
    if params.get("clip_end") is not None:
        cam.clip_end = float(params["clip_end"])

    obj = bpy.data.objects.new(cam.name, cam)
    if params.get("location") is not None:
        obj.location = _vec3(params["location"], "location")
    if params.get("rotation") is not None:
        obj.rotation_euler = _vec3(params["rotation"], "rotation")
    _target_collection(params.get("collection"), create=True).objects.link(obj)

    make_active = bool(params.get("make_active", True))
    if make_active:
        bpy.context.scene.camera = obj

    result = _brief(obj)
    result.update(camera_type=cam.type, lens=cam.lens,
                  scene_camera=bpy.context.scene.camera.name if bpy.context.scene.camera else None)
    return result


@command("objects.add_light", mutates=True)
def add_light(params: dict) -> dict:
    """Create a light. ``size`` maps to the per-type softness/extent property."""
    light_type = _enum(params.get("type", "POINT"), _type_enum("Light", "type"), "type")
    light = bpy.data.lights.new(str(params.get("name") or light_type.title()), type=light_type)

    if params.get("energy") is not None:
        light.energy = float(params["energy"])
    if params.get("color") is not None:
        light.color = _vec3(params["color"], "color")

    size = params.get("size")
    size_field = None
    if size is not None:
        size = float(size)
        if light_type == "SUN":
            light.angle = size  # radians of angular diameter
            size_field = "angle"
        elif light_type == "AREA":
            light.size = size
            size_field = "size"
        else:
            light.shadow_soft_size = size
            size_field = "shadow_soft_size"
    if light_type == "AREA":
        if params.get("shape") is not None:
            light.shape = _enum(params["shape"], _type_enum("AreaLight", "shape"), "shape")
        if params.get("size_y") is not None:
            light.size_y = float(params["size_y"])
    if light_type == "SPOT":
        if params.get("spot_size") is not None:
            light.spot_size = float(params["spot_size"])
        if params.get("spot_blend") is not None:
            light.spot_blend = float(params["spot_blend"])

    obj = bpy.data.objects.new(light.name, light)
    if params.get("location") is not None:
        obj.location = _vec3(params["location"], "location")
    if params.get("rotation") is not None:
        obj.rotation_euler = _vec3(params["rotation"], "rotation")
    _target_collection(params.get("collection"), create=True).objects.link(obj)

    result = _brief(obj)
    result.update(light_type=light.type, energy=light.energy, color=list(light.color),
                  size_applied_to=size_field)
    return result


# ---------------------------------------------------------------------------
# lifecycle
# ---------------------------------------------------------------------------

@command("objects.delete_objects", mutates=True)
def delete_objects(params: dict) -> dict:
    """Delete objects by name via the data API (no selection side effects)."""
    requested = _names(params)
    deleted, missing = [], []
    for name in requested:
        obj = bpy.data.objects.get(name)
        if obj is None:
            missing.append(name)
            continue
        deleted.append(name)
        bpy.data.objects.remove(obj, do_unlink=True)

    purged = None
    if params.get("purge_orphan_data"):
        purged = 0
        for collection in (bpy.data.meshes, bpy.data.curves, bpy.data.lights,
                           bpy.data.cameras, bpy.data.armatures):
            for block in list(collection):
                if block.users == 0:
                    collection.remove(block)
                    purged += 1

    return {"deleted": deleted, "missing": missing, "purged_datablocks": purged,
            "remaining": len(bpy.data.objects)}


@command("objects.duplicate_object", mutates=True)
def duplicate_object(params: dict) -> dict:
    """Copy one object, sharing (linked) or duplicating its object data."""
    src = _obj(params["name"])
    linked = bool(params.get("linked", False))

    copy = src.copy()
    if not linked and src.data is not None:
        copy.data = src.data.copy()
    if params.get("new_name"):
        copy.name = str(params["new_name"])
    if params.get("location") is not None:
        copy.location = _vec3(params["location"], "location")

    collection = params.get("collection")
    if collection:
        _target_collection(collection, create=True).objects.link(copy)
    else:
        for coll in src.users_collection:
            coll.objects.link(copy)
        if not copy.users_collection:
            bpy.context.scene.collection.objects.link(copy)

    result = _brief(copy)
    result.update(source=src.name, linked=linked,
                  requested_name=params.get("new_name"),
                  shares_data_with_source=copy.data is src.data,
                  data_users=copy.data.users if copy.data else None,
                  children_not_duplicated=[c.name for c in src.children])
    return result


# ---------------------------------------------------------------------------
# transforms
# ---------------------------------------------------------------------------

@command("objects.transform_object", mutates=True)
def transform_object(params: dict) -> dict:
    """Set or offset an object's location / rotation / scale via the data API."""
    obj = _obj(params["name"])
    mode = _enum(params.get("mode", "absolute"), ("ABSOLUTE", "DELTA"), "mode")

    changed = []
    if params.get("rotation_mode"):
        obj.rotation_mode = _enum(params["rotation_mode"],
                                  _type_enum("Object", "rotation_mode"), "rotation_mode")
        changed.append("rotation_mode")

    if params.get("location") is not None:
        value = _vec3(params["location"], "location")
        obj.location = value if mode == "ABSOLUTE" else [obj.location[i] + value[i] for i in range(3)]
        changed.append("location")

    if params.get("rotation") is not None:
        if obj.rotation_mode not in _EULER_MODES:
            raise ValueError(
                f"{obj.name!r} uses rotation_mode {obj.rotation_mode!r}, so rotation_euler is "
                f"ignored. Pass rotation_mode='XYZ' in this same call to switch it first."
            )
        value = _vec3(params["rotation"], "rotation")
        if mode == "ABSOLUTE":
            obj.rotation_euler = value
        else:
            obj.rotation_euler = [obj.rotation_euler[i] + value[i] for i in range(3)]
        changed.append("rotation_euler")

    if params.get("scale") is not None:
        value = _vec3(params["scale"], "scale")
        obj.scale = value if mode == "ABSOLUTE" else [obj.scale[i] * value[i] for i in range(3)]
        changed.append("scale")

    if not changed:
        raise ValueError(
            "nothing to do: pass at least one of location, rotation, scale or rotation_mode"
        )

    bpy.context.view_layer.update()
    result = _brief(obj)
    result.update(mode=mode.lower(), changed=changed, rotation_mode=obj.rotation_mode)
    return result


@command("objects.apply_transforms", mutates=True)
def apply_transforms(params: dict) -> dict:
    """Bake location/rotation/scale into the object data, resetting the transform."""
    objs = _objs(params)
    location = bool(params.get("location", False))
    rotation = bool(params.get("rotation", True))
    scale = bool(params.get("scale", True))
    if not (location or rotation or scale):
        raise ValueError("set at least one of location, rotation or scale to true")

    with _object_mode():
        _select_only(objs, active=objs[0])
        bpy.ops.object.transform_apply(
            location=location,
            rotation=rotation,
            scale=scale,
            properties=bool(params.get("properties", True)),
            isolate_users=bool(params.get("isolate_users", False)),
        )

    return {
        "objects": [_brief(o) for o in objs],
        "applied": {"location": location, "rotation": rotation, "scale": scale},
    }


@command("objects.set_origin", mutates=True)
def set_origin(params: dict) -> dict:
    """Move an object's origin (or move geometry to the origin)."""
    objs = _objs(params)
    origin_type = _enum(params.get("type", "ORIGIN_GEOMETRY"),
                        _op_enum(bpy.ops.object.origin_set, "type"), "type")
    center = _enum(params.get("center", "MEDIAN"),
                   _op_enum(bpy.ops.object.origin_set, "center"), "center")

    with _object_mode():
        _select_only(objs, active=objs[0])
        bpy.ops.object.origin_set(type=origin_type, center=center)

    return {"objects": [_brief(o) for o in objs], "type": origin_type, "center": center}


@command("objects.snap_to_ground", mutates=True)
def snap_to_ground(params: dict) -> dict:
    """Drop objects along Z until their world bounding-box minimum sits on a plane."""
    objs = _objs(params)
    ground_z = float(params.get("ground_z", 0.0))
    together = bool(params.get("together", False))
    evaluated = bool(params.get("use_evaluated", False))

    def world_min_z(obj) -> float:
        matrix = obj.matrix_world
        return min((matrix @ corner).z for corner in _local_corners(obj, evaluated))

    moved = []
    if together:
        offset = ground_z - min(world_min_z(o) for o in objs)
        for obj in objs:
            obj.location.z += offset
            moved.append({"name": obj.name, "delta_z": offset})
    else:
        for obj in objs:
            offset = ground_z - world_min_z(obj)
            obj.location.z += offset
            moved.append({"name": obj.name, "delta_z": offset})

    bpy.context.view_layer.update()
    return {"moved": moved, "ground_z": ground_z, "together": together,
            "use_evaluated": evaluated,
            "objects": [_brief(o) for o in objs]}


@command("objects.align_objects", mutates=True)
def align_objects(params: dict) -> dict:
    """Align selected objects on one or more axes relative to a reference."""
    objs = _objs(params)
    if len(objs) < 2:
        raise ValueError("align_objects needs at least two objects")

    axis = params.get("axis", "Z")
    axes = {axis.upper()} if isinstance(axis, str) else {str(a).upper() for a in axis}
    for value in axes:
        _enum(value, _op_enum(bpy.ops.object.align, "align_axis"), "axis")

    mode_map = {"NEGATIVE": "OPT_1", "CENTERS": "OPT_2", "POSITIVE": "OPT_3"}
    relative_map = {"SCENE_ORIGIN": "OPT_1", "CURSOR": "OPT_2",
                    "SELECTION": "OPT_3", "ACTIVE": "OPT_4"}
    mode = _enum(params.get("mode", "CENTERS"), mode_map, "mode")
    relative_to = _enum(params.get("relative_to", "SELECTION"), relative_map, "relative_to")

    active_name = params.get("active")
    active = _obj(active_name) if active_name else objs[0]

    with _object_mode():
        _select_only(objs, active=active)
        bpy.ops.object.align(
            bb_quality=bool(params.get("bb_quality", True)),
            align_mode=mode_map[mode],
            relative_to=relative_map[relative_to],
            align_axis=axes,
        )

    bpy.context.view_layer.update()
    return {"objects": [_brief(o) for o in objs], "axes": sorted(axes),
            "mode": mode, "relative_to": relative_to, "active": active.name}


@command("objects.snap_cursor_to", mutates=True)
def snap_cursor_to(params: dict) -> dict:
    """Move the 3D cursor, which is what ORIGIN_CURSOR and new primitives follow."""
    target = _enum(params.get("target", "WORLD_ORIGIN"),
                   ("WORLD_ORIGIN", "LOCATION", "OBJECT", "SELECTED"), "target")
    use_bounds = bool(params.get("use_bounds", False))

    def centre(obj) -> Vector:
        if not use_bounds:
            return obj.matrix_world.translation.copy()
        corners = [obj.matrix_world @ c for c in _local_corners(obj)]
        return Vector([(min(p[i] for p in corners) + max(p[i] for p in corners)) * 0.5
                       for i in range(3)])

    if target == "WORLD_ORIGIN":
        location = Vector((0.0, 0.0, 0.0))
    elif target == "LOCATION":
        if params.get("location") is None:
            raise ValueError("target='LOCATION' also needs a location [x, y, z]")
        location = Vector(_vec3(params["location"], "location"))
    elif target == "OBJECT":
        if not params.get("object"):
            raise ValueError("target='OBJECT' also needs an object name")
        location = centre(_obj(params["object"]))
    else:
        selected = [o for o in bpy.context.view_layer.objects if o.select_get()]
        if not selected:
            raise RuntimeError(
                "target='SELECTED' but nothing is selected; call objects.select_objects first"
            )
        points = [centre(o) for o in selected]
        location = Vector([sum(p[i] for p in points) / len(points) for i in range(3)])

    bpy.context.scene.cursor.location = location
    return {"cursor": list(bpy.context.scene.cursor.location), "target": target,
            "use_bounds": use_bounds}


@command("objects.object_bounds")
def object_bounds(params: dict) -> dict:
    """Local and world bounding box plus centre and dimensions for one object."""
    obj = _obj(params["name"])
    evaluated = bool(params.get("use_evaluated", False))

    local = _local_corners(obj, evaluated)
    world = [obj.matrix_world @ c for c in local]
    return {
        "name": obj.name,
        "type": obj.type,
        "use_evaluated": evaluated,
        "local": _extent(local),
        "world": _extent(world),
        "dimensions": list(obj.dimensions),
        "location": list(obj.location),
        "matrix_world": [list(row) for row in obj.matrix_world],
        "degenerate": all(abs(v) < 1e-9 for c in local for v in c),
    }


# ---------------------------------------------------------------------------
# hierarchy
# ---------------------------------------------------------------------------

@command("objects.parent_objects", mutates=True)
def parent_objects(params: dict) -> dict:
    """Parent one or more children to an object, including the armature variants."""
    raw = params.get("child", params.get("children"))
    if raw is None:
        raise ValueError("pass child='Name' or children=['A', 'B'] plus parent='Name'")
    children = _objs({"names": raw})
    parent = _obj(params["parent"])
    ptype = _enum(params.get("type", "OBJECT"),
                  _op_enum(bpy.ops.object.parent_set, "type"), "type")

    if ptype.startswith("ARMATURE") or ptype.startswith("BONE"):
        if parent.type != "ARMATURE":
            raise ValueError(
                f"type={ptype!r} requires an ARMATURE parent, but {parent.name!r} is "
                f"{parent.type}. Use type='OBJECT' for plain parenting."
            )
    if ptype.startswith("BONE"):
        bone_name = params.get("bone")
        bones = parent.data.bones
        if bone_name:
            if bone_name not in bones:
                raise KeyError(
                    f"armature {parent.name!r} has no bone {bone_name!r}; it has "
                    f"{[b.name for b in bones][:20]}"
                )
            bones.active = bones[bone_name]
        elif bones.active is None:
            raise ValueError(
                f"type={ptype!r} parents to the armature's *active* bone; pass bone='<name>' "
                f"because {parent.name!r} has no active bone"
            )

    with _object_mode():
        _select_only(children + [parent], active=parent)
        bpy.ops.object.parent_set(
            type=ptype,
            xmirror=bool(params.get("xmirror", False)),
            keep_transform=bool(params.get("keep_transform", True)),
        )

    return {
        "parent": parent.name,
        "type": ptype,
        "children": [
            {"name": c.name, "parent": c.parent.name if c.parent else None,
             "parent_type": c.parent_type, "parent_bone": c.parent_bone,
             "vertex_groups": [g.name for g in c.vertex_groups][:50],
             "modifiers": [m.type for m in c.modifiers]}
            for c in children
        ],
    }


@command("objects.join_objects", mutates=True)
def join_objects(params: dict) -> dict:
    """Merge several objects of one type into a single target object."""
    objs = _objs(params)
    target_name = params.get("target") or objs[0].name
    target = _obj(target_name)
    if target not in objs:
        objs.append(target)
    if len(objs) < 2:
        raise ValueError("join_objects needs at least two distinct objects")

    wrong = [o.name for o in objs if o.type != target.type]
    if wrong:
        raise ValueError(
            f"join only merges objects of the same type as the target ({target.type}); "
            f"these differ: {wrong}"
        )

    merged = [o.name for o in objs if o is not target]
    with _object_mode():
        _select_only(objs, active=target)
        bpy.ops.object.join()

    result = _brief(target)
    result.update(target=target.name, merged=merged,
                  vertices=len(target.data.vertices) if target.type == "MESH" else None,
                  polygons=len(target.data.polygons) if target.type == "MESH" else None)
    return result


@command("objects.separate", mutates=True)
def separate(params: dict) -> dict:
    """Split a mesh into several objects by selection, material or loose parts."""
    obj = _obj(params["name"])
    if obj.type != "MESH":
        raise ValueError(f"separate only works on meshes; {obj.name!r} is {obj.type}")
    by = _enum(params.get("by", "LOOSE"), _op_enum(bpy.ops.mesh.separate, "type"), "by")

    before = set(bpy.data.objects.keys())
    with _object_mode():
        _select_only([obj], active=obj)
        bpy.ops.object.mode_set(mode="EDIT")
        try:
            # SELECTED acts on the selection already stored in the mesh; MATERIAL and
            # LOOSE see nothing to work on unless everything is selected first.
            if by != "SELECTED":
                bpy.ops.mesh.select_all(action="SELECT")
            bpy.ops.mesh.separate(type=by)
        finally:
            bpy.ops.object.mode_set(mode="OBJECT")

    created = sorted(n for n in bpy.data.objects.keys() if n not in before)
    return {
        "source": obj.name,
        "by": by,
        "created": created,
        "objects": [_brief(bpy.data.objects[n]) for n in created],
        "source_vertices": len(obj.data.vertices),
    }


# ---------------------------------------------------------------------------
# selection
# ---------------------------------------------------------------------------

@command("objects.select_objects", mutates=False)
def select_objects(params: dict) -> dict:
    """Set, extend or reduce the object selection."""
    objs = _objs(params)
    mode = _enum(params.get("mode", "SET"), ("SET", "ADD", "REMOVE"), "mode")
    deselect_others = params.get("deselect_others")
    if deselect_others is None:
        deselect_others = mode == "SET"

    if deselect_others:
        _deselect_all()
    for obj in objs:
        _select(obj, mode != "REMOVE")

    active_name = params.get("active")
    if active_name:
        bpy.context.view_layer.objects.active = _obj(active_name)
    elif mode != "REMOVE" and objs:
        bpy.context.view_layer.objects.active = objs[-1]

    active = bpy.context.view_layer.objects.active
    return {
        "selected": [o.name for o in bpy.context.view_layer.objects if o.select_get()],
        "active": active.name if active else None,
        "mode": mode,
        "deselect_others": bool(deselect_others),
    }


@command("objects.set_active", mutates=False)
def set_active(params: dict) -> dict:
    """Make one object the active object (what mode changes and operators act on)."""
    obj = _obj(params["name"])
    if obj.name not in bpy.context.view_layer.objects:
        raise RuntimeError(
            f"{obj.name!r} is not in the active view layer, so it cannot become active. "
            f"Link it into a visible collection first (objects.collection_link)."
        )
    bpy.context.view_layer.objects.active = obj
    if bool(params.get("select", True)):
        _select(obj, True)

    return {"active": obj.name, "type": obj.type, "mode": bpy.context.mode,
            "selected": obj.select_get()}


# ---------------------------------------------------------------------------
# collections
# ---------------------------------------------------------------------------

@command("objects.collection_create", mutates=True)
def collection_create(params: dict) -> dict:
    """Create a collection, optionally nested inside another one."""
    name = str(params["name"])
    existing = bpy.data.collections.get(name)
    if existing is not None and not bool(params.get("allow_duplicate_name", False)):
        raise ValueError(
            f"collection {name!r} already exists; pass allow_duplicate_name=true to make a "
            f"second one (Blender will suffix it) or reuse the existing collection"
        )

    coll = bpy.data.collections.new(name)
    parent_name = params.get("parent")
    parent = _target_collection(parent_name) if parent_name else bpy.context.scene.collection
    parent.children.link(coll)

    return {"name": coll.name, "requested_name": name, "parent": parent.name,
            "children": [c.name for c in coll.children]}


@command("objects.collection_link", mutates=True)
def collection_link(params: dict) -> dict:
    """Link objects into a collection *in addition to* the ones they are already in."""
    objs = _objs(params)
    coll = _required_collection(params, create=bool(params.get("create", True)))
    linked, already = [], []
    for obj in objs:
        if obj.name in coll.objects:
            already.append(obj.name)
            continue
        coll.objects.link(obj)
        linked.append(obj.name)

    return {"collection": coll.name, "linked": linked, "already_linked": already,
            "membership": {o.name: [c.name for c in o.users_collection] for o in objs}}


@command("objects.collection_move", mutates=True)
def collection_move(params: dict) -> dict:
    """Move objects into a collection, unlinking them from every other one."""
    objs = _objs(params)
    coll = _required_collection(params, create=bool(params.get("create", True)))
    before = {o.name: [c.name for c in o.users_collection] for o in objs}
    for obj in objs:
        _link_only_to(obj, coll)

    return {"collection": coll.name, "moved": [o.name for o in objs], "was_in": before,
            "membership": {o.name: [c.name for c in o.users_collection] for o in objs}}


@command("objects.collection_list")
def collection_list(params: dict) -> dict:
    """The scene's collection tree with per-collection object counts."""
    limit = int(params.get("limit", 1000))
    name_cap = int(params.get("names_per_collection", 50))
    scene = bpy.context.scene

    rows: List[dict] = []
    seen = set()

    def walk(coll, depth: int, parent: Optional[str]) -> None:
        seen.add(coll.name)
        rows.append({
            "name": coll.name,
            "depth": depth,
            "parent": parent,
            "objects": len(coll.objects),
            "objects_recursive": len(coll.all_objects),
            "object_names": [o.name for o in coll.objects][:name_cap],
            "children": [c.name for c in coll.children],
            "hide_viewport": coll.hide_viewport,
            "hide_render": coll.hide_render,
        })
        for child in coll.children:
            walk(child, depth + 1, coll.name)

    walk(scene.collection, 0, None)
    unlinked = [c.name for c in bpy.data.collections if c.name not in seen]

    active = bpy.context.view_layer.active_layer_collection
    return {
        "scene": scene.name,
        "master_collection": scene.collection.name,
        "active_collection": active.collection.name if active else None,
        "collections": rows[:limit],
        "count": len(rows),
        "truncated": len(rows) > limit,
        "unlinked_collections": unlinked[:limit],
    }


# ---------------------------------------------------------------------------
# aiming, and editing lights/cameras after creation
# ---------------------------------------------------------------------------

def _aim_euler(from_co, to_co):
    """Euler rotation that points a camera/light's -Z axis at ``to_co``.

    Blender cameras look down local -Z with +Y up, so the usual track-to maths
    applies. Returned as an XYZ euler in radians.
    """
    direction = Vector(to_co) - Vector(from_co)
    if direction.length < 1e-9:
        raise ValueError("cannot aim: the object and its target are at the same point")
    # to_track_quat handles the -Z forward / +Y up convention for us; deriving
    # the euler by hand is where sign conventions get silently reversed.
    return direction.to_track_quat("-Z", "Y").to_euler()


@command("objects.aim_at", mutates=True)
def aim_at(params: dict) -> dict:
    """Rotate an object so its -Z axis points at a target point or object."""
    obj = _obj(params["object"])

    target = params.get("target")
    if target is None:
        raise ValueError("'target' is required: an object name or an [x, y, z] point")
    if isinstance(target, str):
        other = bpy.data.objects.get(target)
        if other is None:
            raise KeyError(f"no object named {target!r} to aim at")
        point = _bounds_center_world(other) if params.get("use_bounds", True) \
            else other.matrix_world.translation
    else:
        point = Vector(_vec3(target, "target"))

    obj.rotation_mode = "XYZ"
    obj.rotation_euler = _aim_euler(obj.matrix_world.translation, point)
    bpy.context.view_layer.update()
    return {"object": obj.name, "aimed_at": list(point),
            "rotation_euler": list(obj.rotation_euler)}


def _bounds_center_world(obj):
    corners = [obj.matrix_world @ Vector(c) for c in obj.bound_box]
    return sum(corners, Vector()) / 8.0


@command("objects.frame_object", mutates=True)
def frame_object(params: dict) -> dict:
    """Place a camera so a target object fills the frame, and aim it at the object."""
    import math

    target = _obj(params["target"])
    cam_name = params.get("camera")
    if cam_name:
        cam = _obj(cam_name)
        if cam.type != "CAMERA":
            raise TypeError(f"{cam.name!r} is a {cam.type}, not a CAMERA")
    else:
        cam = bpy.context.scene.camera
        if cam is None:
            raise RuntimeError(
                "no camera given and the scene has none. Create one with "
                "objects.add_camera first."
            )

    corners = [target.matrix_world @ Vector(c) for c in target.bound_box]
    center = sum(corners, Vector()) / 8.0
    radius = max((c - center).length for c in corners) or 1.0

    # Fit the bounding sphere inside the narrower of the two FOVs, with margin.
    render = bpy.context.scene.render
    fov = cam.data.angle
    aspect = (render.resolution_x * render.pixel_aspect_x) / \
             max(1e-9, render.resolution_y * render.pixel_aspect_y)
    if aspect < 1.0:
        fov = 2.0 * math.atan(math.tan(fov / 2.0) * aspect)
    margin = float(params.get("margin", 1.25))
    distance = (radius * margin) / max(1e-6, math.sin(fov / 2.0))

    direction = params.get("direction", [0.0, -1.0, 0.35])
    offset = Vector(_vec3(direction, "direction"))
    if offset.length < 1e-9:
        raise ValueError("'direction' must not be the zero vector")
    offset.normalize()

    cam.location = center + offset * distance
    cam.rotation_mode = "XYZ"
    cam.rotation_euler = _aim_euler(cam.location, center)
    if params.get("make_active", True):
        bpy.context.scene.camera = cam
    bpy.context.view_layer.update()

    return {"camera": cam.name, "target": target.name,
            "location": list(cam.location), "rotation_euler": list(cam.rotation_euler),
            "distance": distance, "bounds_radius": radius,
            "focal_length_mm": cam.data.lens, "is_scene_camera": bpy.context.scene.camera == cam}


@command("objects.set_camera", mutates=True)
def set_camera(params: dict) -> dict:
    """Edit an existing camera's lens, type, clipping and DOF."""
    obj = _obj(params["camera"])
    if obj.type != "CAMERA":
        raise TypeError(f"{obj.name!r} is a {obj.type}, not a CAMERA")
    cam = obj.data

    if params.get("type") is not None:
        cam.type = _enum(params["type"], _type_enum("Camera", "type"), "type")
    for key, attr, cast in (("lens", "lens", float),
                            ("ortho_scale", "ortho_scale", float),
                            ("clip_start", "clip_start", float),
                            ("clip_end", "clip_end", float),
                            ("shift_x", "shift_x", float),
                            ("shift_y", "shift_y", float)):
        if params.get(key) is not None:
            setattr(cam, attr, cast(params[key]))

    if params.get("dof_distance") is not None:
        cam.dof.use_dof = True
        cam.dof.focus_distance = float(params["dof_distance"])
    if params.get("dof_object") is not None:
        cam.dof.use_dof = True
        cam.dof.focus_object = _obj(params["dof_object"])
    if params.get("fstop") is not None:
        cam.dof.use_dof = True
        cam.dof.aperture_fstop = float(params["fstop"])
    if params.get("use_dof") is not None:
        cam.dof.use_dof = bool(params["use_dof"])
    if params.get("make_active"):
        bpy.context.scene.camera = obj

    return {"camera": obj.name, "type": cam.type, "lens": cam.lens,
            "clip_start": cam.clip_start, "clip_end": cam.clip_end,
            "use_dof": cam.dof.use_dof, "focus_distance": cam.dof.focus_distance,
            "aperture_fstop": cam.dof.aperture_fstop,
            "is_scene_camera": bpy.context.scene.camera == obj}


@command("objects.set_light", mutates=True)
def set_light(params: dict) -> dict:
    """Edit an existing light's energy, colour, size, type and shadow settings."""
    obj = _obj(params["light"])
    if obj.type != "LIGHT":
        raise TypeError(f"{obj.name!r} is a {obj.type}, not a LIGHT")
    light = obj.data

    if params.get("type") is not None:
        light.type = _enum(params["type"], _type_enum("Light", "type"), "type")
    if params.get("energy") is not None:
        light.energy = float(params["energy"])
    if params.get("color") is not None:
        light.color = _vec3(params["color"], "color")
    if params.get("use_shadow") is not None:
        light.use_shadow = bool(params["use_shadow"])

    size_field = None
    if params.get("size") is not None:
        size = float(params["size"])
        if light.type == "SUN":
            light.angle = size
            size_field = "angle"
        elif light.type == "AREA":
            light.size = size
            size_field = "size"
        else:
            light.shadow_soft_size = size
            size_field = "shadow_soft_size"

    if params.get("spot_size") is not None:
        if light.type != "SPOT":
            raise TypeError(f"spot_size only applies to SPOT lights; {obj.name!r} is {light.type}")
        light.spot_size = float(params["spot_size"])
    if params.get("spot_blend") is not None:
        light.spot_blend = float(params["spot_blend"])

    result = {"light": obj.name, "type": light.type, "energy": light.energy,
              "color": list(light.color), "use_shadow": light.use_shadow}
    if size_field:
        result["size_field"] = size_field
        result["size"] = getattr(light, size_field)
    if light.type == "SPOT":
        result["spot_size"] = light.spot_size
        result["spot_blend"] = light.spot_blend
    return result


_RAY_VISIBILITY = ("camera", "diffuse", "glossy", "transmission", "volume_scatter", "shadow")


@command("objects.set_visibility", mutates=True)
def set_visibility(params: dict) -> dict:
    """Set viewport/render visibility and per-ray visibility flags.

    The ray flags are what you need when emissive geometry sits inside a light:
    an opaque bulb mesh blocks its own lamp until ``shadow=False`` clears it,
    and the render just comes back dark with nothing to react to.
    """
    obj = _obj(params["object"])

    applied = {}
    for key, attr in (("hide_viewport", "hide_viewport"), ("hide_render", "hide_render")):
        if params.get(key) is not None:
            setattr(obj, attr, bool(params[key]))
            applied[attr] = getattr(obj, attr)
    if params.get("hide_get") is not None:
        obj.hide_set(bool(params["hide_get"]))
        applied["hide_get"] = obj.hide_get()

    unsupported = []
    for key in _RAY_VISIBILITY:
        if params.get(key) is None:
            continue
        attr = f"visible_{key}"
        if not hasattr(obj, attr):
            unsupported.append(key)
            continue
        setattr(obj, attr, bool(params[key]))
        applied[attr] = getattr(obj, attr)

    result = {"object": obj.name, "applied": applied,
              "ray_visibility": {k: getattr(obj, f"visible_{k}")
                                 for k in _RAY_VISIBILITY if hasattr(obj, f"visible_{k}")},
              "hide_viewport": obj.hide_viewport, "hide_render": obj.hide_render}
    if unsupported:
        result["unsupported"] = (
            f"{unsupported} are Cycles ray-visibility flags not present on this "
            f"object in the current render engine"
        )
    return result

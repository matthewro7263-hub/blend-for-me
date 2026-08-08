"""Modifier stack: add, discover, tune, reorder, apply and remove.

Everything here goes through the data API (``object.modifiers``) except the four
operations Blender exposes only as operators: applying a modifier and the three
multires level operations. Those are run under an explicit context override so
they work on any object regardless of what happens to be active.
"""

from __future__ import annotations

import bpy

from ..registry import command

#: RNA pointer type -> the ``bpy.data`` collection its datablocks live in, so a
#: pointer property (Boolean.object, Shrinkwrap.target, ...) can be set by name.
_DATABLOCKS = {
    "Object": "objects",
    "Collection": "collections",
    "Mesh": "meshes",
    "Material": "materials",
    "Texture": "textures",
    "Image": "images",
    "Armature": "armatures",
    "Curve": "curves",
    "Scene": "scenes",
    "Action": "actions",
    "ParticleSettings": "particles",
}

#: DataTransfer splits its data types across four domain flag-enums, each gated
#: by its own ``use_*_data`` toggle. Handlers take one flat list and route it.
_DT_DOMAINS = (
    ("data_types_verts", "use_vert_data"),
    ("data_types_edges", "use_edge_data"),
    ("data_types_loops", "use_loop_data"),
    ("data_types_polys", "use_poly_data"),
)

_AXES = ("X", "Y", "Z")


# ---------------------------------------------------------------------------
# lookup
# ---------------------------------------------------------------------------

def _object(name: str):
    obj = bpy.data.objects.get(name)
    if obj is None:
        raise KeyError(
            f"no object named {name!r}; call get_scene_info for the real names"
        )
    return obj


def _modifier(obj, ident):
    """Resolve a modifier by name, or by integer position in the stack."""
    if isinstance(ident, bool):
        raise TypeError("modifier must be a name or a stack index, not a boolean")
    if isinstance(ident, int) or (isinstance(ident, str) and ident.lstrip("-").isdigit()):
        index = int(ident)
        try:
            return obj.modifiers[index]
        except IndexError:
            raise IndexError(
                f"{obj.name!r} has {len(obj.modifiers)} modifier(s), so index "
                f"{index} is out of range: {[m.name for m in obj.modifiers]}"
            ) from None
    mod = obj.modifiers.get(ident)
    if mod is None:
        raise KeyError(
            f"{obj.name!r} has no modifier named {ident!r}; it has "
            f"{[m.name for m in obj.modifiers]}"
        )
    return mod


def _modifier_types() -> list:
    return [i.identifier for i in bpy.types.Modifier.bl_rna.properties["type"].enum_items]


def _upper(value):
    return value.upper() if isinstance(value, str) else value


# ---------------------------------------------------------------------------
# property read / write
# ---------------------------------------------------------------------------

def _settable(mod) -> dict:
    return {
        p.identifier: p
        for p in mod.bl_rna.properties
        if p.identifier != "rna_type" and not p.is_readonly
    }


def _read(mod, prop):
    value = getattr(mod, prop.identifier)
    if prop.type == "POINTER":
        return value.name if value is not None else None
    if prop.type == "ENUM":
        return sorted(value) if prop.is_enum_flag else value
    if getattr(prop, "array_length", 0):
        return list(value)
    return value


def _resolve_pointer(prop, value):
    if value is None:
        return None
    target = prop.fixed_type.identifier
    if not isinstance(value, str):
        raise TypeError(
            f"{prop.identifier!r} takes the *name* of a {target} datablock "
            f"(a string), got {type(value).__name__}"
        )
    attr = _DATABLOCKS.get(target)
    if attr is None:
        raise TypeError(
            f"{prop.identifier!r} points at a {target}, which cannot be addressed "
            f"by name from here — use execute_python for this one"
        )
    datablock = getattr(bpy.data, attr).get(value)
    if datablock is None:
        raise KeyError(
            f"no {target} named {value!r} in this file; available: "
            f"{[d.name for d in getattr(bpy.data, attr)][:40]}"
        )
    return datablock


def _write(mod, key: str, value) -> None:
    prop = mod.bl_rna.properties.get(key)
    if prop is None or prop.is_readonly or key == "rna_type":
        why = "is read-only" if prop is not None else "does not exist"
        raise KeyError(
            f"{key!r} {why} on the {mod.type} modifier {mod.name!r}. "
            f"Settable properties: {sorted(_settable(mod))}"
        )

    if prop.type == "POINTER":
        setattr(mod, key, _resolve_pointer(prop, value))
        return

    if prop.type == "ENUM":
        valid = [i.identifier for i in prop.enum_items]
        if prop.is_enum_flag:
            wanted = {value} if isinstance(value, str) else set(value)
            bad = sorted(wanted - set(valid))
            if bad:
                raise ValueError(f"{key}: unknown flag(s) {bad}; valid flags are {valid}")
            setattr(mod, key, wanted)  # RNA flag enums reject lists, they need a set
        else:
            if value not in valid:
                raise ValueError(f"{key}={value!r} is not valid; valid values are {valid}")
            setattr(mod, key, value)
        return

    setattr(mod, key, value)


def _apply_settings(mod, settings) -> list:
    if not settings:
        return []
    if not isinstance(settings, dict):
        raise TypeError(f"settings must be an object/dict, got {type(settings).__name__}")
    for key, value in settings.items():
        _write(mod, key, value)
    return list(settings)


def _describe(mod, prop) -> dict:
    entry = {"type": prop.type, "value": _read(mod, prop)}
    if prop.type == "ENUM":
        entry["options"] = [i.identifier for i in prop.enum_items]
        if prop.is_enum_flag:
            entry["multi_select"] = True
    elif prop.type == "POINTER":
        entry["datablock"] = prop.fixed_type.identifier
    elif prop.type in {"INT", "FLOAT"}:
        entry["soft_min"] = prop.soft_min
        entry["soft_max"] = prop.soft_max
    return entry


def _brief(obj, mod) -> dict:
    return {
        "name": mod.name,
        "type": mod.type,
        "index": obj.modifiers.find(mod.name),
        "show_viewport": mod.show_viewport,
        "show_render": mod.show_render,
        "show_in_editmode": mod.show_in_editmode,
    }


# ---------------------------------------------------------------------------
# creation
# ---------------------------------------------------------------------------

def _new(obj, mod_type: str, name=None, settings=None):
    mod_type = str(mod_type).upper()
    valid = _modifier_types()
    if mod_type not in valid:
        raise ValueError(f"unknown modifier type {mod_type!r}; valid types are {valid}")

    # An empty name makes Blender use its own UI default ("Subdivision", ...).
    mod = obj.modifiers.new(name=name or "", type=mod_type)
    if mod is None:
        # modifiers.new returns None rather than raising for unsupported combos.
        raise RuntimeError(
            f"Blender refused to add a {mod_type} modifier to {obj.name!r}: a "
            f"{obj.type} object does not accept it. Check the object type first "
            f"with get_object_info."
        )
    try:
        _apply_settings(mod, settings)
    except Exception:
        obj.modifiers.remove(mod)  # don't leave a half-configured modifier behind
        raise
    return mod


def _added(obj, mod, **extra) -> dict:
    result = {"object": obj.name, "modifier": _brief(obj, mod)}
    result.update(extra)
    return result


def _convenience(params: dict, mod_type: str, **fields):
    """Add ``mod_type``, write the non-None ``fields``, then the raw settings dict."""
    obj = _object(params["object"])
    mod = _new(obj, mod_type, params.get("name"))
    try:
        for key, value in fields.items():
            if value is not None:
                _write(mod, key, value)
        _apply_settings(mod, params.get("settings"))
    except Exception:
        obj.modifiers.remove(mod)
        raise
    return obj, mod


# ---------------------------------------------------------------------------
# operator context
# ---------------------------------------------------------------------------

def _override(obj):
    """Context override that makes ``obj`` the operator's active, selected object.

    Verified in 5.2: this is enough for ``object.modifier_apply`` and the
    multires operators to act on ``obj`` even while a different object is active
    in the view layer.
    """
    return bpy.context.temp_override(
        object=obj,
        active_object=obj,
        selected_objects=[obj],
        selected_editable_objects=[obj],
    )


def _require_object_mode(obj) -> str:
    """Leave any non-OBJECT mode; the stack operators refuse to run otherwise."""
    was = obj.mode
    if was != "OBJECT":
        if obj.name not in bpy.context.view_layer.objects:
            raise RuntimeError(
                f"{obj.name!r} is in {was} mode but is not in the active view layer, "
                f"so it cannot be switched to OBJECT mode. Unhide/link it first."
            )
        bpy.context.view_layer.objects.active = obj
        bpy.ops.object.mode_set(mode="OBJECT")
    return was


# ---------------------------------------------------------------------------
# generic stack commands
# ---------------------------------------------------------------------------

@command("modifiers.add", mutates=True)
def add(params: dict) -> dict:
    """Add a modifier of any type and optionally write settings onto it."""
    obj = _object(params["object"])
    mod = _new(obj, params["type"], params.get("name"), params.get("settings"))
    return _added(obj, mod, applied_settings=sorted(params.get("settings") or {}))


@command("modifiers.list")
def list_modifiers(params: dict) -> dict:
    """Every modifier on an object with its settable properties and current values."""
    obj = _object(params["object"])
    limit = int(params.get("limit", 1000))
    include = bool(params.get("include_properties", True))

    mods = list(obj.modifiers)
    entries = []
    for mod in mods[:limit]:
        entry = _brief(obj, mod)
        if include:
            entry["properties"] = {
                key: _describe(mod, prop) for key, prop in sorted(_settable(mod).items())
            }
        entries.append(entry)

    return {
        "object": obj.name,
        "object_type": obj.type,
        "count": len(mods),
        "modifiers": entries,
        "truncated": len(mods) > limit,
    }


@command("modifiers.set_prop", mutates=True)
def set_prop(params: dict) -> dict:
    """Write one property, or a whole settings dict, onto an existing modifier."""
    obj = _object(params["object"])
    mod = _modifier(obj, params["modifier"])

    settings = params.get("settings")
    if settings is None:
        if "prop" not in params:
            raise KeyError("pass either 'prop' + 'value', or a 'settings' dict")
        settings = {params["prop"]: params.get("value")}

    keys = _apply_settings(mod, settings)
    return {
        "object": obj.name,
        "modifier": mod.name,
        "type": mod.type,
        "set": {k: _read(mod, mod.bl_rna.properties[k]) for k in keys},
    }


@command("modifiers.apply", mutates=True)
def apply(params: dict) -> dict:
    """Bake a modifier into the object's data and drop it from the stack."""
    obj = _object(params["object"])
    mod = _modifier(obj, params["modifier"])
    single_user = bool(params.get("single_user", False))

    name, mod_type = mod.name, mod.type
    index = obj.modifiers.find(name)
    data = obj.data

    if data is None:
        raise RuntimeError(f"{obj.name!r} (a {obj.type}) has no object data to apply into")
    if getattr(data, "library", None) is not None:
        raise RuntimeError(
            f"cannot apply {name!r}: {obj.name!r} uses linked library data "
            f"({data.library.filepath}). Make it local first."
        )

    users = data.users - (1 if data.use_fake_user else 0)
    if users > 1 and not single_user:
        sharers = [o.name for o in bpy.data.objects if o.data is data]
        raise RuntimeError(
            f"cannot apply {name!r}: the {type(data).__name__} {data.name!r} is shared "
            f"by {users} objects ({sharers[:10]}). Pass single_user=true to give "
            f"{obj.name!r} its own copy first, or unlink the other users."
        )

    keys = getattr(data, "shape_keys", None)
    if keys is not None:
        raise RuntimeError(
            f"cannot apply {name!r}: Blender refuses to apply modifiers to a mesh "
            f"with shape keys ({[k.name for k in keys.key_blocks][:10]}). Delete the "
            f"shape keys, or keep the modifier live instead of applying it."
        )

    if index > 0:
        # Blender applies out-of-order modifiers anyway but the result is the
        # stack evaluated as if this one were first — worth telling the agent.
        note = (f"{name!r} was at index {index}, not the bottom of the stack; the "
                f"result is as if it ran first")
    else:
        note = None

    was_mode = _require_object_mode(obj)
    with _override(obj):
        result = bpy.ops.object.modifier_apply(modifier=name, single_user=single_user)
    if "FINISHED" not in result:
        raise RuntimeError(f"modifier_apply on {name!r} returned {set(result)} instead of FINISHED")

    out = {
        "object": obj.name,
        "applied": name,
        "type": mod_type,
        "remaining": [m.name for m in obj.modifiers],
        "previous_mode": was_mode,
    }
    if note:
        out["note"] = note
    if obj.type == "MESH":
        out["mesh"] = {
            "vertices": len(obj.data.vertices),
            "edges": len(obj.data.edges),
            "polygons": len(obj.data.polygons),
        }
    return out


@command("modifiers.remove", mutates=True)
def remove(params: dict) -> dict:
    """Delete a modifier without baking it in."""
    obj = _object(params["object"])
    mod = _modifier(obj, params["modifier"])
    name, mod_type = mod.name, mod.type
    obj.modifiers.remove(mod)
    return {
        "object": obj.name,
        "removed": name,
        "type": mod_type,
        "remaining": [m.name for m in obj.modifiers],
    }


@command("modifiers.reorder", mutates=True)
def reorder(params: dict) -> dict:
    """Move a modifier to a new position in the stack."""
    obj = _object(params["object"])
    mod = _modifier(obj, params["modifier"])
    name = mod.name
    count = len(obj.modifiers)

    index = int(params["index"])
    if index < 0:
        index += count
    if not 0 <= index < count:
        raise IndexError(
            f"index {params['index']} is out of range: {obj.name!r} has {count} "
            f"modifier(s), so valid indices are 0..{count - 1}"
        )

    current = obj.modifiers.find(name)
    if current != index:
        # Blender rejects moves that break stack rules (multires must stay first,
        # a deform modifier cannot cross a non-deforming one); let that surface.
        obj.modifiers.move(current, index)

    return {
        "object": obj.name,
        "modifier": name,
        "from_index": current,
        "to_index": obj.modifiers.find(name),
        "stack": [m.name for m in obj.modifiers],
    }


# ---------------------------------------------------------------------------
# conveniences
# ---------------------------------------------------------------------------

@command("modifiers.add_subsurf", mutates=True)
def add_subsurf(params: dict) -> dict:
    """Catmull-Clark subdivision surface."""
    obj, mod = _convenience(
        params, "SUBSURF",
        levels=params.get("levels"),
        render_levels=params.get("render_levels"),
        use_limit_surface=params.get("use_limit_surface"),
        subdivision_type=_upper(params.get("subdivision_type")),
        quality=params.get("quality"),
    )
    return _added(obj, mod, levels=mod.levels, render_levels=mod.render_levels,
                  use_limit_surface=mod.use_limit_surface)


@command("modifiers.add_mirror", mutates=True)
def add_mirror(params: dict) -> dict:
    """Mirror across one or more local axes, optionally about another object."""
    axis = _axis_mask(params.get("axis"), default=(True, False, False))
    bisect = _axis_mask(params.get("bisect_axis"), default=None)
    flip = _axis_mask(params.get("flip_axis"), default=None)

    obj, mod = _convenience(
        params, "MIRROR",
        use_axis=axis,
        use_bisect_axis=bisect,
        use_bisect_flip_axis=flip,
        use_clip=params.get("use_clip"),
        use_mirror_merge=params.get("use_mirror_merge"),
        merge_threshold=params.get("merge_threshold"),
        mirror_object=params.get("mirror_object"),
    )
    return _added(obj, mod, use_axis=list(mod.use_axis),
                  use_bisect_axis=list(mod.use_bisect_axis),
                  mirror_object=mod.mirror_object.name if mod.mirror_object else None)


def _axis_mask(value, default):
    """Turn ['X','Z'] / [0,2] / 'X' / [True,False,True] into a 3-bool list."""
    if value is None:
        return list(default) if default is not None else None
    if isinstance(value, str):
        value = [value]
    mask = [False, False, False]
    for item in value:
        if isinstance(item, bool):
            # A raw [True, False, True] mask was passed through verbatim.
            if len(value) != 3:
                raise ValueError("a boolean axis mask must have exactly 3 entries")
            return [bool(v) for v in value]
        if isinstance(item, int):
            if not 0 <= item <= 2:
                raise ValueError(f"axis index {item} out of range; use 0, 1 or 2")
            mask[item] = True
        elif isinstance(item, str) and item.upper() in _AXES:
            mask[_AXES.index(item.upper())] = True
        else:
            raise ValueError(f"unknown axis {item!r}; use 'X', 'Y', 'Z' or 0, 1, 2")
    return mask


@command("modifiers.add_solidify", mutates=True)
def add_solidify(params: dict) -> dict:
    """Give a surface thickness."""
    obj, mod = _convenience(
        params, "SOLIDIFY",
        thickness=params.get("thickness"),
        offset=params.get("offset"),
        use_even_offset=params.get("even_thickness"),
        use_rim=params.get("use_rim"),
        solidify_mode=_upper(params.get("solidify_mode")),
    )
    return _added(obj, mod, thickness=mod.thickness, offset=mod.offset,
                  use_even_offset=mod.use_even_offset)


@command("modifiers.add_boolean", mutates=True)
def add_boolean(params: dict) -> dict:
    """Boolean against another mesh object."""
    solver = params.get("solver")
    alias = None
    if isinstance(solver, str):
        solver = solver.upper()
        # 'FAST' was the pre-5.x identifier for what is now 'FLOAT'.
        if solver == "FAST":
            solver, alias = "FLOAT", "FAST -> FLOAT"

    target = params["target"]
    other = _object(target)
    if other.type != "MESH":
        raise ValueError(
            f"boolean target {target!r} is a {other.type}; it must be a MESH object"
        )

    obj, mod = _convenience(
        params, "BOOLEAN",
        object=target,
        operand_type="OBJECT",
        operation=(params.get("operation") or "DIFFERENCE").upper(),
        solver=solver,
        use_self=params.get("use_self"),
        use_hole_tolerant=params.get("use_hole_tolerant"),
    )
    out = _added(obj, mod, target=target, operation=mod.operation, solver=mod.solver)
    if alias:
        out["solver_alias_applied"] = alias
    return out


@command("modifiers.add_shrinkwrap", mutates=True)
def add_shrinkwrap(params: dict) -> dict:
    """Snap the mesh onto the surface of a target object."""
    obj, mod = _convenience(
        params, "SHRINKWRAP",
        target=params["target"],
        wrap_method=_upper(params.get("wrap_method")),
        wrap_mode=_upper(params.get("wrap_mode")),
        offset=params.get("offset"),
        vertex_group=params.get("vertex_group"),
    )
    return _added(obj, mod, target=mod.target.name if mod.target else None,
                  wrap_method=mod.wrap_method, offset=mod.offset)


@command("modifiers.add_armature", mutates=True)
def add_armature(params: dict) -> dict:
    """Bind a mesh to an armature for vertex-group deformation."""
    armature = params["armature"]
    arm_obj = _object(armature)
    if arm_obj.type != "ARMATURE":
        raise ValueError(
            f"{armature!r} is a {arm_obj.type}; the armature modifier needs an "
            f"ARMATURE object"
        )
    obj, mod = _convenience(
        params, "ARMATURE",
        object=armature,
        use_deform_preserve_volume=params.get("use_deform_preserve_volume"),
        use_vertex_groups=params.get("use_vertex_groups"),
        use_bone_envelopes=params.get("use_bone_envelopes"),
        vertex_group=params.get("vertex_group"),
    )
    return _added(obj, mod, armature=armature,
                  use_deform_preserve_volume=mod.use_deform_preserve_volume)


@command("modifiers.add_multires", mutates=True)
def add_multires(params: dict) -> dict:
    """Multiresolution modifier, the base for multi-level sculpting."""
    obj, mod = _convenience(
        params, "MULTIRES",
        render_levels=params.get("render_levels"),
        quality=params.get("quality"),
        use_creases=params.get("use_creases"),
    )
    return _added(obj, mod, levels=mod.levels, sculpt_levels=mod.sculpt_levels,
                  render_levels=mod.render_levels, total_levels=mod.total_levels)


def _multires(obj, ident):
    mod = _modifier(obj, ident) if ident else next(
        (m for m in obj.modifiers if m.type == "MULTIRES"), None)
    if mod is None:
        raise KeyError(
            f"{obj.name!r} has no Multires modifier; add one with add_multires first"
        )
    if mod.type != "MULTIRES":
        raise ValueError(f"{mod.name!r} is a {mod.type} modifier, not MULTIRES")
    return mod


def _multires_state(obj, mod) -> dict:
    return {
        "object": obj.name,
        "modifier": mod.name,
        "levels": mod.levels,
        "sculpt_levels": mod.sculpt_levels,
        "render_levels": mod.render_levels,
        "total_levels": mod.total_levels,
        "vertices": len(obj.data.vertices) if obj.type == "MESH" else None,
    }


@command("modifiers.multires_subdivide", mutates=True)
def multires_subdivide(params: dict) -> dict:
    """Add one or more subdivision levels to a Multires modifier."""
    obj = _object(params["object"])
    mod = _multires(obj, params.get("modifier"))
    mode = (params.get("mode") or "CATMULL_CLARK").upper()

    valid = [i.identifier for i in
             bpy.ops.object.multires_subdivide.get_rna_type().properties["mode"].enum_items]
    if mode not in valid:
        raise ValueError(f"mode={mode!r} is not valid; valid modes are {valid}")

    levels = int(params.get("levels", 1))
    if levels < 1:
        raise ValueError(f"levels must be >= 1, got {levels}")

    _require_object_mode(obj)
    with _override(obj):
        for step in range(levels):
            result = bpy.ops.object.multires_subdivide(modifier=mod.name, mode=mode)
            if "FINISHED" not in result:
                raise RuntimeError(
                    f"multires_subdivide stopped at level {mod.total_levels} after "
                    f"{step} of {levels} step(s): {set(result)}"
                )

    return dict(_multires_state(obj, mod), mode=mode, added_levels=levels)


@command("modifiers.multires_unsubdivide", mutates=True)
def multires_unsubdivide(params: dict) -> dict:
    """Rebuild a lower Multires level by reversing one subdivision."""
    obj = _object(params["object"])
    mod = _multires(obj, params.get("modifier"))
    _require_object_mode(obj)
    with _override(obj):
        result = bpy.ops.object.multires_unsubdivide(modifier=mod.name)
    if "FINISHED" not in result:
        raise RuntimeError(f"multires_unsubdivide returned {set(result)}")
    return _multires_state(obj, mod)


@command("modifiers.multires_apply_base", mutates=True)
def multires_apply_base(params: dict) -> dict:
    """Push the sculpted detail down into the base mesh."""
    obj = _object(params["object"])
    mod = _multires(obj, params.get("modifier"))
    _require_object_mode(obj)
    with _override(obj):
        # The 5.2 operator is object.multires_base_apply (words in that order).
        result = bpy.ops.object.multires_base_apply(modifier=mod.name)
    if "FINISHED" not in result:
        raise RuntimeError(f"multires_base_apply returned {set(result)}")
    return _multires_state(obj, mod)


@command("modifiers.add_remesh", mutates=True)
def add_remesh(params: dict) -> dict:
    """Rebuild topology as a Remesh modifier (non-destructive until applied)."""
    obj, mod = _convenience(
        params, "REMESH",
        mode=(params.get("mode") or "VOXEL").upper(),
        voxel_size=params.get("voxel_size"),
        octree_depth=params.get("octree_depth"),
        adaptivity=params.get("adaptivity"),
        use_smooth_shade=params.get("use_smooth_shade"),
    )
    return _added(obj, mod, mode=mod.mode, voxel_size=mod.voxel_size,
                  octree_depth=mod.octree_depth)


def _route_data_types(mod, tokens) -> dict:
    """Spread a flat data-type list across DataTransfer's four domain enums."""
    if isinstance(tokens, str):
        tokens = [tokens]
    domain_of = {}
    for prop, _flag in _DT_DOMAINS:
        for item in mod.bl_rna.properties[prop].enum_items:
            domain_of[item.identifier] = prop

    unknown = sorted({t for t in tokens if t not in domain_of})
    if unknown:
        raise ValueError(
            f"unknown data_types {unknown}; valid tokens are {sorted(domain_of)}"
        )

    buckets = {prop: set() for prop, _ in _DT_DOMAINS}
    for token in tokens:
        buckets[domain_of[token]].add(token)
    for prop, flag in _DT_DOMAINS:
        setattr(mod, prop, buckets[prop])
        setattr(mod, flag, bool(buckets[prop]))
    return {prop: sorted(values) for prop, values in buckets.items() if values}


@command("modifiers.add_data_transfer", mutates=True)
def add_data_transfer(params: dict) -> dict:
    """Copy vertex groups, colours, UVs, normals or edge flags from another mesh."""
    source = params["source"]
    src_obj = _object(source)
    if src_obj.type != "MESH":
        raise ValueError(f"data transfer source {source!r} is a {src_obj.type}; it must be a MESH")

    max_distance = params.get("max_distance")
    obj, mod = _convenience(
        params, "DATA_TRANSFER",
        object=source,
        vert_mapping=_upper(params.get("vert_mapping")),
        edge_mapping=_upper(params.get("edge_mapping")),
        loop_mapping=_upper(params.get("loop_mapping")),
        poly_mapping=_upper(params.get("poly_mapping")),
        mix_mode=_upper(params.get("mix_mode")),
        mix_factor=params.get("mix_factor"),
        max_distance=max_distance,
        # max_distance is inert unless its own gate is on, so turn it on with it.
        use_max_distance=True if max_distance is not None else None,
        ray_radius=params.get("ray_radius"),
        use_object_transform=params.get("use_object_transform"),
        vertex_group=params.get("vertex_group"),
    )
    try:
        routed = _route_data_types(mod, params.get("data_types") or [])
    except Exception:
        obj.modifiers.remove(mod)
        raise

    return _added(obj, mod, source=source, data_types=routed,
                  vert_mapping=mod.vert_mapping, mix_mode=mod.mix_mode)

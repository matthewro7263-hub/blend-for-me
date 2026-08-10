"""Vertex-group / weight-painting commands.

Almost everything here writes ``vertex_groups`` data directly rather than
simulating brush strokes: a weight is a number, and a number is better set than
painted. The few genuinely interactive things (screen-space gradient, brush
stroke, heatmap screenshot) are marked ``needs_gui``.
"""

from __future__ import annotations

import contextlib
import os
import tempfile

import bpy

from .. import ctx
from ..registry import command

#: ``group_select_mode`` is a *dynamic* enum: ``BONE_DEFORM`` only appears once
#: the object has an armature, ``BONE_SELECT`` only with an armature in pose
#: mode. Blender rejects a bad value with a message listing what is valid, so we
#: pass the caller's choice straight through.
_DEFAULT_GROUP_SELECT = "ACTIVE"

#: Right/left token pairs used when mirroring group names on the data-API path.
#: Blender's own X-axis operator has a richer flipper; this covers the naming
#: conventions people actually use.
_SIDE_TOKENS = (
    (".L", ".R"), (".l", ".r"), ("_L", "_R"), ("_l", "_r"),
    ("-L", "-R"), ("-l", "-r"),
    ("Left", "Right"), ("left", "right"), ("LEFT", "RIGHT"),
)


# ---------------------------------------------------------------------------
# resolution helpers
# ---------------------------------------------------------------------------

def _mesh_object(name: str):
    obj = bpy.data.objects.get(name)
    if obj is None:
        raise KeyError(f"no object named {name!r}; call get_scene_info for real names")
    if obj.type != "MESH":
        raise TypeError(f"{name!r} is a {obj.type}, not a MESH — vertex groups live on meshes")
    if obj.name not in bpy.context.view_layer.objects:
        raise RuntimeError(
            f"{name!r} is not in the active view layer, so no operator can touch it. "
            "Link it into the current scene collection first."
        )
    return obj


def _armature_object(name: str):
    obj = bpy.data.objects.get(name)
    if obj is None:
        raise KeyError(f"no object named {name!r}; call get_scene_info for real names")
    if obj.type != "ARMATURE":
        raise TypeError(f"{name!r} is a {obj.type}, not an ARMATURE")
    return obj


def _group(obj, name: str):
    group = obj.vertex_groups.get(name)
    if group is None:
        raise KeyError(
            f"{obj.name!r} has no vertex group {name!r}; existing groups: "
            f"{[g.name for g in obj.vertex_groups]}"
        )
    return group


def _rig_of(obj, explicit: str | None):
    """The armature driving ``obj``: the named one, or the armature modifier's."""
    if explicit:
        return _armature_object(explicit)
    for mod in obj.modifiers:
        if mod.type == "ARMATURE" and mod.object is not None:
            return mod.object
    raise ValueError(
        f"{obj.name!r} has no armature modifier, so the deform bones are unknown. "
        "Pass 'armature' explicitly, or bind the mesh first with weights.auto_weights."
    )


def _deform_bone_names(rig) -> list:
    return [b.name for b in rig.data.bones if b.use_deform]


@contextlib.contextmanager
def _active_in_mode(obj, mode: str):
    """Make ``obj`` the active object in ``mode``; restore mode and active after.

    Every ``object.vertex_group_*`` operator polls on the *active* object, and
    several of them additionally poll on the mode (see the per-command notes), so
    this is the only safe way to drive them.
    """
    view_layer = bpy.context.view_layer
    previous_active = view_layer.objects.active
    previous_mode = obj.mode

    if previous_active is not None and previous_active is not obj and previous_active.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    view_layer.objects.active = obj
    obj.select_set(True)
    if obj.mode != mode:
        bpy.ops.object.mode_set(mode=mode)
    try:
        yield
    finally:
        if obj.mode != previous_mode:
            with contextlib.suppress(RuntimeError):
                bpy.ops.object.mode_set(mode=previous_mode)
        if previous_active is not None and previous_active.name in view_layer.objects:
            view_layer.objects.active = previous_active


def _resolve_verts(obj, spec, default_space: str = "LOCAL"):
    """Turn a ``verts_spec`` into ``(indices, description)``.

    Accepts an explicit index list, ``"ALL"``, ``"SELECTED"``, or a bounding box
    ``{"min": [x,y,z], "max": [x,y,z], "space": "LOCAL"|"WORLD"}``.
    """
    mesh = obj.data
    count = len(mesh.vertices)

    if spec is None or (isinstance(spec, str) and spec.upper() == "ALL"):
        return list(range(count)), "ALL"

    if isinstance(spec, str):
        key = spec.upper()
        if key == "SELECTED":
            indices = [v.index for v in mesh.vertices if v.select]
            if not indices:
                raise ValueError(
                    f"verts_spec='SELECTED' but nothing is selected on {obj.name!r}. "
                    "Select vertices first (weights.select_verts_by_weight, or an "
                    "explicit index list)."
                )
            return indices, "SELECTED"
        raise ValueError(
            f"verts_spec {spec!r} not understood; use 'ALL', 'SELECTED', a list of "
            "vertex indices, or {'min': [...], 'max': [...]}"
        )

    if isinstance(spec, dict):
        lo = spec.get("min")
        hi = spec.get("max")
        if lo is None or hi is None:
            raise ValueError("bounding-box verts_spec needs both 'min' and 'max' [x, y, z]")
        space = str(spec.get("space", default_space)).upper()
        if space not in {"LOCAL", "WORLD"}:
            raise ValueError(f"bounding-box space must be LOCAL or WORLD, got {space!r}")
        low = [min(float(lo[i]), float(hi[i])) for i in range(3)]
        high = [max(float(lo[i]), float(hi[i])) for i in range(3)]
        lo, hi = low, high
        matrix = obj.matrix_world
        indices = []
        for vert in mesh.vertices:
            co = matrix @ vert.co if space == "WORLD" else vert.co
            if lo[0] <= co[0] <= hi[0] and lo[1] <= co[1] <= hi[1] and lo[2] <= co[2] <= hi[2]:
                indices.append(vert.index)
        return indices, f"BOX({space})"

    if isinstance(spec, (list, tuple)):
        indices = [int(i) for i in spec]
        bad = [i for i in indices if i < 0 or i >= count]
        if bad:
            raise IndexError(
                f"vertex indices {bad[:10]} are out of range for {obj.name!r} "
                f"(0..{count - 1})"
            )
        return indices, "INDICES"

    raise TypeError(f"verts_spec must be a str, list or dict, got {type(spec).__name__}")


def _weights_of(obj, group_indices: set):
    """Yield ``(vertex, {group_index: weight})`` restricted to ``group_indices``."""
    for vert in obj.data.vertices:
        entry = {e.group: e.weight for e in vert.groups if e.group in group_indices}
        yield vert, entry


# ---------------------------------------------------------------------------
# mode / setup
# ---------------------------------------------------------------------------

@command("weights.enter_weight_paint")
def enter_weight_paint(params: dict) -> dict:
    """Put a mesh into Weight Paint, optionally with its armature posed for bone picking."""
    obj = _mesh_object(params["mesh"])
    rig_name = params.get("armature_for_posing")
    view_layer = bpy.context.view_layer

    if view_layer.objects.active is not None and view_layer.objects.active.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    bpy.ops.object.select_all(action="DESELECT")

    rig = None
    if rig_name:
        rig = _armature_object(rig_name)
        # The armature has to be in Pose mode *before* the mesh takes over as the
        # active object, otherwise ctrl-clicking a bone cannot select its group.
        view_layer.objects.active = rig
        rig.select_set(True)
        bpy.ops.object.mode_set(mode="POSE")

    obj.select_set(True)
    view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode="WEIGHT_PAINT")

    return {
        "mesh": obj.name,
        "mode": bpy.context.mode,
        "armature": rig.name if rig else None,
        "armature_mode": rig.mode if rig else None,
        "vertex_groups": len(obj.vertex_groups),
        "active_group": obj.vertex_groups.active.name if obj.vertex_groups.active else None,
    }


# ---------------------------------------------------------------------------
# group bookkeeping
# ---------------------------------------------------------------------------

@command("weights.vgroup_list")
def vgroup_list(params: dict) -> dict:
    """Every vertex group with its index, lock state and assigned-vertex count."""
    obj = _mesh_object(params["mesh"])
    counts = {g.index: 0 for g in obj.vertex_groups}
    totals = {g.index: 0.0 for g in obj.vertex_groups}
    for vert in obj.data.vertices:
        for elem in vert.groups:
            if elem.group in counts:
                counts[elem.group] += 1
                totals[elem.group] += elem.weight

    deform = set()
    with contextlib.suppress(Exception):
        deform = set(_deform_bone_names(_rig_of(obj, params.get("armature"))))

    active = obj.vertex_groups.active
    return {
        "mesh": obj.name,
        "count": len(obj.vertex_groups),
        "active_group": active.name if active else None,
        "vertex_count": len(obj.data.vertices),
        "groups": [
            {
                "name": g.name,
                "index": g.index,
                "locked": g.lock_weight,
                "vertices": counts[g.index],
                "total_weight": round(totals[g.index], 6),
                "is_deform_bone": g.name in deform if deform else None,
            }
            for g in obj.vertex_groups
        ],
    }


@command("weights.vgroup_create", mutates=True)
def vgroup_create(params: dict) -> dict:
    """Create an empty vertex group (or return the existing one of that name)."""
    obj = _mesh_object(params["mesh"])
    name = params["name"]
    existing = obj.vertex_groups.get(name)
    if existing is not None:
        obj.vertex_groups.active_index = existing.index
        return {"mesh": obj.name, "name": existing.name, "index": existing.index,
                "created": False}
    group = obj.vertex_groups.new(name=name)
    obj.vertex_groups.active_index = group.index
    # Blender uniquifies duplicates, so report the name it actually got.
    return {"mesh": obj.name, "name": group.name, "index": group.index, "created": True}


@command("weights.vgroup_delete", mutates=True)
def vgroup_delete(params: dict) -> dict:
    """Delete one vertex group, or every group on the mesh."""
    obj = _mesh_object(params["mesh"])
    if params.get("all"):
        names = [g.name for g in obj.vertex_groups]
        obj.vertex_groups.clear()
        return {"mesh": obj.name, "deleted": names, "remaining": 0}
    group = _group(obj, params["name"])
    name = group.name
    obj.vertex_groups.remove(group)
    return {"mesh": obj.name, "deleted": [name],
            "remaining": len(obj.vertex_groups)}


@command("weights.vgroup_rename", mutates=True)
def vgroup_rename(params: dict) -> dict:
    """Rename a vertex group. Renaming to a deform bone's name re-binds it to that bone."""
    obj = _mesh_object(params["mesh"])
    group = _group(obj, params["name"])
    group.name = params["new_name"]
    return {"mesh": obj.name, "old_name": params["name"], "name": group.name,
            "index": group.index}


@command("weights.vgroup_lock", mutates=True)
def vgroup_lock(params: dict) -> dict:
    """Lock or unlock groups so normalize/auto-normalize cannot rewrite them."""
    obj = _mesh_object(params["mesh"])
    locked = bool(params.get("locked", True))
    names = params.get("name")
    if names is None:
        targets = list(obj.vertex_groups)
    else:
        if isinstance(names, str):
            names = [names]
        targets = [_group(obj, n) for n in names]
    for group in targets:
        group.lock_weight = locked
    return {"mesh": obj.name, "locked": locked,
            "groups": [g.name for g in targets],
            "all_locked": all(g.lock_weight for g in obj.vertex_groups)}


# ---------------------------------------------------------------------------
# reading / writing weights (pure data API)
# ---------------------------------------------------------------------------

@command("weights.assign_weights", mutates=True)
def assign_weights(params: dict) -> dict:
    """Write one weight to a set of vertices (REPLACE / ADD / SUBTRACT)."""
    obj = _mesh_object(params["mesh"])
    group = _group(obj, params["group"])
    weight = float(params.get("weight", 1.0))
    mode = str(params.get("mode", "REPLACE")).upper()
    if mode not in {"REPLACE", "ADD", "SUBTRACT"}:
        raise ValueError(f"mode must be REPLACE, ADD or SUBTRACT, got {mode!r}")

    indices, described = _resolve_verts(obj, params.get("verts_spec", "ALL"))
    if indices:
        group.add(indices, weight, mode)
    obj.data.update()

    return {"mesh": obj.name, "group": group.name, "mode": mode, "weight": weight,
            "verts_spec": described, "vertices_written": len(indices)}


@command("weights.set_weights", mutates=True)
def set_weights(params: dict) -> dict:
    """Bulk-write an explicit ``{vertex_index: weight}`` map into one group."""
    import math

    obj = _mesh_object(params["mesh"])
    group = _group(obj, params["group"])
    raw = params["weights"]
    if not isinstance(raw, dict):
        raise TypeError("'weights' must be a mapping of vertex index -> weight")

    count = len(obj.data.vertices)
    remove_zero = bool(params.get("remove_zero", False))

    # Pre-parse and validate all entries before touching live vertex group
    entries_to_apply = []
    for key, value in raw.items():
        try:
            index = int(key)
        except (ValueError, TypeError) as exc:
            raise ValueError(f"vertex index must be an integer, got {key!r}") from exc

        if index < 0 or index >= count:
            raise IndexError(f"vertex index {index} out of range for {obj.name!r} (0..{count - 1})")

        try:
            weight = float(value)
        except (ValueError, TypeError) as exc:
            raise ValueError(f"weight must be a float, got {value!r}") from exc

        if not math.isfinite(weight):
            raise ValueError(f"weight must be finite, got {weight!r}")

        entries_to_apply.append((index, weight))

    written = 0
    removed = 0
    for index, weight in entries_to_apply:
        if remove_zero and weight <= 0.0:
            group.remove([index])
            removed += 1
        else:
            group.add([index], weight, "REPLACE")
            written += 1
    obj.data.update()

    return {"mesh": obj.name, "group": group.name,
            "vertices_written": written, "vertices_removed": removed}


@command("weights.get_weights")
def get_weights(params: dict) -> dict:
    """Read a page of weights from one group, walking vertices from ``offset``."""
    obj = _mesh_object(params["mesh"])
    group = _group(obj, params["group"])
    offset = max(0, int(params.get("offset", 0)))
    limit = max(1, int(params.get("limit", 1000)))
    include_zero = bool(params.get("include_zero", False))

    verts = obj.data.vertices
    total = len(verts)
    gi = group.index

    entries = []
    scanned = offset
    while scanned < total and len(entries) < limit:
        vert = verts[scanned]
        weight = next((e.weight for e in vert.groups if e.group == gi), None)
        if weight is not None:
            entries.append({"index": scanned, "weight": round(weight, 6)})
        elif include_zero:
            entries.append({"index": scanned, "weight": 0.0, "assigned": False})
        scanned += 1

    truncated = scanned < total
    return {
        "mesh": obj.name,
        "group": group.name,
        "group_index": gi,
        "total_vertices": total,
        "offset": offset,
        "next_offset": scanned if truncated else None,
        "returned": len(entries),
        "truncated": truncated,
        "weights": entries,
    }


@command("weights.select_verts_by_weight", mutates=True)
def select_verts_by_weight(params: dict) -> dict:
    """Select the vertices whose weight in a group falls inside [min, max]."""
    obj = _mesh_object(params["mesh"])
    group = _group(obj, params["group"])
    lo = float(params.get("min", 0.0))
    hi = float(params.get("max", 1.0))
    include_unassigned = bool(params.get("include_unassigned", False))
    extend = bool(params.get("extend", False))
    limit = max(1, int(params.get("limit", 1000)))

    gi = group.index
    matched = []
    # ``mesh.vertices[].select`` is only writable outside Edit mode — in Edit mode
    # the live BMesh owns selection and these flags are a stale copy. Re-entering
    # the caller's mode afterwards flushes what we wrote here.
    with _active_in_mode(obj, "OBJECT"):
        for vert in obj.data.vertices:
            weight = next((e.weight for e in vert.groups if e.group == gi), None)
            if weight is None:
                hit = include_unassigned and lo <= 0.0 <= hi
                weight = 0.0
            else:
                hit = lo <= weight <= hi
            if hit:
                vert.select = True
                matched.append({"index": vert.index, "weight": round(weight, 6)})
            elif not extend:
                vert.select = False
        obj.data.update()

    return {
        "mesh": obj.name, "group": group.name, "min": lo, "max": hi,
        "selected": len(matched),
        "vertices": matched[:limit],
        "truncated": len(matched) > limit,
        "note": "Selection is stored on the mesh; switch to EDIT mode to see or act on it.",
    }


# ---------------------------------------------------------------------------
# binding
# ---------------------------------------------------------------------------

@command("weights.auto_weights", mutates=True)
def auto_weights(params: dict) -> dict:
    """Bind a mesh to an armature and generate weights (parent_set or weight_from_bones)."""
    obj = _mesh_object(params["mesh"])
    rig = _armature_object(params["armature"])
    method = str(params.get("method", "AUTOMATIC")).upper()
    reuse_binding = bool(params.get("reuse_binding", False))
    view_layer = bpy.context.view_layer

    if view_layer.objects.active is not None and view_layer.objects.active.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")

    if reuse_binding:
        # paint.weight_from_bones re-runs the solver on an already-bound mesh
        # without re-parenting. It polls in Weight Paint mode only (NOT pose mode,
        # despite where the menu entry lives) and reads the armature modifier.
        wfb = {"AUTOMATIC": "AUTOMATIC", "ENVELOPE": "ENVELOPES", "ENVELOPES": "ENVELOPES"}
        if method not in wfb:
            raise ValueError(
                f"reuse_binding supports AUTOMATIC or ENVELOPE, got {method!r}. "
                "Use reuse_binding=false with method='EMPTY' to create empty groups."
            )
        if not any(m.type == "ARMATURE" and m.object is rig for m in obj.modifiers):
            raise ValueError(
                f"{obj.name!r} has no armature modifier pointing at {rig.name!r}, so there "
                "is nothing to re-solve. Call this with reuse_binding=false first."
            )
        bpy.ops.object.select_all(action="DESELECT")
        rig.select_set(True)
        with _active_in_mode(obj, "WEIGHT_PAINT"):
            bpy.ops.paint.weight_from_bones(type=wfb[method])
        used = f"paint.weight_from_bones(type={wfb[method]!r})"
    else:
        parent_type = {
            "AUTOMATIC": "ARMATURE_AUTO",
            "ENVELOPE": "ARMATURE_ENVELOPE",
            "ENVELOPES": "ARMATURE_ENVELOPE",
            "EMPTY": "ARMATURE_NAME",
            "NAME": "ARMATURE_NAME",
        }.get(method)
        if parent_type is None:
            raise ValueError(f"method must be AUTOMATIC, ENVELOPE or EMPTY, got {method!r}")
        bpy.ops.object.select_all(action="DESELECT")
        obj.select_set(True)
        rig.select_set(True)
        # parent_set reads the armature from the *active* object and parents
        # everything else that is selected to it.
        view_layer.objects.active = rig
        bpy.ops.object.parent_set(
            type=parent_type,
            xmirror=bool(params.get("xmirror", False)),
            keep_transform=bool(params.get("keep_transform", False)),
        )
        used = f"object.parent_set(type={parent_type!r})"

    deform = set(_deform_bone_names(rig))
    groups = [g.name for g in obj.vertex_groups]
    return {
        "mesh": obj.name,
        "armature": rig.name,
        "method": method,
        "operator": used,
        "vertex_groups": groups,
        "deform_bones": len(deform),
        "bones_without_group": sorted(deform - set(groups)),
        "modifiers": [m.name for m in obj.modifiers if m.type == "ARMATURE"],
    }


@command("weights.transfer_weights", mutates=True)
def transfer_weights(params: dict) -> dict:
    """Copy vertex-group weights from one mesh to another via ``object.data_transfer``."""
    source = _mesh_object(params["source"])
    target = _mesh_object(params["target"])
    if source is target:
        raise ValueError("source and target must be different objects")

    method = str(params.get("method", "POLYINTERP_NEAREST")).upper()
    valid = [i.identifier for i in
             bpy.ops.object.data_transfer.get_rna_type().properties["vert_mapping"].enum_items]
    if method not in valid:
        raise ValueError(f"method {method!r} is not a vert_mapping; valid: {valid}")

    name_matching = bool(params.get("name_matching", True))
    layers_src = str(params.get("layers_select_src", "ALL")).upper()
    mix_mode = str(params.get("mix_mode", "REPLACE")).upper()
    mix_factor = float(params.get("mix_factor", 1.0))
    max_distance = params.get("max_distance")

    before = {g.name for g in target.vertex_groups}

    if bpy.context.view_layer.objects.active is not None and \
            bpy.context.view_layer.objects.active.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    bpy.ops.object.select_all(action="DESELECT")
    target.select_set(True)
    source.select_set(True)
    # data_transfer pushes from the active object onto every other selected one.
    bpy.context.view_layer.objects.active = source

    kwargs = dict(
        data_type="VGROUP_WEIGHTS",
        use_create=bool(params.get("use_create", True)),
        vert_mapping=method,
        layers_select_src=layers_src,
        layers_select_dst="NAME" if name_matching else "INDEX",
        mix_mode=mix_mode,
        mix_factor=mix_factor,
        use_object_transform=bool(params.get("use_object_transform", True)),
    )
    if max_distance is not None:
        kwargs["use_max_distance"] = True
        kwargs["max_distance"] = float(max_distance)
    bpy.ops.object.data_transfer(**kwargs)

    after = [g.name for g in target.vertex_groups]
    return {
        "source": source.name,
        "target": target.name,
        "method": method,
        "name_matching": name_matching,
        "groups_created": sorted(set(after) - before),
        "target_groups": after,
        "note": "object.vertex_group_transfer_weight was removed in Blender 5.x; "
                "this uses object.data_transfer(data_type='VGROUP_WEIGHTS').",
    }


# ---------------------------------------------------------------------------
# whole-group maths (bpy.ops — no data-API equivalent)
# ---------------------------------------------------------------------------

def _run_vgroup_op(obj, fn, *, only_selected: bool, needs_paint_mode: bool = False,
                   group: str | None = None):
    """Activate ``group``, enter a mode the operator polls in, and run ``fn``.

    ``only_selected=True`` uses Edit mode, where these operators are restricted to
    the current vertex selection. Otherwise they run in Object mode (or Weight
    Paint for the ones whose poll rejects Object mode) and hit every vertex.
    """
    if group is not None:
        obj.vertex_groups.active_index = _group(obj, group).index
    if obj.vertex_groups.active is None:
        raise ValueError(
            f"{obj.name!r} has no active vertex group. Pass 'group', or create one "
            "with weights.vgroup_create."
        )
    mode = "EDIT" if only_selected else ("WEIGHT_PAINT" if needs_paint_mode else "OBJECT")
    with _active_in_mode(obj, mode):
        result = fn()
    obj.data.update()
    return mode, result


def _group_math_command(name, doc, needs_paint_mode=False):
    """Build one of the ``object.vertex_group_*`` wrappers; they share a shape."""

    def build(fn):
        def handler(params: dict) -> dict:
            obj = _mesh_object(params["mesh"])
            only_selected = bool(params.get("only_selected", False))
            mode, _ = _run_vgroup_op(
                obj, lambda: fn(params), only_selected=only_selected,
                needs_paint_mode=needs_paint_mode, group=params.get("group"),
            )
            active = obj.vertex_groups.active
            return {"mesh": obj.name, "group": active.name if active else None,
                    "mode_used": mode, "only_selected": only_selected}

        handler.__name__ = name.split(".")[-1]
        handler.__doc__ = doc
        return command(name, mutates=True)(handler)

    return build


@_group_math_command("weights.normalize",
                     "Scale the active group so its highest weight becomes 1.0.")
def _normalize(params):
    return bpy.ops.object.vertex_group_normalize()


@_group_math_command("weights.normalize_all",
                     "Make every vertex's deform weights sum to 1.0 across all groups.")
def _normalize_all(params):
    return bpy.ops.object.vertex_group_normalize_all(
        group_select_mode=str(params.get("group_select_mode", "ALL")).upper(),
        lock_active=bool(params.get("lock_active", True)),
    )


@_group_math_command("weights.levels",
                     "Apply weight = (weight + offset) * gain, clamped to 0..1.")
def _levels(params):
    return bpy.ops.object.vertex_group_levels(
        group_select_mode=str(params.get("group_select_mode", _DEFAULT_GROUP_SELECT)).upper(),
        offset=float(params.get("offset", 0.0)),
        gain=float(params.get("gain", 1.0)),
    )


@_group_math_command("weights.invert", "Replace every weight with 1 - weight.")
def _invert(params):
    return bpy.ops.object.vertex_group_invert(
        group_select_mode=str(params.get("group_select_mode", _DEFAULT_GROUP_SELECT)).upper(),
        auto_assign=bool(params.get("auto_assign", True)),
        auto_remove=bool(params.get("auto_remove", True)),
    )


@_group_math_command("weights.clean", "Drop weights below a threshold from the group.")
def _clean(params):
    return bpy.ops.object.vertex_group_clean(
        group_select_mode=str(params.get("group_select_mode", _DEFAULT_GROUP_SELECT)).upper(),
        limit=float(params.get("threshold", 0.01)),
        keep_single=bool(params.get("keep_single", False)),
    )


@_group_math_command("weights.quantize", "Round weights onto N evenly spaced steps.")
def _quantize(params):
    return bpy.ops.object.vertex_group_quantize(
        group_select_mode=str(params.get("group_select_mode", _DEFAULT_GROUP_SELECT)).upper(),
        steps=int(params.get("steps", 4)),
    )


@_group_math_command("weights.limit_total",
                     "Keep only the N strongest deform influences per vertex.")
def _limit_total(params):
    return bpy.ops.object.vertex_group_limit_total(
        group_select_mode=str(params.get("group_select_mode", "ALL")).upper(),
        limit=int(params.get("max_influences", 4)),
    )


@_group_math_command("weights.smooth_weights",
                     "Blend each weight toward its connected neighbours.",
                     needs_paint_mode=True)
def _smooth(params):
    return bpy.ops.object.vertex_group_smooth(
        group_select_mode=str(params.get("group_select_mode", _DEFAULT_GROUP_SELECT)).upper(),
        factor=float(params.get("factor", 0.5)),
        repeat=int(params.get("iterations", 1)),
        expand=float(params.get("expand", 0.0)),
    )


# ---------------------------------------------------------------------------
# mirroring
# ---------------------------------------------------------------------------

def _flip_group_name(name: str) -> str:
    for left, right in _SIDE_TOKENS:
        if name.endswith(left):
            return name[: -len(left)] + right
        if name.endswith(right):
            return name[: -len(right)] + left
        if name.startswith(left + "_") or name.startswith(left + "."):
            return right + name[len(left):]
        if name.startswith(right + "_") or name.startswith(right + "."):
            return left + name[len(right):]
    return name


def _mirror_via_kdtree(obj, axis: str, tolerance: float, flip_names: bool,
                       all_groups: bool) -> dict:
    """Swap weights between mirrored vertex pairs across a local axis.

    Blender's own ``object.vertex_group_mirror`` only knows the X axis, so Y and Z
    are done here: build a KD-tree of the object-space coordinates, pair each
    vertex with the nearest vertex to its reflected position, then exchange the
    two weights (matching the operator's swap semantics).
    """
    from mathutils import kdtree

    mesh = obj.data
    verts = mesh.vertices
    axis_index = {"X": 0, "Y": 1, "Z": 2}[axis]

    tree = kdtree.KDTree(len(verts))
    for vert in verts:
        tree.insert(vert.co, vert.index)
    tree.balance()

    partner = {}
    unmatched = 0
    for vert in verts:
        reflected = vert.co.copy()
        reflected[axis_index] = -reflected[axis_index]
        _co, index, distance = tree.find(reflected)
        if distance is not None and distance <= tolerance:
            partner[vert.index] = index
        else:
            unmatched += 1

    groups = list(obj.vertex_groups) if all_groups else (
        [obj.vertex_groups.active] if obj.vertex_groups.active else []
    )
    if not groups:
        raise ValueError(f"{obj.name!r} has no vertex group to mirror")

    by_index = {g.index: g for g in obj.vertex_groups}
    # Snapshot first: writing as we go would read weights we have already swapped.
    snapshot = {g.index: {} for g in obj.vertex_groups}
    for vert in verts:
        for elem in vert.groups:
            if elem.group in snapshot:
                snapshot[elem.group][vert.index] = elem.weight

    written = 0
    cleared = 0
    for group in groups:
        source = snapshot[group.index]
        target = obj.vertex_groups.get(_flip_group_name(group.name)) if flip_names else group
        if target is None:
            target = group
        target_group = by_index[target.index]
        target_snapshot = snapshot[target.index]
        drop = []
        for vert_index, mirror_index in partner.items():
            value = source.get(mirror_index)
            if value is None:
                # The mirror vertex is unassigned, so the pair should be too.
                if vert_index in target_snapshot:
                    drop.append(vert_index)
            else:
                target_group.add([vert_index], value, "REPLACE")
                written += 1
        if drop:
            target_group.remove(drop)
            cleared += len(drop)

    mesh.update()
    return {
        "method": "kdtree",
        "axis": axis,
        "groups": [g.name for g in groups],
        "paired_vertices": len(partner),
        "unpaired_vertices": unmatched,
        "weights_written": written,
        "weights_cleared": cleared,
    }


@command("weights.mirror_weights", mutates=True)
def mirror_weights(params: dict) -> dict:
    """Mirror weights across a local axis, flipping .L/.R group names as it goes."""
    obj = _mesh_object(params["mesh"])
    axis = str(params.get("axis", "X")).upper()
    if axis not in {"X", "Y", "Z"}:
        raise ValueError(f"axis must be X, Y or Z, got {axis!r}")
    all_groups = bool(params.get("all_groups", False))
    flip_names = bool(params.get("flip_group_names", True))
    if params.get("group"):
        obj.vertex_groups.active_index = _group(obj, params["group"]).index

    if axis == "X":
        with _active_in_mode(obj, "OBJECT"):
            bpy.ops.object.vertex_group_mirror(
                mirror_weights=True,
                flip_group_names=flip_names,
                all_groups=all_groups,
                use_topology=bool(params.get("use_topology", False)),
            )
        obj.data.update()
        active = obj.vertex_groups.active
        return {"mesh": obj.name, "method": "object.vertex_group_mirror", "axis": "X",
                "groups": [g.name for g in obj.vertex_groups] if all_groups
                else ([active.name] if active else []),
                "use_topology": bool(params.get("use_topology", False))}

    result = _mirror_via_kdtree(
        obj, axis,
        tolerance=float(params.get("tolerance", 1e-4)),
        flip_names=flip_names,
        all_groups=all_groups,
    )
    result["mesh"] = obj.name
    result["note"] = (
        "Blender 5.2's object.vertex_group_mirror only mirrors along X, so Y/Z are "
        "done with a KD-tree pairing of object-space coordinates. use_topology is "
        "ignored on this path."
    )
    return result


# ---------------------------------------------------------------------------
# diagnostics
# ---------------------------------------------------------------------------

@command("weights.report_unweighted_verts")
def report_unweighted_verts(params: dict) -> dict:
    """List vertices whose total deform weight is zero — they will not follow the rig."""
    obj = _mesh_object(params["mesh"])
    rig = _rig_of(obj, params.get("armature"))
    limit = max(1, int(params.get("limit", 1000)))
    threshold = float(params.get("threshold", 0.0))

    deform_names = set(_deform_bone_names(rig))
    deform_indices = {g.index for g in obj.vertex_groups if g.name in deform_names}
    if not deform_indices:
        raise ValueError(
            f"{obj.name!r} has no vertex group matching a deform bone of {rig.name!r}. "
            "Bind it first with weights.auto_weights."
        )

    offenders = []
    for vert, entry in _weights_of(obj, deform_indices):
        total = sum(entry.values())
        if total <= threshold:
            offenders.append({"index": vert.index,
                              "total_weight": round(total, 6),
                              "co": list(vert.co)})

    return {
        "mesh": obj.name,
        "armature": rig.name,
        "vertex_count": len(obj.data.vertices),
        "deform_groups": len(deform_indices),
        "unweighted_count": len(offenders),
        "vertices": offenders[:limit],
        "truncated": len(offenders) > limit,
    }


@command("weights.report_over_influenced")
def report_over_influenced(params: dict) -> dict:
    """List vertices bound to more deform bones than a game engine will accept."""
    obj = _mesh_object(params["mesh"])
    max_influences = int(params.get("max_influences", 4))
    limit = max(1, int(params.get("limit", 1000)))
    threshold = float(params.get("threshold", 0.0))

    rig = None
    with contextlib.suppress(Exception):
        rig = _rig_of(obj, params.get("armature"))
    if rig is not None:
        deform_names = set(_deform_bone_names(rig))
        indices = {g.index: g.name for g in obj.vertex_groups if g.name in deform_names}
    else:
        indices = {g.index: g.name for g in obj.vertex_groups}

    offenders = []
    histogram = {}
    for vert, entry in _weights_of(obj, set(indices)):
        active = {indices[gi]: w for gi, w in entry.items() if w > threshold}
        histogram[len(active)] = histogram.get(len(active), 0) + 1
        if len(active) > max_influences:
            ordered = sorted(active.items(), key=lambda kv: -kv[1])
            offenders.append({
                "index": vert.index,
                "influences": len(active),
                "groups": [{"name": n, "weight": round(w, 6)} for n, w in ordered],
            })

    return {
        "mesh": obj.name,
        "armature": rig.name if rig else None,
        "max_influences": max_influences,
        "vertex_count": len(obj.data.vertices),
        "over_influenced_count": len(offenders),
        "influence_histogram": {str(k): v for k, v in sorted(histogram.items())},
        "vertices": offenders[:limit],
        "truncated": len(offenders) > limit,
        "fix": "weights.limit_total(max_influences=N) then weights.normalize_all()",
    }


@command("weights.per_bone_weight_summary")
def per_bone_weight_summary(params: dict) -> dict:
    """Per deform bone: how many vertices it holds and how strongly."""
    obj = _mesh_object(params["mesh"])
    rig = _rig_of(obj, params.get("armature"))
    limit = max(1, int(params.get("limit", 1000)))

    deform_names = _deform_bone_names(rig)
    by_name = {g.name: g for g in obj.vertex_groups}
    tracked = {by_name[n].index: n for n in deform_names if n in by_name}

    stats = {name: {"vertices": 0, "total": 0.0, "max": 0.0} for name in tracked.values()}
    for _vert, entry in _weights_of(obj, set(tracked)):
        for group_index, weight in entry.items():
            if weight <= 0.0:
                continue
            row = stats[tracked[group_index]]
            row["vertices"] += 1
            row["total"] += weight
            row["max"] = max(row["max"], weight)

    bones = []
    for name in deform_names:
        row = stats.get(name)
        if row is None:
            bones.append({"bone": name, "has_group": False, "vertices": 0,
                          "total": 0.0, "max": 0.0, "mean": 0.0})
            continue
        count = row["vertices"]
        bones.append({
            "bone": name, "has_group": True, "vertices": count,
            "total": round(row["total"], 6), "max": round(row["max"], 6),
            "mean": round(row["total"] / count, 6) if count else 0.0,
        })

    return {
        "mesh": obj.name,
        "armature": rig.name,
        "deform_bone_count": len(deform_names),
        "bones": bones[:limit],
        "truncated": len(bones) > limit,
        "bones_with_no_group": [b["bone"] for b in bones if not b["has_group"]],
        "empty_bones": [b["bone"] for b in bones if b["has_group"] and b["vertices"] == 0],
        "groups_not_deform_bones": sorted(
            {g.name for g in obj.vertex_groups} - set(deform_names)
        ),
    }


# ---------------------------------------------------------------------------
# viewport-only: gradient, stroke, heatmap
# ---------------------------------------------------------------------------

def _project(region, rv3d, obj, point, space: str):
    from bpy_extras import view3d_utils
    from mathutils import Vector

    world = Vector(point) if space == "WORLD" else obj.matrix_world @ Vector(point)
    screen = view3d_utils.location_3d_to_region_2d(region, rv3d, world)
    if screen is None:
        raise ValueError(
            f"point {list(point)} projects behind the viewport camera, so it has no "
            "screen coordinate. Orbit the view so both endpoints are visible."
        )
    return world, screen


def _set_paint_weight(value: float) -> float:
    """Write the paint weight wherever this build considers authoritative."""
    paint = bpy.context.scene.tool_settings.weight_paint
    unified = paint.unified_paint_settings
    if unified.use_unified_weight or paint.brush is None:
        unified.weight = value
    else:
        paint.brush.weight = value
    return value


@command("weights.weight_gradient", mutates=True, needs_gui=True)
def weight_gradient(params: dict) -> dict:
    """Paint a linear or radial weight gradient between two 3D points (GUI only)."""
    obj = _mesh_object(params["mesh"])
    gradient_type = str(params.get("type", "LINEAR")).upper()
    if gradient_type not in {"LINEAR", "RADIAL"}:
        raise ValueError(f"type must be LINEAR or RADIAL, got {gradient_type!r}")
    space = str(params.get("space", "WORLD")).upper()
    if space not in {"LOCAL", "WORLD"}:
        raise ValueError(f"space must be LOCAL or WORLD, got {space!r}")

    _window, _area, region, view = ctx.require_view3d()
    rv3d = view.region_3d
    _w0, start = _project(region, rv3d, obj, params["start"], space)
    _w1, end = _project(region, rv3d, obj, params["end"], space)

    if params.get("group"):
        obj.vertex_groups.active_index = _group(obj, params["group"]).index
    if obj.vertex_groups.active is None:
        raise ValueError(
            f"{obj.name!r} has no active vertex group to paint into; pass 'group'."
        )

    weight = float(params.get("weight", 1.0))
    with _active_in_mode(obj, "WEIGHT_PAINT"):
        _set_paint_weight(weight)
        with ctx.view3d():
            # xstart/xend/ystart/yend are INT region pixels, not world units.
            bpy.ops.paint.weight_gradient(
                type=gradient_type,
                xstart=int(round(start.x)), ystart=int(round(start.y)),
                xend=int(round(end.x)), yend=int(round(end.y)),
                flip=bool(params.get("flip", False)),
            )
    obj.data.update()

    return {
        "mesh": obj.name,
        "group": obj.vertex_groups.active.name,
        "type": gradient_type,
        "weight": weight,
        "start_region_px": [int(round(start.x)), int(round(start.y))],
        "end_region_px": [int(round(end.x)), int(round(end.y))],
        "region_size": [region.width, region.height],
    }


@command("weights.brush_stroke", mutates=True, needs_gui=True)
def brush_stroke(params: dict) -> dict:
    """Drag the weight brush along a path of 3D points (GUI only; prefer set_weights)."""
    obj = _mesh_object(params["mesh"])
    points = params.get("points") or []
    if len(points) < 2:
        raise ValueError("'points' needs at least two 3D positions to form a stroke")
    space = str(params.get("space", "WORLD")).upper()
    if space not in {"LOCAL", "WORLD"}:
        raise ValueError(f"space must be LOCAL or WORLD, got {space!r}")

    _window, _area, region, view = ctx.require_view3d()
    rv3d = view.region_3d
    inverse = obj.matrix_world.inverted()

    radius = float(params.get("radius_px", 50.0))
    pressure = float(params.get("pressure", 1.0))
    stroke = []
    for i, point in enumerate(points):
        world, screen = _project(region, rv3d, obj, point, space)
        local = inverse @ world
        stroke.append({
            "name": f"stroke{i}",
            "location": (local.x, local.y, local.z),
            "mouse": (screen.x, screen.y),
            "mouse_event": (screen.x, screen.y),
            "pressure": pressure,
            "size": radius,
            "x_tilt": 0.0,
            "y_tilt": 0.0,
            "time": float(i),
            "is_start": i == 0,
        })

    if params.get("group"):
        obj.vertex_groups.active_index = _group(obj, params["group"]).index
    if obj.vertex_groups.active is None:
        raise ValueError(f"{obj.name!r} has no active vertex group to paint into; pass 'group'.")

    weight = float(params.get("weight", 1.0))
    strength = params.get("strength")
    with _active_in_mode(obj, "WEIGHT_PAINT"):
        _set_paint_weight(weight)
        paint = bpy.context.scene.tool_settings.weight_paint
        if paint.brush is not None:
            paint.brush.size = int(round(radius))
            if strength is not None:
                paint.brush.strength = float(strength)
        with ctx.view3d():
            bpy.ops.paint.weight_paint(
                stroke=stroke,
                mode=str(params.get("mode", "NORMAL")).upper(),
            )
    obj.data.update()

    return {"mesh": obj.name, "group": obj.vertex_groups.active.name,
            "points": len(stroke), "weight": weight, "radius_px": radius}


def _screenshot_area(max_size: int) -> dict:
    """Grab the real VIEW_3D pixels, including the weight-paint colour overlay."""
    window, area, region, view = ctx.require_view3d()
    out_dir = tempfile.mkdtemp(prefix="agentmcp-weights-")
    out_path = os.path.join(out_dir, "heatmap.png")
    image = None
    try:
        with bpy.context.temp_override(window=window, screen=window.screen, area=area,
                                       region=region, space_data=view,
                                       scene=bpy.context.scene):
            bpy.ops.screen.screenshot_area(filepath=out_path)

        image = bpy.data.images.load(out_path)
        width, height = image.size
        fitted = ctx._fit(width, height, max_size)
        if fitted != (width, height):
            image.scale(*fitted)
            image.file_format = "PNG"
            image.save(filepath=out_path)
        result = ctx._read_png(out_path)
        result.update(width=fitted[0], height=fitted[1], source="screen.screenshot_area")
        return result
    finally:
        if image is not None:
            with contextlib.suppress(Exception):
                bpy.data.images.remove(image)
        with contextlib.suppress(OSError):
            os.remove(out_path)
        with contextlib.suppress(OSError):
            os.rmdir(out_dir)


@command("weights.weight_heatmap", mutates=True, needs_gui=True)
def weight_heatmap(params: dict) -> dict:
    """Show one group's weights as the blue-to-red viewport heatmap (GUI only)."""
    obj = _mesh_object(params["mesh"])
    if params.get("group"):
        obj.vertex_groups.active_index = _group(obj, params["group"]).index
    if obj.vertex_groups.active is None:
        raise ValueError(f"{obj.name!r} has no vertex group to display; pass 'group'.")

    _window, _area, _region, view = ctx.require_view3d()
    max_size = int(params.get("max_size", 1024))
    use_render = bool(params.get("use_render", False))

    with _active_in_mode(obj, "WEIGHT_PAINT"):
        with ctx.temp_attrs(view.shading, type="SOLID"), \
             ctx.temp_attrs(view.overlay, show_overlays=True,
                            weight_paint_mode_opacity=1.0,
                            show_wpaint_contours=bool(params.get("show_contours", False))):
            payload = (ctx.capture_viewport(shading_mode="SOLID", max_size=max_size)
                       if use_render else _screenshot_area(max_size))

    payload.update(mesh=obj.name, group=obj.vertex_groups.active.name)
    if use_render:
        payload["note"] = (
            "render.opengl output does not include the weight-paint overlay; set "
            "use_render=false (the default) for actual weight colours."
        )
    return payload

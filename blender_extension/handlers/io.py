"""Import / export / blend-file operations.

The option names differ per format in ways that are easy to get wrong — the
``wm.*`` importers/exporters use ``export_selected_objects`` / ``forward_axis`` /
``up_axis`` while the older ``*_scene.*`` add-ons use ``use_selection`` /
``axis_forward`` / ``axis_up``, and USD and Alembic each spell it differently
again. :data:`FORMATS` maps one neutral vocabulary onto the real parameter names,
all verified live against 5.2.
"""

from __future__ import annotations

import os

import bpy

from ..registry import command

#: Neutral option -> real operator parameter, per format.
FORMATS = {
    "OBJ": {
        "ext": (".obj",),
        "import_op": ("wm", "obj_import"),
        "export_op": ("wm", "obj_export"),
        "export_map": {"selected_only": "export_selected_objects",
                       "scale": "global_scale", "forward_axis": "forward_axis",
                       "up_axis": "up_axis", "apply_modifiers": "apply_modifiers"},
        "import_map": {"scale": "global_scale", "forward_axis": "forward_axis",
                       "up_axis": "up_axis"},
    },
    "STL": {
        "ext": (".stl",),
        "import_op": ("wm", "stl_import"),
        "export_op": ("wm", "stl_export"),
        "export_map": {"selected_only": "export_selected_objects",
                       "scale": "global_scale", "forward_axis": "forward_axis",
                       "up_axis": "up_axis", "apply_modifiers": "apply_modifiers"},
        "import_map": {"scale": "global_scale", "forward_axis": "forward_axis",
                       "up_axis": "up_axis"},
    },
    "PLY": {
        "ext": (".ply",),
        "import_op": ("wm", "ply_import"),
        "export_op": ("wm", "ply_export"),
        "export_map": {"selected_only": "export_selected_objects",
                       "scale": "global_scale", "forward_axis": "forward_axis",
                       "up_axis": "up_axis", "apply_modifiers": "apply_modifiers"},
        "import_map": {"scale": "global_scale", "forward_axis": "forward_axis",
                       "up_axis": "up_axis"},
    },
    "FBX": {
        "ext": (".fbx",),
        # wm.fbx_import is the new C++ importer; there is no wm.fbx_export.
        "import_op": ("wm", "fbx_import"),
        "export_op": ("export_scene", "fbx"),
        "export_map": {"selected_only": "use_selection", "scale": "global_scale",
                       "forward_axis": "axis_forward", "up_axis": "axis_up",
                       "apply_modifiers": "use_mesh_modifiers"},
        "import_map": {"scale": "global_scale"},
    },
    "GLTF": {
        "ext": (".gltf", ".glb"),
        "import_op": ("import_scene", "gltf"),
        "export_op": ("export_scene", "gltf"),
        "export_map": {"selected_only": "use_selection", "apply_modifiers": "export_apply"},
        "import_map": {},
    },
    "USD": {
        "ext": (".usd", ".usda", ".usdc", ".usdz"),
        "import_op": ("wm", "usd_import"),
        "export_op": ("wm", "usd_export"),
        "export_map": {"selected_only": "selected_objects_only",
                       "forward_axis": "export_global_forward_selection",
                       "up_axis": "export_global_up_selection"},
        "import_map": {"scale": "scale"},
    },
    "ABC": {
        "ext": (".abc",),
        "import_op": ("wm", "alembic_import"),
        "export_op": ("wm", "alembic_export"),
        "export_map": {"selected_only": "selected", "scale": "global_scale"},
        "import_map": {"scale": "scale"},
    },
}

EXT_TO_FORMAT = {ext: name for name, spec in FORMATS.items() for ext in spec["ext"]}


def _resolve_format(path: str, fmt: str | None) -> str:
    if fmt and str(fmt).upper() not in ("AUTO", ""):
        name = str(fmt).upper()
        if name == "GLB":
            name = "GLTF"
        if name not in FORMATS:
            raise ValueError(f"format must be one of {sorted(FORMATS)} or 'auto', got {fmt!r}")
        return name
    ext = os.path.splitext(path)[1].lower()
    if ext not in EXT_TO_FORMAT:
        raise ValueError(
            f"cannot infer a format from {ext!r}. Pass format= explicitly "
            f"(one of {sorted(FORMATS)}), or use a known extension: "
            f"{sorted(EXT_TO_FORMAT)}"
        )
    return EXT_TO_FORMAT[ext]


def _operator(pair):
    return getattr(getattr(bpy.ops, pair[0]), pair[1])


def _supported(op) -> set:
    return {p.identifier for p in op.get_rna_type().properties}


def _build_kwargs(op, mapping: dict, neutral: dict, extra: dict | None) -> dict:
    """Translate neutral options into the operator's real parameter names.

    Anything the operator does not support is dropped and reported rather than
    raising, so a caller can pass `scale` uniformly without knowing that USD
    spells it differently.
    """
    available = _supported(op)
    kwargs, ignored = {}, []
    for key, value in neutral.items():
        if value is None:
            continue
        real = mapping.get(key)
        if real and real in available:
            kwargs[real] = value
        else:
            ignored.append(key)
    for key, value in (extra or {}).items():
        if key in available:
            kwargs[key] = value
        else:
            ignored.append(key)
    return kwargs, ignored


def _reject_current_file(path: str, action: str) -> None:
    """Blender cannot link/append from the .blend it currently has open."""
    if bpy.data.filepath and os.path.abspath(path) == os.path.abspath(bpy.data.filepath):
        raise ValueError(
            f"cannot {action} from {path} because it is the file currently open. "
            "Its contents are already in this session — address them directly by "
            "name, or open a different file first."
        )


@command("io.import_model", mutates=True)
def import_model(params: dict) -> dict:
    """Import a model file and report exactly which objects it created."""
    path = os.path.expanduser(str(params["path"]))
    if not os.path.isfile(path):
        raise FileNotFoundError(f"no such file: {path}")

    fmt = _resolve_format(path, params.get("format"))
    spec = FORMATS[fmt]
    op = _operator(spec["import_op"])

    neutral = {
        "scale": params.get("scale"),
        "forward_axis": params.get("forward_axis"),
        "up_axis": params.get("up_axis"),
    }
    kwargs, ignored = _build_kwargs(op, spec["import_map"], neutral, params.get("options"))

    before = {o.name for o in bpy.data.objects}
    op(filepath=path, **kwargs)
    created = sorted({o.name for o in bpy.data.objects} - before)

    return {
        "format": fmt,
        "path": path,
        "created_objects": created,
        "created_count": len(created),
        "applied_options": kwargs,
        "ignored_options": ignored,
    }


@command("io.export_model", mutates=False)
def export_model(params: dict) -> dict:
    """Export the scene or the current selection to a model file."""
    path = os.path.expanduser(str(params["path"]))
    fmt = _resolve_format(path, params.get("format"))
    spec = FORMATS[fmt]
    op = _operator(spec["export_op"])

    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)

    selected_only = params.get("selected_only")
    if selected_only:
        selected = [o.name for o in bpy.context.view_layer.objects if o.select_get()]
        if not selected:
            raise RuntimeError(
                "selected_only=True but nothing is selected. Use select_objects "
                "first, or pass selected_only=False to export the whole scene."
            )

    neutral = {
        "selected_only": selected_only,
        "scale": params.get("scale"),
        "forward_axis": params.get("forward_axis"),
        "up_axis": params.get("up_axis"),
        "apply_modifiers": params.get("apply_modifiers"),
    }
    extra = dict(params.get("options") or {})
    if fmt == "GLTF" and "export_format" not in extra:
        extra["export_format"] = "GLB" if path.lower().endswith(".glb") else "GLTF_SEPARATE"

    kwargs, ignored = _build_kwargs(op, spec["export_map"], neutral, extra)
    op(filepath=path, **kwargs)

    if not os.path.isfile(path):
        # glTF separate mode writes a sibling .gltf when given a .glb name, etc.
        raise RuntimeError(f"export reported success but {path} does not exist")

    return {
        "format": fmt,
        "path": path,
        "bytes": os.path.getsize(path),
        "applied_options": kwargs,
        "ignored_options": ignored,
    }


@command("io.save_blend", mutates=False)
def save_blend(params: dict) -> dict:
    """Save the .blend file. Overwriting a *different* existing file needs confirm."""
    path = params.get("path")
    if path:
        path = os.path.expanduser(str(path))
        if not path.endswith(".blend"):
            path += ".blend"
        already_open = os.path.abspath(path) == os.path.abspath(bpy.data.filepath or "")
        if os.path.exists(path) and not already_open and not params.get("confirm"):
            raise PermissionError(
                f"{path} already exists and is not the file currently open. "
                "Pass confirm=true to overwrite it."
            )
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        bpy.ops.wm.save_as_mainfile(filepath=path,
                                    compress=bool(params.get("compress", False)))
    else:
        if not bpy.data.filepath:
            raise ValueError(
                "this scene has never been saved, so there is no path to save to. "
                "Pass an explicit path."
            )
        bpy.ops.wm.save_mainfile()

    return {"path": bpy.data.filepath,
            "bytes": os.path.getsize(bpy.data.filepath) if bpy.data.filepath else 0}


@command("io.open_blend", mutates=False)
def open_blend(params: dict) -> dict:
    """Open a .blend file. Always requires confirm — it discards unsaved changes."""
    path = os.path.expanduser(str(params["path"]))
    if not os.path.isfile(path):
        raise FileNotFoundError(f"no such file: {path}")
    if not params.get("confirm"):
        raise PermissionError(
            f"opening {path} discards everything not saved in the current scene "
            f"(currently {'MODIFIED — unsaved changes would be lost' if bpy.data.is_dirty else 'unmodified'}). "
            "Pass confirm=true to proceed, or call io.save_blend first."
        )
    was_dirty = bpy.data.is_dirty
    bpy.ops.wm.open_mainfile(filepath=path)
    return {"path": bpy.data.filepath, "discarded_unsaved_changes": was_dirty,
            "objects": len(bpy.data.objects)}


@command("io.list_blend_contents", mutates=False)
def list_blend_contents(params: dict) -> dict:
    """List the datablocks inside another .blend, so you know what can be appended."""
    path = os.path.expanduser(str(params["path"]))
    if not os.path.isfile(path):
        raise FileNotFoundError(f"no such file: {path}")
    _reject_current_file(path, "list contents")

    wanted = params.get("datablock_type")
    contents = {}
    with bpy.data.libraries.load(path) as (src, _dst):
        for attr in ("objects", "materials", "meshes", "collections", "actions",
                     "node_groups", "images", "worlds", "armatures", "brushes"):
            if wanted and attr != str(wanted).lower():
                continue
            try:
                contents[attr] = sorted(getattr(src, attr))
            except AttributeError:
                continue
    return {"path": path, "contents": contents,
            "counts": {k: len(v) for k, v in contents.items()}}


@command("io.append_from_blend", mutates=True)
def append_from_blend(params: dict) -> dict:
    """Append (copy in) or link datablocks from another .blend file."""
    path = os.path.expanduser(str(params["path"]))
    if not os.path.isfile(path):
        raise FileNotFoundError(f"no such file: {path}")
    _reject_current_file(path, "append")

    kind = str(params.get("datablock_type", "objects")).lower()
    names = params.get("names")
    if isinstance(names, str):
        names = [names]
    link = bool(params.get("link", False))

    with bpy.data.libraries.load(path, link=link) as (src, dst):
        available = list(getattr(src, kind, []))
        if not available:
            raise ValueError(
                f"{path} has no {kind!r} datablocks. Call io.list_blend_contents "
                "to see what it does contain."
            )
        chosen = available if names is None else [n for n in names if n in available]
        missing = [] if names is None else [n for n in names if n not in available]
        if missing:
            raise KeyError(f"{missing} not found in {path}. Available {kind}: {available}")
        setattr(dst, kind, chosen)

    linked_objects = []
    if kind == "objects":
        collection = bpy.context.collection
        for obj in bpy.data.objects:
            if obj.name in chosen and obj.name not in collection.objects:
                collection.objects.link(obj)
                linked_objects.append(obj.name)

    return {"path": path, "datablock_type": kind, "appended": chosen,
            "linked_into_scene": linked_objects, "link_mode": link}

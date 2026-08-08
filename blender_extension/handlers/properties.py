"""Custom ID properties for pipeline metadata, controls and driver inputs."""

from __future__ import annotations

from collections.abc import Mapping

import bpy

from ..registry import command


_COLLECTIONS = {
    "OBJECT": "objects",
    "MATERIAL": "materials",
    "COLLECTION": "collections",
    "WORLD": "worlds",
    "SCENE": "scenes",
    "ACTION": "actions",
    "NODE_GROUP": "node_groups",
    "ARMATURE": "armatures",
    "CAMERA": "cameras",
    "LIGHT": "lights",
    "IMAGE": "images",
}


def _owner(params: dict):
    kind = str(params.get("target_type", "OBJECT")).upper()
    name = params.get("target")
    if kind == "OBJECT_DATA":
        if not name:
            raise ValueError("target is required for OBJECT_DATA")
        obj = bpy.data.objects.get(str(name))
        if obj is None:
            raise KeyError(f"no object named {name!r}")
        if obj.data is None:
            raise TypeError(f"{obj.name!r} has no object data")
        return kind, obj.data
    collection_name = _COLLECTIONS.get(kind)
    if collection_name is None:
        raise ValueError(
            f"target_type must be one of "
            f"{sorted(set(_COLLECTIONS) | {'OBJECT_DATA'})}, got {kind!r}"
        )
    collection = getattr(bpy.data, collection_name)
    if not name and kind == "SCENE":
        return kind, bpy.context.scene
    if not name and kind == "WORLD" and bpy.context.scene.world is not None:
        return kind, bpy.context.scene.world
    if not name:
        raise ValueError(f"target is required for target_type={kind}")
    owner = collection.get(str(name))
    if owner is None:
        raise KeyError(f"no {kind.lower()} named {name!r}; available: {sorted(collection.keys())[:100]}")
    return kind, owner


def _json_value(value):
    if isinstance(value, Mapping) or hasattr(value, "items"):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if hasattr(value, "to_list"):
        return [_json_value(item) for item in value.to_list()]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _entry(owner, key: str) -> dict:
    ui = {}
    try:
        ui = _json_value(owner.id_properties_ui(key).as_dict())
    except (KeyError, TypeError):
        pass
    value = _json_value(owner[key])
    return {"key": key, "value": value, "value_type": type(value).__name__, "ui": ui}


@command("properties.list")
def list_properties(params: dict) -> dict:
    """List custom ID properties and UI metadata on a Blender datablock."""
    kind, owner = _owner(params)
    limit = max(1, int(params.get("limit", 500)))
    keys = sorted(key for key in owner.keys() if key != "_RNA_UI")
    return {
        "target_type": kind,
        "target": owner.name,
        "count": len(keys),
        "properties": [_entry(owner, key) for key in keys[:limit]],
        "truncated": len(keys) > limit,
    }


@command("properties.set", mutates=True)
def set_property(params: dict) -> dict:
    """Create/update one custom ID property and optional UI limits/description."""
    kind, owner = _owner(params)
    key = str(params["key"]).strip()
    if not key or key == "_RNA_UI":
        raise ValueError("key must be non-empty and cannot be the reserved '_RNA_UI'")
    try:
        owner[key] = params["value"]
    except (TypeError, ValueError) as exc:
        raise TypeError(
            "custom property values must be JSON-like: bool, int, float, string, "
            "a numeric array, or a nested dictionary"
        ) from exc

    ui_values = {}
    for name, cast in (("description", str), ("subtype", str),
                       ("min", float), ("max", float),
                       ("soft_min", float), ("soft_max", float),
                       ("step", float), ("precision", int), ("default", None)):
        if params.get(name) is not None:
            ui_values[name] = params[name] if cast is None else cast(params[name])
    if ui_values:
        try:
            owner.id_properties_ui(key).update(**ui_values)
        except (TypeError, ValueError) as exc:
            del owner[key]
            raise ValueError(f"invalid UI metadata for {key!r}: {exc}") from exc
    owner.update_tag()
    result = _entry(owner, key)
    result.update(target_type=kind, target=owner.name)
    return result


@command("properties.remove", mutates=True)
def remove_property(params: dict) -> dict:
    """Remove one custom ID property."""
    kind, owner = _owner(params)
    key = str(params["key"])
    if key not in owner:
        raise KeyError(f"{owner.name!r} has no custom property {key!r}")
    previous = _json_value(owner[key])
    del owner[key]
    owner.update_tag()
    return {"target_type": kind, "target": owner.name,
            "removed": key, "previous_value": previous}

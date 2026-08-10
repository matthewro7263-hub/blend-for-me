"""Dependency-free helpers for the Blender agent activity overlay.

Keeping command routing and animation math outside :mod:`bpy` makes the logic
cheap to unit test and lets the draw module focus only on Blender/GPU details.
"""

from __future__ import annotations

import math
from typing import Any


EditorRoute = tuple[tuple[str, str | None], ...]


def editor_route(command: str, params: dict[str, Any] | None = None) -> EditorRoute:
    """Return preferred ``(area_type, node_tree_type)`` targets for a command."""
    command = str(command or "")
    prefix = command.split(".", 1)[0]
    params = params or {}

    if command == "execute_python":
        code = str(params.get("code") or "").lower()
        if any(token in code for token in ("compositor", "scene.node_tree", "compositornode")):
            return (("NODE_EDITOR", "CompositorNodeTree"), ("VIEW_3D", None))
        if any(token in code for token in ("geometrynodetree", "geometry nodes", "node_group")):
            return (("NODE_EDITOR", "GeometryNodeTree"), ("VIEW_3D", None))
        if "node" in code and any(token in code for token in ("material", "shader", "principled")):
            return (("NODE_EDITOR", "ShaderNodeTree"), ("VIEW_3D", None))
        if any(token in code for token in ("sequence_editor", "sequences_all", "movieclip")):
            return (("SEQUENCE_EDITOR", None), ("VIEW_3D", None))
        if any(token in code for token in ("fcurve", "keyframe", "animation_data")):
            return (
                ("DOPESHEET_EDITOR", None),
                ("GRAPH_EDITOR", None),
                ("VIEW_3D", None),
            )

    routes: dict[str, EditorRoute] = {
        "shading": (("NODE_EDITOR", "ShaderNodeTree"), ("VIEW_3D", None)),
        "geonodes": (("NODE_EDITOR", "GeometryNodeTree"), ("VIEW_3D", None)),
        "compositor": (("NODE_EDITOR", "CompositorNodeTree"), ("VIEW_3D", None)),
        "uv": (("IMAGE_EDITOR", None), ("VIEW_3D", None)),
        "anim": (
            ("DOPESHEET_EDITOR", None),
            ("GRAPH_EDITOR", None),
            ("VIEW_3D", None),
        ),
        "cinematics": (("SEQUENCE_EDITOR", None), ("VIEW_3D", None)),
    }
    return routes.get(prefix, (("VIEW_3D", None),))


def node_waypoints(params: dict[str, Any] | None) -> list[tuple[str, float, float]]:
    """Extract stable node labels and editor coordinates from command params."""
    params = params or {}
    result: list[tuple[str, float, float]] = []
    nodes = params.get("nodes")
    if isinstance(nodes, list):
        for index, spec in enumerate(nodes):
            if not isinstance(spec, dict):
                continue
            location = spec.get("location")
            if not _xy(location):
                continue
            label = spec.get("label") or spec.get("id") or spec.get("name") or f"node {index + 1}"
            result.append((str(label), float(location[0]), float(location[1])))

    location = params.get("location")
    if not result and _xy(location):
        label = params.get("agent_id") or params.get("node") or params.get("type") or "node"
        result.append((str(label), float(location[0]), float(location[1])))
    return result


def _xy(value: Any) -> bool:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return False
    try:
        return all(math.isfinite(float(item)) for item in value)
    except (TypeError, ValueError):
        return False


def smoothstep(value: float) -> float:
    """Clamp to 0..1 and apply a smooth cubic ease."""
    value = max(0.0, min(1.0, float(value)))
    return value * value * (3.0 - 2.0 * value)


def ease_out_cubic(value: float) -> float:
    value = max(0.0, min(1.0, float(value)))
    return 1.0 - (1.0 - value) ** 3


def spring_ease(value: float) -> float:
    """Fast, damped overshoot that lands exactly on one.

    This is intentionally restrained: enough elasticity to make cursor/window
    motion feel physical without wobbling around the user's actual Blender work.
    """
    value = max(0.0, min(1.0, float(value)))
    if value in {0.0, 1.0}:
        return value
    damping = 7.0
    frequency = 10.5
    raw = 1.0 - math.exp(-damping * value) * math.cos(frequency * value)
    end = 1.0 - math.exp(-damping) * math.cos(frequency)
    return raw / end


def typing_duration(characters: int) -> float:
    """Readable but brisk command typing duration in seconds."""
    return max(0.32, min(1.35, max(0, int(characters)) / 92.0))


def exit_scale(value: float) -> float:
    """A smooth scale-down with a restrained initial overshoot."""
    value = smoothstep(value)
    return max(0.0, 1.0 - value * value)

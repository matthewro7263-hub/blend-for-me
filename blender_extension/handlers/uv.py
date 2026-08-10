"""UV mapping: seams, unwrapping, packing and diagnostics.

Every operator here needs Edit Mode with an appropriate select mode, so the
handlers set that up and restore the caller's previous mode afterwards. Angles
are radians throughout, matching Blender's own API.
"""

from __future__ import annotations

import contextlib
import math

import bpy

from .. import ctx
from ..registry import command


def _mesh_object(name: str | None):
    if name:
        obj = bpy.data.objects.get(name)
        if obj is None:
            raise KeyError(f"no object named {name!r}")
    else:
        obj = bpy.context.view_layer.objects.active
        if obj is None:
            raise RuntimeError("no object given and nothing is active")
    if obj.type != "MESH":
        raise TypeError(f"{obj.name!r} is a {obj.type}; UV tools need a MESH")
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    return obj


@contextlib.contextmanager
def _edit_mode(obj, select_mode: str = "FACE", select_all: bool = True):
    with ctx.preserve_context(active_object=obj, restore_elements=not select_all):
        if bpy.context.mode != "EDIT_MESH":
            bpy.ops.object.mode_set(mode="EDIT")
        bpy.context.tool_settings.mesh_select_mode = (
            select_mode == "VERT", select_mode == "EDGE", select_mode == "FACE"
        )
        if select_all:
            bpy.ops.mesh.select_all(action="SELECT")
        yield


def _supported(op) -> set:
    return {p.identifier for p in op.get_rna_type().properties}


def _filtered(op, **kwargs) -> dict:
    """Drop parameters this Blender build's operator does not have."""
    available = _supported(op)
    return {k: v for k, v in kwargs.items() if v is not None and k in available}


@command("uv.mark_seams", mutates=True)
def mark_seams(params: dict) -> dict:
    """Mark or clear UV seams, by edge index or by sharpness angle."""
    obj = _mesh_object(params.get("object"))
    mesh = obj.data
    clear = bool(params.get("clear", False))
    edges = params.get("edges")

    if isinstance(edges, str) and edges.upper() == "SHARP":
        angle = float(params.get("angle", math.radians(30.0)))
        chosen = [e.index for e in mesh.edges if e.use_edge_sharp]
        if not chosen:  # fall back to geometric sharpness
            mesh.calc_loop_triangles()
            chosen = [e.index for e in mesh.edges
                      if e.use_seam or _edge_angle(mesh, e) > angle]
    elif edges is None:
        raise ValueError(
            "'edges' is required: a list of edge indices, or 'SHARP' to use "
            "sharp-marked/steep edges"
        )
    else:
        chosen = [int(i) for i in edges]

    count = len(mesh.edges)
    bad = [i for i in chosen if i < 0 or i >= count]
    if bad:
        raise IndexError(f"edge indices out of range (mesh has {count} edges): {bad[:10]}")

    for index in chosen:
        mesh.edges[index].use_seam = not clear
    mesh.update()
    return {"object": obj.name, "edges_marked": len(chosen), "cleared": clear,
            "total_seams": sum(1 for e in mesh.edges if e.use_seam)}


def _edge_angle(mesh, edge) -> float:
    faces = [p for p in mesh.polygons if edge.key in
             {tuple(sorted((p.vertices[i], p.vertices[(i + 1) % len(p.vertices)])))
              for i in range(len(p.vertices))}]
    if len(faces) != 2:
        return 0.0
    return faces[0].normal.angle(faces[1].normal)


@command("uv.unwrap", mutates=True)
def unwrap(params: dict) -> dict:
    """Unwrap using existing seams."""
    obj = _mesh_object(params.get("object"))
    method = str(params.get("method", "ANGLE_BASED")).upper()
    valid = [i.identifier for i in
             bpy.ops.uv.unwrap.get_rna_type().properties["method"].enum_items]
    if method not in valid:
        raise ValueError(f"method must be one of {valid}, got {method!r}")

    seams = sum(1 for e in obj.data.edges if e.use_seam)
    kwargs = _filtered(
        bpy.ops.uv.unwrap,
        method=method,
        margin=params.get("margin"),
        fill_holes=params.get("fill_holes"),
        correct_aspect=params.get("correct_aspect"),
        margin_method=(str(params["margin_method"]).upper()
                       if params.get("margin_method") else None),
    )
    with _edit_mode(obj, "FACE"):
        bpy.ops.uv.unwrap(**kwargs)

    return {"object": obj.name, "method": method, "seams": seams,
            "applied": kwargs, "uv_layers": [l.name for l in obj.data.uv_layers],
            "note": "no seams are marked, so the result is likely one distorted island"
            if seams == 0 else None}


@command("uv.smart_project", mutates=True)
def smart_project(params: dict) -> dict:
    """Automatic UVs by splitting on an angle threshold. No seams required."""
    obj = _mesh_object(params.get("object"))
    kwargs = _filtered(
        bpy.ops.uv.smart_project,
        angle_limit=params.get("angle_limit"),
        island_margin=params.get("island_margin"),
        area_weight=params.get("area_weight"),
        correct_aspect=params.get("correct_aspect"),
        scale_to_bounds=params.get("scale_to_bounds"),
    )
    with _edit_mode(obj, "FACE"):
        bpy.ops.uv.smart_project(**kwargs)
    return {"object": obj.name, "applied": kwargs,
            "uv_layers": [l.name for l in obj.data.uv_layers]}


@command("uv.pack_islands", mutates=True)
def pack_islands(params: dict) -> dict:
    """Repack existing UV islands into the 0-1 space."""
    obj = _mesh_object(params.get("object"))
    if not obj.data.uv_layers:
        raise RuntimeError(f"{obj.name!r} has no UV layer to pack — unwrap first")
    kwargs = _filtered(
        bpy.ops.uv.pack_islands,
        margin=params.get("margin"),
        rotate=params.get("rotate"),
        scale=params.get("scale"),
        merge_overlap=params.get("merge_overlap"),
        shape_method=(str(params["shape_method"]).upper()
                      if params.get("shape_method") else None),
        margin_method=(str(params["margin_method"]).upper()
                       if params.get("margin_method") else None),
    )
    with _edit_mode(obj, "FACE"):
        bpy.ops.uv.pack_islands(**kwargs)
    return {"object": obj.name, "applied": kwargs}


@command("uv.stats", mutates=False)
def uv_stats(params: dict) -> dict:
    """UV diagnostics: island count, coverage, out-of-bounds and overlap estimate."""
    obj = _mesh_object(params.get("object"))
    mesh = obj.data
    layer_name = params.get("uv_layer")
    if not mesh.uv_layers:
        return {"object": obj.name, "has_uvs": False,
                "note": "no UV layers — run uv.smart_project or uv.unwrap"}

    layer = mesh.uv_layers.get(layer_name) if layer_name else mesh.uv_layers.active
    if layer is None:
        raise KeyError(f"no UV layer {layer_name!r}. Layers: "
                       f"{[l.name for l in mesh.uv_layers]}")

    uvs = [tuple(d.uv) for d in layer.data]
    if not uvs:
        return {"object": obj.name, "has_uvs": False}

    xs = [u for u, _ in uvs]
    ys = [v for _, v in uvs]
    outside = sum(1 for u, v in uvs if u < 0.0 or u > 1.0 or v < 0.0 or v > 1.0)

    # Island count via union-find over loops that share a UV coordinate.
    parent = list(range(len(mesh.polygons)))

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    seen = {}
    for poly in mesh.polygons:
        for loop_index in poly.loop_indices:
            key = (round(layer.data[loop_index].uv[0], 5),
                   round(layer.data[loop_index].uv[1], 5))
            if key in seen:
                union(seen[key], poly.index)
            else:
                seen[key] = poly.index
    islands = len({find(p.index) for p in mesh.polygons})

    uv_area = 0.0
    for poly in mesh.polygons:
        loops = [layer.data[i].uv for i in poly.loop_indices]
        area = 0.0
        for i in range(len(loops)):
            x1, y1 = loops[i]
            x2, y2 = loops[(i + 1) % len(loops)]
            area += x1 * y2 - x2 * y1
        uv_area += abs(area) * 0.5

    mesh_area = sum(p.area for p in mesh.polygons)
    return {
        "object": obj.name,
        "has_uvs": True,
        "uv_layer": layer.name,
        "uv_layers": [l.name for l in mesh.uv_layers],
        "faces": len(mesh.polygons),
        "islands": islands,
        "uv_area": uv_area,
        "coverage_percent": uv_area * 100.0,
        "overlap_likely": uv_area > 1.0,
        "bounds": {"u_min": min(xs), "u_max": max(xs),
                   "v_min": min(ys), "v_max": max(ys)},
        "loops_outside_0_1": outside,
        "mesh_surface_area": mesh_area,
        "texel_density_hint": (uv_area / mesh_area) if mesh_area else None,
    }


@command("uv.layer_list", mutates=False)
def layer_list(params: dict) -> dict:
    """List an object's UV layers."""
    obj = _mesh_object(params.get("object"))
    return {"object": obj.name,
            "layers": [{"name": l.name, "active": l.active,
                        "active_render": l.active_render} for l in obj.data.uv_layers]}


@command("uv.layer_create", mutates=True)
def layer_create(params: dict) -> dict:
    """Add a UV layer."""
    obj = _mesh_object(params.get("object"))
    layer = obj.data.uv_layers.new(name=str(params.get("name", "UVMap")))
    if params.get("active", True):
        obj.data.uv_layers.active = layer
    return {"object": obj.name, "created": layer.name,
            "layers": [l.name for l in obj.data.uv_layers]}


@command("uv.layer_remove", mutates=True)
def layer_remove(params: dict) -> dict:
    """Remove a UV layer by name."""
    obj = _mesh_object(params.get("object"))
    name = params["name"]
    layer = obj.data.uv_layers.get(name)
    if layer is None:
        raise KeyError(f"no UV layer {name!r}. Layers: "
                       f"{[l.name for l in obj.data.uv_layers]}")
    obj.data.uv_layers.remove(layer)
    return {"object": obj.name, "removed": name,
            "layers": [l.name for l in obj.data.uv_layers]}


@command("uv.layer_set_active", mutates=True)
def layer_set_active(params: dict) -> dict:
    """Make a UV layer the active one for editing and/or rendering."""
    obj = _mesh_object(params.get("object"))
    name = params["name"]
    layer = obj.data.uv_layers.get(name)
    if layer is None:
        raise KeyError(f"no UV layer {name!r}. Layers: "
                       f"{[l.name for l in obj.data.uv_layers]}")
    obj.data.uv_layers.active = layer
    if params.get("for_render"):
        layer.active_render = True
    return {"object": obj.name, "active": layer.name,
            "active_render": layer.active_render}

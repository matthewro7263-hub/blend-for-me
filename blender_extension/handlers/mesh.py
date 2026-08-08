"""Mesh editing commands, bmesh-backed.

Every geometry edit here goes through ``bmesh`` on the object's own mesh data, so
the commands work identically whether the object sits in Object Mode or Edit Mode
and never depend on a 3D Viewport. Selection state is read and written through the
same bmesh, which is what the interactive Edit Mode tools use, so an agent can
select with :func:`select_geometry` and then chain any number of edits.
"""

from __future__ import annotations

import contextlib
import math
import random
from typing import Iterator, List, Optional, Sequence

import bmesh
import bpy
from mathutils import Vector
from mathutils.kdtree import KDTree

from ..registry import command

#: domain name -> BMesh sequence attribute
_SEQ = {"VERT": "verts", "EDGE": "edges", "FACE": "faces"}

#: friendly aliases -> bmesh.ops.symmetrize direction identifiers.
#: bmesh spells them '-X'/'X'; ``bpy.ops.mesh.symmetrize`` spells the same things
#: 'NEGATIVE_X'/'POSITIVE_X'. Accept both plus the obvious '+X'.
_SYMMETRY_AXIS = {
    "-X": "-X", "NEGATIVE_X": "-X", "+X": "X", "X": "X", "POSITIVE_X": "X",
    "-Y": "-Y", "NEGATIVE_Y": "-Y", "+Y": "Y", "Y": "Y", "POSITIVE_Y": "Y",
    "-Z": "-Z", "NEGATIVE_Z": "-Z", "+Z": "Z", "Z": "Z", "POSITIVE_Z": "Z",
}

#: bmesh.ops.triangulate uses different enum spellings than
#: bpy.ops.mesh.quads_convert_to_tris. Accept either spelling.
_QUAD_METHOD = {
    "BEAUTY": "BEAUTY", "FIXED": "FIXED",
    "ALTERNATE": "ALTERNATE", "FIXED_ALTERNATE": "ALTERNATE",
    "SHORT_EDGE": "SHORT_EDGE", "SHORTEST_DIAGONAL": "SHORT_EDGE",
    "LONG_EDGE": "LONG_EDGE", "LONGEST_DIAGONAL": "LONG_EDGE",
}
_NGON_METHOD = {"BEAUTY": "BEAUTY", "EAR_CLIP": "EAR_CLIP", "CLIP": "EAR_CLIP"}

#: friendly domain -> bmesh.ops.delete context
_DELETE_CONTEXT = {
    "VERT": "VERTS", "VERTS": "VERTS",
    "EDGE": "EDGES", "EDGES": "EDGES",
    "FACE": "FACES", "FACES": "FACES",
    "FACE_ONLY": "FACES_ONLY", "FACES_ONLY": "FACES_ONLY",
    "EDGE_FACE": "EDGES_FACES", "EDGES_FACES": "EDGES_FACES",
    "FACE_KEEP_BOUNDARY": "FACES_KEEP_BOUNDARY",
    "FACES_KEEP_BOUNDARY": "FACES_KEEP_BOUNDARY",
}

_DEFAULT_LIMIT = 1000


# ---------------------------------------------------------------------------
# plumbing
# ---------------------------------------------------------------------------

def _mesh_object(params: dict, key: str = "name"):
    name = params[key]
    obj = bpy.data.objects.get(name)
    if obj is None:
        raise KeyError(f"no object named {name!r} — call get_scene_info for the real names")
    if obj.type != "MESH":
        raise TypeError(f"{name!r} is a {obj.type}, not a MESH; mesh.* commands only edit meshes")
    return obj


def _require_object_mode(obj, what: str) -> None:
    if obj.mode != "OBJECT":
        raise RuntimeError(
            f"{what} needs {obj.name!r} to be in Object Mode (it is in {obj.mode}). "
            f"Call set_mode(mode='OBJECT', object={obj.name!r}) first."
        )


@contextlib.contextmanager
def _bmesh(obj) -> Iterator[bmesh.types.BMesh]:
    """Yield a lookup-ready BMesh for ``obj``, writing it back on clean exit.

    In Edit Mode the live edit-mesh is used, so the user sees the change without
    a mode round-trip. Everything else gets a scratch bmesh flushed with
    ``to_mesh``. On an exception nothing is written back, which keeps a failed
    command from leaving half-applied geometry behind.
    """
    mesh = obj.data
    if obj.mode == "EDIT":
        bm = bmesh.from_edit_mesh(mesh)
        _ensure_tables(bm)
        yield bm
        bmesh.update_edit_mesh(mesh, loop_triangles=True, destructive=True)
    else:
        bm = bmesh.new()
        try:
            bm.from_mesh(mesh)
            _ensure_tables(bm)
            yield bm
            bm.to_mesh(mesh)
            mesh.update()
        finally:
            bm.free()


@contextlib.contextmanager
def _bmesh_readonly(obj) -> Iterator[bmesh.types.BMesh]:
    """Yield a lookup-ready BMesh for inspection, writing nothing back.

    In Edit Mode this must be the live edit-mesh: the base ``obj.data`` is only
    re-synced when Blender leaves Edit Mode, so reading it there would report
    pre-edit counts and a pre-edit selection.
    """
    mesh = obj.data
    if obj.mode == "EDIT":
        bm = bmesh.from_edit_mesh(mesh)
        _ensure_tables(bm)
        yield bm
    else:
        bm = bmesh.new()
        try:
            bm.from_mesh(mesh)
            _ensure_tables(bm)
            yield bm
        finally:
            bm.free()


def _ensure_tables(bm) -> None:
    bm.verts.ensure_lookup_table()
    bm.edges.ensure_lookup_table()
    bm.faces.ensure_lookup_table()


def _counts(bm) -> dict:
    return {"vertices": len(bm.verts), "edges": len(bm.edges), "faces": len(bm.faces)}


def _delta(before: dict, after: dict) -> dict:
    return {k: after[k] - before[k] for k in before}


def _selected(bm, domain: str) -> list:
    return [e for e in getattr(bm, _SEQ[domain]) if e.select]


def _selection_or_all(bm, domain: str) -> tuple:
    """Selected elements, or every element when nothing is selected."""
    sel = _selected(bm, domain)
    if sel:
        return sel, True
    return list(getattr(bm, _SEQ[domain])), False


def _require_selection(bm, domain: str, what: str) -> list:
    sel = _selected(bm, domain)
    if not sel:
        raise RuntimeError(
            f"{what} needs a {domain.lower()} selection and nothing is selected. "
            f"Call mesh.select_geometry(domain={domain!r}, ...) first."
        )
    return sel


def _vec3(value, default=(0.0, 0.0, 0.0)) -> Vector:
    if value is None:
        return Vector(default)
    if len(value) != 3:
        raise ValueError(f"expected a 3-component vector, got {value!r}")
    return Vector((float(value[0]), float(value[1]), float(value[2])))


def _to_local_offset(obj, vec: Vector, space: str) -> Vector:
    """Convert a translation vector into the object's local space."""
    space = (space or "OBJECT").upper()
    if space == "OBJECT":
        return vec
    if space != "WORLD":
        raise ValueError(f"space must be 'OBJECT' or 'WORLD', got {space!r}")
    return obj.matrix_world.to_3x3().inverted() @ vec


def _indices(elements: Sequence, limit: int) -> tuple:
    idx = [e.index for e in elements]
    return idx[:limit], len(idx) > limit


def _limit_of(params: dict) -> int:
    return max(0, int(params.get("limit", _DEFAULT_LIMIT)))


# ---------------------------------------------------------------------------
# inspection
# ---------------------------------------------------------------------------

@command("mesh.stats")
def stats(params: dict) -> dict:
    """Topology counts plus manifold/loose/ngon diagnostics and bounding box."""
    obj = _mesh_object(params)
    limit = _limit_of(params)

    with _bmesh_readonly(obj) as bm:
        tris = quads = ngons = 0
        triangle_count = 0
        area = 0.0
        ngon_faces = []
        for face in bm.faces:
            n = len(face.verts)
            triangle_count += n - 2
            area += face.calc_area()
            if n == 3:
                tris += 1
            elif n == 4:
                quads += 1
            else:
                ngons += 1
                ngon_faces.append(face)

        loose_verts = [v for v in bm.verts if not v.link_edges]
        wire_edges = [e for e in bm.edges if e.is_wire]
        boundary_edges = [e for e in bm.edges if e.is_boundary]
        nonmanifold_edges = [e for e in bm.edges if not e.is_manifold]
        nonmanifold_verts = [v for v in bm.verts if v.link_edges and not v.is_manifold]

        watertight = not (boundary_edges or nonmanifold_edges or loose_verts or wire_edges)
        volume = bm.calc_volume(signed=True)

        local = [v.co for v in bm.verts]
        if local:
            bmin = Vector((min(c.x for c in local), min(c.y for c in local), min(c.z for c in local)))
            bmax = Vector((max(c.x for c in local), max(c.y for c in local), max(c.z for c in local)))
        else:
            bmin = bmax = Vector((0.0, 0.0, 0.0))
        corners = [obj.matrix_world @ Vector((x, y, z))
                   for x in (bmin.x, bmax.x) for y in (bmin.y, bmax.y) for z in (bmin.z, bmax.z)]
        wmin = Vector((min(c.x for c in corners), min(c.y for c in corners),
                       min(c.z for c in corners)))
        wmax = Vector((max(c.x for c in corners), max(c.y for c in corners),
                       max(c.z for c in corners)))

        ngon_idx, ngon_trunc = _indices(ngon_faces, limit)
        loose_idx, loose_trunc = _indices(loose_verts, limit)
        wire_idx, wire_trunc = _indices(wire_edges, limit)
        bound_idx, bound_trunc = _indices(boundary_edges, limit)
        nme_idx, nme_trunc = _indices(nonmanifold_edges, limit)
        nmv_idx, nmv_trunc = _indices(nonmanifold_verts, limit)

        return {
            "name": obj.name,
            "mode": obj.mode,
            "counts": _counts(bm),
            "triangles": triangle_count,
            "faces_by_kind": {"tris": tris, "quads": quads, "ngons": ngons},
            "selected": {
                "vertices": sum(1 for v in bm.verts if v.select),
                "edges": sum(1 for e in bm.edges if e.select),
                "faces": sum(1 for f in bm.faces if f.select),
            },
            "diagnostics": {
                "loose_vertices": len(loose_verts),
                "wire_edges": len(wire_edges),
                "boundary_edges": len(boundary_edges),
                "non_manifold_edges": len(nonmanifold_edges),
                "non_manifold_vertices": len(nonmanifold_verts),
                "is_watertight": watertight,
                "normals_point_inward": bool(watertight and volume < 0.0),
            },
            "surface_area": area,
            "volume_signed": volume,
            "volume_reliable": watertight,
            "bounds_local": {"min": list(bmin), "max": list(bmax),
                             "size": list(bmax - bmin), "center": list((bmin + bmax) / 2.0)},
            "bounds_world": {"min": list(wmin), "max": list(wmax),
                             "size": list(wmax - wmin), "center": list((wmin + wmax) / 2.0)},
            "uv_layers": [layer.name for layer in obj.data.uv_layers],
            "material_slots": [m.name if m else None for m in obj.data.materials],
            "shape_keys": [k.name for k in obj.data.shape_keys.key_blocks]
            if obj.data.shape_keys else [],
            "samples": {
                "ngon_faces": ngon_idx,
                "loose_vertices": loose_idx,
                "wire_edges": wire_idx,
                "boundary_edges": bound_idx,
                "non_manifold_edges": nme_idx,
                "non_manifold_vertices": nmv_idx,
            },
            "truncated": any((ngon_trunc, loose_trunc, wire_trunc, bound_trunc,
                              nme_trunc, nmv_trunc)),
            "limit": limit,
        }


# ---------------------------------------------------------------------------
# selection
# ---------------------------------------------------------------------------

def _linked_components(bm, domain: str, seeds: List[int]) -> set:
    seq = getattr(bm, _SEQ[domain])
    total = len(seq)
    frontier = []
    for i in seeds:
        if not 0 <= i < total:
            raise IndexError(f"{domain} index {i} out of range (0..{total - 1})")
        frontier.append(seq[i])

    found = set(frontier)
    while frontier:
        elem = frontier.pop()
        if domain == "VERT":
            neighbours = [e.other_vert(elem) for e in elem.link_edges]
        elif domain == "EDGE":
            neighbours = [e for v in elem.verts for e in v.link_edges]
        else:
            neighbours = [f for e in elem.edges for f in e.link_faces]
        for nb in neighbours:
            if nb is not None and nb not in found:
                found.add(nb)
                frontier.append(nb)
    return found


def _element_normal(elem, domain: str) -> Optional[Vector]:
    if domain == "FACE":
        return elem.normal
    if domain == "VERT":
        return elem.normal
    normals = [f.normal for f in elem.link_faces]
    if not normals:
        return None
    total = Vector((0.0, 0.0, 0.0))
    for n in normals:
        total += n
    return total


def _element_position(elem, domain: str) -> Vector:
    if domain == "VERT":
        return elem.co
    if domain == "EDGE":
        a, b = elem.verts
        return (a.co + b.co) / 2.0
    return elem.calc_center_median()


@command("mesh.select_geometry", mutates=True)
def select_geometry(params: dict) -> dict:
    """Select vertices/edges/faces by index, box, normal, material, linked or random."""
    obj = _mesh_object(params)
    domain = str(params.get("domain", "VERT")).upper()
    if domain not in _SEQ:
        raise ValueError(f"domain must be VERT, EDGE or FACE, got {domain!r}")
    mode = str(params.get("mode", "SET")).upper()
    if mode not in {"SET", "ADD", "SUBTRACT"}:
        raise ValueError(f"mode must be SET, ADD or SUBTRACT, got {mode!r}")
    space = str(params.get("space", "OBJECT")).upper()
    if space not in {"OBJECT", "WORLD"}:
        raise ValueError(f"space must be OBJECT or WORLD, got {space!r}")
    limit = _limit_of(params)
    invert = bool(params.get("invert", False))
    select_all = bool(params.get("select_all", False))

    indices = params.get("indices")
    box_min = params.get("box_min")
    box_max = params.get("box_max")
    normal = params.get("normal")
    material_index = params.get("material_index")
    linked_from = params.get("linked_from")
    random_percent = params.get("random_percent")

    criteria = [indices, box_min or box_max, normal, material_index, linked_from,
                random_percent]
    if not any(c is not None for c in criteria) and not select_all and not invert:
        raise ValueError(
            "select_geometry needs at least one of: indices, box_min/box_max, normal, "
            "material_index, linked_from, random_percent, select_all=True, invert=True"
        )

    applied = []
    with _bmesh(obj) as bm:
        seq = getattr(bm, _SEQ[domain])
        total = len(seq)
        candidates = set(seq)

        if indices is not None:
            picked = set()
            for i in indices:
                i = int(i)
                if not 0 <= i < total:
                    raise IndexError(f"{domain} index {i} out of range (0..{total - 1})")
                picked.add(seq[i])
            candidates &= picked
            applied.append("indices")

        if box_min is not None or box_max is not None:
            lo = _vec3(box_min, (-1e30, -1e30, -1e30))
            hi = _vec3(box_max, (1e30, 1e30, 1e30))
            matrix = obj.matrix_world if space == "WORLD" else None
            inside = set()
            for elem in candidates:
                pos = _element_position(elem, domain)
                if matrix is not None:
                    pos = matrix @ pos
                if (lo.x <= pos.x <= hi.x and lo.y <= pos.y <= hi.y and lo.z <= pos.z <= hi.z):
                    inside.add(elem)
            candidates = inside
            applied.append("box")

        if normal is not None:
            direction = _vec3(normal)
            if direction.length == 0.0:
                raise ValueError("normal must be a non-zero direction vector")
            direction = direction.normalized()
            angle_limit = float(params.get("normal_angle", math.radians(45.0)))
            # World-space normals need the inverse-transpose, not the plain matrix.
            nmat = obj.matrix_world.to_3x3().inverted().transposed() if space == "WORLD" else None
            bm.normal_update()
            aligned = set()
            for elem in candidates:
                n = _element_normal(elem, domain)
                if n is None or n.length == 0.0:
                    continue
                if nmat is not None:
                    n = nmat @ n
                    if n.length == 0.0:
                        continue
                if n.normalized().angle(direction, math.pi) <= angle_limit:
                    aligned.add(elem)
            candidates = aligned
            applied.append("normal")

        if material_index is not None:
            want = int(material_index)
            if domain == "FACE":
                candidates = {f for f in candidates if f.material_index == want}
            else:
                matching = {f for f in bm.faces if f.material_index == want}
                if domain == "VERT":
                    keep = {v for f in matching for v in f.verts}
                else:
                    keep = {e for f in matching for e in f.edges}
                candidates &= keep
            applied.append("material_index")

        if linked_from is not None:
            candidates &= _linked_components(bm, domain, [int(i) for i in linked_from])
            applied.append("linked_from")

        if random_percent is not None:
            pct = max(0.0, min(100.0, float(random_percent))) / 100.0
            rng = random.Random(int(params.get("random_seed", 0)))
            candidates = {e for e in candidates if rng.random() < pct}
            applied.append("random_percent")

        if mode == "SET":
            target = candidates
        elif mode == "ADD":
            target = {e for e in seq if e.select} | candidates
        else:
            target = {e for e in seq if e.select} - candidates

        if invert:
            target = set(seq) - target
            applied.append("invert")

        for v in bm.verts:
            v.select_set(False)
        for e in bm.edges:
            e.select_set(False)
        for f in bm.faces:
            f.select_set(False)
        for elem in target:
            elem.select_set(True)

        bm.select_mode = {domain}
        bm.select_flush_mode()

        chosen = sorted(e.index for e in target)
        result = {
            "name": obj.name,
            "domain": domain,
            "mode": mode,
            "criteria_applied": applied,
            "matched": len(target),
            "total_in_domain": total,
            "indices": chosen[:limit],
            "truncated": len(chosen) > limit,
            "selected": {
                "vertices": sum(1 for v in bm.verts if v.select),
                "edges": sum(1 for e in bm.edges if e.select),
                "faces": sum(1 for f in bm.faces if f.select),
            },
        }

    # Keep the Edit Mode header buttons consistent with what we just selected.
    with contextlib.suppress(Exception):
        bpy.context.scene.tool_settings.mesh_select_mode = (
            domain == "VERT", domain == "EDGE", domain == "FACE")
    return result


# ---------------------------------------------------------------------------
# geometry edits
# ---------------------------------------------------------------------------

@command("mesh.extrude_selection", mutates=True)
def extrude_selection(params: dict) -> dict:
    """Extrude the selected faces/edges/verts and offset the new geometry."""
    obj = _mesh_object(params)
    offset = _to_local_offset(obj, _vec3(params.get("offset")), params.get("space", "OBJECT"))
    normal_offset = float(params.get("normal_offset", 0.0))

    with _bmesh(obj) as bm:
        before = _counts(bm)
        faces = _selected(bm, "FACE")
        edges = _selected(bm, "EDGE")
        verts = _selected(bm, "VERT")
        if not (faces or edges or verts):
            raise RuntimeError(
                "extrude_selection has nothing to extrude. Call "
                "mesh.select_geometry(domain='FACE', ...) first."
            )

        geom = list(faces) + list(edges) + list(verts)
        result = bmesh.ops.extrude_face_region(bm, geom=geom, use_normal_flip=False)
        new_geom = result["geom"]
        new_verts = [e for e in new_geom if isinstance(e, bmesh.types.BMVert)]
        new_faces = [e for e in new_geom if isinstance(e, bmesh.types.BMFace)]

        if offset.length:
            bmesh.ops.translate(bm, verts=new_verts, vec=offset)
        if normal_offset:
            bm.normal_update()
            for vert in new_verts:
                vert.co += vert.normal * normal_offset

        # Leave the freshly created cap selected so the next command chains onto it.
        for v in bm.verts:
            v.select_set(False)
        for e in bm.edges:
            e.select_set(False)
        for f in bm.faces:
            f.select_set(False)
        for vert in new_verts:
            vert.select_set(True)
        bm.select_mode = {"FACE"} if new_faces else {"VERT"}
        bm.select_flush(True)

        _ensure_tables(bm)
        after = _counts(bm)
        return {
            "name": obj.name,
            "before": before,
            "after": after,
            "created": _delta(before, after),
            "extruded_from": {"faces": len(faces), "edges": len(edges), "vertices": len(verts)},
            "offset_local": list(offset),
            "normal_offset": normal_offset,
            "new_vertices": len(new_verts),
            "new_faces": len(new_faces),
        }


@command("mesh.inset", mutates=True)
def inset(params: dict) -> dict:
    """Inset the selected faces by thickness, optionally pushing them in/out by depth."""
    obj = _mesh_object(params)
    thickness = float(params.get("thickness", 0.1))
    depth = float(params.get("depth", 0.0))
    individual = bool(params.get("individual", False))

    with _bmesh(obj) as bm:
        before = _counts(bm)
        faces = _require_selection(bm, "FACE", "inset")
        kwargs = dict(
            faces=faces,
            thickness=thickness,
            depth=depth,
            use_even_offset=bool(params.get("use_even_offset", True)),
            use_interpolate=bool(params.get("use_interpolate", True)),
            use_relative_offset=bool(params.get("use_relative_offset", False)),
        )
        if individual:
            result = bmesh.ops.inset_individual(bm, **kwargs)
        else:
            result = bmesh.ops.inset_region(
                bm,
                use_boundary=bool(params.get("use_boundary", True)),
                use_edge_rail=bool(params.get("use_edge_rail", False)),
                use_outset=bool(params.get("use_outset", False)),
                **kwargs,
            )

        for face in bm.faces:
            face.select_set(False)
        for face in result["faces"]:
            face.select_set(True)
        bm.select_mode = {"FACE"}
        bm.select_flush_mode()

        _ensure_tables(bm)
        after = _counts(bm)
        return {"name": obj.name, "before": before, "after": after,
                "created": _delta(before, after), "inset_faces": len(faces),
                "individual": individual, "thickness": thickness, "depth": depth}


@command("mesh.bevel", mutates=True)
def bevel(params: dict) -> dict:
    """Bevel the selected edges (or vertices) with a given width and segment count."""
    obj = _mesh_object(params)
    affect = str(params.get("affect", "EDGES")).upper()
    if affect not in {"EDGES", "VERTICES"}:
        raise ValueError(f"affect must be 'EDGES' or 'VERTICES', got {affect!r}")
    width = float(params.get("width", 0.1))
    segments = max(1, int(params.get("segments", 1)))
    offset_type = str(params.get("offset_type", "OFFSET")).upper()

    with _bmesh(obj) as bm:
        before = _counts(bm)
        if affect == "EDGES":
            geom = _require_selection(bm, "EDGE", "bevel(affect='EDGES')")
        else:
            geom = _require_selection(bm, "VERT", "bevel(affect='VERTICES')")

        result = bmesh.ops.bevel(
            bm,
            geom=list(geom),
            offset=width,
            offset_type=offset_type,
            profile_type="SUPERELLIPSE",
            segments=segments,
            profile=float(params.get("profile", 0.5)),
            affect=affect,
            clamp_overlap=bool(params.get("clamp_overlap", True)),
            material=int(params.get("material", -1)),
            loop_slide=bool(params.get("loop_slide", True)),
            mark_seam=bool(params.get("mark_seam", False)),
            mark_sharp=bool(params.get("mark_sharp", False)),
            harden_normals=bool(params.get("harden_normals", False)),
            miter_outer=str(params.get("miter_outer", "SHARP")).upper(),
            miter_inner=str(params.get("miter_inner", "SHARP")).upper(),
            spread=float(params.get("spread", 0.1)),
        )

        for face in bm.faces:
            face.select_set(False)
        for face in result["faces"]:
            face.select_set(True)
        bm.select_mode = {"FACE"}
        bm.select_flush_mode()

        _ensure_tables(bm)
        after = _counts(bm)
        return {"name": obj.name, "before": before, "after": after,
                "created": _delta(before, after), "beveled": len(geom), "affect": affect,
                "width": width, "segments": segments,
                "new_faces": len(result["faces"])}


@command("mesh.subdivide", mutates=True)
def subdivide(params: dict) -> dict:
    """Subdivide the selected edges, adding ``cuts`` new vertices along each."""
    obj = _mesh_object(params)
    cuts = max(1, int(params.get("cuts", 1)))
    smoothness = float(params.get("smoothness", 0.0))

    with _bmesh(obj) as bm:
        before = _counts(bm)
        edges = _require_selection(bm, "EDGE", "subdivide")
        result = bmesh.ops.subdivide_edges(
            bm,
            edges=list(edges),
            cuts=cuts,
            smooth=smoothness,
            smooth_falloff=str(params.get("smooth_falloff", "SMOOTH")).upper(),
            fractal=float(params.get("fractal", 0.0)),
            along_normal=float(params.get("along_normal", 0.0)),
            seed=int(params.get("seed", 0)),
            quad_corner_type=str(params.get("quad_corner_type", "STRAIGHT_CUT")).upper(),
            use_grid_fill=bool(params.get("use_grid_fill", True)),
            use_only_quads=bool(params.get("use_only_quads", False)),
            use_single_edge=bool(params.get("use_single_edge", False)),
        )
        new_verts = [e for e in result["geom_inner"] if isinstance(e, bmesh.types.BMVert)]
        _ensure_tables(bm)
        after = _counts(bm)
        return {"name": obj.name, "before": before, "after": after,
                "created": _delta(before, after), "subdivided_edges": len(edges),
                "cuts": cuts, "smoothness": smoothness, "new_inner_vertices": len(new_verts)}


def _edge_ring(seed) -> list:
    """Walk the quad edge ring containing ``seed``.

    An edge ring is the chain of edges reached by hopping to the opposite edge of
    each quad, i.e. the set of edges a loop cut would slice through. Stops at
    triangles, n-gons, non-manifold junctions and mesh boundaries.
    """
    ring = [seed]
    seen = {seed}
    for start in list(seed.link_loops)[:2]:
        loop = start
        while True:
            if len(loop.face.verts) != 4:
                break
            opposite = loop.link_loop_next.link_loop_next
            edge = opposite.edge
            if edge in seen:
                break
            seen.add(edge)
            ring.append(edge)
            across = opposite.link_loop_radial_next
            if across is opposite:
                break  # boundary edge: the ring ends here
            loop = across
    return ring


@command("mesh.edge_ring_subdivide", mutates=True)
def edge_ring_subdivide(params: dict) -> dict:
    """Loop-cut equivalent: subdivide whole edge rings grown from seed edges."""
    obj = _mesh_object(params)
    cuts = max(1, int(params.get("cuts", 1)))
    smoothness = float(params.get("smoothness", 0.0))
    seeds = params.get("seed_edges")

    with _bmesh(obj) as bm:
        before = _counts(bm)
        if seeds is None:
            seed_edges = _require_selection(
                bm, "EDGE", "edge_ring_subdivide (pass seed_edges, or select an edge)")
        else:
            total = len(bm.edges)
            seed_edges = []
            for i in seeds:
                i = int(i)
                if not 0 <= i < total:
                    raise IndexError(f"edge index {i} out of range (0..{total - 1})")
                seed_edges.append(bm.edges[i])

        ring = {}
        for seed in seed_edges:
            for edge in _edge_ring(seed):
                ring[edge.index] = edge
        ring_edges = list(ring.values())

        result = bmesh.ops.subdivide_edges(
            bm,
            edges=ring_edges,
            cuts=cuts,
            smooth=smoothness,
            smooth_falloff=str(params.get("smooth_falloff", "SMOOTH")).upper(),
            use_grid_fill=True,
            quad_corner_type=str(params.get("quad_corner_type", "STRAIGHT_CUT")).upper(),
        )
        new_edges = [e for e in result["geom_inner"] if isinstance(e, bmesh.types.BMEdge)]

        for edge in bm.edges:
            edge.select_set(False)
        for edge in new_edges:
            edge.select_set(True)
        bm.select_mode = {"EDGE"}
        bm.select_flush_mode()

        ring_idx = sorted(ring)
        limit = _limit_of(params)
        _ensure_tables(bm)
        after = _counts(bm)
        return {"name": obj.name, "before": before, "after": after,
                "created": _delta(before, after), "seed_edges": len(seed_edges),
                "ring_edges": len(ring_edges), "cuts": cuts,
                "new_loop_edges": len(new_edges),
                "ring_edge_indices": ring_idx[:limit],
                "truncated": len(ring_idx) > limit}


@command("mesh.merge_by_distance", mutates=True)
def merge_by_distance(params: dict) -> dict:
    """Weld vertices closer together than ``threshold`` (Blender's Merge by Distance)."""
    obj = _mesh_object(params)
    threshold = float(params.get("threshold", 0.0001))

    with _bmesh(obj) as bm:
        before = _counts(bm)
        verts, used_selection = _selection_or_all(bm, "VERT")
        bmesh.ops.remove_doubles(
            bm, verts=list(verts), dist=threshold,
            use_connected=bool(params.get("use_connected", False)),
        )
        _ensure_tables(bm)
        after = _counts(bm)
        return {"name": obj.name, "before": before, "after": after,
                "removed": _delta(after, before), "threshold": threshold,
                "considered_vertices": len(verts), "used_selection": used_selection}


@command("mesh.delete_geometry", mutates=True)
def delete_geometry(params: dict) -> dict:
    """Delete the selected geometry with a chosen Blender delete mode."""
    obj = _mesh_object(params)
    domain = str(params.get("domain", "VERT")).upper()
    context = _DELETE_CONTEXT.get(domain)
    if context is None:
        raise ValueError(
            f"domain must be one of {sorted(_DELETE_CONTEXT)}, got {domain!r}"
        )

    with _bmesh(obj) as bm:
        before = _counts(bm)
        if context == "VERTS":
            geom = _require_selection(bm, "VERT", "delete_geometry(domain='VERT')")
        elif context in {"EDGES", "EDGES_FACES"}:
            geom = _require_selection(bm, "EDGE", f"delete_geometry(domain={domain!r})")
        else:
            geom = _require_selection(bm, "FACE", f"delete_geometry(domain={domain!r})")

        bmesh.ops.delete(bm, geom=list(geom), context=context)
        _ensure_tables(bm)
        after = _counts(bm)
        return {"name": obj.name, "before": before, "after": after,
                "removed": _delta(after, before), "domain": domain,
                "bmesh_context": context, "deleted_elements": len(geom)}


@command("mesh.fill_holes", mutates=True)
def fill_holes(params: dict) -> dict:
    """Cap open boundary loops with faces, skipping holes above ``sides`` edges."""
    obj = _mesh_object(params)
    sides = int(params.get("sides", 0))

    with _bmesh(obj) as bm:
        before = _counts(bm)
        selected = _selected(bm, "EDGE")
        used_selection = bool(selected)
        source = selected if selected else list(bm.edges)
        boundary = [e for e in source if e.is_boundary]
        if not boundary:
            return {"name": obj.name, "before": before, "after": before,
                    "created": _delta(before, before), "filled_faces": 0,
                    "boundary_edges": 0, "used_selection": used_selection,
                    "note": "no open boundary edges found — the mesh is already closed here"}

        result = bmesh.ops.holes_fill(bm, edges=boundary, sides=sides)
        for face in bm.faces:
            face.select_set(False)
        for face in result["faces"]:
            face.select_set(True)
        bm.select_mode = {"FACE"}
        bm.select_flush_mode()

        _ensure_tables(bm)
        after = _counts(bm)
        return {"name": obj.name, "before": before, "after": after,
                "created": _delta(before, after), "filled_faces": len(result["faces"]),
                "boundary_edges": len(boundary), "sides": sides,
                "used_selection": used_selection}


@command("mesh.bridge_edge_loops", mutates=True)
def bridge_edge_loops(params: dict) -> dict:
    """Build a face bridge between the selected open edge loops."""
    obj = _mesh_object(params)

    with _bmesh(obj) as bm:
        before = _counts(bm)
        edges = _require_selection(bm, "EDGE", "bridge_edge_loops")
        result = bmesh.ops.bridge_loops(
            bm,
            edges=list(edges),
            use_pairs=bool(params.get("use_pairs", False)),
            use_cyclic=bool(params.get("use_cyclic", False)),
            use_merge=bool(params.get("use_merge", False)),
            merge_factor=float(params.get("merge_factor", 0.5)),
            twist_offset=int(params.get("twist_offset", 0)),
        )
        if not result["faces"]:
            raise RuntimeError(
                "bridge_edge_loops produced no faces. It needs two (or more) distinct "
                "open edge loops selected — check the selection with mesh.stats and "
                "verify the loops are boundary edges, not interior ones."
            )

        for face in bm.faces:
            face.select_set(False)
        for face in result["faces"]:
            face.select_set(True)
        bm.select_mode = {"FACE"}
        bm.select_flush_mode()

        _ensure_tables(bm)
        after = _counts(bm)
        return {"name": obj.name, "before": before, "after": after,
                "created": _delta(before, after), "bridged_edges": len(edges),
                "new_faces": len(result["faces"])}


@command("mesh.symmetrize", mutates=True)
def symmetrize(params: dict) -> dict:
    """Mirror one half of the mesh onto the other across a local-space axis plane."""
    obj = _mesh_object(params)
    raw = str(params.get("axis", "-X")).upper()
    direction = _SYMMETRY_AXIS.get(raw)
    if direction is None:
        raise ValueError(f"axis must be one of {sorted(_SYMMETRY_AXIS)}, got {raw!r}")
    threshold = float(params.get("threshold", 0.0001))

    with _bmesh(obj) as bm:
        before = _counts(bm)
        selected = (_selected(bm, "VERT") or _selected(bm, "EDGE") or _selected(bm, "FACE"))
        used_selection = bool(selected)
        if used_selection:
            geom = list(_selected(bm, "VERT")) + list(_selected(bm, "EDGE")) \
                + list(_selected(bm, "FACE"))
        else:
            geom = list(bm.verts) + list(bm.edges) + list(bm.faces)

        bmesh.ops.symmetrize(bm, input=geom, direction=direction, dist=threshold)
        _ensure_tables(bm)
        after = _counts(bm)
        return {"name": obj.name, "before": before, "after": after,
                "delta": _delta(before, after), "axis": raw, "bmesh_direction": direction,
                "threshold": threshold, "used_selection": used_selection}


@command("mesh.recalculate_normals", mutates=True)
def recalculate_normals(params: dict) -> dict:
    """Make face winding consistent, pointing outward (or inward)."""
    obj = _mesh_object(params)
    inside = bool(params.get("inside", False))

    with _bmesh(obj) as bm:
        faces, used_selection = _selection_or_all(bm, "FACE")
        faces = list(faces)
        volume_before = bm.calc_volume(signed=True)
        bmesh.ops.recalc_face_normals(bm, faces=faces)
        if inside:
            bmesh.ops.reverse_faces(bm, faces=faces, flip_multires=False)
        bm.normal_update()
        return {"name": obj.name, "faces": len(faces), "inside": inside,
                "used_selection": used_selection,
                "signed_volume_before": volume_before,
                "signed_volume_after": bm.calc_volume(signed=True)}


@command("mesh.flip_normals", mutates=True)
def flip_normals(params: dict) -> dict:
    """Reverse the winding of the selected faces (or every face)."""
    obj = _mesh_object(params)

    with _bmesh(obj) as bm:
        faces, used_selection = _selection_or_all(bm, "FACE")
        faces = list(faces)
        bmesh.ops.reverse_faces(bm, faces=faces, flip_multires=False)
        bm.normal_update()
        return {"name": obj.name, "faces": len(faces), "used_selection": used_selection,
                "signed_volume_after": bm.calc_volume(signed=True)}


@command("mesh.triangulate", mutates=True)
def triangulate(params: dict) -> dict:
    """Convert the selected quads and n-gons into triangles."""
    obj = _mesh_object(params)
    quad_raw = str(params.get("quad_method", "BEAUTY")).upper()
    ngon_raw = str(params.get("ngon_method", "BEAUTY")).upper()
    quad_method = _QUAD_METHOD.get(quad_raw)
    ngon_method = _NGON_METHOD.get(ngon_raw)
    if quad_method is None:
        raise ValueError(f"quad_method must be one of {sorted(_QUAD_METHOD)}, got {quad_raw!r}")
    if ngon_method is None:
        raise ValueError(f"ngon_method must be one of {sorted(_NGON_METHOD)}, got {ngon_raw!r}")

    with _bmesh(obj) as bm:
        before = _counts(bm)
        faces, used_selection = _selection_or_all(bm, "FACE")
        non_tris = [f for f in faces if len(f.verts) > 3]
        if non_tris:
            bmesh.ops.triangulate(bm, faces=non_tris, quad_method=quad_method,
                                  ngon_method=ngon_method)
        _ensure_tables(bm)
        after = _counts(bm)
        return {"name": obj.name, "before": before, "after": after,
                "created": _delta(before, after), "converted_faces": len(non_tris),
                "quad_method": quad_method, "ngon_method": ngon_method,
                "used_selection": used_selection}


# ---------------------------------------------------------------------------
# shading
# ---------------------------------------------------------------------------

def _set_face_smooth(obj, params: dict, smooth: bool) -> dict:
    # Mirrors Object ▸ Shade Smooth/Flat, which is whole-object. Honouring a
    # stale selection by default would silently shade only part of the mesh.
    selected_only = bool(params.get("selected_only", False))
    clear_sharp_edges = bool(params.get("clear_sharp_edges", False))
    with _bmesh(obj) as bm:
        faces = _require_selection(bm, "FACE", f"shade_{'smooth' if smooth else 'flat'}"
                                   "(selected_only=True)") if selected_only else list(bm.faces)
        for face in faces:
            face.smooth = smooth
        cleared = 0
        if clear_sharp_edges:
            for edge in bm.edges:
                if not edge.smooth:
                    edge.smooth = True
                    cleared += 1
        return {"name": obj.name, "faces": len(faces), "total_faces": len(bm.faces),
                "smooth": smooth, "selected_only": selected_only,
                "sharp_edges_cleared": cleared}


@command("mesh.shade_smooth", mutates=True)
def shade_smooth(params: dict) -> dict:
    """Mark every face (or just the selected ones) as smooth-shaded."""
    return _set_face_smooth(_mesh_object(params), params, True)


@command("mesh.shade_flat", mutates=True)
def shade_flat(params: dict) -> dict:
    """Mark every face (or just the selected ones) as flat-shaded."""
    return _set_face_smooth(_mesh_object(params), params, False)


@command("mesh.shade_auto_smooth", mutates=True)
def shade_auto_smooth(params: dict) -> dict:
    """Smooth-shade only where the face angle is below ``angle`` radians.

    ``Mesh.use_auto_smooth`` no longer exists in Blender 5.2 — the two live
    replacements are ``object.shade_smooth_by_angle`` (bakes a ``sharp_edge``
    attribute, destructive) and ``object.shade_auto_smooth`` (adds the
    non-destructive "Smooth by Angle" geometry-nodes modifier).
    """
    obj = _mesh_object(params)
    _require_object_mode(obj, "shade_auto_smooth")
    angle = float(params.get("angle", math.radians(30.0)))
    use_modifier = bool(params.get("use_modifier", False))
    keep_sharp_edges = bool(params.get("keep_sharp_edges", True))

    override = dict(object=obj, active_object=obj,
                    selected_objects=[obj], selected_editable_objects=[obj])
    with bpy.context.temp_override(**override):
        if use_modifier:
            bpy.ops.object.shade_auto_smooth(use_auto_smooth=True, angle=angle)
        else:
            bpy.ops.object.shade_smooth_by_angle(angle=angle,
                                                 keep_sharp_edges=keep_sharp_edges)

    return {
        "name": obj.name,
        "angle_radians": angle,
        "angle_degrees": math.degrees(angle),
        "method": "shade_auto_smooth (modifier)" if use_modifier
        else "shade_smooth_by_angle (baked sharp_edge attribute)",
        "modifiers": [{"name": m.name, "type": m.type} for m in obj.modifiers],
        "smooth_faces": sum(1 for p in obj.data.polygons if p.use_smooth),
        "total_faces": len(obj.data.polygons),
    }


# ---------------------------------------------------------------------------
# whole-object operations
# ---------------------------------------------------------------------------

@command("mesh.decimate", mutates=True)
def decimate(params: dict) -> dict:
    """Add a Decimate modifier, apply it immediately and report the reduction."""
    obj = _mesh_object(params)
    _require_object_mode(obj, "decimate")
    decimate_type = str(params.get("decimate_type", "COLLAPSE")).upper()
    if decimate_type not in {"COLLAPSE", "UNSUBDIV", "DISSOLVE"}:
        raise ValueError(
            f"decimate_type must be COLLAPSE, UNSUBDIV or DISSOLVE, got {decimate_type!r}")
    ratio = float(params.get("ratio", 0.5))

    before = {"vertices": len(obj.data.vertices), "edges": len(obj.data.edges),
              "faces": len(obj.data.polygons)}

    modifier = obj.modifiers.new(name="agent_decimate", type="DECIMATE")
    try:
        modifier.decimate_type = decimate_type
        if decimate_type == "COLLAPSE":
            modifier.ratio = ratio
            modifier.use_collapse_triangulate = bool(params.get("use_collapse_triangulate", False))
            group = params.get("vertex_group")
            if group:
                if group not in obj.vertex_groups:
                    raise KeyError(f"{obj.name!r} has no vertex group named {group!r}")
                modifier.vertex_group = group
                modifier.invert_vertex_group = bool(params.get("invert_vertex_group", False))
                modifier.vertex_group_factor = float(params.get("vertex_group_factor", 1.0))
        elif decimate_type == "UNSUBDIV":
            modifier.iterations = int(params.get("iterations", 1))
        else:
            modifier.angle_limit = float(params.get("angle_limit", math.radians(5.0)))
            modifier.use_dissolve_boundaries = bool(params.get("use_dissolve_boundaries", False))

        if params.get("symmetry_axis") is not None:
            modifier.use_symmetry = True
            modifier.symmetry_axis = str(params["symmetry_axis"]).upper()

        with bpy.context.temp_override(object=obj, active_object=obj,
                                       selected_objects=[obj],
                                       selected_editable_objects=[obj]):
            bpy.ops.object.modifier_apply(modifier=modifier.name)
    except Exception:
        # Never leave a half-configured modifier stuck on the object.
        if modifier.name in obj.modifiers:
            obj.modifiers.remove(modifier)
        raise

    after = {"vertices": len(obj.data.vertices), "edges": len(obj.data.edges),
             "faces": len(obj.data.polygons)}
    achieved = (after["faces"] / before["faces"]) if before["faces"] else 0.0
    return {"name": obj.name, "before": before, "after": after,
            "removed": _delta(after, before), "decimate_type": decimate_type,
            "requested_ratio": ratio if decimate_type == "COLLAPSE" else None,
            "achieved_face_ratio": achieved}


_FALLOFF = {
    "SMOOTH": lambda f: 3.0 * f * f - 2.0 * f * f * f,
    "SPHERE": lambda f: math.sqrt(max(0.0, 2.0 * f - f * f)),
    "ROOT": lambda f: math.sqrt(f),
    "INVERSE_SQUARE": lambda f: f * (2.0 - f),
    "SHARP": lambda f: f * f,
    "LINEAR": lambda f: f,
    "CONSTANT": lambda f: 1.0,
}


@command("mesh.proportional_transform", mutates=True)
def proportional_transform(params: dict) -> dict:
    """Translate the selected vertices, dragging nearby ones along with a falloff."""
    obj = _mesh_object(params)
    translate = _to_local_offset(obj, _vec3(params.get("translate")),
                                 params.get("space", "OBJECT"))
    size = float(params.get("proportional_size", 1.0))
    if size <= 0.0:
        raise ValueError("proportional_size must be greater than 0")
    falloff = str(params.get("falloff", "SMOOTH")).upper()
    if falloff == "RANDOM":
        rng = random.Random(int(params.get("seed", 0)))
        curve = lambda f: rng.random() * f  # noqa: E731
    else:
        curve = _FALLOFF.get(falloff)
        if curve is None:
            raise ValueError(
                f"falloff must be one of {sorted(_FALLOFF) + ['RANDOM']}, got {falloff!r}")

    with _bmesh(obj) as bm:
        selected = _require_selection(bm, "VERT", "proportional_transform")
        chosen = set(selected)

        tree = KDTree(len(selected))
        for i, vert in enumerate(selected):
            tree.insert(vert.co, i)
        tree.balance()

        moved_full = 0
        moved_partial = 0
        for vert in bm.verts:
            if vert in chosen:
                vert.co += translate
                moved_full += 1
                continue
            _, _, distance = tree.find(vert.co)
            if distance is None or distance >= size:
                continue
            weight = curve(1.0 - distance / size)
            if weight <= 0.0:
                continue
            vert.co += translate * weight
            moved_partial += 1

        bm.normal_update()
        return {"name": obj.name, "translate_local": list(translate),
                "proportional_size": size, "falloff": falloff,
                "moved_selected": moved_full, "moved_by_falloff": moved_partial,
                "total_vertices": len(bm.verts)}

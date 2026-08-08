"""Materials, shader node graphs, viewport shading and texture baking."""

from __future__ import annotations

import contextlib
import difflib
import math
import os
import tempfile

import bpy

from .. import ctx
from ..registry import command

#: Principled BSDF sockets renamed in 4.x and still current in 5.2 — see
#: docs/BLENDER_5X_API_NOTES.md. Kept as a lookup so a caller using the old 3.x
#: name gets a pointer instead of a bare KeyError.
_RENAMED_SOCKETS = {
    "Emission": "Emission Color",
    "Specular": "Specular IOR Level",
    "Specular Tint Value": "Specular Tint",
    "Subsurface": "Subsurface Weight",
    "Transmission": "Transmission Weight",
    "Clearcoat": "Coat Weight",
    "Clearcoat Roughness": "Coat Roughness",
    "Sheen": "Sheen Weight",
    "Emission Strength": "Emission Strength",
    "Transmission Roughness": None,  # dropped entirely in 4.x
}

# Caller-owned identity for node graphs. Blender's display names are not stable:
# adding a second "Noise Texture" silently renames it to "Noise Texture.001".
# Keeping the agent id on the node makes large graph builds retry-safe and lets
# every existing single-node command address the same node later by that id.
_AGENT_ID_PROP = "_blender_agent_id"


# ---------------------------------------------------------------------------
# lookup helpers
# ---------------------------------------------------------------------------

def _material(name: str):
    mat = bpy.data.materials.get(name)
    if mat is None:
        raise KeyError(
            f"no material named {name!r}; call shading.material_list to see what exists"
        )
    return mat


def _tree(mat):
    """Node tree for ``mat``, switching the material to nodes if it somehow isn't."""
    if mat.node_tree is None:
        mat.use_nodes = True
    if mat.node_tree is None:
        raise RuntimeError(f"material {mat.name!r} has no shader node tree")
    return mat.node_tree


def _node(tree, name: str):
    node = tree.nodes.get(name)
    if node is not None:
        return node

    matches = [n for n in tree.nodes if n.get(_AGENT_ID_PROP) == name]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise RuntimeError(
            f"agent node id {name!r} is duplicated by {[n.name for n in matches]}; "
            "give those nodes unique ids before editing the graph"
        )
    if not matches:
        raise KeyError(
            f"node name or agent id {name!r} not in this material; nodes are "
            f"{[n.name for n in tree.nodes]} and agent ids are "
            f"{[n.get(_AGENT_ID_PROP) for n in tree.nodes if n.get(_AGENT_ID_PROP)]}"
        )


def _principled(tree):
    for node in tree.nodes:
        if node.type == "BSDF_PRINCIPLED":
            return node
    return None


def _socket(node, key, outputs: bool):
    """Resolve a socket by name, identifier or integer index."""
    sockets = node.outputs if outputs else node.inputs
    side = "output" if outputs else "input"

    if isinstance(key, bool):
        raise TypeError(f"{side} socket key must be a name or index, got {key!r}")
    if isinstance(key, int):
        if not 0 <= key < len(sockets):
            raise IndexError(
                f"node {node.name!r} has {len(sockets)} {side} sockets, "
                f"index {key} is out of range"
            )
        return sockets[key]

    for sock in sockets:
        if sock.name == key or sock.identifier == key:
            return sock

    renamed = _RENAMED_SOCKETS.get(key)
    hint = ""
    if renamed:
        hint = f" (that socket was renamed to {renamed!r} in Blender 4.x)"
    elif key in _RENAMED_SOCKETS:
        hint = " (that socket was removed in Blender 4.x)"
    raise KeyError(
        f"node {node.name!r} has no {side} socket {key!r}{hint}; available: "
        f"{[s.name for s in sockets]}. Duplicate names exist on some nodes — "
        f"pass an integer index to disambiguate."
    )


def _socket_value(sock):
    """JSON-safe view of a socket's ``default_value`` (None for shader sockets)."""
    value = getattr(sock, "default_value", None)
    if value is None:
        return None
    if hasattr(value, "__len__") and not isinstance(value, str):
        return list(value)
    if hasattr(value, "name"):  # pointer socket (object, material, ...)
        return value.name
    return value


def _node_brief(node) -> dict:
    return {
        "name": node.name,
        "agent_id": node.get(_AGENT_ID_PROP),
        "bl_idname": node.bl_idname,
        "type": node.type,
        "label": node.label,
        "location": list(node.location),
        "mute": node.mute,
        "inputs": [
            {"index": i, "name": s.name, "type": s.type,
             "is_linked": s.is_linked, "default_value": _socket_value(s)}
            for i, s in enumerate(node.inputs)
        ],
        "outputs": [
            {"index": i, "name": s.name, "type": s.type, "is_linked": s.is_linked}
            for i, s in enumerate(node.outputs)
        ],
    }


def _image_brief(img) -> dict:
    """Compact, JSON-safe description of an image datablock."""
    packed_files = getattr(img, "packed_files", ())
    return {
        "name": img.name,
        "size": list(img.size),
        "source": img.source,
        "filepath": img.filepath,
        "colorspace": img.colorspace_settings.name,
        "alpha_mode": img.alpha_mode,
        "is_float": img.is_float,
        # Blender 5.2 still accepts is_data when creating an image but no longer
        # exposes Image.is_data. Non-Color is the durable observable state.
        "is_data": img.colorspace_settings.name == "Non-Color",
        "is_dirty": img.is_dirty,
        "packed": bool(getattr(img, "packed_file", None)) or bool(len(packed_files)),
        "users": img.users,
    }


def _validate_shader_node_type(node_type: str) -> None:
    if not isinstance(node_type, str) or not node_type:
        raise TypeError("each node type must be a non-empty Blender bl_idname string")
    cls = getattr(bpy.types, node_type, None)
    if cls is not None:
        try:
            if issubclass(cls, bpy.types.ShaderNode) or node_type in {
                "NodeFrame", "NodeReroute"
            }:
                return
        except TypeError:
            pass
    known = [n for n in dir(bpy.types) if n.startswith("ShaderNode")]
    known.extend(["NodeFrame", "NodeReroute"])
    close = difflib.get_close_matches(node_type, known, n=5, cutoff=0.4)
    raise ValueError(
        f"{node_type!r} is not a shader node type in this Blender build. "
        "Use a bl_idname such as 'ShaderNodeTexNoise' or "
        "'ShaderNodeBsdfPrincipled'."
        + (f" Did you mean: {close}?" if close else "")
    )


def _validate_color(color, *, label: str) -> list[float]:
    try:
        result = [float(component) for component in color]
    except (TypeError, ValueError):
        raise TypeError(f"{label} must be an RGB or RGBA number list") from None
    if len(result) == 3:
        result.append(1.0)
    if len(result) != 4 or not all(math.isfinite(value) for value in result):
        raise ValueError(f"{label} must contain 3 or 4 finite numbers")
    return result


def _apply_color_ramp(node, spec: dict) -> dict:
    """Replace a Color Ramp node's stops and configure interpolation."""
    ramp = getattr(node, "color_ramp", None)
    if ramp is None:
        raise TypeError(
            f"node {node.name!r} ({node.bl_idname}) has no color_ramp; "
            "use ShaderNodeValToRGB for a Color Ramp"
        )
    if not isinstance(spec, dict):
        raise TypeError("color_ramp must be an object with an elements list")

    elements = spec.get("elements")
    if not isinstance(elements, list) or len(elements) < 2:
        raise ValueError("color_ramp.elements must contain at least two stops")

    wanted = []
    for index, item in enumerate(elements):
        if not isinstance(item, dict) or "position" not in item or "color" not in item:
            raise ValueError(
                f"color_ramp.elements[{index}] needs position and color"
            )
        position = float(item["position"])
        if not math.isfinite(position) or not 0.0 <= position <= 1.0:
            raise ValueError(
                f"color_ramp.elements[{index}].position must be between 0 and 1"
            )
        wanted.append((position, _validate_color(
            item["color"], label=f"color_ramp.elements[{index}].color"
        )))
    wanted.sort(key=lambda item: item[0])

    for attr in ("color_mode", "hue_interpolation", "interpolation"):
        if attr in spec:
            try:
                setattr(ramp, attr, str(spec[attr]).upper())
            except TypeError:
                prop = ramp.bl_rna.properties[attr]
                valid = [item.identifier for item in prop.enum_items]
                raise ValueError(
                    f"color_ramp.{attr}={spec[attr]!r} is invalid; valid: {valid}"
                ) from None

    while len(ramp.elements) > 2:
        ramp.elements.remove(ramp.elements[-1])
    ramp.elements[0].position, ramp.elements[0].color = wanted[0]
    ramp.elements[1].position, ramp.elements[1].color = wanted[-1]
    for position, color in wanted[1:-1]:
        element = ramp.elements.new(position)
        element.color = color

    return {
        "color_mode": ramp.color_mode,
        "hue_interpolation": ramp.hue_interpolation,
        "interpolation": ramp.interpolation,
        "elements": [
            {"position": element.position, "color": list(element.color)}
            for element in ramp.elements
        ],
    }


def _apply_prop(node, prop: str, value) -> dict:
    """Set a node attribute, falling back to an input socket's ``default_value``.

    Returns a small record of what was actually written so the caller can tell
    the agent which of the two interpretations applied.
    """
    # `bl_rna.properties` is a bpy_prop_collection keyed by string; calling
    # .get() with an int raises SystemError rather than returning None. An
    # integer `prop` is always a socket index anyway — nodes like ShaderNodeMix
    # have several sockets sharing a name, so an index is the only way to
    # address them unambiguously.
    rna = node.bl_rna.properties.get(prop) if isinstance(prop, str) else None
    if rna is not None and not rna.is_readonly:
        if rna.type == "POINTER" and isinstance(value, str):
            fixed = rna.fixed_type.identifier
            collection = {"Image": bpy.data.images, "Material": bpy.data.materials,
                          "Object": bpy.data.objects, "Texture": bpy.data.textures,
                          "NodeTree": bpy.data.node_groups}.get(fixed)
            if collection is None:
                raise TypeError(
                    f"{prop!r} points at a {fixed}; a name string cannot be resolved "
                    f"for that type"
                )
            target = collection.get(value)
            if target is None:
                raise KeyError(f"no {fixed} datablock named {value!r}")
            setattr(node, prop, target)
        elif rna.type in {"FLOAT", "INT", "BOOLEAN"} and getattr(rna, "array_length", 0):
            setattr(node, prop, tuple(value))
        else:
            setattr(node, prop, value)
        return {"target": "property", "prop": prop, "value": value}

    sock = _socket(node, prop, outputs=False)
    if not hasattr(sock, "default_value"):
        raise TypeError(
            f"input socket {sock.name!r} carries a shader, not a value — link a node "
            f"into it with shading.link_nodes instead"
        )
    current = sock.default_value
    if hasattr(current, "__len__") and not isinstance(current, str):
        want = len(current)
        seq = list(value) if hasattr(value, "__len__") else [value] * want
        if len(seq) == 3 and want == 4:
            seq = seq + [1.0]  # RGB → RGBA, the mistake every agent makes once
        if len(seq) != want:
            raise ValueError(
                f"socket {sock.name!r} needs {want} components, got {len(seq)}"
            )
        sock.default_value = seq
    else:
        sock.default_value = value
    index = next((i for i, s in enumerate(node.inputs) if s == sock), None)
    duplicates = [i for i, s in enumerate(node.inputs) if s.name == sock.name]
    record = {"target": "socket", "socket": sock.name, "socket_index": index,
              "linked": sock.is_linked, "value": _socket_value(sock)}
    if isinstance(prop, str) and len(duplicates) > 1:
        # ShaderNodeMix and friends repeat socket names per data type; a name
        # match silently picks the first, which is rarely the one meant.
        record["ambiguous"] = (
            f"{len(duplicates)} input sockets are named {sock.name!r} "
            f"(indices {duplicates}); wrote index {index}. Pass the integer "
            f"index instead if you meant a different one."
        )
    return record


def _stack_left(tree, node, anchor, column: int) -> None:
    """Park ``node`` ``column`` columns left of ``anchor``, stacked by row."""
    rows = sum(1 for n in tree.nodes if abs(n.location.x - (anchor.location.x - 300.0 * column)) < 1.0)
    node.location = (anchor.location.x - 300.0 * column, anchor.location.y - 320.0 * rows)


def _load_image(path: str, colorspace=None, name=None):
    resolved = bpy.path.abspath(path)
    if not os.path.isfile(resolved):
        raise FileNotFoundError(f"no image file at {resolved!r} (given {path!r})")
    img = bpy.data.images.load(resolved, check_existing=True)
    if name:
        img.name = name
    if colorspace:
        valid = [i.identifier for i in
                 img.colorspace_settings.bl_rna.properties["name"].enum_items]
        if colorspace not in valid:
            raise ValueError(
                f"colorspace {colorspace!r} unknown; the ones you want are 'sRGB' for "
                f"colour maps and 'Non-Color' for data maps. Full list: {valid}"
            )
        img.colorspace_settings.name = colorspace
    return img


def _hook_texture(tree, bsdf, img, hook_to: str, colorspace=None) -> dict:
    """Wire an Image Texture into ``hook_to`` on ``bsdf``, inserting a Normal Map node.

    Colorspace defaults from the destination socket: RGBA sockets get ``sRGB``,
    everything else (roughness, metallic, normal, ...) gets ``Non-Color``, which is
    what stops Blender gamma-correcting data maps.
    """
    tex = tree.nodes.new("ShaderNodeTexImage")
    tex.image = img
    tex.label = hook_to

    target = _socket(bsdf, hook_to, outputs=False)
    if colorspace is None:
        img.colorspace_settings.name = "sRGB" if target.type == "RGBA" else "Non-Color"

    created = [tex.name]
    if hook_to == "Normal":
        nmap = tree.nodes.new("ShaderNodeNormalMap")
        _stack_left(tree, nmap, bsdf, 1)
        _stack_left(tree, tex, bsdf, 2)
        tree.links.new(tex.outputs["Color"], nmap.inputs["Color"])
        tree.links.new(nmap.outputs["Normal"], target)
        created.append(nmap.name)
    else:
        _stack_left(tree, tex, bsdf, 1)
        tree.links.new(tex.outputs["Color"], target)

    return {"image_node": tex.name, "created_nodes": created,
            "colorspace": img.colorspace_settings.name, "hooked_to": target.name}


# ---------------------------------------------------------------------------
# materials
# ---------------------------------------------------------------------------

@command("shading.create_material", mutates=True)
def create_material(params: dict) -> dict:
    """Create a Principled BSDF material from PBR values."""
    name = params.get("name") or "Material"
    mat = bpy.data.materials.new(name)  # 5.2 materials already ship a node tree
    tree = _tree(mat)
    bsdf = _principled(tree)
    if bsdf is None:
        bsdf = tree.nodes.new("ShaderNodeBsdfPrincipled")
        output = next((n for n in tree.nodes if n.type == "OUTPUT_MATERIAL"), None)
        if output is None:
            output = tree.nodes.new("ShaderNodeOutputMaterial")
        tree.links.new(bsdf.outputs["BSDF"], output.inputs["Surface"])

    wanted = {
        "Base Color": params.get("base_color"),
        "Metallic": params.get("metallic"),
        "Roughness": params.get("roughness"),
        "IOR": params.get("ior"),
        "Alpha": params.get("alpha"),
        "Emission Color": params.get("emission_color"),
        "Emission Strength": params.get("emission_strength"),
    }

    applied, missing = {}, []
    for socket_name, value in wanted.items():
        if value is None:
            continue
        if socket_name not in bsdf.inputs:
            missing.append(socket_name)
            continue
        _apply_prop(bsdf, socket_name, value)
        applied[socket_name] = _socket_value(bsdf.inputs[socket_name])

    alpha = params.get("alpha")
    if alpha is not None and float(alpha) < 1.0:
        # EEVEE Next drives transparency off surface_render_method; blend_method is
        # the legacy field and is still present, so set both where they exist.
        with contextlib.suppress(Exception):
            mat.surface_render_method = "BLENDED"
        with contextlib.suppress(Exception):
            mat.blend_method = "BLEND"

    normal = {}
    normal_map = params.get("normal_map")
    if normal_map:
        img = _load_image(normal_map, colorspace="Non-Color")
        normal = _hook_texture(tree, bsdf, img, "Normal", colorspace="Non-Color")

    return {
        "material": mat.name,
        "principled_node": bsdf.name,
        "applied": applied,
        "missing_sockets": missing,
        "normal_map": normal or None,
        "available_inputs": [s.name for s in bsdf.inputs],
    }


@command("shading.assign_material", mutates=True)
def assign_material(params: dict) -> dict:
    """Put a material into an object's slot, optionally only on selected faces."""
    obj_name = params["object"]
    obj = bpy.data.objects.get(obj_name)
    if obj is None:
        raise KeyError(f"no object named {obj_name!r}")
    if obj.data is None or not hasattr(obj.data, "materials"):
        raise TypeError(f"{obj_name!r} is a {obj.type} and cannot hold materials")

    mat = _material(params["material"])
    slot = params.get("slot")
    to_faces = bool(params.get("to_selected_faces", False))
    slots = obj.data.materials

    if slot is None:
        existing = next((i for i, m in enumerate(slots) if m is not None and m.name == mat.name), None)
        if existing is not None:
            slot = existing
        elif len(slots) and not to_faces:
            # Whole-object assignment mirrors the Properties editor: overwrite the
            # active slot rather than adding one no face points at.
            slot = min(obj.active_material_index, len(slots) - 1)
            slots[slot] = mat
        else:
            # Per-face assignment must not disturb the faces already using the
            # active slot, so give the material a slot of its own.
            slots.append(mat)
            slot = len(slots) - 1
    else:
        slot = int(slot)
        if slot < 0:
            raise ValueError(f"slot must be >= 0, got {slot}")
        while len(slots) <= slot:
            slots.append(None)
        slots[slot] = mat

    faces = 0
    if to_faces:
        if obj.type != "MESH":
            raise TypeError(f"to_selected_faces needs a MESH, {obj_name!r} is {obj.type}")
        if obj.mode == "EDIT":
            import bmesh

            bm = bmesh.from_edit_mesh(obj.data)
            for face in bm.faces:
                if face.select:
                    face.material_index = slot
                    faces += 1
            bmesh.update_edit_mesh(obj.data)
        else:
            for poly in obj.data.polygons:
                if poly.select:
                    poly.material_index = slot
                    faces += 1
        if faces == 0:
            raise RuntimeError(
                "no faces are selected, so nothing was assigned. Select faces in Edit "
                "Mode first, or call again with to_selected_faces=false to give the "
                "whole object this material."
            )
    else:
        obj.active_material_index = slot

    return {
        "object": obj.name,
        "material": mat.name,
        "slot": slot,
        "slot_count": len(slots),
        "slots": [m.name if m else None for m in slots],
        "faces_assigned": faces if to_faces else None,
        "mode": obj.mode,
    }


@command("shading.material_list")
def material_list(params: dict) -> dict:
    """List materials in the blend file, or just one object's slots."""
    limit = int(params.get("limit", 1000))
    obj_name = params.get("object")

    if obj_name:
        obj = bpy.data.objects.get(obj_name)
        if obj is None:
            raise KeyError(f"no object named {obj_name!r}")
        slots = getattr(obj.data, "materials", None)
        if slots is None:
            raise TypeError(f"{obj_name!r} is a {obj.type} and cannot hold materials")
        mats = [m for m in slots]
    else:
        mats = list(bpy.data.materials)

    entries = []
    for i, mat in enumerate(mats[:limit]):
        if mat is None:
            entries.append({"slot": i, "name": None})
            continue
        tree = mat.node_tree
        entry = {
            "name": mat.name,
            "users": mat.users,
            "fake_user": mat.use_fake_user,
            "node_count": len(tree.nodes) if tree else 0,
            "surface_render_method": getattr(mat, "surface_render_method", None),
        }
        if obj_name:
            entry["slot"] = i
        bsdf = _principled(tree) if tree else None
        if bsdf is not None:
            entry["principled"] = {
                s: _socket_value(bsdf.inputs[s])
                for s in ("Base Color", "Metallic", "Roughness", "Alpha")
                if s in bsdf.inputs
            }
        entries.append(entry)

    return {"count": len(mats), "materials": entries, "truncated": len(mats) > limit}


# ---------------------------------------------------------------------------
# node graph
# ---------------------------------------------------------------------------

@command("shading.get_node_graph")
def get_node_graph(params: dict) -> dict:
    """Nodes and links of a material's shader tree."""
    mat = _material(params["material"])
    tree = _tree(mat)
    limit = int(params.get("limit", 1000))

    nodes = list(tree.nodes)
    links = list(tree.links)
    return {
        "material": mat.name,
        "node_count": len(nodes),
        "link_count": len(links),
        "nodes": [_node_brief(n) for n in nodes[:limit]],
        "links": [
            {"from_node": l.from_node.name, "from_socket": l.from_socket.name,
             "to_node": l.to_node.name, "to_socket": l.to_socket.name,
             "valid": l.is_valid}
            for l in links[:limit]
        ],
        "active_node": tree.nodes.active.name if tree.nodes.active else None,
        "truncated": len(nodes) > limit or len(links) > limit,
    }


@command("shading.build_node_graph", mutates=True)
def build_node_graph(params: dict) -> dict:
    """Create or update a complete shader graph using caller-stable node ids."""
    mat = _material(params["material"])
    tree = _tree(mat)
    node_specs = params["nodes"]
    link_specs = params.get("links") or []
    clear_existing = bool(params.get("clear_existing", False))
    remove_unlisted = bool(params.get("remove_unlisted", False))

    if not isinstance(node_specs, list) or not node_specs:
        raise ValueError("nodes must be a non-empty list")
    if len(node_specs) > 500:
        raise ValueError("one graph build is limited to 500 nodes")
    if not isinstance(link_specs, list):
        raise TypeError("links must be a list")
    if len(link_specs) > 2000:
        raise ValueError("one graph build is limited to 2000 links")

    normalized: list[dict] = []
    ids: set[str] = set()
    for index, spec in enumerate(node_specs):
        if not isinstance(spec, dict):
            raise TypeError(f"nodes[{index}] must be an object")
        agent_id = spec.get("id")
        node_type = spec.get("type")
        if not isinstance(agent_id, str) or not agent_id.strip():
            raise ValueError(f"nodes[{index}].id must be a non-empty string")
        if agent_id in ids:
            raise ValueError(f"duplicate node id {agent_id!r}")
        ids.add(agent_id)
        _validate_shader_node_type(node_type)

        location = spec.get("location")
        if location is not None and (
            not isinstance(location, (list, tuple)) or len(location) != 2
        ):
            raise ValueError(f"nodes[{index}].location must be [x, y]")
        props = spec.get("props")
        if props is not None and not isinstance(props, dict):
            raise TypeError(f"nodes[{index}].props must be an object")
        image_name = spec.get("image")
        if image_name is not None and bpy.data.images.get(image_name) is None:
            raise KeyError(
                f"nodes[{index}].image names missing datablock {image_name!r}; "
                "use shading.create_texture_image or shading.load_image_texture first"
            )
        ramp = spec.get("color_ramp")
        if ramp is not None:
            # Structural validation happens before clear_existing so malformed
            # stops cannot destroy an existing graph.
            if node_type != "ShaderNodeValToRGB":
                raise TypeError(
                    f"nodes[{index}].color_ramp requires type='ShaderNodeValToRGB'"
                )
            if not isinstance(ramp, dict):
                raise TypeError(f"nodes[{index}].color_ramp must be an object")
            elements = ramp.get("elements")
            if not isinstance(elements, list) or len(elements) < 2:
                raise ValueError(
                    f"nodes[{index}].color_ramp.elements needs at least two stops"
                )
            for stop_index, stop in enumerate(elements):
                if not isinstance(stop, dict) or "position" not in stop or "color" not in stop:
                    raise ValueError(
                        f"nodes[{index}].color_ramp.elements[{stop_index}] needs "
                        "position and color"
                    )
                position = float(stop["position"])
                if not math.isfinite(position) or not 0.0 <= position <= 1.0:
                    raise ValueError(
                        f"nodes[{index}].color_ramp.elements[{stop_index}].position "
                        "must be between 0 and 1"
                    )
                _validate_color(
                    stop["color"],
                    label=(
                        f"nodes[{index}].color_ramp.elements[{stop_index}].color"
                    ),
                )
        normalized.append(spec)

    specs_by_id = {spec["id"]: spec for spec in normalized}
    for spec in normalized:
        parent_ref = spec.get("parent")
        if parent_ref is None:
            continue
        if not isinstance(parent_ref, str):
            raise TypeError(f"node {spec['id']!r} parent must be a node id or name")
        parent_spec = specs_by_id.get(parent_ref)
        if parent_spec is not None:
            if parent_spec["type"] != "NodeFrame":
                raise TypeError(
                    f"node {spec['id']!r} parent {parent_ref!r} is not a NodeFrame"
                )
        elif clear_existing:
            raise KeyError(
                f"node {spec['id']!r} parent {parent_ref!r} is not in nodes, and "
                "clear_existing removes every external node"
            )
        elif _node(tree, parent_ref).type != "FRAME":
            raise TypeError(f"node {spec['id']!r} parent {parent_ref!r} is not a Frame")

    for index, spec in enumerate(link_specs):
        if not isinstance(spec, dict):
            raise TypeError(f"links[{index}] must be an object")
        missing = [key for key in ("from", "from_socket", "to", "to_socket")
                   if key not in spec]
        if missing:
            raise ValueError(f"links[{index}] is missing {missing}")
        if not isinstance(spec["from"], str) or not isinstance(spec["to"], str):
            raise TypeError(f"links[{index}].from and .to must be node ids or names")
        if clear_existing:
            for endpoint in ("from", "to"):
                if spec[endpoint] not in ids:
                    raise KeyError(
                        f"links[{index}].{endpoint}={spec[endpoint]!r} is not in nodes, "
                        "and clear_existing removes every external node"
                    )
        else:
            for endpoint in ("from", "to"):
                if spec[endpoint] not in ids:
                    _node(tree, spec[endpoint])

    active_ref = params.get("active")
    if active_ref is not None:
        if not isinstance(active_ref, str):
            raise TypeError("active must be a node id or name")
        if active_ref not in ids:
            if clear_existing:
                raise KeyError(
                    f"active node {active_ref!r} is not in nodes, and clear_existing "
                    "removes every external node"
                )
            _node(tree, active_ref)

    managed: dict[str, object] = {}
    for node in tree.nodes:
        agent_id = node.get(_AGENT_ID_PROP)
        if not agent_id:
            continue
        if agent_id in managed:
            raise RuntimeError(
                f"existing agent node id {agent_id!r} is duplicated by "
                f"{managed[agent_id].name!r} and {node.name!r}"
            )
        managed[agent_id] = node

    # Fail on type conflicts before clearing or editing anything.
    if not clear_existing:
        claimed_existing: dict[int, str] = {}
        for spec in normalized:
            candidate = managed.get(spec["id"])
            if candidate is None and spec.get("name"):
                candidate = tree.nodes.get(spec["name"])
            if candidate is None:
                candidate = tree.nodes.get(spec["id"])
            if candidate is None:
                continue
            old_agent_id = candidate.get(_AGENT_ID_PROP)
            if old_agent_id and old_agent_id != spec["id"]:
                raise ValueError(
                    f"node {candidate.name!r} is already managed as {old_agent_id!r}; "
                    f"cannot also adopt it as {spec['id']!r}"
                )
            pointer = candidate.as_pointer()
            if pointer in claimed_existing:
                raise ValueError(
                    f"node specs {claimed_existing[pointer]!r} and {spec['id']!r} "
                    f"both resolve to existing node {candidate.name!r}"
                )
            claimed_existing[pointer] = spec["id"]
            if candidate.bl_idname != spec["type"]:
                raise TypeError(
                    f"node id {spec['id']!r} resolves to {candidate.name!r} of type "
                    f"{candidate.bl_idname}, not requested {spec['type']}. Use a new "
                    "id, remove the old node, or set clear_existing=true."
                )

    removed = []
    if clear_existing:
        for node in list(tree.nodes):
            removed.append(node.name)
            tree.nodes.remove(node)
        managed.clear()

    node_map: dict[str, object] = {}
    created: list[str] = []
    updated: list[str] = []
    applied: dict[str, list[dict]] = {}
    ramps: dict[str, dict] = {}

    for spec in normalized:
        agent_id = spec["id"]
        node = managed.get(agent_id)
        if node is None and spec.get("name"):
            node = tree.nodes.get(spec["name"])
        if node is None:
            node = tree.nodes.get(agent_id)

        if node is None:
            node = tree.nodes.new(spec["type"])
            created.append(agent_id)
        else:
            updated.append(agent_id)

        node[_AGENT_ID_PROP] = agent_id
        if spec.get("name") is not None:
            node.name = str(spec["name"])
        if "label" in spec:
            node.label = str(spec.get("label") or "")
        if spec.get("location") is not None:
            node.location = tuple(float(value) for value in spec["location"])
        if "mute" in spec:
            node.mute = bool(spec["mute"])

        records = []
        for prop, value in (spec.get("props") or {}).items():
            records.append(_apply_prop(node, prop, value))
        if spec.get("image") is not None:
            records.append(_apply_prop(node, "image", spec["image"]))
        if records:
            applied[agent_id] = records
        if spec.get("color_ramp") is not None:
            ramps[agent_id] = _apply_color_ramp(node, spec["color_ramp"])
        node_map[agent_id] = node

    # Frames/parents can only resolve after every node exists.
    for spec in normalized:
        parent_ref = spec.get("parent")
        if parent_ref is None:
            continue
        if not isinstance(parent_ref, str):
            raise TypeError(f"node {spec['id']!r} parent must be a node id or name")
        parent = node_map.get(parent_ref) or _node(tree, parent_ref)
        if parent.type != "FRAME":
            raise TypeError(
                f"parent {parent_ref!r} resolves to {parent.name!r}, which is not a Frame"
            )
        node_map[spec["id"]].parent = parent

    if remove_unlisted:
        for agent_id, node in list(managed.items()):
            if agent_id not in ids and tree.nodes.get(node.name) == node:
                removed.append(node.name)
                tree.nodes.remove(node)

    resolved_links = []
    for index, spec in enumerate(link_specs):
        from_node = node_map.get(spec["from"])
        if from_node is None:
            from_node = _node(tree, spec["from"])
        to_node = node_map.get(spec["to"])
        if to_node is None:
            to_node = _node(tree, spec["to"])
        from_socket = _socket(from_node, spec["from_socket"], outputs=True)
        to_socket = _socket(to_node, spec["to_socket"], outputs=False)
        resolved_links.append((index, spec, from_node, from_socket, to_node, to_socket))

    links_created = 0
    links_reused = 0
    links_replaced = 0
    link_results = []
    for index, spec, from_node, from_socket, to_node, to_socket in resolved_links:
        exact = next((
            link for link in tree.links
            if link.from_socket == from_socket and link.to_socket == to_socket
        ), None)
        if exact is not None:
            link = exact
            links_reused += 1
        else:
            incoming = [link for link in tree.links if link.to_socket == to_socket]
            if incoming and not bool(spec.get("replace", True)):
                raise RuntimeError(
                    f"links[{index}] destination {to_node.name!r}:{to_socket.name!r} "
                    "is already linked; omit replace=false to replace it"
                )
            for old in incoming:
                tree.links.remove(old)
                links_replaced += 1
            link = tree.links.new(from_socket, to_socket)
            if not link.is_valid:
                tree.links.remove(link)
                raise TypeError(
                    f"links[{index}] is incompatible: {from_node.name}:"
                    f"{from_socket.name} -> {to_node.name}:{to_socket.name}"
                )
            links_created += 1
        link_results.append({
            "from": spec["from"],
            "from_node": from_node.name,
            "from_socket": from_socket.name,
            "to": spec["to"],
            "to_node": to_node.name,
            "to_socket": to_socket.name,
            "valid": link.is_valid,
        })

    if active_ref is not None:
        tree.nodes.active = node_map.get(active_ref) or _node(tree, active_ref)

    return {
        "material": mat.name,
        "nodes": {agent_id: node.name for agent_id, node in node_map.items()},
        "created": created,
        "updated": updated,
        "removed": removed,
        "applied": applied,
        "color_ramps": ramps,
        "links": link_results,
        "links_created": links_created,
        "links_reused": links_reused,
        "links_replaced": links_replaced,
        "node_count": len(tree.nodes),
        "link_count": len(tree.links),
        "active_node": tree.nodes.active.name if tree.nodes.active else None,
    }


@command("shading.add_node", mutates=True)
def add_node(params: dict) -> dict:
    """Add a shader node to a material and optionally set its props."""
    mat = _material(params["material"])
    tree = _tree(mat)
    node_type = params["type"]

    _validate_shader_node_type(node_type)
    node = tree.nodes.new(node_type)

    agent_id = params.get("agent_id")
    if agent_id:
        if any(n != node and n.get(_AGENT_ID_PROP) == agent_id for n in tree.nodes):
            tree.nodes.remove(node)
            raise ValueError(f"agent node id {agent_id!r} already exists")
        node[_AGENT_ID_PROP] = str(agent_id)

    location = params.get("location")
    if location is not None:
        node.location = (float(location[0]), float(location[1]))
    if params.get("label"):
        node.label = params["label"]

    applied = []
    for prop, value in (params.get("props") or {}).items():
        applied.append(_apply_prop(node, prop, value))

    return {"material": mat.name, "node": node.name, "applied": applied,
            **_node_brief(node)}


@command("shading.link_nodes", mutates=True)
def link_nodes(params: dict) -> dict:
    """Connect one node's output socket to another node's input socket."""
    mat = _material(params["material"])
    tree = _tree(mat)

    from_node = _node(tree, params["from_node"])
    to_node = _node(tree, params["to_node"])
    from_sock = _socket(from_node, params["from_socket"], outputs=True)
    to_sock = _socket(to_node, params["to_socket"], outputs=False)

    link = tree.links.new(from_sock, to_sock)
    return {
        "material": mat.name,
        "from_node": link.from_node.name,
        "from_socket": link.from_socket.name,
        "to_node": link.to_node.name,
        "to_socket": link.to_socket.name,
        "valid": link.is_valid,
        "link_count": len(tree.links),
    }


@command("shading.set_node_prop", mutates=True)
def set_node_prop(params: dict) -> dict:
    """Set a node attribute, or an input socket's default_value when no such attribute."""
    mat = _material(params["material"])
    tree = _tree(mat)
    node = _node(tree, params["node"])
    result = _apply_prop(node, params["prop"], params["value"])
    return {"material": mat.name, "node": node.name, **result}


@command("shading.remove_node", mutates=True)
def remove_node(params: dict) -> dict:
    """Delete a node from a material's shader tree."""
    mat = _material(params["material"])
    tree = _tree(mat)
    node = _node(tree, params["node"])

    was_output = node.type == "OUTPUT_MATERIAL"
    name = node.name
    tree.nodes.remove(node)

    remaining_outputs = [n.name for n in tree.nodes if n.type == "OUTPUT_MATERIAL"]
    return {
        "material": mat.name,
        "removed": name,
        "was_material_output": was_output,
        "node_count": len(tree.nodes),
        "material_output_nodes": remaining_outputs,
        "warning": "material has no Material Output node left and will render black"
        if not remaining_outputs else None,
    }


@command("shading.load_image_texture", mutates=True)
def load_image_texture(params: dict) -> dict:
    """Load an image file as an Image Texture node, optionally wired into the BSDF."""
    mat = _material(params["material"])
    tree = _tree(mat)
    path = params["path"]
    colorspace = params.get("colorspace")
    hook_to = params.get("hook_to")

    img = _load_image(path, colorspace=colorspace, name=params.get("image_name"))

    if not hook_to:
        tex = tree.nodes.new("ShaderNodeTexImage")
        tex.image = img
        bsdf = _principled(tree)
        if bsdf is not None:
            _stack_left(tree, tex, bsdf, 1)
        return {
            "material": mat.name, "image": img.name, "filepath": img.filepath,
            "size": list(img.size), "image_node": tex.name, "created_nodes": [tex.name],
            "colorspace": img.colorspace_settings.name, "hooked_to": None,
        }

    bsdf = _principled(tree)
    if bsdf is None:
        raise RuntimeError(
            f"material {mat.name!r} has no Principled BSDF to hook into. Add one with "
            f"shading.add_node(type='ShaderNodeBsdfPrincipled') or drop hook_to and "
            f"wire it yourself with shading.link_nodes."
        )

    hooked = _hook_texture(tree, bsdf, img, hook_to, colorspace=colorspace)
    return {"material": mat.name, "image": img.name, "filepath": img.filepath,
            "size": list(img.size), **hooked}


@command("shading.create_texture_image", mutates=True)
def create_texture_image(params: dict) -> dict:
    """Create or safely reuse an internal image for textures, masks or baking."""
    name = str(params.get("name") or "Agent Texture")
    width = int(params.get("width", 1024))
    height = int(params.get("height", 1024))
    if not 1 <= width <= 16384 or not 1 <= height <= 16384:
        raise ValueError("width and height must each be between 1 and 16384")
    if width * height > 67_108_864:
        raise ValueError("image is limited to 67,108,864 pixels (8192 x 8192)")

    alpha = bool(params.get("alpha", True))
    float_buffer = bool(params.get("float_buffer", False))
    is_data = bool(params.get("is_data", False))
    reuse_existing = bool(params.get("reuse_existing", True))
    img = bpy.data.images.get(name)
    reused = img is not None
    if img is not None:
        if not reuse_existing:
            raise ValueError(
                f"image {name!r} already exists; choose a new name or set "
                "reuse_existing=true"
            )
        if tuple(img.size) != (width, height):
            raise ValueError(
                f"image {name!r} is {img.size[0]}x{img.size[1]}, not requested "
                f"{width}x{height}; choose a new name so existing texture users are "
                "not silently broken"
            )
        if bool(img.is_float) != float_buffer:
            raise ValueError(
                f"image {name!r} float_buffer={img.is_float}, not requested "
                f"{float_buffer}; choose a new name"
            )
    else:
        img = bpy.data.images.new(
            name, width=width, height=height, alpha=alpha,
            float_buffer=float_buffer, is_data=is_data,
        )

    generated_type = str(params.get("generated_type", "BLANK")).upper()
    try:
        img.generated_type = generated_type
    except TypeError:
        prop = img.bl_rna.properties["generated_type"]
        valid = [item.identifier for item in prop.enum_items]
        raise ValueError(f"generated_type {generated_type!r} invalid; valid: {valid}") from None

    colorspace = params.get("colorspace")
    if colorspace is None:
        colorspace = "Non-Color" if is_data else "sRGB"
    try:
        img.colorspace_settings.name = colorspace
    except TypeError:
        valid = [item.identifier for item in
                 img.colorspace_settings.bl_rna.properties["name"].enum_items]
        raise ValueError(f"colorspace {colorspace!r} invalid; valid: {valid}") from None

    color = _validate_color(params.get("color", [0.0, 0.0, 0.0, 1.0]), label="color")
    img.generated_color = color

    pixels = params.get("pixels")
    pixels_written = 0
    if pixels is not None:
        if not isinstance(pixels, list):
            raise TypeError("pixels must be a flat [R,G,B,A, ...] number list")
        expected = width * height * 4
        if len(pixels) != expected:
            raise ValueError(
                f"pixels needs exactly width*height*4 = {expected} values, "
                f"got {len(pixels)}"
            )
        values = [float(value) for value in pixels]
        if not all(math.isfinite(value) for value in values):
            raise ValueError("pixels must contain only finite numbers")
        img.pixels.foreach_set(values)
        img.update()
        pixels_written = width * height

    result = _image_brief(img)
    result.update({
        "created": not reused,
        "reused": reused,
        "generated_type": img.generated_type,
        "generated_color": list(img.generated_color),
        "pixels_written": pixels_written,
        "note": (
            "Generated images are stored inside the blend and do not need packing. "
            "Use shading.save_image to create an external file."
        ),
    })
    return result


@command("shading.list_images")
def list_images(params: dict) -> dict:
    """List texture/image datablocks with file and colour-management state."""
    limit = int(params.get("limit", 1000))
    if limit < 1:
        raise ValueError("limit must be at least 1")
    images = list(bpy.data.images)
    return {
        "count": len(images),
        "images": [_image_brief(img) for img in images[:limit]],
        "truncated": len(images) > limit,
    }


@command("shading.save_image", mutates=True)
def save_image(params: dict) -> dict:
    """Save an image datablock to an external texture file."""
    image_name = params["image"]
    img = bpy.data.images.get(image_name)
    if img is None:
        raise KeyError(
            f"no image named {image_name!r}; call shading.list_images to see images"
        )

    resolved = bpy.path.abspath(params["path"])
    if not os.path.isabs(resolved):
        resolved = os.path.abspath(resolved)
    os.makedirs(os.path.dirname(resolved), exist_ok=True)

    file_format = params.get("file_format")
    if file_format is not None:
        file_format = str(file_format).upper()
        try:
            img.file_format = file_format
        except TypeError:
            valid = [item.identifier for item in
                     img.bl_rna.properties["file_format"].enum_items]
            raise ValueError(f"file_format {file_format!r} invalid; valid: {valid}") from None
    img.filepath_raw = resolved
    img.save()
    if not os.path.isfile(resolved):
        raise RuntimeError(f"Blender reported success but did not write {resolved!r}")

    if bool(params.get("pack", False)):
        img.pack()

    return {
        **_image_brief(img),
        "saved_path": resolved,
        "bytes": os.path.getsize(resolved),
        "file_format": img.file_format,
    }


# ---------------------------------------------------------------------------
# viewport
# ---------------------------------------------------------------------------

@command("shading.set_viewport_shading", needs_gui=True)
def set_viewport_shading(params: dict) -> dict:
    """Set the 3D viewport's shading mode, colour source and studio light (GUI only)."""
    _window, _area, _region, space = ctx.require_view3d()
    shading = space.shading

    def _enum(prop: str):
        return [i.identifier for i in shading.bl_rna.properties[prop].enum_items]

    for key in ("type", "color_type", "studio_light"):
        value = params.get(key)
        if value is None:
            continue
        value = value.upper() if key != "studio_light" else value
        try:
            setattr(shading, key, value)
        except TypeError:
            raise ValueError(
                f"{key}={value!r} is not valid here; this build offers {_enum(key)}. "
                f"Note color_type only shows in SOLID shading and studio_light depends "
                f"on the current shading type."
            ) from None

    return {
        "type": shading.type,
        "color_type": shading.color_type,
        "studio_light": shading.studio_light,
        "light": shading.light,
        "available_types": _enum("type"),
        "available_color_types": _enum("color_type"),
        "available_studio_lights": _enum("studio_light"),
    }


# ---------------------------------------------------------------------------
# baking
# ---------------------------------------------------------------------------

#: Bake outputs that are data, not colour — these must live in Non-Color.
_DATA_BAKES = {"NORMAL", "AO", "ROUGHNESS", "UV", "POSITION", "SHADOW"}


def _bake_target_node(tree, img):
    """Reuse or create the Image Texture node pointing at ``img``, made active."""
    node = next((n for n in tree.nodes
                 if n.type == "TEX_IMAGE" and n.image is not None and n.image.name == img.name),
                None)
    if node is None:
        node = tree.nodes.new("ShaderNodeTexImage")
        node.image = img
        node.label = f"bake target: {img.name}"
        node.location = (-900.0, 400.0)
    for other in tree.nodes:
        other.select = False
    node.select = True
    tree.nodes.active = node
    return node


@command("shading.bake", mutates=True)
def bake(params: dict) -> dict:
    """Bake AO/NORMAL/DIFFUSE/COMBINED to an image with Cycles."""
    obj_name = params["object"]
    obj = bpy.data.objects.get(obj_name)
    if obj is None:
        raise KeyError(f"no object named {obj_name!r}")
    if obj.type != "MESH":
        raise TypeError(f"baking needs a MESH, {obj_name!r} is a {obj.type}")

    bake_type = str(params.get("type", "AO")).upper()
    valid = [i.identifier for i in
             bpy.ops.object.bake.get_rna_type().properties["type"].enum_items]
    if bake_type not in valid:
        raise ValueError(f"bake type {bake_type!r} unknown; valid: {valid}")

    size = int(params.get("size", 1024))
    margin = int(params.get("margin", 16))
    samples = int(params.get("samples", 32))
    filepath = params.get("filepath")
    return_image = bool(params.get("return_image", True))
    is_data = bake_type in _DATA_BAKES

    image_name = params.get("image_name") or f"{obj.name}_{bake_type.lower()}"
    img = bpy.data.images.get(image_name)
    if img is not None and tuple(img.size) != (size, size):
        bpy.data.images.remove(img)
        img = None
    if img is None:
        img = bpy.data.images.new(image_name, width=size, height=size,
                                  alpha=True, float_buffer=False, is_data=is_data)
    img.colorspace_settings.name = "Non-Color" if is_data else "sRGB"

    mesh = obj.data
    uv_created = False
    if not mesh.uv_layers:
        mesh.uv_layers.new(name="UVMap", do_init=True)
        uv_created = True

    if not mesh.materials or all(m is None for m in mesh.materials):
        mesh.materials.clear()
        mesh.materials.append(bpy.data.materials.new(f"{obj.name}_bake"))

    target_nodes = {}
    empty_slots = []
    for index, mat in enumerate(mesh.materials):
        if mat is None:
            empty_slots.append(index)
            continue
        target_nodes[mat.name] = _bake_target_node(_tree(mat), img).name

    scene = bpy.context.scene
    view_layer = bpy.context.view_layer
    saved_engine = scene.render.engine
    saved_active = view_layer.objects.active
    saved_selection = [o.name for o in view_layer.objects if o.select_get()]
    saved_mode = obj.mode
    saved_cycles = {}

    gpu = ctx.enable_cycles_metal()
    try:
        scene.render.engine = "CYCLES"
    except TypeError as exc:
        raise RuntimeError(
            "Cycles is unavailable in this Blender, and baking requires it — EEVEE "
            f"cannot bake. Underlying error: {exc}"
        ) from None

    try:
        cycles = scene.cycles
        saved_cycles = {"samples": cycles.samples, "device": cycles.device}
        cycles.samples = samples
        if gpu.get("device_type") == "METAL":
            cycles.device = "GPU"

        if obj.mode != "OBJECT":
            view_layer.objects.active = obj
            bpy.ops.object.mode_set(mode="OBJECT")
        for other in view_layer.objects:
            other.select_set(False)
        obj.select_set(True)
        view_layer.objects.active = obj

        kwargs = {
            "type": bake_type,
            "margin": margin,
            "margin_type": str(params.get("margin_type", "ADJACENT_FACES")).upper(),
            "use_clear": bool(params.get("use_clear", True)),
            "use_selected_to_active": False,
            "target": "IMAGE_TEXTURES",
            "save_mode": "INTERNAL",
        }
        if bake_type in {"DIFFUSE", "GLOSSY", "TRANSMISSION"}:
            # Without a pass filter these bake full lighting; COLOR alone is the albedo.
            kwargs["pass_filter"] = set(params.get("pass_filter") or ["COLOR"])
        if bake_type == "NORMAL":
            kwargs["normal_space"] = str(params.get("normal_space", "TANGENT")).upper()

        result = bpy.ops.object.bake(**kwargs)
        if "FINISHED" not in result:
            raise RuntimeError(
                f"object.bake returned {set(result)} for {obj_name!r}. Usual causes: the "
                f"UV map overlaps or is empty, or the material has no active Image "
                f"Texture node. Bake target nodes were {target_nodes}."
            )
    finally:
        scene.render.engine = saved_engine
        for key, value in saved_cycles.items():
            with contextlib.suppress(Exception):
                setattr(scene.cycles, key, value)
        # Restore the mode while the baked object is still active, otherwise
        # mode_set would drag whatever was active before into that mode.
        if saved_mode != "OBJECT":
            view_layer.objects.active = obj
            with contextlib.suppress(Exception):
                bpy.ops.object.mode_set(mode=saved_mode)
        for other in view_layer.objects:
            other.select_set(other.name in saved_selection)
        if saved_active is not None:
            view_layer.objects.active = saved_active

    out = {
        "object": obj.name,
        "type": bake_type,
        "image": img.name,
        "size": list(img.size),
        "colorspace": img.colorspace_settings.name,
        "samples": samples,
        "margin": margin,
        "uv_layer": mesh.uv_layers.active.name if mesh.uv_layers.active else None,
        "uv_auto_created": uv_created,
        "bake_target_nodes": target_nodes,
        "empty_material_slots": empty_slots,
        "gpu": gpu,
        "filepath": None,
    }

    saved_format = img.file_format
    img.file_format = "PNG"
    tmp_dir = None
    try:
        if filepath:
            out_path = bpy.path.abspath(filepath)
            os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
            img.save(filepath=out_path)
            out["filepath"] = out_path
        elif return_image:
            tmp_dir = tempfile.mkdtemp(prefix="agentmcp-bake-")
            out_path = os.path.join(tmp_dir, f"{bake_type.lower()}.png")
            # save_copy leaves the datablock's own filepath alone, so the image stays
            # internal to the .blend instead of becoming a link to a temp file.
            img.save(filepath=out_path, save_copy=True)
        else:
            out_path = None

        if return_image and out_path:
            out.update(ctx._read_png(out_path))
    finally:
        img.file_format = saved_format
        if tmp_dir:
            with contextlib.suppress(OSError):
                os.remove(os.path.join(tmp_dir, f"{bake_type.lower()}.png"))
            with contextlib.suppress(OSError):
                os.rmdir(tmp_dir)

    return out

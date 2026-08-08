"""Geometry nodes: node groups, graph construction and modifier inputs.

The interface API changed in Blender 4.0 — ``node_tree.inputs`` /
``node_tree.outputs`` were removed in favour of
``node_tree.interface.new_socket(name, in_out=..., socket_type=...)``. On the
modifier, group inputs are keyed by socket **identifier** (``"Socket_2"``), not
by their human-readable name, so :func:`_resolve_socket` maps one to the other.
"""

from __future__ import annotations

import bpy

from ..registry import command


def _group(name: str):
    group = bpy.data.node_groups.get(name)
    if group is None:
        raise KeyError(
            f"no node group named {name!r}. Existing: "
            f"{[g.name for g in bpy.data.node_groups]}"
        )
    return group


def _object(name: str | None):
    if name:
        obj = bpy.data.objects.get(name)
        if obj is None:
            raise KeyError(f"no object named {name!r}")
        return obj
    obj = bpy.context.view_layer.objects.active
    if obj is None:
        raise RuntimeError("no object given and nothing is active")
    return obj


def _interface_items(group):
    """Interface sockets as plain dicts, in declaration order."""
    items = []
    for item in group.interface.items_tree:
        if getattr(item, "item_type", "SOCKET") != "SOCKET":
            continue
        entry = {
            "name": item.name,
            "identifier": item.identifier,
            "in_out": item.in_out,
            "socket_type": item.socket_type,
        }
        if hasattr(item, "default_value"):
            value = item.default_value
            entry["default"] = list(value) if hasattr(value, "__len__") and not isinstance(value, str) else value
        items.append(entry)
    return items


def _resolve_socket(group, key: str) -> dict:
    """Accept a socket identifier or its human name; return the interface entry."""
    items = _interface_items(group)
    for item in items:
        if item["identifier"] == key:
            return item
    matches = [i for i in items if i["name"] == key and i["in_out"] == "INPUT"]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise KeyError(
            f"{key!r} is ambiguous — several inputs share that name. Use one of "
            f"their identifiers: {[m['identifier'] for m in matches]}"
        )
    raise KeyError(
        f"no group input named or identified by {key!r}. Inputs: "
        f"{[(i['name'], i['identifier']) for i in items if i['in_out'] == 'INPUT']}"
    )


def _modifier_inputs(modifier):
    """Return the mapping that holds a Nodes modifier's group input values.

    Blender 5.x moved these off the modifier's ID properties onto
    ``modifier.properties.inputs`` (a ``GeometryNodesInterfaceInputs`` keyed by
    socket identifier). Older builds keyed the modifier itself. Note: never call
    ``modifier.keys()`` on 5.2 — it segfaults.
    """
    properties = getattr(modifier, "properties", None)
    inputs = getattr(properties, "inputs", None)
    if inputs is not None:
        return inputs
    return modifier


@command("geonodes.create_group", mutates=True)
def create_group(params: dict) -> dict:
    """Create a Geometry Nodes group, optionally wired Group Input -> Group Output."""
    name = str(params.get("name", "Geometry Nodes"))
    group = bpy.data.node_groups.new(name, "GeometryNodeTree")

    created = {"group": group.name, "nodes": [], "sockets": []}
    if params.get("with_default_io", True):
        in_socket = group.interface.new_socket(
            "Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
        out_socket = group.interface.new_socket(
            "Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
        node_in = group.nodes.new("NodeGroupInput")
        node_out = group.nodes.new("NodeGroupOutput")
        node_in.location = (-300, 0)
        node_out.location = (300, 0)
        group.links.new(node_in.outputs[0], node_out.inputs[0])
        created["nodes"] = [node_in.name, node_out.name]
        created["sockets"] = [
            {"name": "Geometry", "identifier": in_socket.identifier, "in_out": "INPUT"},
            {"name": "Geometry", "identifier": out_socket.identifier, "in_out": "OUTPUT"},
        ]
    return created


@command("geonodes.add_socket", mutates=True)
def add_socket(params: dict) -> dict:
    """Add an input or output socket to a node group's interface."""
    group = _group(params["group"])
    socket = group.interface.new_socket(
        str(params["name"]),
        in_out=str(params.get("in_out", "INPUT")).upper(),
        socket_type=str(params.get("socket_type", "NodeSocketFloat")),
        description=str(params.get("description", "")),
    )
    if params.get("default") is not None and hasattr(socket, "default_value"):
        socket.default_value = params["default"]
    for bound in ("min_value", "max_value"):
        if params.get(bound) is not None and hasattr(socket, bound):
            setattr(socket, bound, params[bound])
    return {"group": group.name, "name": socket.name,
            "identifier": socket.identifier, "in_out": socket.in_out,
            "socket_type": socket.socket_type}


@command("geonodes.add_node", mutates=True)
def add_node(params: dict) -> dict:
    """Add a node to a geometry node group."""
    group = _group(params["group"])
    node_type = str(params["type"])
    try:
        node = group.nodes.new(node_type)
    except RuntimeError as exc:
        raise ValueError(
            f"unknown node type {node_type!r} ({exc}). Geometry node bl_idnames "
            "look like 'GeometryNodeSubdivideMesh', 'GeometryNodeTransform', "
            "'ShaderNodeValue'. Use describe_api('bpy.types.GeometryNode') or "
            "search_python_api to find the right one."
        ) from exc

    if params.get("location"):
        node.location = tuple(params["location"])
    if params.get("name"):
        node.name = str(params["name"])

    applied, failed = {}, {}
    for key, value in (params.get("props") or {}).items():
        try:
            if hasattr(node, key):
                setattr(node, key, value)
                applied[key] = value
            else:
                socket = node.inputs.get(key)
                if socket is not None and hasattr(socket, "default_value"):
                    socket.default_value = value
                    applied[key] = value
                else:
                    failed[key] = "no such property or input socket"
        except Exception as exc:
            failed[key] = f"{type(exc).__name__}: {exc}"

    return {
        "group": group.name, "node": node.name, "type": node.bl_idname,
        "location": list(node.location),
        "inputs": [s.name for s in node.inputs],
        "outputs": [s.name for s in node.outputs],
        "applied_props": applied, "failed_props": failed,
    }


def _find_socket(node, key, collection):
    """Resolve a socket by name or index within a node's inputs/outputs."""
    if isinstance(key, int):
        if key < 0 or key >= len(collection):
            raise IndexError(f"socket index {key} out of range for "
                             f"{node.name!r} ({len(collection)} sockets)")
        return collection[key]
    socket = collection.get(key)
    if socket is None:
        raise KeyError(f"{node.name!r} has no socket {key!r}. Available: "
                       f"{[s.name for s in collection]}")
    return socket


@command("geonodes.link", mutates=True)
def link(params: dict) -> dict:
    """Connect one node's output socket to another node's input socket."""
    group = _group(params["group"])
    from_node = group.nodes.get(params["from_node"])
    to_node = group.nodes.get(params["to_node"])
    missing = [n for n, o in (("from_node", from_node), ("to_node", to_node)) if o is None]
    if missing:
        raise KeyError(f"{missing} not found. Nodes: {[n.name for n in group.nodes]}")

    out_socket = _find_socket(from_node, params.get("from_socket", 0), from_node.outputs)
    in_socket = _find_socket(to_node, params.get("to_socket", 0), to_node.inputs)
    group.links.new(out_socket, in_socket)
    return {"group": group.name,
            "linked": f"{from_node.name}.{out_socket.name} -> {to_node.name}.{in_socket.name}",
            "link_count": len(group.links)}


@command("geonodes.get_graph", mutates=False)
def get_graph(params: dict) -> dict:
    """Read a node group's full graph: interface, nodes and links."""
    group = _group(params["group"])
    limit = int(params.get("limit", 200))

    nodes = []
    for node in list(group.nodes)[:limit]:
        entry = {"name": node.name, "type": node.bl_idname,
                 "location": list(node.location),
                 "inputs": [], "outputs": [s.name for s in node.outputs]}
        for socket in node.inputs:
            item = {"name": socket.name, "linked": socket.is_linked}
            if hasattr(socket, "default_value") and not socket.is_linked:
                value = socket.default_value
                item["default_value"] = (
                    list(value) if hasattr(value, "__len__") and not isinstance(value, str)
                    else value)
            entry["inputs"].append(item)
        nodes.append(entry)

    links = [{"from_node": l.from_node.name, "from_socket": l.from_socket.name,
              "to_node": l.to_node.name, "to_socket": l.to_socket.name}
             for l in list(group.links)[:limit]]

    return {"group": group.name, "type": group.bl_idname,
            "interface": _interface_items(group),
            "nodes": nodes, "links": links,
            "node_count": len(group.nodes), "link_count": len(group.links),
            "truncated": len(group.nodes) > limit or len(group.links) > limit}


@command("geonodes.remove_node", mutates=True)
def remove_node(params: dict) -> dict:
    """Remove a node from a group."""
    group = _group(params["group"])
    name = params["node"]
    node = group.nodes.get(name)
    if node is None:
        raise KeyError(f"no node {name!r} in {group.name!r}. Nodes: "
                       f"{[n.name for n in group.nodes]}")
    group.nodes.remove(node)
    return {"group": group.name, "removed": name, "node_count": len(group.nodes)}


@command("geonodes.add_modifier", mutates=True)
def add_modifier(params: dict) -> dict:
    """Attach a Geometry Nodes modifier to an object."""
    obj = _object(params.get("object"))
    group_name = params.get("group")

    group = bpy.data.node_groups.get(group_name) if group_name else None
    if group is None:
        if not params.get("create_if_missing", True):
            raise KeyError(f"no node group named {group_name!r}")
        group = bpy.data.node_groups.new(group_name or "Geometry Nodes",
                                         "GeometryNodeTree")
        group.interface.new_socket("Geometry", in_out="INPUT",
                                   socket_type="NodeSocketGeometry")
        group.interface.new_socket("Geometry", in_out="OUTPUT",
                                   socket_type="NodeSocketGeometry")
        node_in = group.nodes.new("NodeGroupInput")
        node_out = group.nodes.new("NodeGroupOutput")
        node_in.location, node_out.location = (-300, 0), (300, 0)
        group.links.new(node_in.outputs[0], node_out.inputs[0])

    modifier = obj.modifiers.new(str(params.get("name", "GeometryNodes")), "NODES")
    modifier.node_group = group
    return {"object": obj.name, "modifier": modifier.name, "group": group.name,
            "inputs": [i for i in _interface_items(group) if i["in_out"] == "INPUT"]}


@command("geonodes.list_inputs", mutates=False)
def list_inputs(params: dict) -> dict:
    """List a Geometry Nodes modifier's group inputs and their current values."""
    obj = _object(params.get("object"))
    modifier = obj.modifiers.get(params["modifier"])
    if modifier is None:
        raise KeyError(f"{obj.name!r} has no modifier {params['modifier']!r}. "
                       f"Modifiers: {[m.name for m in obj.modifiers]}")
    if modifier.type != "NODES":
        raise TypeError(f"{modifier.name!r} is a {modifier.type}, not a Nodes modifier")
    if modifier.node_group is None:
        return {"object": obj.name, "modifier": modifier.name, "group": None,
                "inputs": []}

    store = _modifier_inputs(modifier)
    inputs = []
    for item in _interface_items(modifier.node_group):
        if item["in_out"] != "INPUT":
            continue
        entry = dict(item)
        try:
            value = store[item["identifier"]]
            entry["value"] = (list(value) if hasattr(value, "__len__")
                              and not isinstance(value, str) else value)
        except (KeyError, TypeError):
            entry["value"] = None  # geometry sockets carry no modifier-side value
        inputs.append(entry)
    return {"object": obj.name, "modifier": modifier.name,
            "group": modifier.node_group.name, "inputs": inputs}


@command("geonodes.set_input", mutates=True)
def set_input(params: dict) -> dict:
    """Set a Geometry Nodes modifier input, addressed by socket name or identifier."""
    obj = _object(params.get("object"))
    modifier = obj.modifiers.get(params["modifier"])
    if modifier is None:
        raise KeyError(f"{obj.name!r} has no modifier {params['modifier']!r}. "
                       f"Modifiers: {[m.name for m in obj.modifiers]}")
    if modifier.node_group is None:
        raise RuntimeError(f"{modifier.name!r} has no node group assigned")

    item = _resolve_socket(modifier.node_group, str(params["input"]))
    if item["in_out"] != "INPUT":
        raise ValueError(f"{item['name']!r} is an OUTPUT socket, not an input")

    value = params["value"]
    store = _modifier_inputs(modifier)
    try:
        store[item["identifier"]] = value
    except (TypeError, KeyError) as exc:
        raise TypeError(
            f"cannot set {item['name']!r} ({item['socket_type']}) to {value!r}: {exc}. "
            "Geometry sockets are wired in the graph, not set on the modifier."
        ) from exc

    obj.update_tag()
    stored = store[item["identifier"]]
    return {"object": obj.name, "modifier": modifier.name,
            "input": item["name"], "identifier": item["identifier"],
            "value": list(stored) if hasattr(stored, "__len__")
            and not isinstance(stored, str) else stored}

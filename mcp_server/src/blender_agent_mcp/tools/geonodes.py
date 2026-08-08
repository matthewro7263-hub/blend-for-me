"""Geometry nodes tools: node groups, graph construction and modifier inputs."""

from __future__ import annotations

from typing import Any, Optional, Union

from ..server import call, clean


def register(mcp) -> None:

    @mcp.tool()
    def geonodes_create_group(name: str = "Geometry Nodes",
                              with_default_io: bool = True) -> dict:
        """Create a Geometry Nodes group, pre-wired Group Input -> Group Output.

        Returns the socket `identifier`s (`Socket_0`, `Socket_1`, …). Those
        identifiers — not the display names — are how the modifier addresses
        inputs, so keep them.
        """
        return call("geonodes.create_group",
                    clean(name=name, with_default_io=with_default_io))

    @mcp.tool()
    def geonodes_add_socket(
        group: str,
        name: str,
        socket_type: str = "NodeSocketFloat",
        in_out: str = "INPUT",
        default: Optional[Any] = None,
        min_value: Optional[float] = None,
        max_value: Optional[float] = None,
        description: Optional[str] = None,
    ) -> dict:
        """Add an input or output socket to a node group's interface.

        Args:
            socket_type: `NodeSocketFloat`, `NodeSocketInt`, `NodeSocketVector`,
                `NodeSocketBool`, `NodeSocketColor`, `NodeSocketGeometry`,
                `NodeSocketObject`, `NodeSocketString`.
            in_out: INPUT or OUTPUT.
            default / min_value / max_value: Ignored for socket types that have
                no value (geometry).

        Blender 4.0 removed `node_tree.inputs`; this uses the current
        `interface.new_socket` API. The returned `identifier` is what
        `geonodes_set_input` needs.
        """
        return call("geonodes.add_socket", clean(
            group=group, name=name, socket_type=socket_type, in_out=in_out,
            default=default, min_value=min_value, max_value=max_value,
            description=description))

    @mcp.tool()
    def geonodes_add_node(
        group: str,
        type: str,
        location: Optional[list[float]] = None,
        name: Optional[str] = None,
        props: Optional[dict] = None,
    ) -> dict:
        """Add a node to a geometry node group.

        Args:
            type: The node's `bl_idname`, e.g. `GeometryNodeTransform`,
                `GeometryNodeSubdivideMesh`, `GeometryNodeMeshCube`,
                `GeometryNodeDistributePointsOnFaces`. Search for the exact name
                with `search_python_api` — an unknown type is rejected with a
                hint rather than silently creating nothing.
            location: [x, y] in node-editor space. Space nodes ~200 apart so the
                graph stays readable to a human opening the file later.
            props: Node attributes *or* input-socket default values, by name.
                Anything that does not apply comes back in `failed_props`
                instead of raising — always check that field.

        Returns the node's real name (Blender may uniquify it) plus its input
        and output socket names, which you need for `geonodes_link`.
        """
        return call("geonodes.add_node", clean(
            group=group, type=type, location=location, name=name, props=props))

    @mcp.tool()
    def geonodes_link(
        group: str,
        from_node: str,
        to_node: str,
        from_socket: Union[str, int] = 0,
        to_socket: Union[str, int] = 0,
    ) -> dict:
        """Connect one node's output to another node's input.

        Sockets are addressed by name (`"Geometry"`, `"Scale"`) or by index.
        Index 0 is usually the geometry socket, which makes `0 -> 0` the normal
        way to chain geometry operations.

        Linking an incompatible pair silently does nothing in Blender rather
        than erroring — verify with `geonodes_get_graph` that `link_count` rose.
        """
        return call("geonodes.link", clean(
            group=group, from_node=from_node, to_node=to_node,
            from_socket=from_socket, to_socket=to_socket))

    @mcp.tool()
    def geonodes_get_graph(group: str, limit: int = 200) -> dict:
        """Read a node group's whole graph: interface sockets, nodes and links.

        The way to inspect an existing setup before modifying it. Unlinked input
        sockets report their `default_value`; linked ones report `linked: true`.
        Mirrors `get_node_graph` for materials, so the shape is the same.
        """
        return call("geonodes.get_graph", clean(group=group, limit=limit))

    @mcp.tool()
    def geonodes_remove_node(group: str, node: str) -> dict:
        """Remove a node from a group. Its links go with it."""
        return call("geonodes.remove_node", clean(group=group, node=node))

    @mcp.tool()
    def add_geonodes_modifier(
        group: Optional[str] = None,
        object: Optional[str] = None,
        name: str = "GeometryNodes",
        create_if_missing: bool = True,
    ) -> dict:
        """Attach a Geometry Nodes modifier to an object.

        Args:
            group: Node group to use. Created (pre-wired) when it does not exist
                and `create_if_missing` is true.
            name: Modifier name, used to address it later.

        Like all modifiers this is non-destructive — the base mesh is untouched
        until you apply it with `apply_modifier`.
        """
        return call("geonodes.add_modifier", clean(
            group=group, object=object, name=name,
            create_if_missing=create_if_missing))

    @mcp.tool()
    def geonodes_list_inputs(modifier: str, object: Optional[str] = None) -> dict:
        """List a Geometry Nodes modifier's inputs with their current values.

        Call this before `geonodes_set_input` to learn each socket's name,
        `identifier` and type. Geometry sockets report no value — they are wired
        in the graph, not set on the modifier.
        """
        return call("geonodes.list_inputs", clean(modifier=modifier, object=object))

    @mcp.tool()
    def geonodes_set_input(
        modifier: str,
        input: str,
        value: Any,
        object: Optional[str] = None,
    ) -> dict:
        """Set a Geometry Nodes modifier input value.

        Args:
            input: Socket display name (`"Scale"`) or identifier (`"Socket_2"`).
                Names are resolved for you; an ambiguous name returns the
                candidate identifiers so you can disambiguate.
            value: Number, bool, string, or a list for vector/color sockets.

        This is the parametric control surface — expose a value as a group input
        once, then drive it from here without touching the graph again.
        """
        return call("geonodes.set_input", clean(
            modifier=modifier, input=input, value=value, object=object))

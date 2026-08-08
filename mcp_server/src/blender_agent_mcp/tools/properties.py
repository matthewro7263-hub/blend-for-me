"""Custom ID-property tools for metadata, controls and driver inputs."""

from __future__ import annotations

from typing import Any, Optional

from ..server import call, clean


def register(mcp) -> None:

    @mcp.tool()
    def list_custom_properties(
        target_type: str = "OBJECT",
        target: Optional[str] = None,
        limit: int = 500,
    ) -> dict:
        """List custom properties and their UI metadata on a Blender datablock.

        `target_type` supports OBJECT, OBJECT_DATA, MATERIAL, COLLECTION, WORLD,
        SCENE, ACTION, NODE_GROUP, ARMATURE, CAMERA, LIGHT and IMAGE. `target` is
        the datablock name; omit it for the current SCENE or active World.

        Use custom properties for shot IDs, asset status, dialogue cues, rig
        controls and driver inputs that must survive saving the .blend file.
        """
        return call("properties.list", clean(
            target_type=target_type, target=target, limit=limit))

    @mcp.tool()
    def set_custom_property(
        key: str,
        value: Any,
        target_type: str = "OBJECT",
        target: Optional[str] = None,
        description: Optional[str] = None,
        subtype: Optional[str] = None,
        min: Optional[float] = None,
        max: Optional[float] = None,
        soft_min: Optional[float] = None,
        soft_max: Optional[float] = None,
        step: Optional[float] = None,
        precision: Optional[int] = None,
        default: Any = None,
    ) -> dict:
        """Create or update a persistent custom property on a Blender datablock.

        Values may be booleans, numbers, strings, numeric arrays or nested
        dictionaries. Numeric UI metadata makes rig controls pleasant in Blender's
        Custom Properties panel and gives drivers a documented range.

        Example: put `mood=0.7` on a character object with min=0, max=1, then use
        `add_driver` to drive a material or shape key from that property.
        """
        return call("properties.set", clean(
            key=key, value=value, target_type=target_type, target=target,
            description=description, subtype=subtype, min=min, max=max,
            soft_min=soft_min, soft_max=soft_max, step=step,
            precision=precision, default=default,
        ))

    @mcp.tool()
    def remove_custom_property(
        key: str,
        target_type: str = "OBJECT",
        target: Optional[str] = None,
    ) -> dict:
        """Remove one custom property and return its previous value."""
        return call("properties.remove", clean(
            key=key, target_type=target_type, target=target))

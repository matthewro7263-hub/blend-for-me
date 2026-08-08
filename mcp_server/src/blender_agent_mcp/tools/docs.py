"""Documentation and tutorial tools.

These give prose, concepts and version-correct deep links. For the exact
signature of an operator in the *running* Blender, use `describe_api` instead —
it reads live RNA and can never be out of date.
"""

from __future__ import annotations

from typing import Optional

from ..docs import inventory
from ..server import clean  # noqa: F401  (kept for symmetry with other modules)


def register(mcp) -> None:

    @mcp.tool()
    def search_blender_manual(query: str, limit: int = 12) -> dict:
        """Search the official Blender user manual for concepts and workflows.

        Use for "how does X work" and "what is the right workflow for Y" —
        sculpting theory, weight painting practice, modifier explanations. For an
        operator's exact parameters use `describe_api`.

        Searches the manual's Sphinx inventory, so every result is a real,
        version-correct deep link rather than a guessed URL. The manual version
        is taken from the connected Blender, falling back to `latest`.

        Returns `{symbol, role, url, dispname, score}`, best match first. Follow
        up with `get_doc_page(url)` to read one.
        """
        version = inventory.manual_version()
        base = inventory.MANUAL_URL.format(version=version)
        try:
            results = inventory.search_inventory(query, base, limit=limit)
        except inventory.InventoryError as exc:
            return {"query": query, "manual_version": version,
                    "results": [], "error": str(exc)}
        return {"query": query, "manual_version": version,
                "count": len(results), "results": results}

    @mcp.tool()
    def search_python_api(query: str, limit: int = 12) -> dict:
        """Search the Blender Python API reference for a class, operator or property.

        Good for finding *which* symbol you want (`bpy.ops.mesh.bevel`,
        `bpy.types.SubsurfModifier`) and linking to its prose documentation.

        Once you know the symbol, `describe_api` gives its live signature from
        the running Blender — which is authoritative, since the online reference
        tracks a different version.
        """
        base = inventory.API_URL
        try:
            results = inventory.search_inventory(query, base, limit=limit)
        except inventory.InventoryError as exc:
            return {"query": query, "results": [], "error": str(exc)}
        return {"query": query, "count": len(results), "results": results}

    @mcp.tool()
    def get_doc_page(url: str, max_chars: int = 12000) -> dict:
        """Fetch a Blender documentation page and return it as readable markdown.

        Args:
            url: A URL from `search_blender_manual` or `search_python_api`.
            max_chars: Cap on returned text; `truncated` and `total_chars` tell
                you when content was cut.

        Headings, code blocks, lists and links are preserved; navigation and
        scripts are stripped. Section `anchors` are returned so you can link to
        a specific part.

        Restricted to Blender documentation and community hosts — it is a
        documentation reader, not a general web fetcher. Pages are cached for
        6 hours.
        """
        try:
            return inventory.fetch_page_markdown(url, max_chars=max_chars)
        except (PermissionError, ValueError) as exc:
            return {"url": url, "error": str(exc)}
        except RuntimeError as exc:
            return {"url": url, "error": str(exc)}

    @mcp.tool()
    def find_tutorials(topic: str, level: Optional[str] = None, limit: int = 8) -> dict:
        """Find tutorials and community answers about a Blender topic.

        Complements the manual: use this when you want a worked example, a
        community solution, or an explanation of a technique the manual only
        documents mechanically.

        Args:
            topic: What to learn, e.g. "sculpting a creature head",
                "weight painting a shoulder".
            level: Optional "beginner" / "intermediate" / "advanced" hint.

        Results are ranked toward blender.org, Blender Studio, the manual,
        Blender Stack Exchange and the official YouTube channel. Needs network
        access; returns an empty list with a `note` rather than failing when
        unavailable.
        """
        return inventory.search_tutorials(topic, level=level, limit=limit)

    @mcp.tool()
    def docs_cache_info(clear: bool = False) -> dict:
        """Report or clear the documentation cache under ~/.cache/blender-agent-mcp.

        Inventories are cached for 24 hours and pages for 6. Clear it after a
        Blender upgrade if search results still point at the previous version.
        """
        return inventory.cache_info(clear=clear)

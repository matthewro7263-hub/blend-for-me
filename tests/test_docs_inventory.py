"""Unit tests for the intersphinx inventory parser, ranking and HTML extractor.

No network: the inventory is constructed locally so the parser is tested against
a byte-exact v2 file rather than whatever docs.blender.org serves today.
"""

from __future__ import annotations

import zlib

import pytest

from blender_agent_mcp.docs import inventory
from blender_agent_mcp.docs.inventory import InventoryError, parse_inventory


def make_inventory(lines: list[str], project: str = "Blender", version: str = "5.2") -> bytes:
    header = (
        f"# Sphinx inventory version 2\n"
        f"# Project: {project}\n"
        f"# Version: {version}\n"
        f"# The remainder of this file is compressed using zlib.\n"
    ).encode("utf-8")
    return header + zlib.compress("\n".join(lines).encode("utf-8") + b"\n")


BASE = "https://docs.blender.org/api/current/"

SAMPLE = [
    "bpy.ops.mesh.bevel py:function 1 bpy.ops.mesh.html#$ -",
    "bpy.types.SubsurfModifier py:class 1 bpy.types.SubsurfModifier.html#$ -",
    "bpy.types.Brush.size py:property 1 bpy.types.Brush.html#$ Brush size",
    "bpy.ops.sculpt.brush_stroke py:function 1 bpy.ops.sculpt.html#$ -",
    "bpy.ops.object.voxel_remesh py:function 1 bpy.ops.object.html#$ -",
]


def test_parses_entries_and_expands_dollar():
    entries = parse_inventory(make_inventory(SAMPLE), BASE)
    assert len(entries) == 5
    bevel = next(e for e in entries if e["symbol"] == "bpy.ops.mesh.bevel")
    # "$" in the uri stands for the symbol name.
    assert bevel["url"] == BASE + "bpy.ops.mesh.html#bpy.ops.mesh.bevel"
    assert bevel["role"] == "py:function"


def test_dash_dispname_falls_back_to_name():
    entries = parse_inventory(make_inventory(SAMPLE), BASE)
    bevel = next(e for e in entries if e["symbol"] == "bpy.ops.mesh.bevel")
    assert bevel["dispname"] == "bpy.ops.mesh.bevel"


def test_explicit_dispname_is_kept():
    entries = parse_inventory(make_inventory(SAMPLE), BASE)
    size = next(e for e in entries if e["symbol"] == "bpy.types.Brush.size")
    assert size["dispname"] == "Brush size"


def test_rejects_wrong_header():
    raw = b"# Sphinx inventory version 1\n# Project: x\n# Version: 1\n# c\n" + zlib.compress(b"")
    with pytest.raises(InventoryError, match="unsupported inventory header"):
        parse_inventory(raw, BASE)


def test_rejects_non_zlib_body():
    raw = (b"# Sphinx inventory version 2\n# Project: x\n# Version: 1\n"
           b"# compressed\n" + b"this is not zlib data")
    with pytest.raises(InventoryError, match="not valid zlib"):
        parse_inventory(raw, BASE)


def test_rejects_truncated_header():
    with pytest.raises(InventoryError, match="truncated"):
        parse_inventory(b"# Sphinx inventory version 2\n# Project: x\n", BASE)


def test_ignores_comment_and_blank_lines():
    entries = parse_inventory(make_inventory(["# a comment", "", SAMPLE[0]]), BASE)
    assert len(entries) == 1


def _search(query, entries):
    ranked = sorted(entries, key=lambda e: inventory._score(query, e), reverse=True)
    return [e["symbol"] for e in ranked]


def test_exact_symbol_ranks_first():
    entries = parse_inventory(make_inventory(SAMPLE), BASE)
    assert _search("bpy.ops.mesh.bevel", entries)[0] == "bpy.ops.mesh.bevel"


def test_short_name_matches_trailing_component():
    entries = parse_inventory(make_inventory(SAMPLE), BASE)
    assert _search("bevel", entries)[0] == "bpy.ops.mesh.bevel"


def test_multiword_query_finds_the_right_symbol():
    entries = parse_inventory(make_inventory(SAMPLE), BASE)
    assert _search("brush stroke", entries)[0] == "bpy.ops.sculpt.brush_stroke"


def test_ranking_prefers_specific_over_generic():
    entries = parse_inventory(make_inventory(SAMPLE), BASE)
    assert _search("voxel remesh", entries)[0] == "bpy.ops.object.voxel_remesh"


# ---------------------------------------------------------------------------
# page extraction
# ---------------------------------------------------------------------------

HTML = """
<html><head><title>t</title><style>.x{}</style></head><body>
<nav>skip this navigation</nav>
<h1 id="bevel">Bevel</h1>
<p>Rounds off an <strong>edge</strong>.</p>
<pre>bpy.ops.mesh.bevel(offset=0.1)</pre>
<ul><li>Width</li><li>Segments</li></ul>
<a href="https://docs.blender.org/x">See also</a>
<script>alert(1)</script>
</body></html>
"""


def _markdown(source: str) -> str:
    extractor = inventory._Extractor()
    extractor.feed(source)
    return extractor.markdown()


def test_extractor_keeps_content_and_drops_chrome():
    text = _markdown(HTML)
    assert "# Bevel" in text
    assert "Rounds off an **edge**" in text
    assert "skip this navigation" not in text
    assert "alert(1)" not in text


def test_extractor_preserves_code_blocks_verbatim():
    text = _markdown(HTML)
    assert "```" in text
    assert "bpy.ops.mesh.bevel(offset=0.1)" in text


def test_extractor_keeps_lists_and_links_and_anchors():
    extractor = inventory._Extractor()
    extractor.feed(HTML)
    text = extractor.markdown()
    assert "- Width" in text
    assert "[See also](https://docs.blender.org/x)" in text
    assert "bevel" in extractor.anchors


def test_fetch_rejects_disallowed_host():
    with pytest.raises(PermissionError, match="not an allowed documentation host"):
        inventory.fetch_page_markdown("https://example.com/evil")


def test_fetch_rejects_non_http_scheme():
    with pytest.raises(ValueError, match="only http"):
        inventory.fetch_page_markdown("file:///etc/passwd")


# ---------------------------------------------------------------------------
# cache
# ---------------------------------------------------------------------------

def test_cache_round_trip(tmp_path, monkeypatch):
    monkeypatch.setattr(inventory, "CACHE_DIR", tmp_path)
    inventory._cache_write("k", ".inv", [{"symbol": "a"}])
    assert inventory._cache_read("k", ".inv", 3600) == [{"symbol": "a"}]


def test_expired_cache_entry_is_ignored(tmp_path, monkeypatch):
    monkeypatch.setattr(inventory, "CACHE_DIR", tmp_path)
    inventory._cache_write("k", ".inv", [{"symbol": "a"}])
    assert inventory._cache_read("k", ".inv", 0) is None  # ttl 0 => always stale


def test_corrupt_cache_entry_does_not_raise(tmp_path, monkeypatch):
    monkeypatch.setattr(inventory, "CACHE_DIR", tmp_path)
    inventory._cache_write("k", ".inv", [{"symbol": "a"}])
    inventory._cache_path("k", ".inv").write_text("{ this is not json")
    assert inventory._cache_read("k", ".inv", 3600) is None


def test_cache_info_and_clear(tmp_path, monkeypatch):
    monkeypatch.setattr(inventory, "CACHE_DIR", tmp_path)
    inventory._cache_write("k1", ".inv", [1])
    inventory._cache_write("k2", ".page", {"markdown": "x"})
    info = inventory.cache_info()
    assert info["entries"] == 2 and info["inventories"] == 1 and info["pages"] == 1
    assert inventory.clear_cache()["removed"] == 2
    assert inventory.cache_info()["entries"] == 0

"""Documentation search: intersphinx inventories, page fetching and caching."""

from __future__ import annotations

from .inventory import (  # noqa: F401
    CACHE_DIR,
    InventoryError,
    cache_info,
    clear_cache,
    fetch_page_markdown,
    manual_version,
    parse_inventory,
    search_inventory,
    search_tutorials,
)

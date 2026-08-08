"""Sphinx intersphinx inventories, page extraction and a small on-disk cache.

Searching the official ``objects.inv`` inventories gives exact, version-correct
deep links without scraping. The v2 format is four plain-text ``# ...`` header
lines followed by a zlib-compressed body of

    <name> <domain>:<role> <priority> <uri> <dispname>

where a ``$`` in the uri stands for the name and a ``-`` dispname means "same as
name". Parsed here directly rather than depending on ``sphobjinv``.
"""

from __future__ import annotations

import difflib
import hashlib
import html
import json
import os
import pathlib
import re
import time
import zlib
from html.parser import HTMLParser
from typing import Iterable, Optional

import httpx

CACHE_DIR = pathlib.Path(
    os.environ.get("BLENDER_AGENT_CACHE",
                   pathlib.Path.home() / ".cache" / "blender-agent-mcp")
)

INVENTORY_TTL = 24 * 3600      # inventories change only on a Blender release
PAGE_TTL = 6 * 3600

MANUAL_URL = "https://docs.blender.org/manual/en/{version}/"
API_URL = "https://docs.blender.org/api/current/"

#: Hosts `get_doc_page` will fetch. This is a documentation reader, not a
#: general web proxy that an injected instruction could aim anywhere.
ALLOWED_HOSTS = {
    "docs.blender.org", "www.blender.org", "blender.org",
    "studio.blender.org", "blender.stackexchange.com",
    "developer.blender.org", "projects.blender.org", "code.blender.org",
    "www.youtube.com", "youtube.com",
}

PREFERRED_DOMAINS = [
    "docs.blender.org", "blender.org", "studio.blender.org",
    "blender.stackexchange.com", "www.youtube.com",
]

USER_AGENT = "blender-agent-mcp/0.1 (+https://github.com/your-org/blender-agent-mcp)"


class InventoryError(RuntimeError):
    """The inventory could not be fetched or parsed."""


# ---------------------------------------------------------------------------
# cache
# ---------------------------------------------------------------------------

def _cache_path(key: str, suffix: str) -> pathlib.Path:
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:32]
    return CACHE_DIR / f"{digest}{suffix}"


def _cache_read(key: str, suffix: str, ttl: float):
    path = _cache_path(key, suffix)
    try:
        if not path.is_file() or (time.time() - path.stat().st_mtime) > ttl:
            return None
        with path.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError, json.JSONDecodeError):
        # A truncated or corrupt entry must never break a lookup.
        return None


def _cache_write(key: str, suffix: str, payload) -> None:
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        path = _cache_path(key, suffix)
        tmp = path.with_suffix(path.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(payload, fh)
        tmp.replace(path)  # atomic, so a crash cannot leave a partial entry
    except OSError:
        pass


def cache_info(clear: bool = False) -> dict:
    """Report (and optionally clear) the on-disk cache."""
    if clear:
        return clear_cache()
    if not CACHE_DIR.is_dir():
        return {"directory": str(CACHE_DIR), "entries": 0, "bytes": 0, "exists": False}
    entries = [p for p in CACHE_DIR.iterdir() if p.is_file()]
    return {
        "directory": str(CACHE_DIR),
        "exists": True,
        "entries": len(entries),
        "bytes": sum(p.stat().st_size for p in entries),
        "inventories": sum(1 for p in entries if p.suffix == ".inv"),
        "pages": sum(1 for p in entries if p.suffix == ".page"),
    }


def clear_cache() -> dict:
    removed = 0
    freed = 0
    if CACHE_DIR.is_dir():
        for path in CACHE_DIR.iterdir():
            if path.is_file():
                try:
                    freed += path.stat().st_size
                    path.unlink()
                    removed += 1
                except OSError:
                    pass
    return {"directory": str(CACHE_DIR), "removed": removed, "freed_bytes": freed}


# ---------------------------------------------------------------------------
# inventory
# ---------------------------------------------------------------------------

def parse_inventory(raw: bytes, base_url: str) -> list[dict]:
    """Parse a Sphinx ``objects.inv`` (v2) into entries.

    Raises:
        InventoryError: when the header is missing or not version 2.
    """
    newline = raw.find(b"\n")
    if newline < 0:
        raise InventoryError("inventory is not line-delimited")
    header = raw[:newline].decode("utf-8", "replace").strip()
    if "Sphinx inventory version 2" not in header:
        raise InventoryError(f"unsupported inventory header: {header!r}")

    # Three more plain-text lines (project, version, compression note), then the
    # zlib-compressed body.
    offset = newline + 1
    for _ in range(3):
        nxt = raw.find(b"\n", offset)
        if nxt < 0:
            raise InventoryError("truncated inventory header")
        offset = nxt + 1

    try:
        body = zlib.decompress(raw[offset:]).decode("utf-8", "replace")
    except zlib.error as exc:
        raise InventoryError(f"inventory body is not valid zlib: {exc}") from exc

    pattern = re.compile(r"^(?P<name>.+?)\s+(?P<domain>\S+):(?P<role>\S+)\s+"
                         r"(?P<priority>-?\d+)\s+(?P<uri>\S*)\s+(?P<disp>.*)$")
    entries = []
    for line in body.splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        match = pattern.match(line)
        if match is None:
            continue
        name = match["name"]
        uri = match["uri"].replace("$", name)
        disp = match["disp"].strip()
        entries.append({
            "symbol": name,
            "role": f"{match['domain']}:{match['role']}",
            "url": base_url.rstrip("/") + "/" + uri.lstrip("/"),
            "dispname": name if disp in ("-", "") else disp,
        })
    return entries


def _load_inventory(base_url: str) -> list[dict]:
    key = base_url + "objects.inv"
    cached = _cache_read(key, ".inv", INVENTORY_TTL)
    if cached is not None:
        return cached
    try:
        response = httpx.get(key, timeout=20.0, follow_redirects=True,
                             headers={"User-Agent": USER_AGENT})
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise InventoryError(
            f"could not download {key}: {exc}. Documentation search needs "
            "network access; `describe_api` works offline and gives exact "
            "signatures from the running Blender."
        ) from exc
    entries = parse_inventory(response.content, base_url)
    _cache_write(key, ".inv", entries)
    return entries


def manual_version(default: str = "latest") -> str:
    """Derive the manual version (e.g. '5.2') from the connected Blender."""
    try:
        from .. import bridge_client

        info = bridge_client.call("get_version", {}, timeout=5.0)
        major, minor = info["blender_version"][:2]
        return f"{major}.{minor}"
    except Exception:
        return default


def _score(query: str, entry: dict) -> float:
    """Rank matches: exact, then prefix, then substring, then fuzzy."""
    symbol = entry["symbol"].lower()
    q = query.lower()
    tail = symbol.rsplit(".", 1)[-1]

    if symbol == q or tail == q:
        return 1000.0
    score = difflib.SequenceMatcher(None, q, symbol).ratio() * 100.0
    if tail.startswith(q):
        score += 300.0
    elif symbol.startswith(q):
        score += 250.0
    elif q in tail:
        score += 150.0
    elif q in symbol:
        score += 100.0

    # Multi-word queries: reward entries containing every token.
    tokens = [t for t in re.split(r"[\s_.\-]+", q) if t]
    if len(tokens) > 1 and all(t in symbol for t in tokens):
        score += 120.0
    score -= min(len(symbol), 80) * 0.1  # prefer the more specific symbol
    return score


def search_inventory(query: str, base_url: str, limit: int = 12) -> list[dict]:
    entries = _load_inventory(base_url)
    ranked = sorted(entries, key=lambda e: _score(query, e), reverse=True)
    out = []
    for entry in ranked[:limit]:
        item = dict(entry)
        item["score"] = round(_score(query, entry), 1)
        out.append(item)
    return out


# ---------------------------------------------------------------------------
# page extraction
# ---------------------------------------------------------------------------

class _Extractor(HTMLParser):
    """Turn documentation HTML into readable markdown.

    Deliberately small and dependency-free: keeps headings, paragraphs, lists,
    code blocks and links, and drops navigation, scripts and styling.
    """

    SKIP = {"script", "style", "nav", "header", "footer", "form", "svg", "noscript"}
    HEADINGS = {"h1": "# ", "h2": "## ", "h3": "### ", "h4": "#### ",
                "h5": "##### ", "h6": "###### "}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.anchors: list[str] = []
        self._skip_depth = 0
        self._pre = False
        self._heading: Optional[str] = None
        self._href: Optional[str] = None
        self._link_text: list[str] = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in self.SKIP:
            self._skip_depth += 1
            return
        if self._skip_depth:
            return
        if tag in self.HEADINGS:
            self._heading = self.HEADINGS[tag]
            self.parts.append("\n\n" + self._heading)
            if attrs.get("id"):
                self.anchors.append(attrs["id"])
        elif tag == "pre":
            self._pre = True
            self.parts.append("\n\n```\n")
        elif tag == "code" and not self._pre:
            self.parts.append("`")
        elif tag in ("p", "div", "section"):
            self.parts.append("\n\n")
        elif tag == "li":
            self.parts.append("\n- ")
        elif tag == "br":
            self.parts.append("\n")
        elif tag == "a":
            self._href = attrs.get("href")
            self._link_text = []
        elif tag in ("strong", "b"):
            self.parts.append("**")
        elif tag in ("em", "i"):
            self.parts.append("*")
        elif attrs.get("id"):
            self.anchors.append(attrs["id"])

    def handle_endtag(self, tag):
        if tag in self.SKIP:
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        if self._skip_depth:
            return
        if tag in self.HEADINGS:
            self._heading = None
            self.parts.append("\n")
        elif tag == "pre":
            self._pre = False
            self.parts.append("\n```\n")
        elif tag == "code" and not self._pre:
            self.parts.append("`")
        elif tag == "a":
            text = "".join(self._link_text).strip()
            if text and self._href and not self._href.startswith("#"):
                self.parts.append(f"[{text}]({self._href})")
            else:
                self.parts.append(text)
            self._href = None
            self._link_text = []
        elif tag in ("strong", "b"):
            self.parts.append("**")
        elif tag in ("em", "i"):
            self.parts.append("*")

    def handle_data(self, data):
        if self._skip_depth:
            return
        if self._href is not None:
            self._link_text.append(data)
            return
        self.parts.append(data if self._pre else re.sub(r"\s+", " ", data))

    def markdown(self) -> str:
        text = "".join(self.parts)
        text = re.sub(r"\n{3,}", "\n\n", text)
        text = re.sub(r"[ \t]{2,}", " ", text)
        return text.strip()


def fetch_page_markdown(url: str, max_chars: int = 12000) -> dict:
    """Fetch a documentation page and return readable markdown."""
    from urllib.parse import urlparse

    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"only http(s) URLs are supported, got {parsed.scheme!r}")
    host = (parsed.hostname or "").lower()
    if host not in ALLOWED_HOSTS:
        raise PermissionError(
            f"{host!r} is not an allowed documentation host. This tool reads "
            f"Blender documentation, not arbitrary web pages. Allowed: "
            f"{sorted(ALLOWED_HOSTS)}"
        )

    cached = _cache_read(url, ".page", PAGE_TTL)
    if cached is None:
        try:
            response = httpx.get(url, timeout=25.0, follow_redirects=True,
                                 headers={"User-Agent": USER_AGENT})
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise RuntimeError(f"could not fetch {url}: {exc}") from exc
        extractor = _Extractor()
        extractor.feed(response.text)
        cached = {"markdown": extractor.markdown(), "anchors": extractor.anchors[:200],
                  "url": str(response.url)}
        _cache_write(url, ".page", cached)

    text = cached["markdown"]
    truncated = len(text) > max_chars
    return {
        "url": cached["url"],
        "markdown": text[:max_chars],
        "truncated": truncated,
        "total_chars": len(text),
        "anchors": cached["anchors"][:60],
    }


# ---------------------------------------------------------------------------
# tutorials
# ---------------------------------------------------------------------------

_RESULT_RE = re.compile(
    r'<a[^>]+class="[^"]*result__a[^"]*"[^>]+href="(?P<href>[^"]+)"[^>]*>(?P<title>.*?)</a>'
    r'.*?(?:<a[^>]+class="[^"]*result__snippet[^"]*"[^>]*>(?P<snippet>.*?)</a>)?',
    re.S,
)


def _strip_tags(value: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", value)).strip()


def search_tutorials(topic: str, level: Optional[str] = None, limit: int = 8) -> dict:
    """Keyless web search biased toward reputable Blender sources."""
    from urllib.parse import parse_qs, unquote, urlparse

    query = f"Blender {topic} tutorial"
    if level:
        query += f" {level}"

    try:
        response = httpx.post(
            "https://html.duckduckgo.com/html/",
            data={"q": query},
            timeout=20.0,
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT,
                     "Content-Type": "application/x-www-form-urlencoded"},
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        return {"query": query, "results": [],
                "note": f"tutorial search unavailable ({exc}). Try "
                        f"search_blender_manual for official documentation."}

    results = []
    for match in _RESULT_RE.finditer(response.text):
        href = html.unescape(match["href"])
        # DuckDuckGo wraps results in a redirect carrying the real target.
        if "duckduckgo.com/l/" in href or href.startswith("//duckduckgo.com/l/"):
            target = parse_qs(urlparse(href).query).get("uddg")
            if target:
                href = unquote(target[0])
        if not href.startswith("http"):
            continue
        title = _strip_tags(match["title"] or "")
        snippet = _strip_tags(match["snippet"] or "")
        if not title:
            continue
        host = (urlparse(href).hostname or "").lower()
        results.append({"title": title, "url": href, "snippet": snippet[:300],
                        "source": host})

    def rank(item) -> int:
        host = item["source"]
        for index, preferred in enumerate(PREFERRED_DOMAINS):
            if host.endswith(preferred):
                return index
        return len(PREFERRED_DOMAINS)

    seen: set[str] = set()
    unique = []
    for item in sorted(results, key=rank):
        if item["url"] in seen:
            continue
        seen.add(item["url"])
        unique.append(item)

    return {
        "query": query,
        "results": unique[:limit],
        "note": None if unique else
        "no results parsed — the search endpoint may have changed shape",
    }

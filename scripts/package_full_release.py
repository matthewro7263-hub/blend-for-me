"""Package the complete Blend for me project (Extension + MCP Server + Skill + Docs) into a release ZIP archive."""

from __future__ import annotations

import os
import pathlib
import zipfile

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
DIST_DIR = REPO_ROOT / "dist"
VERSION = "0.0.1-beta"
OUTPUT_ZIP = DIST_DIR / f"blend-for-me-v{VERSION}-full.zip"

EXCLUDE_DIRS = {
    "__pycache__",
    ".pytest_cache",
    ".venv",
    "venv",
    ".git",
    ".github",
    "dist",
    "build",
    "egg-info",
}

EXCLUDE_EXTS = {
    ".pyc",
    ".pyo",
    ".ds_store",
}


def add_file(zf: zipfile.ZipFile, src: pathlib.Path, arcname: str) -> None:
    if src.name.lower() in EXCLUDE_EXTS or src.name.startswith("._"):
        return
    print(f"  adding {arcname}")
    zf.write(src, arcname)


def add_dir(zf: zipfile.ZipFile, src_dir: pathlib.Path, arc_prefix: str) -> None:
    for root, dirs, files in os.walk(src_dir):
        dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS and not d.endswith(".egg-info")]
        for f in files:
            file_path = pathlib.Path(root) / f
            rel_path = file_path.relative_to(src_dir)
            arcname = f"{arc_prefix}/{rel_path}"
            add_file(zf, file_path, arcname)


def main() -> None:
    DIST_DIR.mkdir(parents=True, exist_ok=True)
    print(f"Creating bundle: {OUTPUT_ZIP}")

    with zipfile.ZipFile(OUTPUT_ZIP, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        # 1. Add extension source & extension zip artifact
        ext_zip_path = DIST_DIR / f"blender_agent_mcp-{VERSION}.zip"
        if ext_zip_path.exists():
            add_file(zf, ext_zip_path, f"extension/blender_agent_mcp-{VERSION}.zip")
        add_dir(zf, REPO_ROOT / "blender_extension", "extension/blender_extension")

        # 2. Add MCP server source
        add_dir(zf, REPO_ROOT / "mcp_server", "mcp_server")

        # 3. Add Agent Skill
        add_dir(zf, REPO_ROOT / "skills" / "blender-agent-mcp", "skills/blender-agent-mcp")

        # 4. Add root documentation & legal files
        for doc in ["LICENSE", "README.md", "CHANGELOG.md", "SECURITY.md", "CONTRIBUTING.md", "Makefile"]:
            doc_path = REPO_ROOT / doc
            if doc_path.exists():
                add_file(zf, doc_path, doc)

    size_kb = OUTPUT_ZIP.stat().st_size / 1024
    print(f"Created release bundle: {OUTPUT_ZIP} ({size_kb:.1f} KiB)")


if __name__ == "__main__":
    main()

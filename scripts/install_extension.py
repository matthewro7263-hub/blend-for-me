#!/usr/bin/env python3
"""Install the built extension zip into Blender's ``user_default`` repository.

    python3 scripts/install_extension.py [--zip dist/...] [--blender /path]

Equivalent to Preferences ▸ Get Extensions ▸ Install from Disk, but scriptable —
handy for a dev loop where you rebuild and reinstall repeatedly.

The permission prompt does not appear on this path; installing from the command
line is itself the consent. Review ``blender_manifest.toml`` before running it on
an extension you did not write.
"""

from __future__ import annotations

import argparse
import pathlib
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
DEFAULT_BLENDER = "/Applications/Blender.app/Contents/MacOS/Blender"


def newest_zip(dist: pathlib.Path) -> pathlib.Path:
    zips = sorted(dist.glob("blender_agent_mcp-*.zip"), key=lambda p: p.stat().st_mtime)
    if not zips:
        sys.exit(f"no extension zip in {dist} — run 'make build-ext' first")
    return zips[-1]


def run(cmd: list[str]) -> int:
    print("$", " ".join(cmd))
    proc = subprocess.run(cmd, capture_output=True, text=True)
    # Blender prints a lot of unrelated add-on chatter; surface only what matters.
    for line in (proc.stdout + proc.stderr).splitlines():
        low = line.lower()
        if any(k in low for k in ("error", "warning", "install", "extension", "enabled",
                                  "fatal", "traceback")):
            print("  ", line)
    return proc.returncode


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--zip", default=None)
    parser.add_argument("--blender", default=DEFAULT_BLENDER)
    parser.add_argument("--repo", default="user_default")
    args = parser.parse_args()

    zip_path = pathlib.Path(args.zip) if args.zip else newest_zip(REPO / "dist")
    if not zip_path.is_file():
        sys.exit(f"no such file: {zip_path}")

    rc = run([args.blender, "--command", "extension", "validate", str(zip_path)])
    if rc != 0:
        sys.exit("manifest validation failed — fix blender_manifest.toml first")

    rc = run([args.blender, "--command", "extension", "install-file",
              "--repo", args.repo, "--enable", str(zip_path)])
    if rc != 0:
        sys.exit("install failed")

    print(f"\ninstalled + enabled {zip_path.name}")
    print("Now open Blender, press N in the 3D Viewport, pick the 'Agent MCP' tab,")
    print("and press Start Server.")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Build an installable Blender extension zip from ``blender_extension/``.

Prefers Blender's own builder (``blender --command extension build``) because it
validates the manifest exactly the way the installer will. Falls back to a plain
zip when Blender is unavailable, honouring the manifest's exclude patterns.

    python3 scripts/build_extension.py [--output dist] [--blender /path/to/Blender]
"""

from __future__ import annotations

import argparse
import fnmatch
import pathlib
import shutil
import subprocess
import sys
import zipfile

REPO = pathlib.Path(__file__).resolve().parent.parent
SOURCE = REPO / "blender_extension"
DEFAULT_BLENDER = "/Applications/Blender.app/Contents/MacOS/Blender"

DEFAULT_EXCLUDES = ["__pycache__/", "/.git/", "/*.zip", ".DS_Store", "/tests/"]


def read_manifest() -> dict:
    manifest_path = SOURCE / "blender_manifest.toml"
    if not manifest_path.is_file():
        sys.exit(f"error: no manifest at {manifest_path}")
    try:
        import tomllib
    except ModuleNotFoundError:  # pragma: no cover - Python < 3.11
        sys.exit("error: Python 3.11+ required (needs tomllib)")
    with manifest_path.open("rb") as fh:
        return tomllib.load(fh)


def build_with_blender(blender: str, output_dir: pathlib.Path) -> pathlib.Path | None:
    if not pathlib.Path(blender).exists():
        return None
    output_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        blender, "--command", "extension", "build",
        "--source-dir", str(SOURCE),
        "--output-dir", str(output_dir),
    ]
    print("$", " ".join(cmd))
    proc = subprocess.run(cmd, capture_output=True, text=True)
    sys.stdout.write(proc.stdout)
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr)
        print("! Blender's builder failed; falling back to a plain zip", file=sys.stderr)
        return None
    zips = sorted(output_dir.glob("*.zip"), key=lambda p: p.stat().st_mtime)
    return zips[-1] if zips else None


def _excluded(rel: str, patterns: list[str]) -> bool:
    for pattern in patterns:
        anchored = pattern.startswith("/")
        pat = pattern[1:] if anchored else pattern
        if pat.endswith("/"):
            directory = pat.rstrip("/")
            parts = rel.split("/")
            if anchored:
                if parts and parts[0] == directory:
                    return True
            elif directory in parts:
                return True
            continue
        target = rel if anchored else rel.split("/")[-1]
        if fnmatch.fnmatch(target, pat):
            return True
    return False


def build_plain_zip(manifest: dict, output_dir: pathlib.Path) -> pathlib.Path:
    ext_id = manifest["id"]
    version = manifest["version"]
    patterns = manifest.get("build", {}).get("paths_exclude_pattern", DEFAULT_EXCLUDES)

    output_dir.mkdir(parents=True, exist_ok=True)
    out = output_dir / f"{ext_id}-{version}.zip"
    if out.exists():
        out.unlink()

    count = 0
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(SOURCE.rglob("*")):
            if not path.is_file():
                continue
            rel = path.relative_to(SOURCE).as_posix()
            if _excluded(rel, patterns):
                continue
            # Blender expects the extension's files at the archive root.
            zf.write(path, rel)
            count += 1
    print(f"packed {count} files")
    return out


def verify(zip_path: pathlib.Path, manifest: dict) -> None:
    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
    roots = {n.split("/")[0] for n in names}
    # Blender's builder nests everything under <id>/; a plain zip keeps it flat.
    if "blender_manifest.toml" in names:
        prefix = ""
    else:
        prefix = f"{manifest['id']}/"
        if f"{prefix}blender_manifest.toml" not in names:
            sys.exit(f"error: manifest missing from zip (roots seen: {sorted(roots)})")
    required = [
        "blender_manifest.toml", "__init__.py", "activity_model.py",
        "activity_ui.py", "assets/agent_cursor.svg", "bridge.py", "ctx.py",
        "protocol.py", "registry.py", "handlers/__init__.py",
    ]
    missing = [r for r in required if f"{prefix}{r}" not in names]
    if missing:
        sys.exit(f"error: zip is missing {missing}")
    if any(".pyc" in n or "__pycache__" in n for n in names):
        sys.exit("error: zip contains __pycache__/.pyc files")
    print(f"verified: {len(names)} entries, manifest + all core modules present")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="dist", help="output directory (default: dist)")
    parser.add_argument("--blender", default=DEFAULT_BLENDER, help="path to the Blender binary")
    parser.add_argument("--plain", action="store_true",
                        help="skip Blender's builder and always write a plain zip")
    args = parser.parse_args()

    manifest = read_manifest()
    output_dir = (REPO / args.output).resolve()

    built = None
    if not args.plain:
        built = build_with_blender(args.blender, output_dir)
    if built is None:
        built = build_plain_zip(manifest, output_dir)

    verify(built, manifest)
    size_kb = built.stat().st_size / 1024
    print(f"\n{built}  ({size_kb:.1f} KiB)")
    print("\nInstall it with:")
    print("  Blender ▸ Edit ▸ Preferences ▸ Get Extensions ▸ ▾ ▸ Install from Disk…")
    print(f"  then pick {built.name}, approve the network permission, and press Start Server")


if __name__ == "__main__":
    main()

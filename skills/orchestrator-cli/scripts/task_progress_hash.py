#!/usr/bin/env python3
"""Emit a deterministic content hash for selected task paths."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def update_file_hash(digest, root: Path, file_path: Path) -> str:
    relative = file_path.relative_to(root).as_posix()
    digest.update(b"file\0")
    digest.update(relative.encode("utf-8"))
    digest.update(b"\0")
    with file_path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return relative


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Hash selected task paths without writing to the workspace."
    )
    parser.add_argument("--root", required=True, help="Workspace root.")
    parser.add_argument(
        "--path",
        action="append",
        required=True,
        dest="paths",
        help="Relative file or directory to include; repeat for each task path.",
    )
    args = parser.parse_args()

    root = Path(args.root).resolve()
    if not root.is_dir():
        parser.error(f"--root is not a directory: {root}")

    digest = hashlib.sha256()
    files: list[str] = []
    missing: list[str] = []
    seen: set[Path] = set()

    for requested in sorted(args.paths):
        target = (root / requested).resolve()
        try:
            target.relative_to(root)
        except ValueError:
            parser.error(f"--path escapes --root: {requested}")

        if not target.exists():
            normalized = requested.replace("\\", "/")
            digest.update(b"missing\0")
            digest.update(normalized.encode("utf-8"))
            digest.update(b"\0")
            missing.append(normalized)
            continue

        candidates = [target] if target.is_file() else sorted(
            path for path in target.rglob("*") if path.is_file()
        )
        for file_path in candidates:
            resolved = file_path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            files.append(update_file_hash(digest, root, resolved))

    print(
        json.dumps(
            {
                "root": str(root),
                "paths": sorted(args.paths),
                "files": sorted(files),
                "missing": sorted(missing),
                "sha256": digest.hexdigest(),
            },
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

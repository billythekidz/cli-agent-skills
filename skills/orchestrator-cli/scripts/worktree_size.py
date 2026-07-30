#!/usr/bin/env python3
"""Measure a worktree without following external symlinks."""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import stat
import sys
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Measure a worktree and enforce a maximum logical byte size."
    )
    parser.add_argument("--path", required=True, help="Worktree directory to measure.")
    parser.add_argument(
        "--max-bytes",
        type=int,
        default=500_000_000,
        help="Maximum permitted logical size in bytes (default: 500000000).",
    )
    return parser.parse_args()


def is_link_or_reparse_point(path: Path) -> bool:
    metadata = path.lstat()
    if stat.S_ISLNK(metadata.st_mode):
        return True
    return bool(getattr(metadata, "st_file_attributes", 0) & 0x400)


def allocated_size(path: Path, metadata: os.stat_result) -> int:
    if os.name != "nt":
        blocks = getattr(metadata, "st_blocks", None)
        return blocks * 512 if blocks is not None else metadata.st_size

    get_compressed_file_size = ctypes.windll.kernel32.GetCompressedFileSizeW
    get_compressed_file_size.argtypes = [ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_ulong)]
    get_compressed_file_size.restype = ctypes.c_ulong
    high = ctypes.c_ulong(0)
    low = get_compressed_file_size(str(path), ctypes.byref(high))
    if low == 0xFFFFFFFF:
        error_code = ctypes.get_last_error()
        if error_code:
            raise OSError(error_code, "GetCompressedFileSizeW failed", str(path))
    return (high.value << 32) | low


def main() -> int:
    args = parse_args()
    root = Path(args.path).resolve()
    if not root.is_dir():
        print(json.dumps({"error": "path is not a directory", "path": str(root)}))
        return 2
    if args.max_bytes < 0:
        print(json.dumps({"error": "max-bytes must be non-negative"}))
        return 2

    total_bytes = 0
    allocated_bytes = 0
    file_count = 0
    skipped_symlinks = 0
    limit_exceeded = False
    for directory, dirnames, filenames in os.walk(root, followlinks=False):
        directory_path = Path(directory)
        kept_directories: list[str] = []
        for dirname in dirnames:
            candidate = directory_path / dirname
            try:
                if is_link_or_reparse_point(candidate):
                    skipped_symlinks += 1
                else:
                    kept_directories.append(dirname)
            except OSError:
                print(json.dumps({"error": "cannot stat directory", "path": str(candidate)}))
                return 2
        dirnames[:] = kept_directories

        for filename in filenames:
            candidate = directory_path / filename
            try:
                if is_link_or_reparse_point(candidate):
                    skipped_symlinks += 1
                    continue
                metadata = candidate.stat()
                total_bytes += metadata.st_size
                allocated_bytes += allocated_size(candidate, metadata)
                file_count += 1
                if allocated_bytes > args.max_bytes:
                    limit_exceeded = True
                    break
            except OSError:
                print(json.dumps({"error": "cannot stat file", "path": str(candidate)}))
                return 2
        if limit_exceeded:
            break

    result = {
        "path": str(root),
        "logical_bytes": total_bytes,
        "allocated_bytes": allocated_bytes,
        "file_count": file_count,
        "skipped_symlinks": skipped_symlinks,
        "max_bytes": args.max_bytes,
        "measurement_complete": not limit_exceeded,
        "within_limit": not limit_exceeded,
    }
    print(json.dumps(result, sort_keys=True))
    return 0 if result["within_limit"] else 1


if __name__ == "__main__":
    sys.exit(main())

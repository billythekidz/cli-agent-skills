#!/usr/bin/env python3
"""Emit bounded matching JSONL evidence without exposing a whole map file."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Emit matching JSONL records with a strict output-byte cap."
    )
    parser.add_argument("--path", required=True, help="JSONL evidence file to query.")
    parser.add_argument(
        "--contains",
        action="append",
        required=True,
        help="Case-insensitive substring to match; repeat for OR matching.",
    )
    parser.add_argument(
        "--max-output-bytes",
        type=int,
        default=48_000,
        help="Maximum emitted UTF-8 bytes, including the summary (default: 48000).",
    )
    parser.add_argument(
        "--max-records",
        type=int,
        default=100,
        help="Maximum matching records to emit (default: 100).",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
    source = Path(args.path).resolve()
    if not source.is_file():
        print(json.dumps({"error": "path is not a file", "path": str(source)}))
        return 2
    if args.max_output_bytes < 1_024 or args.max_records <= 0:
        print(json.dumps({"error": "max-output-bytes must be at least 1024 and max-records must be positive"}))
        return 2

    needles = tuple(value.casefold() for value in args.contains if value)
    if not needles:
        print(json.dumps({"error": "at least one non-empty contains value is required"}))
        return 2

    emitted_bytes = 0
    emitted_records = 0
    matched_records = 0
    truncated = False
    records: list[str] = []

    def summary_text() -> str:
        return json.dumps(
            {
                "type": "evidence_excerpt_summary",
                "path": str(source),
                "contains": list(args.contains),
                "matched_records_seen": matched_records,
                "emitted_records": emitted_records,
                "emitted_bytes": emitted_bytes,
                "max_output_bytes": args.max_output_bytes,
                "truncated": truncated,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )

    with source.open("r", encoding="utf-8", errors="replace") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not any(needle in line.casefold() for needle in needles):
                continue
            matched_records += 1
            record = json.dumps(
                {"line": line_number, "record": line.rstrip("\r\n")},
                ensure_ascii=False,
                separators=(",", ":"),
            )
            record_bytes = (record + "\n").encode("utf-8")
            if emitted_records >= args.max_records:
                truncated = True
                break
            prospective_summary = summary_text().encode("utf-8")
            if emitted_bytes + len(record_bytes) + len(prospective_summary) + 1 > args.max_output_bytes:
                truncated = True
                break
            records.append(record)
            emitted_bytes += len(record_bytes)
            emitted_records += 1

    summary = summary_text()
    while records and emitted_bytes + len((summary + "\n").encode("utf-8")) > args.max_output_bytes:
        removed = records.pop()
        emitted_bytes -= len((removed + "\n").encode("utf-8"))
        emitted_records -= 1
        truncated = True
        summary = summary_text()

    for record in records:
        sys.stdout.write(record + "\n")
    sys.stdout.write(summary + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

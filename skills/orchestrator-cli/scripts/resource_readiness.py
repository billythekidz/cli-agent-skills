#!/usr/bin/env python3
"""Run one bounded resource check and emit a safe, structured result."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check one required task resource.")
    parser.add_argument("--name", required=True, help="Stable resource name for the dispatch record.")
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=15.0,
        help="Maximum health-check runtime (default: 15).",
    )
    parser.add_argument("command", nargs=argparse.REMAINDER, help="Command after --.")
    return parser.parse_args()


def tail(value: str, limit: int = 2_000) -> str:
    return value[-limit:]


def main() -> int:
    args = parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if args.timeout_seconds <= 0 or not command:
        print(json.dumps({"resource": args.name, "status": "invalid-check"}))
        return 2

    started = time.monotonic()
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=args.timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        result = {
            "resource": args.name,
            "status": "timeout",
            "timeout_seconds": args.timeout_seconds,
            "duration_seconds": round(time.monotonic() - started, 3),
            "stdout_tail": tail(error.stdout or ""),
            "stderr_tail": tail(error.stderr or ""),
        }
        print(json.dumps(result, ensure_ascii=False))
        return 1
    except OSError as error:
        print(json.dumps({"resource": args.name, "status": "cannot-start", "error": str(error)}))
        return 1

    result = {
        "resource": args.name,
        "status": "ready" if completed.returncode == 0 else "failed",
        "returncode": completed.returncode,
        "duration_seconds": round(time.monotonic() - started, 3),
        "stdout_tail": tail(completed.stdout),
        "stderr_tail": tail(completed.stderr),
    }
    print(json.dumps(result, ensure_ascii=False))
    return 0 if completed.returncode == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

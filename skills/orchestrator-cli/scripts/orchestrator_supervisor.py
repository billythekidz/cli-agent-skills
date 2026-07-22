#!/usr/bin/env python3
"""Tiny cross-platform live-process supervisor for orchestrator-cli.

The supervisor keeps OS process handles in one long-lived local process so a
later command can inject another prompt into the same stdin route. Durable state
is SQLite plus JSONL logs under .orchestrator/runtime; the in-memory handle is
the live route and is intentionally not recoverable after supervisor exit.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import socket
import socketserver
import sqlite3
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ACTIVE_STATES = {"running", "waiting_input"}
DEFAULT_HOST = "127.0.0.1"
READY_TIMEOUT_SECONDS = 10.0


def utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def json_dump(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=True, sort_keys=True)


def ok(**kwargs: Any) -> dict[str, Any]:
    return {"ok": True, **kwargs}


def err(code: str, message: str, **kwargs: Any) -> dict[str, Any]:
    return {"ok": False, "error": {"code": code, "message": message, **kwargs}}


@dataclass(frozen=True)
class RuntimePaths:
    root: Path

    @property
    def db(self) -> Path:
        return self.root / "supervisor.sqlite3"

    @property
    def server_info(self) -> Path:
        return self.root / "server.json"

    @property
    def logs(self) -> Path:
        return self.root / "logs"

    @property
    def server_log(self) -> Path:
        return self.logs / "server.log"

    def ensure(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self.logs.mkdir(parents=True, exist_ok=True)


def default_runtime_root(cwd: Path | None = None) -> Path:
    return (cwd or Path.cwd()) / ".orchestrator" / "runtime"


def connect_timeout() -> float:
    raw = os.environ.get("ORCHESTRATOR_SUPERVISOR_CONNECT_TIMEOUT", "")
    if not raw:
        return READY_TIMEOUT_SECONDS
    try:
        return max(1.0, float(raw))
    except ValueError:
        return READY_TIMEOUT_SECONDS


class Store:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()
        self._init()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        return connection

    def _init(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS processes (
                    dispatch_id TEXT PRIMARY KEY,
                    provider TEXT NOT NULL,
                    protocol TEXT NOT NULL,
                    transport TEXT NOT NULL,
                    workspace TEXT NOT NULL,
                    command_json TEXT NOT NULL,
                    pid INTEGER,
                    status TEXT NOT NULL,
                    native_session_id TEXT,
                    current_turn TEXT,
                    exit_code INTEGER,
                    log_file TEXT NOT NULL,
                    error TEXT,
                    started_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )

    def upsert_process(self, record: dict[str, Any]) -> None:
        columns = [
            "dispatch_id",
            "provider",
            "protocol",
            "transport",
            "workspace",
            "command_json",
            "pid",
            "status",
            "native_session_id",
            "current_turn",
            "exit_code",
            "log_file",
            "error",
            "started_at",
            "updated_at",
        ]
        values = [record.get(column) for column in columns]
        placeholders = ",".join("?" for _ in columns)
        assignments = ",".join(f"{column}=excluded.{column}" for column in columns[1:])
        with self._lock, self._connect() as connection:
            connection.execute(
                f"""
                INSERT INTO processes ({",".join(columns)})
                VALUES ({placeholders})
                ON CONFLICT(dispatch_id) DO UPDATE SET {assignments}
                """,
                values,
            )

    def update_process(self, dispatch_id: str, **fields: Any) -> None:
        if not fields:
            return
        fields["updated_at"] = utc_now()
        assignments = ", ".join(f"{key}=?" for key in fields)
        values = [*fields.values(), dispatch_id]
        with self._lock, self._connect() as connection:
            connection.execute(
                f"UPDATE processes SET {assignments} WHERE dispatch_id=?",
                values,
            )

    def get(self, dispatch_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM processes WHERE dispatch_id=?",
                (dispatch_id,),
            ).fetchone()
        return dict(row) if row else None

    def list(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM processes ORDER BY started_at, dispatch_id"
            ).fetchall()
        return [dict(row) for row in rows]


class ProcessHandle:
    def __init__(
        self,
        dispatch_id: str,
        process: subprocess.Popen[str],
        log_file: Path,
        protocol: str,
    ) -> None:
        self.dispatch_id = dispatch_id
        self.process = process
        self.log_file = log_file
        self.protocol = protocol
        self.write_lock = threading.Lock()
        self.current_turn: str | None = None


class SupervisorService:
    def __init__(self, paths: RuntimePaths) -> None:
        paths.ensure()
        self.paths = paths
        self.store = Store(paths.db)
        self.handles: dict[str, ProcessHandle] = {}
        self._lock = threading.Lock()
        self._log_lock = threading.Lock()

    def health(self) -> dict[str, Any]:
        return ok(
            pid=os.getpid(),
            runtime_root=str(self.paths.root),
            active_dispatches=sorted(self.handles),
        )

    def start_process(self, request: dict[str, Any]) -> dict[str, Any]:
        dispatch_id = str(request.get("dispatch_id") or "").strip()
        if not dispatch_id:
            return err("invalid-request", "dispatch_id is required.")
        command = request.get("command")
        if not isinstance(command, list) or not all(isinstance(part, str) for part in command):
            return err("invalid-request", "command must be a JSON array of strings.")

        workspace = Path(str(request.get("workspace") or os.getcwd())).resolve()
        if not workspace.is_dir():
            return err("invalid-workspace", f"Workspace does not exist: {workspace}")

        existing = self.store.get(dispatch_id)
        if existing and existing.get("status") in ACTIVE_STATES:
            return err("already-active", f"{dispatch_id} is already active.")
        with self._lock:
            if dispatch_id in self.handles:
                return err("already-active", f"{dispatch_id} has a retained live handle.")

        provider = str(request.get("provider") or "custom")
        protocol = str(request.get("protocol") or "text")
        transport = str(request.get("transport") or "stdio")
        started_at = utc_now()
        log_file = self.paths.logs / f"{safe_filename(dispatch_id)}.jsonl"

        try:
            process = subprocess.Popen(
                command,
                cwd=workspace,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
            )
        except OSError as exc:
            record = {
                "dispatch_id": dispatch_id,
                "provider": provider,
                "protocol": protocol,
                "transport": transport,
                "workspace": str(workspace),
                "command_json": json_dump(command),
                "pid": None,
                "status": "dispatch-failed",
                "native_session_id": None,
                "current_turn": None,
                "exit_code": None,
                "log_file": str(log_file),
                "error": str(exc),
                "started_at": started_at,
                "updated_at": started_at,
            }
            self.store.upsert_process(record)
            return err("dispatch-failed", str(exc))

        handle = ProcessHandle(dispatch_id, process, log_file, protocol)
        with self._lock:
            self.handles[dispatch_id] = handle

        self.store.upsert_process(
            {
                "dispatch_id": dispatch_id,
                "provider": provider,
                "protocol": protocol,
                "transport": transport,
                "workspace": str(workspace),
                "command_json": json_dump(command),
                "pid": process.pid,
                "status": "running",
                "native_session_id": None,
                "current_turn": None,
                "exit_code": None,
                "log_file": str(log_file),
                "error": None,
                "started_at": started_at,
                "updated_at": started_at,
            }
        )
        self._append_log(log_file, {"event": "started", "pid": process.pid, "command": command})
        threading.Thread(
            target=self._read_stream,
            args=(handle, process.stdout, "stdout"),
            daemon=True,
        ).start()
        threading.Thread(
            target=self._read_stream,
            args=(handle, process.stderr, "stderr"),
            daemon=True,
        ).start()
        threading.Thread(target=self._watch, args=(handle,), daemon=True).start()
        return ok(dispatch_id=dispatch_id, pid=process.pid, log_file=str(log_file))

    def send_prompt(self, request: dict[str, Any]) -> dict[str, Any]:
        dispatch_id = str(request.get("dispatch_id") or "").strip()
        prompt = request.get("prompt")
        if not dispatch_id or not isinstance(prompt, str):
            return err("invalid-request", "dispatch_id and prompt are required.")
        with self._lock:
            handle = self.handles.get(dispatch_id)
        if handle is None or handle.process.poll() is not None:
            self.store.update_process(
                dispatch_id,
                status="live-transport-unavailable",
                error="No retained live process handle is available.",
            )
            return err(
                "live-transport-unavailable",
                "No retained live process handle is available.",
            )
        if handle.process.stdin is None:
            return err("live-transport-unavailable", "Process stdin is not available.")

        payload = encode_prompt(handle.protocol, prompt, handle.current_turn)
        try:
            with handle.write_lock:
                handle.process.stdin.write(payload)
                handle.process.stdin.flush()
        except OSError as exc:
            self.store.update_process(
                dispatch_id,
                status="live-transport-unavailable",
                error=str(exc),
            )
            return err("live-transport-unavailable", str(exc))

        self._append_log(
            handle.log_file,
            {
                "event": "prompt-sent",
                "protocol": handle.protocol,
                "bytes": len(payload.encode("utf-8")),
            },
        )
        self.store.update_process(dispatch_id, status="running")
        return ok(dispatch_id=dispatch_id, queued=True, bytes=len(payload.encode("utf-8")))

    def status(self, request: dict[str, Any]) -> dict[str, Any]:
        dispatch_id = request.get("dispatch_id")
        if dispatch_id:
            record = self.store.get(str(dispatch_id))
            if not record:
                return err("not-found", f"No dispatch record: {dispatch_id}")
            record["live_handle"] = str(dispatch_id) in self.handles
            return ok(process=record)
        rows = self.store.list()
        for row in rows:
            row["live_handle"] = row["dispatch_id"] in self.handles
        return ok(processes=rows)

    def stop(self, request: dict[str, Any]) -> dict[str, Any]:
        dispatch_id = str(request.get("dispatch_id") or "").strip()
        if not dispatch_id:
            return err("invalid-request", "dispatch_id is required.")
        with self._lock:
            handle = self.handles.get(dispatch_id)
        if not handle:
            self.store.update_process(dispatch_id, status="stopped")
            return ok(dispatch_id=dispatch_id, stopped=False, reason="no-live-handle")

        process = handle.process
        if process.poll() is None:
            terminate_process(process)
            try:
                process.wait(timeout=float(request.get("timeout") or 5))
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        with self._lock:
            self.handles.pop(dispatch_id, None)
        self.store.update_process(
            dispatch_id,
            status="stopped",
            exit_code=process.returncode,
        )
        self._append_log(handle.log_file, {"event": "stopped", "exit_code": process.returncode})
        return ok(dispatch_id=dispatch_id, stopped=True, exit_code=process.returncode)

    def shutdown(self) -> dict[str, Any]:
        dispatches = list(self.handles)
        for dispatch_id in dispatches:
            self.stop({"dispatch_id": dispatch_id, "timeout": 2})
        return ok(shutdown=True, stopped=dispatches)

    def _read_stream(self, handle: ProcessHandle, stream: Any, name: str) -> None:
        if stream is None:
            return
        for line in stream:
            line = line.rstrip("\n")
            self._append_log(handle.log_file, {"event": name, "line": line})
            self._learn_from_line(handle, line)

    def _watch(self, handle: ProcessHandle) -> None:
        exit_code = handle.process.wait()
        with self._lock:
            self.handles.pop(handle.dispatch_id, None)
        status = "stopped" if exit_code == 0 else "worker-error"
        self.store.update_process(handle.dispatch_id, status=status, exit_code=exit_code)
        self._append_log(handle.log_file, {"event": "exited", "exit_code": exit_code})

    def _learn_from_line(self, handle: ProcessHandle, line: str) -> None:
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            return
        if not isinstance(payload, dict):
            return

        session_id = first_string(
            payload,
            "session_id",
            "sessionId",
            "thread_id",
            "threadId",
            "conversation_id",
            "conversationId",
        )
        turn_id = first_string(payload, "turn_id", "turnId", "current_turn", "currentTurn")

        method = payload.get("method")
        params = payload.get("params")
        if isinstance(params, dict):
            session_id = session_id or first_string(
                params, "session_id", "sessionId", "thread_id", "threadId"
            )
            turn_id = turn_id or first_string(params, "turn_id", "turnId")
        if method == "turn/started" and turn_id:
            handle.current_turn = turn_id

        updates: dict[str, Any] = {}
        if session_id:
            updates["native_session_id"] = session_id
        if turn_id:
            updates["current_turn"] = turn_id
        if updates:
            self.store.update_process(handle.dispatch_id, **updates)

    def _append_log(self, path: Path, payload: dict[str, Any]) -> None:
        payload = {"ts": utc_now(), **payload}
        with self._log_lock:
            with path.open("a", encoding="utf-8") as output:
                output.write(json_dump(payload) + "\n")


def first_string(payload: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def safe_filename(value: str) -> str:
    return "".join(character if character.isalnum() or character in "._-" else "_" for character in value)


def encode_prompt(protocol: str, prompt: str, current_turn: str | None) -> str:
    if protocol == "text":
        return prompt + "\n"
    if protocol == "jsonl":
        return json_dump({"type": "user", "text": prompt}) + "\n"
    if protocol == "claude-stream-json":
        return (
            json_dump(
                {
                    "type": "user",
                    "message": {
                        "role": "user",
                        "content": [{"type": "text", "text": prompt}],
                    },
                }
            )
            + "\n"
        )
    if protocol == "codex-app-server":
        request_id = str(uuid.uuid4())
        if current_turn:
            return (
                json_dump(
                    {
                        "id": request_id,
                        "method": "turn/steer",
                        "params": {
                            "expectedTurnId": current_turn,
                            "prompt": prompt,
                        },
                    }
                )
                + "\n"
            )
        return (
            json_dump(
                {
                    "id": request_id,
                    "method": "turn/start",
                    "params": {"prompt": prompt},
                }
            )
            + "\n"
        )
    raise ValueError(f"Unsupported protocol: {protocol}")


def terminate_process(process: subprocess.Popen[str]) -> None:
    if os.name == "nt":
        process.terminate()
    else:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except OSError:
            process.terminate()


class SupervisorTCPServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, server_address: tuple[str, int], service: SupervisorService) -> None:
        self.service = service
        super().__init__(server_address, SupervisorRequestHandler)


class SupervisorRequestHandler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        line = self.rfile.readline().decode("utf-8", errors="replace")
        try:
            request = json.loads(line)
        except json.JSONDecodeError as exc:
            self._write(err("invalid-json", str(exc)))
            return

        if not isinstance(request, dict):
            self._write(err("invalid-request", "Request must be a JSON object."))
            return
        if request.get("token") != self.server.token:  # type: ignore[attr-defined]
            self._write(err("unauthorized", "Invalid supervisor token."))
            return

        method = request.get("method")
        params = request.get("params") if isinstance(request.get("params"), dict) else {}
        service: SupervisorService = self.server.service  # type: ignore[attr-defined]
        try:
            if method == "health":
                response = service.health()
            elif method == "start":
                response = service.start_process(params)
            elif method == "send":
                response = service.send_prompt(params)
            elif method == "status":
                response = service.status(params)
            elif method == "stop":
                response = service.stop(params)
            elif method == "shutdown":
                response = service.shutdown()
                self._write(response)
                threading.Thread(target=self.server.shutdown, daemon=True).start()
                return
            else:
                response = err("unknown-method", f"Unknown method: {method}")
        except Exception as exc:  # pragma: no cover - last-resort guard for daemon stability.
            response = err("internal-error", str(exc))
        self._write(response)

    def _write(self, response: dict[str, Any]) -> None:
        self.wfile.write((json_dump(response) + "\n").encode("utf-8"))


def read_server_info(paths: RuntimePaths) -> dict[str, Any] | None:
    try:
        payload = json.loads(paths.server_info.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def rpc(paths: RuntimePaths, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    info = read_server_info(paths)
    if not info:
        return err("server-not-running", "Supervisor server info was not found.")
    host = str(info.get("host") or DEFAULT_HOST)
    port = int(info.get("port") or 0)
    token = str(info.get("token") or "")
    request = {"token": token, "method": method, "params": params or {}}
    try:
        with socket.create_connection((host, port), timeout=5) as sock:
            sock.sendall((json_dump(request) + "\n").encode("utf-8"))
            response = sock.makefile("r", encoding="utf-8").readline()
    except OSError as exc:
        return err("server-unreachable", str(exc))
    try:
        payload = json.loads(response)
    except json.JSONDecodeError as exc:
        return err("invalid-response", str(exc), raw=response)
    return payload if isinstance(payload, dict) else err("invalid-response", "Response was not JSON object.")


def ensure_server(paths: RuntimePaths, script_path: Path) -> dict[str, Any]:
    health = rpc(paths, "health")
    if health.get("ok"):
        return health

    paths.ensure()
    token = uuid.uuid4().hex
    command = [
        sys.executable,
        str(script_path),
        "--runtime-root",
        str(paths.root),
        "serve",
        "--token",
        token,
    ]
    server_log = paths.server_log.open("a", encoding="utf-8")
    kwargs: dict[str, Any] = {
        "stdin": subprocess.DEVNULL,
        "stdout": server_log,
        "stderr": server_log,
        "cwd": str(Path.cwd()),
    }
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    subprocess.Popen(command, **kwargs)
    server_log.close()

    deadline = time.monotonic() + connect_timeout()
    while time.monotonic() < deadline:
        time.sleep(0.1)
        info = read_server_info(paths)
        if info and info.get("token") == token:
            health = rpc(paths, "health")
            if health.get("ok"):
                return health
    return err("server-start-timeout", f"Supervisor did not become ready at {paths.root}.")


def run_server(args: argparse.Namespace) -> int:
    paths = RuntimePaths(Path(args.runtime_root).resolve())
    paths.ensure()
    service = SupervisorService(paths)
    with SupervisorTCPServer((DEFAULT_HOST, 0), service) as server:
        server.token = args.token or uuid.uuid4().hex  # type: ignore[attr-defined]
        host, port = server.server_address
        paths.server_info.write_text(
            json_dump(
                {
                    "host": host,
                    "port": port,
                    "pid": os.getpid(),
                    "token": server.token,  # type: ignore[attr-defined]
                    "runtime_root": str(paths.root),
                    "started_at": utc_now(),
                }
            )
            + "\n",
            encoding="utf-8",
        )
        server.serve_forever(poll_interval=0.25)
    return 0


def print_payload(payload: dict[str, Any], as_json: bool) -> None:
    if as_json:
        print(json_dump(payload))
        return
    if payload.get("ok"):
        print(json_dump(payload))
    else:
        error = payload.get("error") if isinstance(payload.get("error"), dict) else {}
        print(f"{error.get('code', 'error')}: {error.get('message', payload)}", file=sys.stderr)


def command_doctor(args: argparse.Namespace, paths: RuntimePaths) -> int:
    health = rpc(paths, "health")
    payload = ok(
        platform=sys.platform,
        python=sys.version.split()[0],
        runtime_root=str(paths.root),
        server_running=bool(health.get("ok")),
        server=health if health.get("ok") else None,
        providers={
            "claude": shutil.which("claude"),
            "codex": shutil.which("codex"),
            "agy": shutil.which("agy"),
        },
        protocols=["text", "jsonl", "claude-stream-json", "codex-app-server"],
        unsupported_without_optional_runtime=["portable interactive PTY injection"],
    )
    print_payload(payload, args.json)
    return 0


def command_start(args: argparse.Namespace, paths: RuntimePaths) -> int:
    server = ensure_server(paths, Path(__file__).resolve())
    if not server.get("ok"):
        print_payload(server, args.json)
        return 1
    payload = rpc(
        paths,
        "start",
        {
            "dispatch_id": args.dispatch_id,
            "provider": args.provider,
            "protocol": args.protocol,
            "transport": args.transport,
            "workspace": args.workspace,
            "command": args.command,
        },
    )
    print_payload(payload, args.json)
    return 0 if payload.get("ok") else 1


def command_send(args: argparse.Namespace, paths: RuntimePaths) -> int:
    payload = rpc(paths, "send", {"dispatch_id": args.dispatch_id, "prompt": args.prompt})
    print_payload(payload, args.json)
    return 0 if payload.get("ok") else 1


def command_status(args: argparse.Namespace, paths: RuntimePaths) -> int:
    payload = rpc(paths, "status", {"dispatch_id": args.dispatch_id})
    print_payload(payload, args.json)
    return 0 if payload.get("ok") else 1


def command_stop(args: argparse.Namespace, paths: RuntimePaths) -> int:
    payload = rpc(paths, "stop", {"dispatch_id": args.dispatch_id})
    print_payload(payload, args.json)
    return 0 if payload.get("ok") else 1


def command_shutdown(args: argparse.Namespace, paths: RuntimePaths) -> int:
    payload = rpc(paths, "shutdown")
    print_payload(payload, args.json)
    return 0 if payload.get("ok") else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Cross-platform local process supervisor for orchestrator-cli."
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON to stdout.")
    parser.add_argument(
        "--runtime-root",
        default=str(default_runtime_root()),
        help="Runtime directory. Defaults to .orchestrator/runtime in the current repo.",
    )
    subparsers = parser.add_subparsers(dest="command_name", required=True)

    subparsers.add_parser("doctor", help="Report supervisor state and provider commands.")

    serve = subparsers.add_parser("serve", help=argparse.SUPPRESS)
    serve.add_argument("--token", default="")

    start = subparsers.add_parser("start", help="Start a retained live process.")
    start.add_argument("--dispatch-id", required=True)
    start.add_argument("--provider", default="custom")
    start.add_argument("--protocol", default="text", choices=["text", "jsonl", "claude-stream-json", "codex-app-server"])
    start.add_argument("--transport", default="stdio")
    start.add_argument("--workspace", default=str(Path.cwd()))
    start.add_argument("command", nargs=argparse.REMAINDER, help="Command after --.")

    send = subparsers.add_parser("send", help="Inject a prompt into a retained process.")
    send.add_argument("dispatch_id")
    send.add_argument("prompt")

    status = subparsers.add_parser("status", help="Show one dispatch or all dispatches.")
    status.add_argument("dispatch_id", nargs="?")

    stop = subparsers.add_parser("stop", help="Terminate one retained process.")
    stop.add_argument("dispatch_id")

    subparsers.add_parser("shutdown", help="Stop all retained processes and exit the server.")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    paths = RuntimePaths(Path(args.runtime_root).resolve())

    if args.command_name == "serve":
        return run_server(args)
    if args.command_name == "doctor":
        return command_doctor(args, paths)
    if args.command_name == "start":
        if args.command and args.command[0] == "--":
            args.command = args.command[1:]
        if not args.command:
            print_payload(err("invalid-request", "start requires a command after --."), args.json)
            return 1
        return command_start(args, paths)
    if args.command_name == "send":
        return command_send(args, paths)
    if args.command_name == "status":
        return command_status(args, paths)
    if args.command_name == "stop":
        return command_stop(args, paths)
    if args.command_name == "shutdown":
        return command_shutdown(args, paths)
    parser.error("unknown command")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

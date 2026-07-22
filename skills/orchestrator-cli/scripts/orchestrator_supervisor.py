#!/usr/bin/env python3
"""Tiny cross-platform live-process supervisor for orchestrator-cli.

The supervisor keeps OS process handles in one long-lived local process so a
later command can inject another prompt into the same live route. Durable state
is SQLite plus JSONL logs under .orchestrator/runtime. stdio handles are not
recoverable after supervisor exit; an isolated tmux route can be reattached when
its recorded session and socket are still alive.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
import os
import shlex
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
# Process startup can include provider wrapper import/auth initialization. Keep
# the default long enough for a cold local environment while allowing callers
# to override it with ORCHESTRATOR_SUPERVISOR_CONNECT_TIMEOUT.
READY_TIMEOUT_SECONDS = 60.0
SUPPORTED_PROTOCOLS = [
    "text",
    "jsonl",
    "claude-stream-json",
    "codex-app-server",
    "antigravity-pty",
]
ANTIGRAVITY_PROVIDER = "antigravity-cli"


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

    @contextmanager
    def _session(self):
        connection = self._connect()
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _init(self) -> None:
        with self._session() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS processes (
                    dispatch_id TEXT PRIMARY KEY,
                    provider TEXT NOT NULL,
                    protocol TEXT NOT NULL,
                    transport TEXT NOT NULL,
                    transport_meta_json TEXT,
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
            columns = {
                row[1]
                for row in connection.execute("PRAGMA table_info(processes)").fetchall()
            }
            if "transport_meta_json" not in columns:
                connection.execute("ALTER TABLE processes ADD COLUMN transport_meta_json TEXT")

    def upsert_process(self, record: dict[str, Any]) -> None:
        columns = [
            "dispatch_id",
            "provider",
            "protocol",
            "transport",
            "transport_meta_json",
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
        with self._lock, self._session() as connection:
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
        with self._lock, self._session() as connection:
            connection.execute(
                f"UPDATE processes SET {assignments} WHERE dispatch_id=?",
                values,
            )

    def get(self, dispatch_id: str) -> dict[str, Any] | None:
        with self._session() as connection:
            row = connection.execute(
                "SELECT * FROM processes WHERE dispatch_id=?",
                (dispatch_id,),
            ).fetchone()
        return dict(row) if row else None

    def list(self) -> list[dict[str, Any]]:
        with self._session() as connection:
            rows = connection.execute(
                "SELECT * FROM processes ORDER BY started_at, dispatch_id"
            ).fetchall()
        return [dict(row) for row in rows]


class TransportUnavailable(RuntimeError):
    """The requested live transport is not available on this machine."""


def resolve_live_transport(
    provider: str,
    requested: str,
    platform_name: str | None = None,
) -> str:
    """Resolve the provider-specific live route without probing a process.

    Claude and Codex are deliberately kept on their protocol-native stdio
    routes.  Antigravity is an interactive TUI, so its live route is a PTY:
    tmux on macOS and pywinpty/ConPTY on Windows.
    """

    platform_name = platform_name or sys.platform
    requested = (requested or "auto").lower()
    if provider != ANTIGRAVITY_PROVIDER:
        if requested in {"stdio", "auto"}:
            return "stdio"
        raise ValueError(
            f"PTY transports are reserved for provider {ANTIGRAVITY_PROVIDER}."
        )

    if requested == "stdio":
        raise ValueError("antigravity-cli requires a PTY transport for live prompts.")
    if requested in {"auto", "pty"}:
        if platform_name == "darwin":
            return "tmux"
        if platform_name == "win32":
            return "winpty"
        raise TransportUnavailable(
            "Antigravity PTY live transport is currently supported on macOS "
            "(tmux) and Windows (pywinpty/ConPTY)."
        )
    if requested == "tmux":
        if platform_name != "darwin":
            raise ValueError("tmux is the Antigravity PTY backend for macOS only.")
        return "tmux"
    if requested in {"winpty", "conpty"}:
        if platform_name != "win32":
            raise ValueError("winpty is the Antigravity PTY backend for Windows only.")
        return "winpty"
    raise ValueError(f"Unsupported live transport: {requested}")


class WinPtyTransport:
    """Small adapter around the optional pywinpty package."""

    def __init__(self, process: Any, command: list[str], workspace: Path) -> None:
        self.process = process
        self.command = command
        self.workspace = workspace
        self.returncode: int | None = None

    @classmethod
    def start(cls, command: list[str], workspace: Path) -> "WinPtyTransport":
        try:
            import winpty  # type: ignore[import-not-found]
        except ImportError as exc:
            raise TransportUnavailable(
                "Windows Antigravity PTY requires pywinpty. Install it with "
                "`py -m pip install pywinpty` and run doctor again."
            ) from exc

        command_line = subprocess.list2cmdline(command)
        try:
            process = winpty.PtyProcess.spawn(command_line, cwd=str(workspace))
        except (OSError, RuntimeError, TypeError) as exc:
            raise TransportUnavailable(f"Could not start Windows PTY: {exc}") from exc
        return cls(process, command, workspace)

    @property
    def pid(self) -> int | None:
        value = getattr(self.process, "pid", None)
        return int(value) if value is not None else None

    def write(self, payload: str) -> None:
        self.process.write(payload)

    def read(self, size: int = 4096) -> str:
        return str(self.process.read(size))

    def poll(self) -> int | None:
        alive = self.process.isalive()
        if alive:
            return None
        if self.returncode is None:
            value = getattr(self.process, "exitstatus", None)
            self.returncode = int(value) if isinstance(value, int) else 0
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        deadline = None if timeout is None else time.monotonic() + timeout
        while self.poll() is None:
            if deadline is not None and time.monotonic() >= deadline:
                raise subprocess.TimeoutExpired(self.command, timeout)
            time.sleep(0.05)
        return int(self.returncode or 0)

    def terminate(self) -> None:
        try:
            self.process.terminate()
        except TypeError:
            self.process.terminate(False)

    def kill(self) -> None:
        try:
            self.process.terminate(force=True)
        except TypeError:
            self.process.terminate(True)

    @property
    def metadata(self) -> dict[str, Any]:
        return {
            "backend": "pywinpty",
            "workspace": str(self.workspace),
        }


class TmuxTransport:
    """An isolated tmux session used as an interactive PTY on macOS."""

    def __init__(
        self,
        socket_path: Path,
        session: str,
        window: str,
        target: str,
        raw_log: Path,
        pid: int | None,
        workspace: Path,
        command: list[str],
    ) -> None:
        self.socket_path = socket_path
        self.session = session
        self.window = window
        self.target = target
        self.raw_log = raw_log
        self._pid = pid
        self.workspace = workspace
        self.command = command
        self.returncode: int | None = None

    @classmethod
    def start(
        cls,
        runtime_root: Path,
        dispatch_id: str,
        command: list[str],
        workspace: Path,
        log_file: Path,
    ) -> "TmuxTransport":
        tmux = shutil.which("tmux")
        if not tmux:
            raise TransportUnavailable(
                "macOS Antigravity PTY requires tmux. Install it with "
                "`brew install tmux` and run doctor again."
            )

        safe_id = safe_filename(dispatch_id)
        # tmux has a short Unix-domain socket path limit.  Keep the durable
        # metadata in the runtime DB, but place the actual socket in /tmp and
        # use a deterministic digest so long Issue/task IDs remain valid.
        socket_key = hashlib.sha256(f"{runtime_root}:{dispatch_id}".encode()).hexdigest()[:20]
        socket_path = Path("/tmp") / f"orch-{socket_key}.sock"
        session = f"orchestrator-{safe_id}"
        window = "agent"
        target = f"{session}:{window}"
        raw_log = log_file.with_suffix(".raw.log")
        raw_log.parent.mkdir(parents=True, exist_ok=True)
        raw_log.touch()
        if socket_path.exists():
            socket_path.unlink()

        cls._run(
            socket_path,
            [
                "new-session",
                "-d",
                "-s",
                session,
                "-n",
                window,
                "-c",
                str(workspace),
                "sh",
                "-lc",
                shlex.join(command),
            ],
        )
        cls._run(
            socket_path,
            [
                "pipe-pane",
                "-o",
                "-t",
                target,
                f"cat >> {shlex.quote(str(raw_log))}",
            ],
        )
        pid_text = cls._run(
            socket_path,
            ["display-message", "-p", "-t", target, "#{pane_pid}"],
        ).strip()
        try:
            pid = int(pid_text)
        except ValueError:
            pid = None
        return cls(socket_path, session, window, target, raw_log, pid, workspace, command)

    @classmethod
    def from_metadata(cls, metadata: dict[str, Any], command: list[str]) -> "TmuxTransport":
        return cls(
            socket_path=Path(str(metadata["socket"])),
            session=str(metadata["session"]),
            window=str(metadata["window"]),
            target=str(metadata.get("target") or f"{metadata['session']}:{metadata['window']}"),
            raw_log=Path(str(metadata["raw_log"])),
            pid=int(metadata["pid"]) if metadata.get("pid") else None,
            workspace=Path(str(metadata.get("workspace") or Path.cwd())),
            command=command,
        )

    @staticmethod
    def _run(socket_path: Path, arguments: list[str], **kwargs: Any) -> str:
        # -f /dev/null prevents user plugins/configuration from attaching to
        # the isolated supervisor session or delaying process startup.
        command = ["tmux", "-f", "/dev/null", "-S", str(socket_path), *arguments]
        try:
            result = subprocess.run(
                command,
                check=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                **kwargs,
            )
        except FileNotFoundError as exc:
            raise TransportUnavailable(
                "macOS Antigravity PTY requires tmux. Install it with "
                "`brew install tmux` and run doctor again."
            ) from exc
        except subprocess.CalledProcessError as exc:
            detail = (exc.stderr or exc.stdout or str(exc)).strip()
            raise RuntimeError(f"tmux command failed: {detail}") from exc
        return result.stdout

    @property
    def pid(self) -> int | None:
        return self._pid

    @property
    def metadata(self) -> dict[str, Any]:
        return {
            "backend": "tmux",
            "socket": str(self.socket_path),
            "session": self.session,
            "window": self.window,
            "target": self.target,
            "raw_log": str(self.raw_log),
            "pid": self._pid,
            "workspace": str(self.workspace),
        }

    def write(self, payload: str) -> None:
        # tmux's paste-buffer preserves spaces and punctuation; send-keys then
        # submits the line.  The prompt encoder supplies CR for the PTY route,
        # but tmux needs an explicit Enter key after pasting the text.
        text = payload.rstrip("\r\n")
        buffer_name = f"orchestrator-{safe_filename(self.session)}-{uuid.uuid4().hex}"
        if text:
            self._run(
                self.socket_path,
                ["load-buffer", "-b", buffer_name, "-"],
                input=text,
            )
            try:
                self._run(
                    self.socket_path,
                    ["paste-buffer", "-p", "-b", buffer_name, "-t", self.target],
                )
            finally:
                try:
                    self._run(self.socket_path, ["delete-buffer", "-b", buffer_name])
                except RuntimeError:
                    pass
        self._run(self.socket_path, ["send-keys", "-t", self.target, "Enter"])

    def poll(self) -> int | None:
        try:
            self._run(self.socket_path, ["has-session", "-t", self.session])
        except (RuntimeError, TransportUnavailable):
            if self.returncode is None:
                self.returncode = 0
            return self.returncode
        return None

    def wait(self, timeout: float | None = None) -> int:
        deadline = None if timeout is None else time.monotonic() + timeout
        while self.poll() is None:
            if deadline is not None and time.monotonic() >= deadline:
                raise subprocess.TimeoutExpired(self.command, timeout)
            time.sleep(0.1)
        return int(self.returncode or 0)

    def terminate(self) -> None:
        if self.poll() is not None:
            return
        try:
            self._run(self.socket_path, ["send-keys", "-t", self.target, "C-c"])
        finally:
            try:
                self._run(self.socket_path, ["kill-session", "-t", self.session])
            except RuntimeError:
                pass

    def kill(self) -> None:
        try:
            self._run(self.socket_path, ["kill-session", "-t", self.session])
        except RuntimeError:
            pass


class ProcessHandle:
    def __init__(
        self,
        dispatch_id: str,
        process: Any,
        log_file: Path,
        protocol: str,
        transport: str,
        transport_meta: dict[str, Any] | None = None,
    ) -> None:
        self.dispatch_id = dispatch_id
        self.process = process
        self.log_file = log_file
        self.protocol = protocol
        self.transport = transport
        self.transport_meta = transport_meta or {}
        self.write_lock = threading.Lock()
        self.current_thread: str | None = None
        self.current_turn: str | None = None
        self.ready = threading.Event()

    @property
    def pid(self) -> int | None:
        return getattr(self.process, "pid", None)

    @property
    def returncode(self) -> int | None:
        return getattr(self.process, "returncode", None)

    def poll(self) -> int | None:
        return self.process.poll()

    def wait(self, timeout: float | None = None) -> int:
        return int(self.process.wait(timeout=timeout))

    def write(self, payload: str) -> None:
        if self.transport == "stdio":
            stream = getattr(self.process, "stdin", None)
            if stream is None:
                raise OSError("Process stdin is not available.")
            stream.write(payload)
            stream.flush()
            return
        self.process.write(payload)

    def terminate(self) -> None:
        if self.transport == "stdio":
            terminate_process(self.process)
        else:
            self.process.terminate()

    def kill(self) -> None:
        if self.transport == "stdio":
            self.process.kill()
        else:
            self.process.kill()


class SupervisorService:
    def __init__(self, paths: RuntimePaths) -> None:
        paths.ensure()
        self.paths = paths
        self.store = Store(paths.db)
        self.handles: dict[str, ProcessHandle] = {}
        self._lock = threading.Lock()
        self._log_lock = threading.Lock()
        self._rehydrate_tmux_handles()

    def health(self) -> dict[str, Any]:
        return ok(
            pid=os.getpid(),
            runtime_root=str(self.paths.root),
            active_dispatches=sorted(self.handles),
        )

    def _rehydrate_tmux_handles(self) -> None:
        """Reattach live tmux routes after the localhost daemon restarts."""
        for record in self.store.list():
            if record.get("status") not in ACTIVE_STATES or record.get("transport") != "tmux":
                continue
            try:
                metadata = json.loads(str(record.get("transport_meta_json") or "{}"))
                command = json.loads(str(record.get("command_json") or "[]"))
                if not isinstance(metadata, dict) or not isinstance(command, list):
                    raise ValueError("Invalid tmux metadata in supervisor store.")
                transport = TmuxTransport.from_metadata(metadata, command)
                if transport.poll() is not None:
                    raise TransportUnavailable("Recorded tmux session is no longer running.")
                handle = ProcessHandle(
                    str(record["dispatch_id"]),
                    transport,
                    Path(str(record["log_file"])),
                    str(record["protocol"]),
                    "tmux",
                    metadata,
                )
                handle.current_thread = record.get("native_session_id")
                handle.current_turn = record.get("current_turn")
                with self._lock:
                    self.handles[handle.dispatch_id] = handle
                threading.Thread(target=self._tail_tmux_log, args=(handle,), daemon=True).start()
                threading.Thread(target=self._watch, args=(handle,), daemon=True).start()
                self._append_log(handle.log_file, {"event": "rehydrated", "pid": handle.pid})
            except (KeyError, TypeError, ValueError, OSError, RuntimeError, TransportUnavailable) as exc:
                self.store.update_process(
                    str(record["dispatch_id"]),
                    status="live-transport-unavailable",
                    error=f"Could not reattach tmux live route: {exc}",
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
        requested_transport = str(request.get("transport") or "stdio")
        if protocol == "antigravity-pty" and requested_transport == "stdio":
            requested_transport = "auto"
        started_at = utc_now()
        log_file = self.paths.logs / f"{safe_filename(dispatch_id)}.jsonl"

        try:
            transport = resolve_live_transport(provider, requested_transport)
            transport_meta: dict[str, Any] = {}
            if transport == "stdio":
                process: Any = subprocess.Popen(
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
            elif transport == "tmux":
                process = TmuxTransport.start(
                    self.paths.root,
                    dispatch_id,
                    command,
                    workspace,
                    log_file,
                )
                transport_meta = process.metadata
            elif transport == "winpty":
                process = WinPtyTransport.start(command, workspace)
                transport_meta = process.metadata
            else:  # pragma: no cover - resolve_live_transport is exhaustive.
                raise TransportUnavailable(f"Unsupported live transport: {transport}")
        except (OSError, TransportUnavailable, RuntimeError, ValueError) as exc:
            failed_transport = requested_transport
            try:
                failed_transport = resolve_live_transport(provider, requested_transport)
            except (TransportUnavailable, ValueError):
                pass
            record = {
                "dispatch_id": dispatch_id,
                "provider": provider,
                "protocol": protocol,
                "transport": failed_transport,
                "transport_meta_json": None,
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
            code = "invalid-request" if isinstance(exc, ValueError) else "dispatch-failed"
            if isinstance(exc, TransportUnavailable):
                code = "live-transport-unavailable"
            return err(code, str(exc))

        handle = ProcessHandle(
            dispatch_id,
            process,
            log_file,
            protocol,
            transport,
            transport_meta,
        )
        with self._lock:
            self.handles[dispatch_id] = handle

        self.store.upsert_process(
            {
                "dispatch_id": dispatch_id,
                "provider": provider,
                "protocol": protocol,
                "transport": transport,
                "transport_meta_json": json_dump(transport_meta),
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
        if transport == "stdio":
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
        elif transport == "winpty":
            threading.Thread(target=self._read_pty_stream, args=(handle,), daemon=True).start()
        elif transport == "tmux":
            threading.Thread(target=self._tail_tmux_log, args=(handle,), daemon=True).start()
        threading.Thread(target=self._watch, args=(handle,), daemon=True).start()
        if protocol == "codex-app-server":
            threading.Thread(target=self._bootstrap_codex, args=(handle,), daemon=True).start()
        return ok(
            dispatch_id=dispatch_id,
            pid=process.pid,
            log_file=str(log_file),
            transport=transport,
            transport_meta=transport_meta,
        )

    def send_prompt(self, request: dict[str, Any]) -> dict[str, Any]:
        dispatch_id = str(request.get("dispatch_id") or "").strip()
        prompt = request.get("prompt")
        if not dispatch_id or not isinstance(prompt, str):
            return err("invalid-request", "dispatch_id and prompt are required.")
        with self._lock:
            handle = self.handles.get(dispatch_id)
        if handle is None or handle.poll() is not None:
            self.store.update_process(
                dispatch_id,
                status="live-transport-unavailable",
                error="No retained live process handle is available.",
            )
            return err(
                "live-transport-unavailable",
                "No retained live process handle is available.",
            )
        if handle.transport == "stdio" and handle.process.stdin is None:
            return err("live-transport-unavailable", "Process stdin is not available.")

        if handle.protocol == "codex-app-server":
            ready = handle.ready.wait(timeout=READY_TIMEOUT_SECONDS)
            if not ready or not handle.current_thread:
                self.store.update_process(
                    dispatch_id,
                    status="live-transport-unavailable",
                    error="Codex app-server handshake did not produce a thread ID.",
                )
                return err(
                    "live-transport-unavailable",
                    "Codex app-server handshake did not produce a thread ID.",
                )

        try:
            payload = encode_prompt(
                handle.protocol,
                prompt,
                handle.current_turn,
                handle.current_thread,
            )
            with handle.write_lock:
                handle.write(payload)
        except ValueError as exc:
            return err("invalid-request", str(exc))
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
            expose_transport_metadata(record)
            record["live_handle"] = str(dispatch_id) in self.handles
            return ok(process=record)
        rows = self.store.list()
        for row in rows:
            expose_transport_metadata(row)
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

        if handle.poll() is None:
            handle.terminate()
            try:
                handle.wait(timeout=float(request.get("timeout") or 5))
            except subprocess.TimeoutExpired:
                handle.kill()
                handle.wait(timeout=5)
        with self._lock:
            self.handles.pop(dispatch_id, None)
        self.store.update_process(
            dispatch_id,
            status="stopped",
            exit_code=handle.returncode,
        )
        self._append_log(handle.log_file, {"event": "stopped", "exit_code": handle.returncode})
        return ok(dispatch_id=dispatch_id, stopped=True, exit_code=handle.returncode)

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

    def _read_pty_stream(self, handle: ProcessHandle) -> None:
        """Read output from a WinPTY process and normalize it into JSONL."""
        while True:
            try:
                chunk = handle.process.read(4096)
            except (EOFError, OSError, RuntimeError):
                break
            if not chunk:
                if handle.poll() is not None:
                    break
                time.sleep(0.05)
                continue
            for line in str(chunk).replace("\r\n", "\n").replace("\r", "\n").splitlines():
                self._append_log(handle.log_file, {"event": "stdout", "line": line})
                self._learn_from_line(handle, line)

    def _tail_tmux_log(self, handle: ProcessHandle) -> None:
        """Tail tmux's raw pane capture and emit supervisor JSONL events."""
        raw_path = Path(str(handle.transport_meta.get("raw_log", "")))
        offset = 0
        while True:
            if raw_path.exists():
                with raw_path.open("r", encoding="utf-8", errors="replace") as source:
                    source.seek(offset)
                    chunk = source.read()
                    offset = source.tell()
                for line in chunk.replace("\r\n", "\n").replace("\r", "\n").splitlines():
                    self._append_log(handle.log_file, {"event": "stdout", "line": line})
                    self._learn_from_line(handle, line)
            if handle.poll() is not None:
                # One final pass closes the small race between pane exit and
                # the pipe-pane writer flushing its last bytes.
                if raw_path.exists():
                    with raw_path.open("r", encoding="utf-8", errors="replace") as source:
                        source.seek(offset)
                        chunk = source.read()
                    if chunk:
                        offset += len(chunk)
                        for line in chunk.replace("\r\n", "\n").replace("\r", "\n").splitlines():
                            self._append_log(handle.log_file, {"event": "stdout", "line": line})
                            self._learn_from_line(handle, line)
                break
            time.sleep(0.05)

    def _watch(self, handle: ProcessHandle) -> None:
        exit_code = handle.wait()
        handle.ready.set()
        with self._lock:
            self.handles.pop(handle.dispatch_id, None)
        status = "stopped" if exit_code == 0 else "worker-error"
        try:
            self.store.update_process(handle.dispatch_id, status=status, exit_code=exit_code)
            self._append_log(handle.log_file, {"event": "exited", "exit_code": exit_code})
        except (OSError, sqlite3.OperationalError):
            # A short-lived fixture may remove its temporary runtime directory
            # immediately after stopping the daemon.  The process is already
            # gone, so there is no durable state left for this watcher to write.
            return

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
        result = payload.get("result")
        if isinstance(result, dict):
            thread = result.get("thread")
            if isinstance(thread, dict):
                session_id = session_id or first_string(thread, "id", "thread_id", "threadId")
            turn = result.get("turn")
            if isinstance(turn, dict):
                turn_id = turn_id or first_string(turn, "id", "turn_id", "turnId")
        if isinstance(params, dict):
            session_id = session_id or first_string(
                params, "session_id", "sessionId", "thread_id", "threadId"
            )
            turn_id = turn_id or first_string(params, "turn_id", "turnId")
            thread = params.get("thread")
            if isinstance(thread, dict):
                session_id = session_id or first_string(thread, "id", "thread_id", "threadId")
            turn = params.get("turn")
            if isinstance(turn, dict):
                turn_id = turn_id or first_string(turn, "id", "turn_id", "turnId")
        if session_id and handle.protocol == "codex-app-server":
            handle.current_thread = session_id
            handle.ready.set()
        if method == "turn/started" and turn_id:
            handle.current_turn = turn_id
        elif method == "turn/completed":
            handle.current_turn = None
            turn_id = None

        updates: dict[str, Any] = {}
        if session_id:
            updates["native_session_id"] = session_id
        if turn_id:
            updates["current_turn"] = turn_id
        elif method == "turn/completed":
            updates["current_turn"] = None
        if updates:
            self.store.update_process(handle.dispatch_id, **updates)

    def _bootstrap_codex(self, handle: ProcessHandle) -> None:
        """Initialize one Codex app-server and retain its thread ID."""
        initialize_id = str(uuid.uuid4())
        thread_start_id = str(uuid.uuid4())
        try:
            with handle.write_lock:
                if handle.process.stdin is None:
                    raise OSError("Process stdin is not available.")
                handle.process.stdin.write(
                    json_dump(
                        {
                            "method": "initialize",
                            "id": initialize_id,
                            "params": {
                                "clientInfo": {
                                    "name": "orchestrator-cli-supervisor",
                                    "title": "Orchestrator CLI Supervisor",
                                    "version": "1.0",
                                }
                            },
                        }
                    )
                    + "\n"
                )
                handle.process.stdin.write(
                    json_dump({"method": "initialized", "params": {}}) + "\n"
                )
                handle.process.stdin.write(
                    json_dump(
                        {
                            "method": "thread/start",
                            "id": thread_start_id,
                            "params": {},
                        }
                    )
                    + "\n"
                )
                handle.process.stdin.flush()
        except (OSError, RuntimeError, TransportUnavailable) as exc:
            self.store.update_process(
                handle.dispatch_id,
                status="live-transport-unavailable",
                error=str(exc),
            )
            handle.ready.set()

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


def expose_transport_metadata(record: dict[str, Any]) -> None:
    raw = record.pop("transport_meta_json", None)
    if isinstance(raw, str) and raw:
        try:
            metadata = json.loads(raw)
        except json.JSONDecodeError:
            metadata = {}
    else:
        metadata = {}
    record["transport_meta"] = metadata if isinstance(metadata, dict) else {}


def encode_prompt(
    protocol: str,
    prompt: str,
    current_turn: str | None,
    current_thread: str | None = None,
) -> str:
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
        if not current_thread:
            raise ValueError("Codex app-server thread ID is not ready.")
        request_id = str(uuid.uuid4())
        if current_turn:
            return (
                json_dump(
                    {
                        "id": request_id,
                        "method": "turn/steer",
                        "params": {
                            "threadId": current_thread,
                            "expectedTurnId": current_turn,
                            "input": [{"type": "text", "text": prompt}],
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
                        "params": {
                            "threadId": current_thread,
                            "input": [{"type": "text", "text": prompt}],
                        },
                }
            )
            + "\n"
        )
    if protocol == "antigravity-pty":
        # A TUI reads a human prompt from the terminal line discipline.  CR
        # is the portable submit key for the PTY adapters in this supervisor.
        return prompt + "\r"
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
    server_process = subprocess.Popen(command, **kwargs)
    server_log.close()

    deadline = time.monotonic() + connect_timeout()
    while time.monotonic() < deadline:
        time.sleep(0.1)
        info = read_server_info(paths)
        if info and info.get("token") == token:
            health = rpc(paths, "health")
            if health.get("ok"):
                return health
    # Do not leave a detached daemon behind when readiness fails. In
    # particular, a slow/blocked interpreter could otherwise survive the
    # caller's timeout and make later test runs appear to hang as well.
    if server_process.poll() is None:
        terminate_process(server_process)
        try:
            server_process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server_process.kill()
            server_process.wait(timeout=5)
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
    tmux_path = shutil.which("tmux")
    winpty_available = False
    if sys.platform == "win32":
        try:
            import winpty  # type: ignore[import-not-found,unused-ignore]

            winpty_available = bool(winpty)
        except ImportError:
            winpty_available = False
    missing_live_transports: list[str] = []
    if sys.platform == "darwin" and not tmux_path:
        missing_live_transports.append(
            "tmux for Antigravity PTY (install with `brew install tmux`)"
        )
    if sys.platform == "win32" and not winpty_available:
        missing_live_transports.append(
            "pywinpty for Antigravity PTY (install with `py -m pip install pywinpty`)"
        )
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
        protocols=SUPPORTED_PROTOCOLS,
        live_transports={
            "antigravity-macos": {"backend": "tmux", "path": tmux_path},
            "antigravity-windows": {"backend": "pywinpty", "available": winpty_available},
        },
        unsupported_without_optional_runtime=missing_live_transports,
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
    start.add_argument("--protocol", default="text", choices=SUPPORTED_PROTOCOLS)
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

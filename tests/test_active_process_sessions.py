"""Opt-in probes for multiple turns delivered to one still-running CLI process."""

from __future__ import annotations

import json
import os
import queue
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator


LIVE_FLAG = "RUN_LIVE_ACTIVE_PROCESS_TESTS"
PROVIDERS = frozenset({"claude", "codex"})


def selected_providers() -> frozenset[str]:
    raw = os.environ.get("ACTIVE_PROCESS_TEST_PROVIDERS", "claude,codex")
    selected = frozenset(item.strip().lower() for item in raw.split(",") if item.strip())
    unknown = selected - PROVIDERS
    if unknown:
        raise ValueError(
            "ACTIVE_PROCESS_TEST_PROVIDERS supports only "
            f"{', '.join(sorted(PROVIDERS))}; received {', '.join(sorted(unknown))}."
        )
    return selected


def timeout_seconds() -> int:
    raw = os.environ.get("ACTIVE_PROCESS_TEST_TIMEOUT_SECONDS", "180")
    try:
        timeout = int(raw)
    except ValueError as error:
        raise ValueError("ACTIVE_PROCESS_TEST_TIMEOUT_SECONDS must be a whole number.") from error
    if timeout < 30:
        raise ValueError("ACTIVE_PROCESS_TEST_TIMEOUT_SECONDS must be at least 30.")
    return timeout


def cli_command(executable: str) -> list[str]:
    resolved = shutil.which(executable)
    if not resolved:
        raise FileNotFoundError(f"{executable!r} is not on PATH.")

    path = Path(resolved)
    if os.name == "nt" and path.suffix.lower() == ".ps1":
        command_wrapper = path.with_suffix(".cmd")
        if command_wrapper.is_file():
            return [str(command_wrapper)]

        powershell = shutil.which("pwsh") or shutil.which("powershell")
        if powershell:
            return [powershell, "-NoProfile", "-File", str(path)]
        raise FileNotFoundError(f"No executable wrapper is available for {path}.")
    return [str(path)]


class JsonlProcess:
    def __init__(
        self,
        command: list[str],
        cwd: Path,
        *,
        env: dict[str, str] | None = None,
    ) -> None:
        self.process = subprocess.Popen(
            command,
            cwd=cwd,
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        self.events: queue.Queue[dict[str, Any]] = queue.Queue()
        self.seen: list[dict[str, Any]] = []
        self._stdout_thread = threading.Thread(target=self._read_stdout, daemon=True)
        self._stderr_thread = threading.Thread(target=self._drain_stderr, daemon=True)
        self._stdout_thread.start()
        self._stderr_thread.start()

    def _read_stdout(self) -> None:
        assert self.process.stdout is not None
        for line in self.process.stdout:
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(event, dict):
                self.events.put(event)

    def _drain_stderr(self) -> None:
        assert self.process.stderr is not None
        for _ in self.process.stderr:
            pass

    def send(self, message: dict[str, Any]) -> None:
        if self.process.stdin is None:
            raise RuntimeError("The process has no writable stdin.")
        self.process.stdin.write(json.dumps(message, ensure_ascii=False) + "\n")
        self.process.stdin.flush()

    def wait_for(
        self,
        predicate: Callable[[dict[str, Any]], bool],
        timeout: int,
        label: str,
    ) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise RuntimeError(f"Process exited before {label} (code {self.process.returncode}).")
            try:
                event = self.events.get(timeout=min(0.25, deadline - time.monotonic()))
            except queue.Empty:
                continue
            self.seen.append(event)
            if predicate(event):
                return event
        raise TimeoutError(f"Timed out waiting for {label}.")

    def close(self) -> None:
        if self.process.stdin and not self.process.stdin.closed:
            self.process.stdin.close()
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
        for stream in (self.process.stdout, self.process.stderr):
            if stream and not stream.closed:
                stream.close()
        self._stdout_thread.join(timeout=1)
        self._stderr_thread.join(timeout=1)


class ActiveProcessLiveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if os.environ.get(LIVE_FLAG) != "1":
            raise unittest.SkipTest(
                f"Set {LIVE_FLAG}=1 to run authenticated live-process probes."
            )
        cls.providers = selected_providers()
        if not cls.providers:
            raise unittest.SkipTest("No providers were selected.")
        cls.timeout = timeout_seconds()

    def require_provider(self, provider: str) -> None:
        if provider not in self.providers:
            self.skipTest(f"{provider} was not selected in ACTIVE_PROCESS_TEST_PROVIDERS.")

    @contextmanager
    def temporary_git_repository(self) -> Iterator[Path]:
        root = Path(tempfile.mkdtemp(prefix="cli-agent-skills-active-process-"))
        workspace = root / "workspace"
        workspace.mkdir()
        initialization = subprocess.run(
            ["git", "init", "--quiet"],
            cwd=workspace,
            text=True,
            capture_output=True,
            errors="replace",
            check=False,
        )
        if initialization.returncode:
            self.fail("Could not initialize the temporary Git repository.")

        try:
            yield workspace
        finally:
            self.remove_tree_with_retry(root)

    @staticmethod
    def remove_tree_with_retry(path: Path) -> None:
        for _ in range(8):
            try:
                shutil.rmtree(path)
                return
            except FileNotFoundError:
                return
            except OSError:
                time.sleep(0.75)

    def assert_workspace_clean(self, workspace: Path) -> None:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=workspace,
            text=True,
            capture_output=True,
            errors="replace",
            check=False,
        )
        self.assertEqual(result.returncode, 0, "Could not inspect the temporary workspace.")
        self.assertEqual(result.stdout, "", "The probe changed the temporary workspace.")

    def isolated_codex_environment(self, workspace: Path) -> dict[str, str]:
        codex_home = workspace.parent / "codex-home"
        codex_home.mkdir()
        environment = os.environ.copy()
        environment["CODEX_HOME"] = str(codex_home)
        environment["CODEX_SQLITE_HOME"] = str(codex_home / "sqlite")
        environment["RUST_LOG"] = "error"

        auth_file = os.environ.get("ACTIVE_PROCESS_CODEX_AUTH_FILE")
        if auth_file:
            source = Path(auth_file).expanduser()
            if not source.is_file():
                self.fail("ACTIVE_PROCESS_CODEX_AUTH_FILE does not name a readable file.")
            shutil.copy2(source, codex_home / "auth.json")
        elif not environment.get("CODEX_ACCESS_TOKEN"):
            self.skipTest(
                "Set CODEX_ACCESS_TOKEN or ACTIVE_PROCESS_CODEX_AUTH_FILE to run the "
                "isolated Codex active-process probe."
            )
        return environment

    def test_claude_stream_json_keeps_one_process_for_two_turns(self) -> None:
        self.require_provider("claude")
        nonce = f"claude-active-{uuid.uuid4().hex}"
        follow_up = (
            "Without using tools, return exactly the token from my previous message."
        )
        budget = os.environ.get("ACTIVE_PROCESS_CLAUDE_BUDGET_USD", "1")

        with self.temporary_git_repository() as workspace:
            command = cli_command("claude") + [
                "-p",
                "--input-format",
                "stream-json",
                "--output-format",
                "stream-json",
                "--no-session-persistence",
                "--tools",
                "",
                "--permission-mode",
                "plan",
                "--safe-mode",
                "--max-budget-usd",
                budget,
            ]
            process = JsonlProcess(command, workspace)
            try:
                process.send(
                    {
                        "type": "user",
                        "message": {
                            "role": "user",
                            "content": (
                                "Do not use tools, inspect files, run commands, or change "
                                f"anything. Remember this exact token: {nonce}. Reply with it."
                            ),
                        },
                    }
                )
                initial = process.wait_for(
                    lambda event: event.get("type") == "result",
                    self.timeout,
                    "Claude's first result",
                )
                session_id = initial.get("session_id")
                self.assertIsInstance(session_id, str)
                self.assertIn(nonce, str(initial.get("result", "")))
                self.assertIsNone(process.process.poll(), "Claude exited after the first turn.")

                process.send(
                    {
                        "type": "user",
                        "message": {"role": "user", "content": follow_up},
                    }
                )
                follow_up_result = process.wait_for(
                    lambda event: event.get("type") == "result",
                    self.timeout,
                    "Claude's second result",
                )
                self.assertEqual(follow_up_result.get("session_id"), session_id)
                self.assertIn(nonce, str(follow_up_result.get("result", "")))
            finally:
                process.close()
            self.assert_workspace_clean(workspace)

    def test_codex_app_server_steers_an_in_flight_turn(self) -> None:
        self.require_provider("codex")
        nonce = f"codex-steer-{uuid.uuid4().hex}"

        with self.temporary_git_repository() as workspace:
            environment = self.isolated_codex_environment(workspace)
            command = cli_command("codex") + ["app-server", "--stdio"]
            process = JsonlProcess(command, workspace, env=environment)
            try:
                process.send(
                    {
                        "method": "initialize",
                        "id": 1,
                        "params": {
                            "clientInfo": {
                                "name": "cli-agent-skills-active-process-test",
                                "title": "CLI Agent Skills Active Process Test",
                                "version": "1.0",
                            }
                        },
                    }
                )
                process.wait_for(
                    lambda event: event.get("id") == 1 and "result" in event,
                    self.timeout,
                    "Codex app-server initialization",
                )
                process.send({"method": "initialized", "params": {}})
                process.send({"method": "thread/start", "id": 2, "params": {}})
                thread_response = process.wait_for(
                    lambda event: event.get("id") == 2 and "result" in event,
                    self.timeout,
                    "Codex thread/start",
                )
                thread_id = thread_response.get("result", {}).get("thread", {}).get("id")
                self.assertIsInstance(thread_id, str)

                first_turn = self.start_codex_turn(
                    process,
                    3,
                    thread_id,
                    (
                        "Without using tools, begin a detailed 3,000-word explanation of "
                        "how HTTP caching works. Do not make changes or inspect files. "
                        f"End with: initial {nonce}"
                    ),
                )
                steer_request = {
                    "method": "turn/steer",
                    "id": 4,
                    "params": {
                        "threadId": thread_id,
                        "expectedTurnId": first_turn,
                        "input": [
                            {
                                "type": "text",
                                "text": (
                                    "Keep the same turn. Stop the long explanation and reply "
                                    f"exactly: steered {nonce}"
                                ),
                            }
                        ],
                    },
                }
                process.send(steer_request)
                steer_response = process.wait_for(
                    lambda event: event.get("id") == 4,
                    self.timeout,
                    "Codex turn/steer response",
                )
                self.assertNotIn(
                    "error",
                    steer_response,
                    f"Codex rejected turn/steer: {steer_response}",
                )
                self.assertEqual(
                    steer_response.get("result", {}).get("turnId"),
                    first_turn,
                    "Codex accepted steering for a different turn.",
                )

                process.send(
                    {
                        "method": "turn/interrupt",
                        "id": 5,
                        "params": {"threadId": thread_id, "turnId": first_turn},
                    }
                )
                interrupt_response = process.wait_for(
                    lambda event: event.get("id") == 5,
                    self.timeout,
                    "Codex turn/interrupt response",
                )
                self.assertNotIn(
                    "error",
                    interrupt_response,
                    f"Codex rejected turn/interrupt: {interrupt_response}",
                )

                first_completion = self.wait_for_codex_turn(process, first_turn)
                self.assertEqual(
                    first_completion.get("params", {}).get("turn", {}).get("status"),
                    "interrupted",
                    "Codex did not cleanly interrupt the steered turn.",
                )
                self.assertIsNone(process.process.poll(), "Codex app-server exited after steering.")
            finally:
                process.close()
            self.assert_workspace_clean(workspace)

    def start_codex_turn(
        self,
        process: JsonlProcess,
        request_id: int,
        thread_id: str,
        prompt: str,
    ) -> str:
        process.send(
            {
                "method": "turn/start",
                "id": request_id,
                "params": {
                    "threadId": thread_id,
                    "input": [{"type": "text", "text": prompt}],
                },
            }
        )
        started = process.wait_for(
            lambda event: (
                event.get("method") == "turn/started"
                and event.get("params", {}).get("threadId") == thread_id
            ),
            self.timeout,
            f"Codex turn/started for request {request_id}",
        )
        turn_id = started.get("params", {}).get("turn", {}).get("id")
        if not isinstance(turn_id, str):
            self.fail("Codex app-server did not announce a turn ID.")
        return turn_id

    def wait_for_codex_turn(self, process: JsonlProcess, turn_id: str) -> dict[str, Any]:
        return process.wait_for(
            lambda event: (
                event.get("method") == "turn/completed"
                and event.get("params", {}).get("turn", {}).get("id") == turn_id
            ),
            self.timeout,
            f"Codex turn/completed for {turn_id}",
        )

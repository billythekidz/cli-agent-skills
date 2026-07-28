"""Local supervisor tests that do not require real provider credentials."""

from __future__ import annotations

import json
import importlib.util
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from unittest import mock
from pathlib import Path


REPOSITORY = Path(__file__).resolve().parents[1]
SUPERVISOR = (
    REPOSITORY
    / "skills"
    / "orchestrator-cli"
    / "scripts"
    / "orchestrator_supervisor.py"
)


def load_supervisor_module():
    spec = importlib.util.spec_from_file_location("orchestrator_supervisor", SUPERVISOR)
    if spec is None or spec.loader is None:
        raise AssertionError(f"Could not load supervisor module from {SUPERVISOR}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def run_supervisor(
    runtime_root: Path,
    *args: str,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        [
            sys.executable,
            str(SUPERVISOR),
            "--runtime-root",
            str(runtime_root),
            "--json",
            *args,
        ],
        text=True,
        capture_output=True,
        errors="replace",
        timeout=20,
        check=False,
    )
    if check and result.returncode:
        raise AssertionError(
            f"supervisor command failed: {args!r}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result


def payload(result: subprocess.CompletedProcess[str]) -> dict[str, object]:
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise AssertionError(f"stdout was not JSON: {result.stdout!r}") from error
    assert isinstance(data, dict)
    return data


def shutdown_and_wait(runtime_root: Path) -> None:
    run_supervisor(runtime_root, "shutdown", check=False)
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        result = run_supervisor(runtime_root, "status", check=False)
        if result.returncode != 0:
            return
        data = payload(result)
        if not data.get("ok"):
            return
        time.sleep(0.1)


class OrchestratorSupervisorTests(unittest.TestCase):
    def test_server_start_timeout_reaps_unready_daemon(self) -> None:
        supervisor = load_supervisor_module()
        with tempfile.TemporaryDirectory(prefix="orchestrator-supervisor-timeout-") as temp:
            root = Path(temp)
            runtime = root / "runtime"
            blocked_server = root / "blocked_server.py"
            blocked_server.write_text(
                "import time\ntime.sleep(30)\n",
                encoding="utf-8",
            )

            with mock.patch.object(supervisor, "connect_timeout", return_value=0.2):
                result = supervisor.ensure_server(
                    supervisor.RuntimePaths(runtime), blocked_server
                )

            self.assertFalse(result["ok"])
            self.assertEqual(result["error"]["code"], "server-start-timeout")
            self.assertFalse((runtime / "server.json").exists())

    def test_codex_app_server_encoder_matches_documented_wire_protocol(self) -> None:
        supervisor = load_supervisor_module()

        start = json.loads(
            supervisor.encode_prompt("codex-app-server", "first", None, "thread-1")
        )
        self.assertEqual(start["method"], "turn/start")
        self.assertEqual(start["params"]["threadId"], "thread-1")
        self.assertEqual(start["params"]["input"], [{"type": "text", "text": "first"}])

        steer = json.loads(
            supervisor.encode_prompt("codex-app-server", "second", "turn-1", "thread-1")
        )
        self.assertEqual(steer["method"], "turn/steer")
        self.assertEqual(steer["params"]["threadId"], "thread-1")
        self.assertEqual(steer["params"]["expectedTurnId"], "turn-1")
        self.assertEqual(steer["params"]["input"], [{"type": "text", "text": "second"}])

        with self.assertRaisesRegex(ValueError, "thread ID"):
            supervisor.encode_prompt("codex-app-server", "missing", None, None)

    def test_codex_app_server_fixture_bootstraps_and_steers_one_handle(self) -> None:
        with tempfile.TemporaryDirectory(prefix="orchestrator-supervisor-codex-") as temp:
            root = Path(temp)
            runtime = root / "runtime"
            workspace = root / "workspace"
            workspace.mkdir()
            worker = root / "codex_app_server_fixture.py"
            worker.write_text(
                textwrap.dedent(
                    """
                    import json
                    import sys

                    turn_number = 0
                    for line in sys.stdin:
                        request = json.loads(line)
                        print(json.dumps({"event": "request", "request": request}), flush=True)
                        method = request.get("method")
                        if method == "initialize":
                            print(json.dumps({"id": request.get("id"), "result": {"ok": True}}), flush=True)
                        elif method == "thread/start":
                            print(json.dumps({
                                "id": request.get("id"),
                                "result": {"thread": {"id": "thread-fixture"}},
                            }), flush=True)
                        elif method == "turn/start":
                            turn_number += 1
                            print(json.dumps({
                                "method": "turn/started",
                                "params": {
                                    "threadId": "thread-fixture",
                                    "turn": {"id": f"turn-{turn_number}"},
                                },
                            }), flush=True)
                        elif method == "turn/steer":
                            print(json.dumps({
                                "method": "turn/completed",
                                "params": {"turn": {"id": "turn-1", "status": "completed"}},
                            }), flush=True)
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )

            dispatch_id = "task-TASK-codex-attempt-1"
            try:
                started = payload(
                    run_supervisor(
                        runtime,
                        "start",
                        "--dispatch-id",
                        dispatch_id,
                        "--provider",
                        "codex-cli",
                        "--protocol",
                        "codex-app-server",
                        "--workspace",
                        str(workspace),
                        "--",
                        sys.executable,
                        str(worker),
                    )
                )
                self.assertTrue(started["ok"])
                first_pid = started["pid"]

                self.assertTrue(
                    payload(run_supervisor(runtime, "send", dispatch_id, "first prompt"))["ok"]
                )

                def process_status() -> dict[str, object]:
                    current = payload(run_supervisor(runtime, "status", dispatch_id))
                    process = current.get("process")
                    self.assertIsInstance(process, dict)
                    return process

                deadline = time.monotonic() + 5
                process = process_status()
                while time.monotonic() < deadline and process.get("current_turn") != "turn-1":
                    time.sleep(0.05)
                    process = process_status()
                self.assertEqual(process.get("current_turn"), "turn-1")
                self.assertEqual(process.get("native_session_id"), "thread-fixture")

                self.assertTrue(
                    payload(run_supervisor(runtime, "send", dispatch_id, "steer prompt"))["ok"]
                )
                deadline = time.monotonic() + 5
                process = process_status()
                while time.monotonic() < deadline and process.get("current_turn") is not None:
                    time.sleep(0.05)
                    process = process_status()
                self.assertIsNone(process.get("current_turn"))

                self.assertTrue(
                    payload(run_supervisor(runtime, "send", dispatch_id, "queued prompt"))["ok"]
                )
                status = process_status()
                self.assertEqual(status.get("pid"), first_pid)
                self.assertTrue(status.get("live_handle"))

                log_file = Path(str(status["log_file"]))
                deadline = time.monotonic() + 5
                requests: list[dict[str, object]] = []
                while time.monotonic() < deadline:
                    requests = []
                    for line in log_file.read_text(encoding="utf-8").splitlines():
                        event = json.loads(line)
                        request = event.get("line")
                        if event.get("event") == "stdout" and isinstance(request, str):
                            message = json.loads(request)
                            if message.get("event") == "request":
                                request_body = message.get("request")
                                if isinstance(request_body, dict):
                                    requests.append(request_body)
                    if any(request.get("method") == "turn/steer" for request in requests):
                        break
                    time.sleep(0.05)

                methods = [request.get("method") for request in requests]
                self.assertIn("initialize", methods)
                self.assertIn("thread/start", methods)
                self.assertIn("turn/start", methods)
                self.assertIn("turn/steer", methods)
                steer_requests = [request for request in requests if request.get("method") == "turn/steer"]
                self.assertEqual(steer_requests[0]["params"]["threadId"], "thread-fixture")
                self.assertEqual(steer_requests[0]["params"]["expectedTurnId"], "turn-1")
            finally:
                shutdown_and_wait(runtime)

    def test_supervisor_keeps_one_process_and_injects_multiple_prompts(self) -> None:
        with tempfile.TemporaryDirectory(prefix="orchestrator-supervisor-") as temp:
            root = Path(temp)
            runtime = root / "runtime"
            workspace = root / "workspace"
            workspace.mkdir()
            worker = root / "echo_worker.py"
            worker.write_text(
                textwrap.dedent(
                    """
                    import json
                    import sys

                    print(json.dumps({"session_id": "fixture-session", "event": "ready"}), flush=True)
                    for index, line in enumerate(sys.stdin, start=1):
                        print(json.dumps({"event": "prompt", "index": index, "text": line.strip()}), flush=True)
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )

            try:
                doctor = payload(run_supervisor(runtime, "doctor"))
                self.assertTrue(doctor["ok"])
                self.assertFalse(doctor["server_running"])

                started = payload(
                    run_supervisor(
                        runtime,
                        "start",
                        "--dispatch-id",
                        "task-TASK-1-attempt-1",
                        "--provider",
                        "fixture",
                        "--protocol",
                        "text",
                        "--workspace",
                        str(workspace),
                        "--",
                        sys.executable,
                        str(worker),
                    )
                )
                self.assertTrue(started["ok"])
                first_pid = started["pid"]

                first_send = payload(
                    run_supervisor(runtime, "send", "task-TASK-1-attempt-1", "first prompt")
                )
                second_send = payload(
                    run_supervisor(runtime, "send", "task-TASK-1-attempt-1", "second prompt")
                )
                self.assertTrue(first_send["ok"])
                self.assertTrue(second_send["ok"])

                status = payload(run_supervisor(runtime, "status", "task-TASK-1-attempt-1"))
                self.assertTrue(status["ok"])
                process = status["process"]
                self.assertIsInstance(process, dict)
                self.assertEqual(process["pid"], first_pid)
                self.assertEqual(process["native_session_id"], "fixture-session")
                self.assertTrue(process["live_handle"])

                log_file = Path(str(process["log_file"]))
                deadline = time.monotonic() + 5
                log_text = ""
                while time.monotonic() < deadline:
                    log_text = log_file.read_text(encoding="utf-8")
                    if "second prompt" in log_text:
                        break
                    time.sleep(0.1)
                self.assertIn("first prompt", log_text)
                self.assertIn("second prompt", log_text)
            finally:
                shutdown_and_wait(runtime)

    def test_send_after_shutdown_reports_live_transport_unavailable(self) -> None:
        with tempfile.TemporaryDirectory(prefix="orchestrator-supervisor-") as temp:
            runtime = Path(temp) / "runtime"
            stopped = payload(run_supervisor(runtime, "send", "missing-dispatch", "hello", check=False))
            self.assertFalse(stopped["ok"])
            error = stopped["error"]
            self.assertIsInstance(error, dict)
            self.assertIn(
                error["code"],
                {"server-not-running", "server-unreachable", "live-transport-unavailable"},
            )

    def test_doctor_reports_platform_and_documented_protocols(self) -> None:
        with tempfile.TemporaryDirectory(prefix="orchestrator-supervisor-") as temp:
            runtime = Path(temp) / "runtime"
            doctor = payload(run_supervisor(runtime, "doctor"))
            self.assertTrue(doctor["ok"])
            # doctor names the current platform (darwin/linux/win32) and the
            # Four protocol-native routes plus Antigravity's PTY route.
            self.assertIsInstance(doctor["platform"], str)
            self.assertEqual(
                doctor["protocols"],
                [
                    "text",
                    "jsonl",
                    "claude-stream-json",
                    "codex-app-server",
                    "antigravity-pty",
                ],
            )
            self.assertFalse(doctor["server_running"])
            # The supervisor resolves the runtime root (following the macOS
            # /var -> /private/var symlink), so compare resolved paths.
            self.assertEqual(doctor["runtime_root"], str(runtime.resolve()))
            self.assertIsInstance(doctor["live_transports"], dict)
            self.assertEqual(doctor["live_transports"]["antigravity-macos"]["backend"], "tmux")

    def test_status_lists_all_dispatches_without_an_id(self) -> None:
        with tempfile.TemporaryDirectory(prefix="orchestrator-supervisor-") as temp:
            root = Path(temp)
            runtime = root / "runtime"
            workspace = root / "workspace"
            workspace.mkdir()
            worker = root / "idle_worker.py"
            worker.write_text(
                "import sys, time\n"
                "for _ in sys.stdin:\n"
                "    pass\n"
                "time.sleep(0)\n",
                encoding="utf-8",
            )
            try:
                started = payload(
                    run_supervisor(
                        runtime,
                        "start",
                        "--dispatch-id",
                        "task-TASK-list-1-attempt-1",
                        "--provider",
                        "fixture",
                        "--protocol",
                        "text",
                        "--workspace",
                        str(workspace),
                        "--",
                        sys.executable,
                        str(worker),
                    )
                )
                self.assertTrue(started["ok"])
                # status with no dispatch_id returns the full list.
                listing = payload(run_supervisor(runtime, "status"))
                self.assertTrue(listing["ok"])
                processes = listing["processes"]
                self.assertIsInstance(processes, list)
                ids = [p["dispatch_id"] for p in processes]
                self.assertIn("task-TASK-list-1-attempt-1", ids)
            finally:
                shutdown_and_wait(runtime)

    def test_status_for_unknown_dispatch_reports_not_found(self) -> None:
        with tempfile.TemporaryDirectory(prefix="orchestrator-supervisor-") as temp:
            root = Path(temp)
            runtime = root / "runtime"
            workspace = root / "workspace"
            workspace.mkdir()
            worker = root / "idle_worker.py"
            worker.write_text("import sys\nfor _ in sys.stdin: pass\n", encoding="utf-8")
            try:
                # Start one real dispatch so the server is running and the DB
                # exists; otherwise status short-circuits to server-not-running.
                started = payload(
                    run_supervisor(
                        runtime,
                        "start",
                        "--dispatch-id",
                        "task-TASK-known-attempt-1",
                        "--provider",
                        "fixture",
                        "--protocol",
                        "text",
                        "--workspace",
                        str(workspace),
                        "--",
                        sys.executable,
                        str(worker),
                    )
                )
                self.assertTrue(started["ok"])
                # A different dispatch id that was never started is not-found.
                missing = payload(
                    run_supervisor(runtime, "status", "no-such-dispatch", check=False)
                )
                self.assertFalse(missing["ok"])
                self.assertEqual(missing["error"]["code"], "not-found")
            finally:
                shutdown_and_wait(runtime)

    def test_start_rejects_nonexistent_workspace(self) -> None:
        with tempfile.TemporaryDirectory(prefix="orchestrator-supervisor-") as temp:
            runtime = Path(temp) / "runtime"
            bogus_workspace = Path(temp) / "does-not-exist"
            result = payload(
                run_supervisor(
                    runtime,
                    "start",
                    "--dispatch-id",
                    "task-TASK-badws-attempt-1",
                    "--provider",
                    "fixture",
                    "--protocol",
                    "text",
                    "--workspace",
                    str(bogus_workspace),
                    "--",
                    sys.executable,
                    "-c",
                    "pass",
                    check=False,
                )
            )
            self.assertFalse(result["ok"])
            self.assertEqual(result["error"]["code"], "invalid-workspace")

    def test_start_requires_a_command(self) -> None:
        with tempfile.TemporaryDirectory(prefix="orchestrator-supervisor-") as temp:
            runtime = Path(temp) / "runtime"
            workspace = Path(temp) / "workspace"
            workspace.mkdir()
            result = payload(
                run_supervisor(
                    runtime,
                    "start",
                    "--dispatch-id",
                    "task-TASK-nocmd-attempt-1",
                    "--provider",
                    "fixture",
                    "--protocol",
                    "text",
                    "--workspace",
                    str(workspace),
                    check=False,
                )
            )
            self.assertFalse(result["ok"])
            self.assertEqual(result["error"]["code"], "invalid-request")

    def test_stop_terminates_a_retained_process_and_records_exit(self) -> None:
        with tempfile.TemporaryDirectory(prefix="orchestrator-supervisor-") as temp:
            root = Path(temp)
            runtime = root / "runtime"
            workspace = root / "workspace"
            workspace.mkdir()
            worker = root / "idle_worker.py"
            worker.write_text("import sys\nfor _ in sys.stdin: pass\n", encoding="utf-8")
            try:
                started = payload(
                    run_supervisor(
                        runtime,
                        "start",
                        "--dispatch-id",
                        "task-TASK-stop-1-attempt-1",
                        "--provider",
                        "fixture",
                        "--protocol",
                        "text",
                        "--workspace",
                        str(workspace),
                        "--",
                        sys.executable,
                        str(worker),
                    )
                )
                self.assertTrue(started["ok"])

                stopped = payload(
                    run_supervisor(runtime, "stop", "task-TASK-stop-1-attempt-1")
                )
                self.assertTrue(stopped["ok"])
                self.assertTrue(stopped["stopped"])
                self.assertIsInstance(stopped["exit_code"], int)

                # After stop, the dispatch is no longer live.
                status = payload(
                    run_supervisor(runtime, "status", "task-TASK-stop-1-attempt-1")
                )
                process = status["process"]
                self.assertEqual(process["status"], "stopped")
                self.assertFalse(process["live_handle"])
            finally:
                shutdown_and_wait(runtime)

    def test_double_start_of_same_dispatch_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory(prefix="orchestrator-supervisor-") as temp:
            root = Path(temp)
            runtime = root / "runtime"
            workspace = root / "workspace"
            workspace.mkdir()
            worker = root / "idle_worker.py"
            worker.write_text("import sys\nfor _ in sys.stdin: pass\n", encoding="utf-8")
            dispatch = "task-TASK-double-attempt-1"
            try:
                first = payload(
                    run_supervisor(
                        runtime,
                        "start",
                        "--dispatch-id",
                        dispatch,
                        "--provider",
                        "fixture",
                        "--protocol",
                        "text",
                        "--workspace",
                        str(workspace),
                        "--",
                        sys.executable,
                        str(worker),
                    )
                )
                self.assertTrue(first["ok"])
                # A second start while the first is still active must be refused.
                second = payload(
                    run_supervisor(
                        runtime,
                        "start",
                        "--dispatch-id",
                        dispatch,
                        "--provider",
                        "fixture",
                        "--protocol",
                        "text",
                        "--workspace",
                        str(workspace),
                        "--",
                        sys.executable,
                        str(worker),
                        check=False,
                    )
                )
                self.assertFalse(second["ok"])
                self.assertEqual(second["error"]["code"], "already-active")
            finally:
                shutdown_and_wait(runtime)


if __name__ == "__main__":
    unittest.main()

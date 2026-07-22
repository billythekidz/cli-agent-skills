"""Local supervisor tests that do not require real provider credentials."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path


REPOSITORY = Path(__file__).resolve().parents[1]
SUPERVISOR = (
    REPOSITORY
    / "skills"
    / "orchestrator-cli"
    / "scripts"
    / "orchestrator_supervisor.py"
)


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


if __name__ == "__main__":
    unittest.main()

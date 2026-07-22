"""TDD coverage for Antigravity's interactive PTY transport.

Claude and Codex remain on their protocol-native transports.  These tests
cover the provider-specific PTY boundary: an optional WinPTY/ConPTY adapter on
Windows and an isolated tmux-backed PTY on macOS.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import time
import types
import unittest
from unittest import mock
from pathlib import Path


REPOSITORY = Path(__file__).resolve().parents[1]
SUPERVISOR = REPOSITORY / "skills" / "orchestrator-cli" / "scripts" / "orchestrator_supervisor.py"


def load_supervisor_module():
    spec = importlib.util.spec_from_file_location("orchestrator_supervisor_pty_test", SUPERVISOR)
    if spec is None or spec.loader is None:
        raise AssertionError(f"Could not load supervisor module from {SUPERVISOR}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def run_supervisor(runtime: Path, *args: str, check: bool = True) -> dict[str, object]:
    result = subprocess.run(
        [
            sys.executable,
            str(SUPERVISOR),
            "--runtime-root",
            str(runtime),
            "--json",
            *args,
        ],
        cwd=REPOSITORY,
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
    data = json.loads(result.stdout)
    if not isinstance(data, dict):
        raise AssertionError(f"Supervisor response was not an object: {data!r}")
    return data


def shutdown_supervisor(runtime: Path) -> None:
    run_supervisor(runtime, "shutdown", check=False)


class AntigravityPtyContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.supervisor = load_supervisor_module()

    def test_claude_and_codex_keep_stdio_transport(self) -> None:
        self.assertEqual(
            self.supervisor.resolve_live_transport("claude-cli", "stdio", "darwin"),
            "stdio",
        )
        self.assertEqual(
            self.supervisor.resolve_live_transport("codex-cli", "stdio", "darwin"),
            "stdio",
        )

    def test_antigravity_selects_tmux_on_macos(self) -> None:
        self.assertEqual(
            self.supervisor.resolve_live_transport("antigravity-cli", "pty", "darwin"),
            "tmux",
        )

    def test_antigravity_selects_winpty_on_windows(self) -> None:
        self.assertEqual(
            self.supervisor.resolve_live_transport("antigravity-cli", "pty", "win32"),
            "winpty",
        )

    def test_antigravity_pty_requires_the_antigravity_provider(self) -> None:
        with self.assertRaisesRegex(ValueError, "antigravity-cli"):
            self.supervisor.resolve_live_transport("claude-cli", "pty", "darwin")

    def test_antigravity_prompt_uses_carriage_return(self) -> None:
        self.assertEqual(
            self.supervisor.encode_prompt("antigravity-pty", "follow-up", None),
            "follow-up\r",
        )

    def test_winpty_adapter_spawns_and_writes_to_one_pty(self) -> None:
        calls: list[tuple[str, object]] = []

        class FakePtyProcess:
            def __init__(self) -> None:
                self.pid = 4312

            @classmethod
            def spawn(cls, command, **kwargs):
                calls.append(("spawn", (command, kwargs)))
                return cls()

            def write(self, payload: str) -> None:
                calls.append(("write", payload))

            def isalive(self) -> bool:
                return True

            def terminate(self, force: bool = False) -> None:
                calls.append(("terminate", force))

        fake_winpty = types.SimpleNamespace(PtyProcess=FakePtyProcess)
        with mock.patch.dict(sys.modules, {"winpty": fake_winpty}):
            transport = self.supervisor.WinPtyTransport.start(
                ["agy", "--sandbox", "-i", "initial"],
                Path("C:/workspace"),
            )
            transport.write("follow-up\r")
            self.assertEqual(transport.pid, 4312)
            self.assertEqual([kind for kind, _ in calls], ["spawn", "write"])
            self.assertEqual(calls[-1][1], "follow-up\r")

    def test_winpty_missing_dependency_explains_install(self) -> None:
        with mock.patch.dict(sys.modules, {"winpty": None}):
            with self.assertRaisesRegex(RuntimeError, "py -m pip install pywinpty"):
                self.supervisor.WinPtyTransport.start(["agy"], Path("C:/workspace"))

    def test_tmux_missing_dependency_explains_install(self) -> None:
        with tempfile.TemporaryDirectory(prefix="antigravity-tmux-missing-") as temp:
            with mock.patch.object(self.supervisor.shutil, "which", return_value=None):
                with self.assertRaisesRegex(RuntimeError, "brew install tmux"):
                    self.supervisor.TmuxTransport.start(
                        Path(temp),
                        "task-missing-tmux",
                        ["agy"],
                        Path(temp),
                        Path(temp) / "logs" / "task.jsonl",
                    )


@unittest.skipUnless(
    sys.platform == "darwin" and shutil.which("tmux"),
    "macOS tmux integration test",
)
class MacOsTmuxPtyIntegrationTests(unittest.TestCase):
    def test_tmux_backend_keeps_one_pane_and_injects_followups(self) -> None:
        with tempfile.TemporaryDirectory(prefix="antigravity-tmux-pty-") as temp:
            root = Path(temp)
            runtime = root / "runtime"
            workspace = root / "workspace"
            workspace.mkdir()
            worker = root / "pty_worker.py"
            worker.write_text(
                textwrap.dedent(
                    """
                    import sys

                    print("PTY-READY", flush=True)
                    for line in sys.stdin:
                        print("PTY-ECHO:" + line.rstrip("\\r\\n"), flush=True)
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )
            dispatch_id = "task-TASK-antigravity-pty-attempt-1"
            try:
                started = run_supervisor(
                    runtime,
                    "start",
                    "--dispatch-id",
                    dispatch_id,
                    "--provider",
                    "antigravity-cli",
                    "--protocol",
                    "antigravity-pty",
                    "--transport",
                    "tmux",
                    "--workspace",
                    str(workspace),
                    "--",
                    sys.executable,
                    str(worker),
                )
                self.assertTrue(started["ok"], started)
                log_path = Path(str(started["log_file"]))

                sent = run_supervisor(runtime, "send", dispatch_id, "first prompt")
                self.assertTrue(sent["ok"], sent)
                deadline = time.monotonic() + 8
                log_text = ""
                while time.monotonic() < deadline:
                    if log_path.exists():
                        log_text = log_path.read_text(encoding="utf-8", errors="replace")
                    if "PTY-ECHO:first prompt" in log_text:
                        break
                    time.sleep(0.1)
                self.assertIn("PTY-ECHO:first prompt", log_text)

                first_status = run_supervisor(runtime, "status", dispatch_id)["process"]
                self.assertIsInstance(first_status, dict)
                self.assertEqual(first_status["transport"], "tmux")
                first_pid = first_status["pid"]
                metadata = first_status["transport_meta"]
                self.assertIsInstance(metadata, dict)
                self.assertTrue(metadata["session"])
                self.assertTrue(metadata["window"])
                self.assertTrue(metadata["socket"])

                sent = run_supervisor(runtime, "send", dispatch_id, "second prompt")
                self.assertTrue(sent["ok"], sent)
                deadline = time.monotonic() + 8
                while time.monotonic() < deadline:
                    log_text = log_path.read_text(encoding="utf-8", errors="replace")
                    if "PTY-ECHO:second prompt" in log_text:
                        break
                    time.sleep(0.1)
                self.assertIn("PTY-ECHO:second prompt", log_text)

                final_status = run_supervisor(runtime, "status", dispatch_id)["process"]
                self.assertEqual(final_status["pid"], first_pid)
                self.assertTrue(final_status["live_handle"])
                self.assertEqual(final_status["transport_meta"], metadata)
            finally:
                shutdown_supervisor(runtime)

    def test_tmux_route_rehydrates_after_supervisor_object_restart(self) -> None:
        supervisor = load_supervisor_module()
        with tempfile.TemporaryDirectory(prefix="antigravity-tmux-rehydrate-") as temp:
            root = Path(temp)
            runtime = root / "runtime"
            workspace = root / "workspace"
            workspace.mkdir()
            worker = root / "pty_worker.py"
            worker.write_text(
                "import sys\n"
                "print('REHYDRATE-READY', flush=True)\n"
                "for line in sys.stdin:\n"
                "    print('REHYDRATE-ECHO:' + line.rstrip('\\r\\n'), flush=True)\n",
                encoding="utf-8",
            )
            dispatch_id = "task-TASK-antigravity-rehydrate-attempt-1"
            first = supervisor.SupervisorService(supervisor.RuntimePaths(runtime))
            second = None
            try:
                started = first.start_process(
                    {
                        "dispatch_id": dispatch_id,
                        "provider": "antigravity-cli",
                        "protocol": "antigravity-pty",
                        "transport": "tmux",
                        "workspace": str(workspace),
                        "command": [sys.executable, str(worker)],
                    }
                )
                self.assertTrue(started["ok"], started)
                log_file = Path(str(started["log_file"]))

                second = supervisor.SupervisorService(supervisor.RuntimePaths(runtime))
                self.assertIn(dispatch_id, second.handles)
                status = second.status({"dispatch_id": dispatch_id})["process"]
                self.assertTrue(status["live_handle"])
                self.assertEqual(status["transport"], "tmux")

                sent = second.send_prompt({"dispatch_id": dispatch_id, "prompt": "rehydrated prompt"})
                self.assertTrue(sent["ok"], sent)
                deadline = time.monotonic() + 8
                while time.monotonic() < deadline:
                    text = log_file.read_text(encoding="utf-8", errors="replace")
                    if "REHYDRATE-ECHO:rehydrated prompt" in text:
                        break
                    time.sleep(0.1)
                self.assertIn("REHYDRATE-ECHO:rehydrated prompt", text)
            finally:
                if second is not None:
                    second.shutdown()
                first.shutdown()


if __name__ == "__main__":
    unittest.main()

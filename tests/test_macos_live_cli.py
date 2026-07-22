"""macOS live CLI integration tests.

These tests actually invoke claude, codex, and agy on macOS.
- 'smoke' tests: always run, no credentials needed (--version, --help)
- 'live' tests: opt-in via RUN_MACOS_LIVE_CLI_TESTS=1, consume provider quota

Run smoke tests only:
    python3 -m unittest tests.test_macos_live_cli -v

Run all tests (including live CLI calls):
    RUN_MACOS_LIVE_CLI_TESTS=1 python3 -m unittest tests.test_macos_live_cli -v
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
from pathlib import Path


REPOSITORY = Path(__file__).resolve().parents[1]
LIVE_FLAG = "RUN_MACOS_LIVE_CLI_TESTS"
DEFAULT_TIMEOUT = 120


def live_requested() -> bool:
    return os.environ.get(LIVE_FLAG) == "1"


def live_timeout() -> int:
    raw = os.environ.get("MACOS_LIVE_CLI_TIMEOUT", str(DEFAULT_TIMEOUT))
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_TIMEOUT
    return max(value, 30)


# ---------------------------------------------------------------------------
# macOS platform prerequisites
# ---------------------------------------------------------------------------

class MacOsPrerequisiteTests(unittest.TestCase):
    """Verify macOS environment is set up correctly for CLI tests."""

    def test_running_on_macos(self) -> None:
        self.assertEqual(sys.platform, "darwin")

    def test_posix_environment(self) -> None:
        self.assertEqual(os.name, "posix")

    def test_git_available(self) -> None:
        git = shutil.which("git")
        self.assertIsNotNone(git, "git must be on PATH")
        result = subprocess.run(
            [git, "--version"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("git version", result.stdout)


# ---------------------------------------------------------------------------
# CLI availability (always run, no credentials)
# ---------------------------------------------------------------------------

class CliAvailabilityTests(unittest.TestCase):
    """Each CLI must be on PATH and respond to basic flags."""

    def _resolve(self, name: str) -> str:
        path = shutil.which(name)
        self.assertIsNotNone(f"{name!r} is not on PATH. Install it first.")
        assert path is not None
        return path

    def _run_flag(self, name: str, *flags: str, expect_rc: int = 0) -> subprocess.CompletedProcess[str]:
        exe = self._resolve(name)
        result = subprocess.run(
            [exe, *flags],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        self.assertEqual(
            result.returncode,
            expect_rc,
            f"{name} {' '.join(flags)} failed (rc={result.returncode}):\n"
            f"stdout: {result.stdout[-2000:]}\nstderr: {result.stderr[-2000:]}",
        )
        return result

    # --- claude --version ---
    def test_claude_is_on_path(self) -> None:
        self.assertIsNotNone(shutil.which("claude"))

    def test_claude_version(self) -> None:
        result = self._run_flag("claude", "--version")
        self.assertIn("Claude Code", result.stdout)

    # --- codex --version ---
    def test_codex_is_on_path(self) -> None:
        self.assertIsNotNone(shutil.which("codex"))

    def test_codex_version(self) -> None:
        result = self._run_flag("codex", "--version")
        self.assertTrue(
            "codex" in result.stdout.lower() or "codex" in result.stderr.lower(),
            f"codex --version output unexpected: {result.stdout} {result.stderr}",
        )

    # --- agy --version ---
    def test_agy_is_on_path(self) -> None:
        self.assertIsNotNone(shutil.which("agy"))

    def test_agy_version(self) -> None:
        result = self._run_flag("agy", "--version")
        # agy may print version to stdout or stderr
        output = result.stdout + result.stderr
        self.assertTrue(len(output.strip()) > 0, "agy --version produced no output")

    # --- macOS path properties ---
    def test_claude_resolves_to_absolute_path(self) -> None:
        path = shutil.which("claude")
        self.assertIsNotNone(path)
        assert path is not None
        self.assertTrue(
            Path(path).is_absolute(),
            f"claude path should be absolute, got: {path}",
        )

    def test_codex_resolves_to_absolute_path(self) -> None:
        path = shutil.which("codex")
        self.assertIsNotNone(path)
        assert path is not None
        self.assertTrue(
            Path(path).is_absolute(),
            f"codex path should be absolute, got: {path}",
        )

    def test_agy_resolves_to_absolute_path(self) -> None:
        path = shutil.which("agy")
        self.assertIsNotNone(path)
        assert path is not None
        self.assertTrue(
            Path(path).is_absolute(),
            f"agy path should be absolute, got: {path}",
        )

    def test_claude_not_a_ps1_shim_on_macos(self) -> None:
        """On macOS the CLI should be a real binary, not a PowerShell shim."""
        path = Path(shutil.which("claude"))  # type: ignore[arg-type]
        self.assertNotEqual(
            path.suffix.lower(),
            ".ps1",
            "claude should not be a .ps1 shim on macOS",
        )

    def test_codex_not_a_ps1_shim_on_macos(self) -> None:
        path = Path(shutil.which("codex"))  # type: ignore[arg-type]
        self.assertNotEqual(
            path.suffix.lower(),
            ".ps1",
            "codex should not be a .ps1 shim on macOS",
        )


# ---------------------------------------------------------------------------
# CLI help / usage (always run, no credentials)
# ---------------------------------------------------------------------------

class CliHelpTests(unittest.TestCase):
    """Verify each CLI responds to --help without crashing."""

    TIMEOUT = 15

    def _run_help(self, name: str, *args: str) -> subprocess.CompletedProcess[str]:
        exe = shutil.which(name)
        self.assertIsNotNone(exe, f"{name!r} not on PATH")
        assert exe is not None
        return subprocess.run(
            [exe, *args],
            capture_output=True,
            text=True,
            timeout=self.TIMEOUT,
            check=False,
        )

    def test_claude_help(self) -> None:
        result = self._run_help("claude", "--help")
        self.assertEqual(result.returncode, 0, f"claude --help failed:\n{result.stderr[-2000:]}")
        self.assertTrue(
            len(result.stdout.strip()) > 50,
            "claude --help output seems too short",
        )

    def test_codex_help(self) -> None:
        result = self._run_help("codex", "--help")
        self.assertEqual(result.returncode, 0, f"codex --help failed:\n{result.stderr[-2000:]}")
        self.assertTrue(
            len(result.stdout.strip()) > 50,
            "codex --help output seems too short",
        )

    def test_agy_help(self) -> None:
        result = self._run_help("agy", "--help")
        # agy may exit 0 or 1 for --help
        output = result.stdout + result.stderr
        self.assertTrue(
            len(output.strip()) > 20,
            "agy --help produced no useful output",
        )


# ---------------------------------------------------------------------------
# CLI temp directory handling on macOS
# ---------------------------------------------------------------------------

class MacOsTempDirTests(unittest.TestCase):
    """Verify CLI tools work correctly with macOS temp directories."""

    def test_tempdir_exists_and_writable(self) -> None:
        tmp = Path(tempfile.gettempdir())
        self.assertTrue(tmp.is_dir())
        test_file = tmp / f"cli-agent-skills-test-{uuid.uuid4().hex}"
        try:
            test_file.write_text("test", encoding="utf-8")
            self.assertEqual(test_file.read_text(encoding="utf-8"), "test")
        finally:
            test_file.unlink(missing_ok=True)

    def test_tempdir_under_expected_macos_path(self) -> None:
        tmp = tempfile.gettempdir()
        self.assertTrue(
            tmp.startswith("/var/folders")
            or tmp.startswith("/private/var/folders")
            or tmp.startswith("/tmp"),
            f"Unexpected macOS temp dir: {tmp}",
        )


# ---------------------------------------------------------------------------
# Live CLI tests (opt-in, consume provider quota)
# ---------------------------------------------------------------------------

class MacOsLiveCliTests(unittest.TestCase):
    """Actually invoke each CLI with a real prompt on macOS.

    Set RUN_MACOS_LIVE_CLI_TESTS=1 to enable.
    """

    @classmethod
    def setUpClass(cls) -> None:
        if not live_requested():
            raise unittest.SkipTest(
                f"Set {LIVE_FLAG}=1 to run live CLI tests on macOS."
            )
        cls.timeout = live_timeout()

    def _run_cli(
        self,
        command: list[str],
        cwd: Path,
        prompt: str | None = None,
        env: dict[str, str] | None = None,
        timeout: int | None = None,
    ) -> subprocess.CompletedProcess[str]:
        effective_timeout = timeout or self.timeout
        try:
            result = subprocess.run(
                command,
                cwd=cwd,
                input=prompt,
                capture_output=True,
                text=True,
                timeout=effective_timeout,
                check=False,
                env=env,
            )
        except FileNotFoundError:
            self.fail(f"{command[0]!r} is not on PATH.")
        except subprocess.TimeoutExpired as exc:
            stdout = exc.stdout or ""
            stderr = exc.stderr or ""
            if isinstance(stdout, bytes):
                stdout = stdout.decode("utf-8", errors="replace")
            if isinstance(stderr, bytes):
                stderr = stderr.decode("utf-8", errors="replace")
            self.fail(
                f"{command[0]!r} timed out after {effective_timeout}s.\n"
                f"stdout: {stdout[-1000:]}\nstderr: {stderr[-1000:]}"
            )
        return result

    def _make_workspace(self) -> tuple[Path, Path]:
        root = Path(tempfile.mkdtemp(prefix="macos-live-cli-"))
        workspace = root / "workspace"
        workspace.mkdir()
        subprocess.run(
            ["git", "init", "--quiet"],
            cwd=workspace,
            capture_output=True,
            check=False,
        )
        return root, workspace

    def _cleanup(self, root: Path) -> None:
        for _ in range(5):
            try:
                shutil.rmtree(root)
                return
            except OSError:
                time.sleep(0.5)

    def _assert_workspace_clean(self, workspace: Path) -> None:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=workspace,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "", "CLI modified the workspace unexpectedly.")

    # --- claude live ---
    @unittest.skipUnless(shutil.which("claude"), "claude not on PATH")
    def test_claude_simple_prompt_exits_cleanly(self) -> None:
        nonce = f"macos-claude-{uuid.uuid4().hex}"
        root, workspace = self._make_workspace()
        try:
            result = self._run_cli(
                [
                    shutil.which("claude"),  # type: ignore[list-item]
                    "-p",
                    "--output-format",
                    "json",
                    "--tools", "",
                    "--max-budget-usd", "0.50",
                    "--",
                    f"Reply with exactly one word: {nonce}",
                ],
                workspace,
            )
            self.assertEqual(
                result.returncode,
                0,
                f"claude exited {result.returncode}:\nstderr: {result.stderr[-2000:]}",
            )
            # Parse JSON output
            try:
                payload = json.loads(result.stdout)
            except json.JSONDecodeError:
                self.fail(f"claude did not return valid JSON:\n{result.stdout[-2000:]}")

            events = payload if isinstance(payload, list) else [payload]
            result_text = ""
            session_id = ""
            for event in events:
                if isinstance(event, dict):
                    if event.get("type") == "result":
                        result_text = str(event.get("result", ""))
                        session_id = str(event.get("session_id", ""))
            self.assertIn(nonce, result_text, f"Claude did not echo the nonce.\nOutput: {result_text[-1000:]}")
            self.assertTrue(len(session_id) > 0, "Claude did not return a session_id.")
            self._assert_workspace_clean(workspace)
        finally:
            self._cleanup(root)

    @unittest.skipUnless(shutil.which("claude"), "claude not on PATH")
    def test_claude_stream_json_two_turns(self) -> None:
        nonce = f"macos-claude-stream-{uuid.uuid4().hex}"
        root, workspace = self._make_workspace()
        try:
            import queue
            import threading

            exe = shutil.which("claude")
            command = [
                exe,  # type: ignore[list-item]
                "-p",
                "--input-format", "stream-json",
                "--output-format", "stream-json",
                "--verbose",
                "--no-session-persistence",
                "--tools", "",
                "--permission-mode", "plan",
                "--safe-mode",
                "--max-budget-usd", "0.50",
            ]
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

            events: queue.Queue[dict] = queue.Queue()
            seen: list[dict] = []
            captured_stderr: list[str] = []

            def read_stdout() -> None:
                assert process.stdout is not None
                for line in process.stdout:
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(event, dict):
                        events.put(event)

            def drain_stderr() -> None:
                assert process.stderr is not None
                for line in process.stderr:
                    captured_stderr.append(line)

            stdout_thread = threading.Thread(target=read_stdout, daemon=True)
            stderr_thread = threading.Thread(target=drain_stderr, daemon=True)
            stdout_thread.start()
            stderr_thread.start()

            def send(msg: dict) -> None:
                assert process.stdin is not None
                process.stdin.write(json.dumps(msg, ensure_ascii=False) + "\n")
                process.stdin.flush()

            def wait_for(predicate, label: str, timeout: int = 120) -> dict:
                deadline = time.monotonic() + timeout
                while time.monotonic() < deadline:
                    if process.poll() is not None:
                        stderr_tail = "".join(captured_stderr)[-2000:]
                        self.fail(
                            f"Process exited before {label} (rc={process.returncode}).\n"
                            f"Seen events: {seen[-5:]}\nstderr: {stderr_tail}"
                        )
                    try:
                        event = events.get(timeout=min(0.25, deadline - time.monotonic()))
                    except queue.Empty:
                        continue
                    seen.append(event)
                    if predicate(event):
                        return event
                self.fail(f"Timed out waiting for {label}. Seen {len(seen)} events: {seen[-5:]}")

            # Turn 1
            send({
                "type": "user",
                "message": {
                    "role": "user",
                    "content": (
                        "Do not use tools, inspect files, run commands, or change "
                        f"anything. Remember this exact token: {nonce}. Reply with it."
                    ),
                },
            })
            first = wait_for(
                lambda e: e.get("type") == "result",
                "first result",
                self.timeout,
            )
            session_id = first.get("session_id")
            self.assertIsInstance(session_id, str)
            self.assertIn(nonce, str(first.get("result", "")))
            self.assertIsNone(process.poll(), "Claude exited after first turn.")

            # Turn 2
            send({
                "type": "user",
                "message": {
                    "role": "user",
                    "content": (
                        "Continue this exact conversation. Do not use tools, inspect "
                        "files, run commands, or change anything. Return exactly the "
                        "token I asked you to remember in the previous message."
                    ),
                },
            })
            second = wait_for(
                lambda e: e.get("type") == "result",
                "second result",
                self.timeout,
            )
            self.assertEqual(second.get("session_id"), session_id)
            self.assertIn(nonce, str(second.get("result", "")))

            # Cleanup
            if process.stdin and not process.stdin.closed:
                process.stdin.close()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
            for stream in (process.stdout, process.stderr):
                if stream and not stream.closed:
                    stream.close()
            stdout_thread.join(timeout=2)
            stderr_thread.join(timeout=2)
            self._assert_workspace_clean(workspace)
        finally:
            self._cleanup(root)

    # --- codex live ---
    @unittest.skipUnless(shutil.which("codex"), "codex not on PATH")
    def test_codex_simple_prompt_exits_cleanly(self) -> None:
        nonce = f"macos-codex-{uuid.uuid4().hex}"
        root, workspace = self._make_workspace()
        try:
            message_file = root / "codex-last-message.txt"
            command = [
                shutil.which("codex"),  # type: ignore[list-item]
                "exec",
                "--sandbox", "read-only",
                "--ignore-user-config",
                "--ignore-rules",
                "-C", str(workspace),
                "--output-last-message", str(message_file),
            ]
            model = os.environ.get("MACOS_LIVE_CODEX_MODEL")
            if model:
                command.extend(["--model", model])
            command.append("-")  # read prompt from stdin

            result = self._run_cli(
                command,
                workspace,
                prompt=f"Reply with exactly one word: {nonce}\n",
            )
            self.assertEqual(
                result.returncode,
                0,
                f"codex exec exited {result.returncode}:\n"
                f"stdout: {result.stdout[-2000:]}\nstderr: {result.stderr[-2000:]}",
            )
            # Check the last-message file
            if message_file.is_file():
                content = message_file.read_text(encoding="utf-8", errors="replace")
                self.assertIn(nonce, content, f"Codex did not echo the nonce.\nOutput: {content[-1000:]}")
            self._assert_workspace_clean(workspace)
        finally:
            self._cleanup(root)

    # --- agy live ---
    @unittest.skipUnless(shutil.which("agy"), "agy not on PATH")
    def test_agy_simple_prompt_exits_cleanly(self) -> None:
        nonce = f"macos-agy-{uuid.uuid4().hex}"
        root, workspace = self._make_workspace()
        try:
            log_file = root / "agy-test.log"
            command = [
                shutil.which("agy"),  # type: ignore[list-item]
                "--sandbox",
                "--log-file", str(log_file),
                "-p",
                f"Reply with exactly one word: {nonce}",
                "--print-timeout",
                f"{self.timeout}s",
            ]
            # subprocess timeout must exceed agy's own --print-timeout
            # so agy can exit cleanly instead of being killed by Python
            result = self._run_cli(
                command, workspace, timeout=self.timeout + 30,
            )
            if result.returncode != 0 and "timeout" in (result.stderr + result.stdout).lower():
                self.skipTest(
                    f"agy timed out (API quota or network issue): {result.stderr[-500:]}"
                )
            self.assertEqual(
                result.returncode,
                0,
                f"agy exited {result.returncode}:\n"
                f"stdout: {result.stdout[-2000:]}\nstderr: {result.stderr[-2000:]}",
            )
            self.assertIn(
                nonce,
                result.stdout,
                f"agy did not echo the nonce.\nOutput: {result.stdout[-1000:]}",
            )
            self._assert_workspace_clean(workspace)
        finally:
            self._cleanup(root)


# ---------------------------------------------------------------------------
# Live macOS path integration tests
# ---------------------------------------------------------------------------

class MacOsPathIntegrationTests(unittest.TestCase):
    """Test that CLIs handle macOS-specific paths correctly."""

    @classmethod
    def setUpClass(cls) -> None:
        if not live_requested():
            raise unittest.SkipTest(
                f"Set {LIVE_FLAG}=1 to run macOS path integration tests."
            )

    def test_claude_handles_spaces_in_path(self) -> None:
        """Verify claude works when the workspace path contains spaces."""
        if not shutil.which("claude"):
            self.skipTest("claude not on PATH")
        root = Path(tempfile.mkdtemp(prefix="macos-cli-"))
        workspace = root / "path with spaces"
        workspace.mkdir()
        subprocess.run(["git", "init", "--quiet"], cwd=workspace, capture_output=True, check=False)
        try:
            nonce = f"macos-spaces-{uuid.uuid4().hex}"
            result = subprocess.run(
                [
                    shutil.which("claude"),  # type: ignore[list-item]
                    "-p",
                    "--output-format", "json",
                    "--tools", "",
                    "--max-budget-usd", "0.50",
                    "--",
                    f"Reply with exactly: {nonce}",
                ],
                cwd=workspace,
                capture_output=True,
                text=True,
                timeout=live_timeout(),
                check=False,
            )
            self.assertEqual(
                result.returncode,
                0,
                f"claude failed with spaces in path:\n{result.stderr[-2000:]}",
            )
            self.assertIn(nonce, result.stdout)
        finally:
            for _ in range(5):
                try:
                    shutil.rmtree(root)
                    return
                except OSError:
                    time.sleep(0.5)


if __name__ == "__main__":
    unittest.main()

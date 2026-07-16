"""Opt-in live probes that prove exact provider-native session continuation."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
import unittest
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


LIVE_FLAG = "RUN_LIVE_SKILL_TESTS"
PROVIDERS = frozenset({"claude", "codex", "antigravity"})


def selected_providers() -> frozenset[str]:
    raw = os.environ.get("SKILL_TEST_PROVIDERS", "claude,codex,antigravity")
    selected = frozenset(item.strip().lower() for item in raw.split(",") if item.strip())
    unknown = selected - PROVIDERS
    if unknown:
        raise ValueError(
            "SKILL_TEST_PROVIDERS supports only "
            f"{', '.join(sorted(PROVIDERS))}; received {', '.join(sorted(unknown))}."
        )
    return selected


def timeout_seconds() -> int:
    raw = os.environ.get("SKILL_TEST_TIMEOUT_SECONDS", "600")
    try:
        timeout = int(raw)
    except ValueError as error:
        raise ValueError("SKILL_TEST_TIMEOUT_SECONDS must be a whole number.") from error
    if timeout < 30:
        raise ValueError("SKILL_TEST_TIMEOUT_SECONDS must be at least 30.")
    return timeout


def normalize_path(path: str | Path) -> str:
    return os.path.normcase(os.path.normpath(str(Path(path).resolve())))


class NativeSessionLiveTests(unittest.TestCase):
    """One initial turn plus one exact-ID resume per selected provider."""

    @classmethod
    def setUpClass(cls) -> None:
        if os.environ.get(LIVE_FLAG) != "1":
            raise unittest.SkipTest(
                f"Set {LIVE_FLAG}=1 to run authenticated provider session probes."
            )

        cls.providers = selected_providers()
        if not cls.providers:
            raise unittest.SkipTest("No providers were selected.")
        cls.timeout = timeout_seconds()
        cls.keep_artifacts = os.environ.get("KEEP_NATIVE_SESSION_ARTIFACTS") == "1"

    def require_provider(self, provider: str) -> None:
        if provider not in self.providers:
            self.skipTest(f"{provider} was not selected in SKILL_TEST_PROVIDERS.")

    @contextmanager
    def temporary_git_repository(self) -> Iterator[tuple[Path, Path]]:
        root = Path(tempfile.mkdtemp(prefix="cli-agent-skills-native-session-"))
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
            self.fail(self.process_details(["git", "init", "--quiet"], initialization))

        try:
            yield workspace, root
        finally:
            if self.keep_artifacts:
                print(f"Kept native-session probe artifacts at {root}")
            else:
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
        if path.exists():
            print(f"Could not remove temporary native-session artifacts: {path}")

    @staticmethod
    def process_details(command: list[str], result: subprocess.CompletedProcess[str]) -> str:
        return (
            f"Command failed ({result.returncode}): {command!r}\n"
            f"stdout:\n{result.stdout[-4000:]}\n"
            f"stderr:\n{result.stderr[-4000:]}"
        )

    def run_cli(
        self,
        command: list[str],
        workspace: Path,
        prompt: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        try:
            result = subprocess.run(
                command,
                cwd=workspace,
                input=prompt,
                text=True,
                capture_output=True,
                errors="replace",
                timeout=self.timeout + 30,
                check=False,
            )
        except FileNotFoundError:
            self.fail(
                f"{command[0]!r} is not on PATH. Install it or remove this provider "
                "from SKILL_TEST_PROVIDERS."
            )
        except subprocess.TimeoutExpired as error:
            self.fail(
                f"{command[0]!r} did not finish within {self.timeout + 30} seconds: {error}"
            )

        if result.returncode:
            self.fail(self.process_details(command, result))
        return result

    def cli_command(self, executable: str) -> list[str]:
        resolved = shutil.which(executable)
        if not resolved:
            self.fail(
                f"{executable!r} is not on PATH. Install it or remove this provider "
                "from SKILL_TEST_PROVIDERS."
            )

        path = Path(resolved)
        if os.name == "nt" and path.suffix.lower() == ".ps1":
            command_wrapper = path.with_suffix(".cmd")
            if command_wrapper.is_file():
                return [str(command_wrapper)]

            powershell = shutil.which("pwsh") or shutil.which("powershell")
            if powershell:
                return [powershell, "-NoProfile", "-File", str(path)]
            self.fail(
                f"{executable!r} resolved to {path}, but no executable wrapper was found."
            )
        return [str(path)]

    def assert_workspace_clean(self, workspace: Path) -> None:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=workspace,
            text=True,
            capture_output=True,
            errors="replace",
            check=False,
        )
        self.assertEqual(result.returncode, 0, self.process_details(["git", "status"], result))
        self.assertEqual(
            result.stdout,
            "",
            f"Session probe changed the temporary workspace:\n{result.stdout}",
        )

    @staticmethod
    def initial_prompt(nonce: str) -> str:
        return (
            "This is a non-invasive native-session continuity probe. Do not use tools, "
            "inspect files, run commands, access the network, or modify anything. "
            f"Remember this exact token: {nonce}. Reply with exactly: ACK {nonce}."
        )

    @staticmethod
    def resume_prompt() -> str:
        return (
            "Continue this exact conversation. Do not use tools, inspect files, run "
            "commands, access the network, or modify anything. Return exactly the token "
            "I asked you to remember in the previous message."
        )

    @staticmethod
    def require_uuid(value: object, provider: str) -> str:
        if not isinstance(value, str):
            raise AssertionError(f"{provider} did not return a string native session ID.")
        try:
            return str(uuid.UUID(value))
        except ValueError as error:
            raise AssertionError(
                f"{provider} returned a non-UUID native session ID: {value!r}"
            ) from error

    def test_claude_resumes_the_exact_native_session(self) -> None:
        self.require_provider("claude")
        nonce = f"claude-session-{uuid.uuid4().hex}"
        follow_up = self.resume_prompt()
        self.assertNotIn(nonce, follow_up)
        budget = os.environ.get("NATIVE_SESSION_CLAUDE_BUDGET_USD", "1")

        with self.temporary_git_repository() as (workspace, _):
            initial_command = self.cli_command("claude") + [
                "-p",
                "--output-format",
                "json",
                "--tools",
                "",
                "--max-budget-usd",
                budget,
                "--",
                self.initial_prompt(nonce),
            ]
            initial = self.run_cli(initial_command, workspace)
            initial_id, initial_text = self.parse_claude_result(initial.stdout)
            initial_id = self.require_uuid(initial_id, "Claude")
            self.assertIn(nonce, initial_text)
            self.assert_workspace_clean(workspace)

            resume_command = self.cli_command("claude") + [
                "-r",
                initial_id,
                "-p",
                "--output-format",
                "json",
                "--tools",
                "",
                "--max-budget-usd",
                budget,
                "--",
                follow_up,
            ]
            resumed = self.run_cli(resume_command, workspace)
            resumed_id, resumed_text = self.parse_claude_result(resumed.stdout)

            self.assertEqual(
                resumed_id,
                initial_id,
                "Claude resumed a different native session.",
            )
            self.assertIn(nonce, resumed_text)
            self.assert_workspace_clean(workspace)

    def test_codex_resumes_the_exact_native_session(self) -> None:
        self.require_provider("codex")
        nonce = f"codex-session-{uuid.uuid4().hex}"
        follow_up = self.resume_prompt()
        self.assertNotIn(nonce, follow_up)

        with self.temporary_git_repository() as (workspace, root):
            initial_message = root / "codex-initial-message.txt"
            resume_message = root / "codex-resume-message.txt"
            initial_command = self.cli_command("codex") + [
                "exec",
                "--json",
                "--sandbox",
                "read-only",
                "--ignore-user-config",
                "--ignore-rules",
                "-C",
                str(workspace),
                "--output-last-message",
                str(initial_message),
            ]
            model = os.environ.get("NATIVE_SESSION_CODEX_MODEL")
            if model:
                initial_command.extend(["--model", model])
            initial_command.append("-")

            initial = self.run_cli(
                initial_command,
                workspace,
                self.initial_prompt(nonce) + "\n",
            )
            initial_id = self.require_uuid(
                self.parse_codex_thread_id(initial.stdout),
                "Codex",
            )
            self.assertIn(nonce, self.read_last_message(initial_message, initial))
            self.assert_workspace_clean(workspace)

            resume_command = self.cli_command("codex") + [
                "exec",
                "resume",
                "--json",
                "--ignore-user-config",
                "--ignore-rules",
                "--output-last-message",
                str(resume_message),
            ]
            if model:
                resume_command.extend(["--model", model])
            resume_command.extend([initial_id, "-"])
            resumed = self.run_cli(resume_command, workspace, follow_up + "\n")
            resumed_id = self.require_uuid(
                self.parse_codex_thread_id(resumed.stdout),
                "Codex",
            )

            self.assertEqual(
                resumed_id,
                initial_id,
                "Codex resumed a different native thread.",
            )
            self.assertIn(nonce, self.read_last_message(resume_message, resumed))
            self.assert_workspace_clean(workspace)

    def test_antigravity_resumes_the_exact_native_conversation(self) -> None:
        self.require_provider("antigravity")
        nonce = f"antigravity-session-{uuid.uuid4().hex}"
        follow_up = self.resume_prompt()
        self.assertNotIn(nonce, follow_up)

        with self.temporary_git_repository() as (workspace, root):
            initial_log = root / "antigravity-initial.log"
            resume_log = root / "antigravity-resume.log"
            initial_command = self.cli_command("agy") + [
                "--sandbox",
                "--log-file",
                str(initial_log),
                "-p",
                self.initial_prompt(nonce),
                "--print-timeout",
                f"{self.timeout}s",
            ]
            initial = self.run_cli(initial_command, workspace)
            self.assertIn(nonce, initial.stdout)
            initial_id = self.read_antigravity_workspace_session(workspace)
            self.assert_workspace_clean(workspace)

            resume_command = self.cli_command("agy") + [
                "--sandbox",
                "--conversation",
                initial_id,
                "--log-file",
                str(resume_log),
                "-p",
                follow_up,
                "--print-timeout",
                f"{self.timeout}s",
            ]
            resumed = self.run_cli(resume_command, workspace)
            self.assertIn(nonce, resumed.stdout)
            self.assertEqual(
                self.read_antigravity_workspace_session(workspace),
                initial_id,
                "Antigravity resumed a different native conversation.",
            )
            self.assert_workspace_clean(workspace)

    def parse_claude_result(self, stdout: str) -> tuple[str, str]:
        try:
            payload = json.loads(stdout)
        except json.JSONDecodeError as error:
            self.fail(f"Claude did not return JSON output: {error}\n{stdout[-4000:]}")

        events = payload if isinstance(payload, list) else [payload]
        session_id = None
        result_text = None
        for event in events:
            if not isinstance(event, dict):
                continue
            if event.get("type") == "system" and event.get("subtype") == "init":
                session_id = event.get("session_id")
            if event.get("type") == "result":
                session_id = event.get("session_id", session_id)
                result_text = event.get("result")

        if not isinstance(session_id, str) or not isinstance(result_text, str):
            self.fail(f"Claude JSON lacked a session ID or final result:\n{stdout[-4000:]}")
        return session_id, result_text

    def parse_codex_thread_id(self, stdout: str) -> str:
        for line in stdout.splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("type") == "thread.started":
                thread_id = event.get("thread_id")
                if isinstance(thread_id, str):
                    return thread_id
        self.fail(f"Codex JSONL lacked thread.started/thread_id:\n{stdout[-4000:]}")

    def read_last_message(
        self,
        path: Path,
        command_result: subprocess.CompletedProcess[str],
    ) -> str:
        if not path.is_file():
            self.fail(
                f"Codex did not write {path.name}.\n"
                f"{self.process_details(['codex', 'exec'], command_result)}"
            )
        return path.read_text(encoding="utf-8", errors="replace")

    def read_antigravity_workspace_session(self, workspace: Path) -> str:
        cache_path = (
            Path.home()
            / ".gemini"
            / "antigravity-cli"
            / "cache"
            / "last_conversations.json"
        )
        expected_workspace = normalize_path(workspace)
        deadline = time.monotonic() + 5

        while time.monotonic() < deadline:
            try:
                payload = json.loads(cache_path.read_text(encoding="utf-8"))
            except (FileNotFoundError, json.JSONDecodeError):
                time.sleep(0.25)
                continue

            if isinstance(payload, dict):
                for recorded_workspace, session_id in payload.items():
                    if normalize_path(recorded_workspace) == expected_workspace:
                        try:
                            return self.require_uuid(session_id, "Antigravity")
                        except AssertionError as error:
                            self.fail(str(error))
            time.sleep(0.25)

        self.fail(
            "Antigravity did not persist a UUID for this exact workspace in "
            f"{cache_path}. The installed CLI persistence contract may have changed; "
            "do not replace this exact-ID probe with -c/--continue."
        )

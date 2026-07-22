"""Opt-in end-to-end tests for the four documented CLI skill workflows.

These tests intentionally execute the installed provider CLIs. They are not
part of the default offline suite because they require provider authentication,
consume quota, persist native conversations, and may let an agent edit a
temporary workspace. Set RUN_REAL_CLI_SKILL_TESTS=1 to run them.

There is no separate ``orchestrator`` executable in this repository. Its real
workflow is therefore tested in local-Markdown mode: the test creates the
documented control-plane records, dispatches one real direct CLI worker, records
the native session ID, and writes/validates the required handoff.
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
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from tests import test_native_sessions as native_sessions


LIVE_FLAG = "RUN_REAL_CLI_SKILL_TESTS"
PROVIDER_ENV = "REAL_CLI_SKILL_PROVIDERS"
TIMEOUT_ENV = "REAL_CLI_SKILL_TIMEOUT_SECONDS"
CLAUDE_BUDGET_ENV = "REAL_CLI_CLAUDE_BUDGET_USD"
CODEX_AUTH_ENV = "REAL_CLI_CODEX_AUTH_FILE"
ORCHESTRATOR_PROVIDER_ENV = "REAL_CLI_ORCHESTRATOR_PROVIDER"

DIRECT_PROVIDERS = frozenset({"claude", "codex", "antigravity"})
ALL_PROVIDERS = DIRECT_PROVIDERS | {"orchestrator"}
MARKER_NAME = "cli-skill-e2e-marker.txt"


def selected_providers() -> frozenset[str]:
    raw = os.environ.get(PROVIDER_ENV, "claude,codex,antigravity,orchestrator")
    selected = frozenset(item.strip().lower() for item in raw.split(",") if item.strip())
    unknown = selected - ALL_PROVIDERS
    if unknown:
        raise ValueError(
            f"{PROVIDER_ENV} supports only {', '.join(sorted(ALL_PROVIDERS))}; "
            f"received {', '.join(sorted(unknown))}."
        )
    return selected


def timeout_seconds() -> int:
    raw = os.environ.get(TIMEOUT_ENV, "900")
    try:
        value = int(raw)
    except ValueError as error:
        raise ValueError(f"{TIMEOUT_ENV} must be a whole number.") from error
    if value < 30:
        raise ValueError(f"{TIMEOUT_ENV} must be at least 30.")
    return value


class RealCliWorkflowTests(unittest.TestCase):
    """Run bounded real tasks and verify native continuation semantics."""

    @classmethod
    def setUpClass(cls) -> None:
        if os.environ.get(LIVE_FLAG) != "1":
            raise unittest.SkipTest(
                f"Set {LIVE_FLAG}=1 to run authenticated real CLI workflow tests."
            )
        cls.providers = selected_providers()
        if not cls.providers:
            raise unittest.SkipTest("No real CLI providers were selected.")
        cls.timeout = timeout_seconds()

    def require_provider(self, provider: str) -> None:
        if provider not in self.providers:
            self.skipTest(f"{provider} was not selected in {PROVIDER_ENV}.")

    def resolve_cli(self, executable: str) -> list[str]:
        resolved = shutil.which(executable)
        if not resolved:
            self.skipTest(f"{executable!r} is not installed or not on PATH.")

        path = Path(resolved)
        if os.name == "nt" and path.suffix.lower() == ".ps1":
            wrapper = path.with_suffix(".cmd")
            if wrapper.is_file():
                return [str(wrapper)]
            powershell = shutil.which("pwsh") or shutil.which("powershell")
            if powershell:
                return [powershell, "-NoProfile", "-File", str(path)]
            self.skipTest(f"No PowerShell executable is available for {path}.")
        return [str(path)]

    def command_for(self, provider: str) -> list[str]:
        return self.resolve_cli(
            {"claude": "claude", "codex": "codex", "antigravity": "agy"}[provider]
        )

    def run_command(
        self,
        command: list[str],
        cwd: Path,
        *,
        input_text: str | None = None,
        env: dict[str, str] | None = None,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        try:
            result = subprocess.run(
                command,
                cwd=cwd,
                input=input_text,
                text=True,
                capture_output=True,
                errors="replace",
                env=env,
                timeout=self.timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as error:
            self.fail(f"Timed out after {self.timeout}s: {command!r}\n{error}")

        if check and result.returncode:
            self.fail(self.command_failure(command, result))
        return result

    @staticmethod
    def command_failure(
        command: list[str], result: subprocess.CompletedProcess[str]
    ) -> str:
        return (
            f"Command failed ({result.returncode}): {command!r}\n"
            f"stdout:\n{result.stdout[-6000:]}\n"
            f"stderr:\n{result.stderr[-6000:]}"
        )

    def preflight(self, provider: str) -> list[str]:
        command = self.command_for(provider)
        help_command = command + (["exec", "--help"] if provider == "codex" else ["--help"])
        self.run_command(help_command, Path.cwd())

        if provider == "claude":
            auth = self.run_command(command + ["auth", "status"], Path.cwd(), check=False)
            if auth.returncode:
                self.skipTest("Claude auth status did not succeed; authenticate Claude first.")
        return command

    @contextmanager
    def temporary_git_repository(self) -> Iterator[Path]:
        root = Path(tempfile.mkdtemp(prefix="cli skill real e2e "))
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
            self.fail(self.command_failure(["git", "init", "--quiet"], initialization))
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
        if path.exists():
            raise AssertionError(f"Could not remove temporary real CLI workspace: {path}")

    @staticmethod
    def marker_token(provider: str) -> str:
        return f"REAL-{provider.upper()}-{uuid.uuid4().hex}"

    @staticmethod
    def task_prompt(workspace: Path, token: str) -> str:
        marker = workspace / MARKER_NAME
        return f"""Workspace: {workspace}
Task: create exactly one file named {MARKER_NAME} containing the token {token}.
Scope: only {marker}; do not change any other file and do not create a commit.
Constraints: do not use the network, do not inspect files outside this workspace,
do not start another agent, and do not modify .git or control-plane records.
Verify: read {marker} and confirm its trimmed contents equal {token}.
Return: a concise summary, changed files, verification result, blockers, and the
provider-native session ID if the CLI reports one.
"""

    @staticmethod
    def resume_prompt(token: str) -> str:
        return (
            "Continue the exact provider-native conversation in the same workspace. "
            "Do not use tools or modify files. Return exactly this token and nothing "
            f"else: {token}"
        )

    def codex_environment(self, workspace: Path) -> dict[str, str]:
        environment = os.environ.copy()
        codex_home = workspace.parent / "codex-home"
        codex_home.mkdir()
        environment["CODEX_HOME"] = str(codex_home)
        environment["CODEX_SQLITE_HOME"] = str(codex_home / "sqlite")
        environment["RUST_LOG"] = "error"

        auth_source = os.environ.get(CODEX_AUTH_ENV)
        if not auth_source:
            configured_home = os.environ.get("CODEX_HOME")
            if configured_home:
                auth_source = str(Path(configured_home).expanduser() / "auth.json")
            else:
                auth_source = str(Path.home() / ".codex" / "auth.json")
        source = Path(auth_source).expanduser()
        if source.is_file():
            shutil.copy2(source, codex_home / "auth.json")
        elif not environment.get("CODEX_ACCESS_TOKEN"):
            self.skipTest(
                f"Codex credentials unavailable; set CODEX_ACCESS_TOKEN or {CODEX_AUTH_ENV}."
            )
        return environment

    def native_probe(self) -> native_sessions.NativeSessionLiveTests:
        return native_sessions.NativeSessionLiveTests("runTest")

    def run_claude_initial(self, workspace: Path, token: str) -> tuple[str, str]:
        command = self.preflight("claude")
        budget = os.environ.get(CLAUDE_BUDGET_ENV, "1")
        result = self.run_command(
            command
            + [
                "-p",
                "--output-format",
                "json",
                "--dangerously-skip-permissions",
                "--max-budget-usd",
                budget,
                "--",
                self.task_prompt(workspace, token),
            ],
            workspace,
        )
        session_id, response = self.native_probe().parse_claude_result(result.stdout)
        return self.native_probe().require_uuid(session_id, "Claude"), response

    def run_claude_resume(self, workspace: Path, session_id: str, token: str) -> str:
        command = self.preflight("claude")
        budget = os.environ.get(CLAUDE_BUDGET_ENV, "1")
        result = self.run_command(
            command
            + [
                "-r",
                session_id,
                "-p",
                "--output-format",
                "json",
                "--dangerously-skip-permissions",
                "--max-budget-usd",
                budget,
                "--",
                self.resume_prompt(token),
            ],
            workspace,
        )
        resumed_id, response = self.native_probe().parse_claude_result(result.stdout)
        self.assertEqual(
            self.native_probe().require_uuid(resumed_id, "Claude"),
            session_id,
        )
        return response

    def run_codex_initial(
        self, workspace: Path, token: str
    ) -> tuple[str, str, dict[str, str]]:
        command = self.preflight("codex")
        environment = self.codex_environment(workspace)
        output_path = workspace.parent / "codex-initial-message.txt"
        args = command + [
            "exec",
            "--json",
            "--dangerously-bypass-approvals-and-sandbox",
            "--ignore-user-config",
            "--ignore-rules",
            "-C",
            str(workspace),
            "--output-last-message",
            str(output_path),
        ]
        model = os.environ.get("REAL_CLI_CODEX_MODEL")
        if model:
            args.extend(["--model", model])
        args.append("-")
        result = self.run_command(
            args,
            workspace,
            input_text=self.task_prompt(workspace, token) + "\n",
            env=environment,
        )
        session_id = self.native_probe().require_uuid(
            self.native_probe().parse_codex_thread_id(result.stdout), "Codex"
        )
        if not output_path.is_file():
            self.fail(self.command_failure(args, result) + "\nCodex did not write its final message.")
        return session_id, output_path.read_text(encoding="utf-8", errors="replace"), environment

    def run_codex_resume(
        self, workspace: Path, session_id: str, token: str, environment: dict[str, str]
    ) -> str:
        command = self.preflight("codex")
        output_path = workspace.parent / "codex-resume-message.txt"
        args = command + [
            "exec",
            "resume",
            "--json",
            "--dangerously-bypass-approvals-and-sandbox",
            "--ignore-user-config",
            "--ignore-rules",
            "--output-last-message",
            str(output_path),
        ]
        model = os.environ.get("REAL_CLI_CODEX_MODEL")
        if model:
            args.extend(["--model", model])
        args.extend([session_id, "-"])
        result = self.run_command(
            args,
            workspace,
            input_text=self.resume_prompt(token) + "\n",
            env=environment,
        )
        resumed_id = self.native_probe().require_uuid(
            self.native_probe().parse_codex_thread_id(result.stdout), "Codex"
        )
        self.assertEqual(resumed_id, session_id)
        if not output_path.is_file():
            self.fail(self.command_failure(args, result) + "\nCodex did not write its final message.")
        return output_path.read_text(encoding="utf-8", errors="replace")

    def run_antigravity_initial(self, workspace: Path, token: str) -> tuple[str, str]:
        command = self.preflight("antigravity")
        log_path = workspace.parent / "agy-initial.log"
        result = self.run_command(
            command
            + [
                "--mode",
                "accept-edits",
                "--dangerously-skip-permissions",
                "--log-file",
                str(log_path),
                "-p",
                self.task_prompt(workspace, token),
                "--print-timeout",
                f"{self.timeout}s",
            ],
            workspace,
        )
        session_id = self.native_probe().read_antigravity_workspace_session(workspace)
        return session_id, result.stdout

    def run_antigravity_resume(self, workspace: Path, session_id: str, token: str) -> str:
        command = self.preflight("antigravity")
        log_path = workspace.parent / "agy-resume.log"
        result = self.run_command(
            command
            + [
                "--mode",
                "accept-edits",
                "--dangerously-skip-permissions",
                "--conversation",
                session_id,
                "--log-file",
                str(log_path),
                "-p",
                self.resume_prompt(token),
                "--print-timeout",
                f"{self.timeout}s",
            ],
            workspace,
        )
        resumed_id = self.native_probe().read_antigravity_workspace_session(workspace)
        self.assertEqual(resumed_id, session_id)
        return result.stdout

    def assert_marker_and_scope(self, workspace: Path, token: str, *, control_plane: bool = False) -> None:
        marker = workspace / MARKER_NAME
        self.assertTrue(marker.is_file(), f"Real CLI did not create {marker}.")
        self.assertEqual(marker.read_text(encoding="utf-8").strip(), token)

        status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=workspace,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(status.returncode, 0, status.stderr)
        changed = sorted(line[3:] for line in status.stdout.splitlines() if line)
        expected = [MARKER_NAME]
        if control_plane:
            expected.extend(
                [
                    ".orchestrator/INDEX.md",
                    ".orchestrator/handoffs/TASK-001-attempt-1.md",
                    ".orchestrator/tasks/TASK-001.md",
                ]
            )
        self.assertEqual(changed, sorted(expected), status.stdout)

    def test_claude_skill_real_bounded_task_and_exact_resume(self) -> None:
        self.require_provider("claude")
        token = self.marker_token("claude")
        with self.temporary_git_repository() as workspace:
            session_id, initial = self.run_claude_initial(workspace, token)
            self.assertIn(token, initial)
            self.assert_marker_and_scope(workspace, token)
            resumed = self.run_claude_resume(workspace, session_id, token)
            self.assertIn(token, resumed)
            self.assert_marker_and_scope(workspace, token)

    def test_codex_skill_real_bounded_task_and_exact_resume(self) -> None:
        self.require_provider("codex")
        token = self.marker_token("codex")
        with self.temporary_git_repository() as workspace:
            session_id, initial, environment = self.run_codex_initial(workspace, token)
            self.assertIn(token, initial)
            self.assert_marker_and_scope(workspace, token)
            resumed = self.run_codex_resume(workspace, session_id, token, environment)
            self.assertIn(token, resumed)
            self.assert_marker_and_scope(workspace, token)

    def test_antigravity_skill_real_bounded_task_and_exact_resume(self) -> None:
        self.require_provider("antigravity")
        token = self.marker_token("antigravity")
        with self.temporary_git_repository() as workspace:
            session_id, initial = self.run_antigravity_initial(workspace, token)
            self.assertIn(token, initial)
            self.assert_marker_and_scope(workspace, token)
            resumed = self.run_antigravity_resume(workspace, session_id, token)
            self.assertIn(token, resumed)
            self.assert_marker_and_scope(workspace, token)

    def test_antigravity_skill_real_original_pty_follow_up(self) -> None:
        """Use one real PTY, as required by Antigravity's live workflow."""
        self.require_provider("antigravity")
        if os.environ.get("RUN_REAL_CLI_ANTIGRAVITY_PTY") != "1":
            self.skipTest("Set RUN_REAL_CLI_ANTIGRAVITY_PTY=1 to run the PTY probe.")
        if sys.platform != "darwin":
            self.skipTest("The unattended Antigravity PTY probe is enabled only on macOS.")

        import pty
        import select

        initial = (
            "Begin a long, harmless explanation of HTTP caching. Do not use tools or "
            "modify files. Include FIRST-PTY-MARKER only after several paragraphs."
        )
        follow_up = "Stop the current response and reply exactly SECOND-PTY-MARKER."
        with self.temporary_git_repository() as workspace:
            master, slave = pty.openpty()
            process = subprocess.Popen(
                self.resolve_cli("agy") + ["--sandbox", "-i", initial],
                cwd=workspace,
                stdin=slave,
                stdout=slave,
                stderr=slave,
                close_fds=True,
                env=os.environ.copy(),
            )
            os.close(slave)
            output = bytearray()

            def read_until(needle: str, deadline: float) -> str:
                while time.monotonic() < deadline:
                    if process.poll() is not None:
                        break
                    ready, _, _ = select.select(
                        [master], [], [], min(0.25, deadline - time.monotonic())
                    )
                    if not ready:
                        continue
                    try:
                        output.extend(os.read(master, 8192))
                    except OSError:
                        break
                    decoded = output.decode("utf-8", errors="replace")
                    if needle in decoded:
                        return decoded
                return output.decode("utf-8", errors="replace")

            try:
                # Drain the first response while retaining the original process.
                read_until("FIRST-PTY-MARKER", time.monotonic() + 2)
                self.assertIsNone(process.poll(), "Antigravity exited before PTY follow-up.")
                os.write(master, (follow_up + "\r").encode("utf-8"))
                transcript = read_until("SECOND-PTY-MARKER", time.monotonic() + self.timeout)
                self.assertIn("SECOND-PTY-MARKER", transcript)
            finally:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)
                os.close(master)
            status = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=workspace,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(status.returncode, 0, status.stderr)
            self.assertEqual(status.stdout, "")

    def test_orchestrator_skill_local_markdown_dispatches_real_worker(self) -> None:
        self.require_provider("orchestrator")
        worker = os.environ.get(ORCHESTRATOR_PROVIDER_ENV, "codex").strip().lower()
        if worker not in DIRECT_PROVIDERS:
            self.fail(f"{ORCHESTRATOR_PROVIDER_ENV} must be one of {sorted(DIRECT_PROVIDERS)}.")

        dispatch_id = "task-TASK-001-attempt-1"
        token = self.marker_token(f"orchestrator-{worker}")
        with self.temporary_git_repository() as workspace:
            orchestrator = workspace / ".orchestrator"
            (orchestrator / "tasks").mkdir(parents=True)
            (orchestrator / "handoffs").mkdir()
            (orchestrator / "bugs").mkdir()
            index = orchestrator / "INDEX.md"
            task = orchestrator / "tasks" / "TASK-001.md"
            handoff = orchestrator / "handoffs" / "TASK-001-attempt-1.md"
            index.write_text(
                "# Orchestration Index\n\n"
                "## Control Plane\n"
                "- Mode: `local-markdown`\n"
                "- GitHub status: `not probed; test is offline by policy`\n\n"
                "## Task Ledger\n"
                "| Task | Dispatch | Mode | CLI / tier | Owns | Depends on | State |\n"
                "| --- | --- | --- | --- | --- | --- | --- |\n"
                f"| TASK-001 | `{dispatch_id}` | assign | {worker} / default | "
                f"`{MARKER_NAME}` | none | dispatched |\n\n"
                "## Append-Only Events\n"
                f"- Dispatch `{dispatch_id}` recorded before worker launch.\n",
                encoding="utf-8",
            )
            task.write_text(
                f"# TASK-001: Real CLI worker\n\n"
                "Parent: `INDEX.md`\n"
                "Status: `dispatched`\n"
                f"Dispatch: `{dispatch_id}`\n\n"
                "## Objective\n"
                f"Create `{MARKER_NAME}` in the dedicated workspace.\n\n"
                "## Ownership\n"
                f"- CLI / model tier: `{worker}` / default\n"
                f"- Worktree / branch: `{workspace}` / none\n"
                f"- May change: `{MARKER_NAME}`\n"
                "- Must not change: `.orchestrator/`, external state\n\n"
                "## Acceptance Checks\n"
                f"- `{MARKER_NAME}` contains the requested token\n",
                encoding="utf-8",
            )

            if worker == "claude":
                native_id, response = self.run_claude_initial(workspace, token)
            elif worker == "codex":
                native_id, response, _ = self.run_codex_initial(workspace, token)
            else:
                native_id, response = self.run_antigravity_initial(workspace, token)

            self.assertIn(token, response)
            handoff.write_text(
                "## Handoff\n"
                f"Dispatch: `{dispatch_id}`\n"
                "Status: `ready for review`\n"
                f"Native session: `{worker}` / `{native_id}` / `new`\n"
                "Process state: `stopped`\n"
                "Live transport: `unavailable`\n"
                "Current turn: `idle`\n\n"
                f"Changed: `{MARKER_NAME}`\n"
                "Branch/commit: none / none\n"
                f"Verification: marker content and git scope check -> passed\n"
                f"Evidence: real {worker} CLI created the marker in the dedicated workspace.\n"
                "Blocker: none\n"
                "Next owner: integration owner / run parent acceptance checks\n",
                encoding="utf-8",
            )
            task.write_text(
                task.read_text(encoding="utf-8").replace(
                    "Status: `dispatched`", "Status: `ready for review`"
                ),
                encoding="utf-8",
            )
            index.write_text(
                index.read_text(encoding="utf-8")
                + f"- Handoff `{dispatch_id}` verified with native session `{native_id}`.\n",
                encoding="utf-8",
            )

            self.assertIn(dispatch_id, index.read_text(encoding="utf-8"))
            self.assertIn(dispatch_id, task.read_text(encoding="utf-8"))
            handoff_text = handoff.read_text(encoding="utf-8")
            for field in (
                dispatch_id,
                f"`{worker}` / `{native_id}` / `new`",
                "Status: `ready for review`",
                "Process state: `stopped`",
                "Live transport: `unavailable`",
                "Current turn: `idle`",
                "Verification:",
                "Evidence:",
            ):
                self.assertIn(field, handoff_text)
            self.assert_marker_and_scope(workspace, token, control_plane=True)


if __name__ == "__main__":
    unittest.main()

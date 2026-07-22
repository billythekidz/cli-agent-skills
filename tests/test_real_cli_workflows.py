"""Opt-in end-to-end tests for the four documented CLI skill workflows.

These tests intentionally execute the installed provider CLIs. They are not
part of the default offline suite because they require provider authentication,
consume quota, persist native conversations, and may let an agent edit a
temporary workspace. Set RUN_REAL_CLI_SKILL_TESTS=1 to run them.

There is no separate ``orchestrator`` executable in this repository. Its real
workflow is tested in both documented modes: local-Markdown control-plane
records dispatch one real direct CLI worker, while opt-in supervisor probes
retain a real Claude or Codex process and inject a second prompt through the
same live handle.
"""

from __future__ import annotations

import json
import os
import signal
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
SUPERVISOR_LIVE_FLAG = "RUN_REAL_ORCHESTRATOR_SUPERVISOR_TESTS"
SUPERVISOR_ANTIGRAVITY_PTY_FLAG = "RUN_REAL_ORCHESTRATOR_ANTIGRAVITY_PTY"
FRESH_START_FLAG = "RUN_REAL_CLI_FRESH_START_TESTS"
FRESH_START_TIMEOUT_ENV = "REAL_CLI_FRESH_START_TIMEOUT_SECONDS"

DIRECT_PROVIDERS = frozenset({"claude", "codex", "antigravity"})
ALL_PROVIDERS = DIRECT_PROVIDERS | {"orchestrator"}
MARKER_NAME = "cli-skill-e2e-marker.txt"
SUPERVISOR_SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "orchestrator-cli"
    / "scripts"
    / "orchestrator_supervisor.py"
)
FRESH_START_REFERENCE = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "orchestrator-cli"
    / "references"
    / "fresh-start-without-integrations.md"
)


def fresh_start_hint() -> str:
    return (
        "Recovery after the 300-second startup budget: stop the failed route, "
        "run a fresh probe without MCP/plugins, and create a new dispatch/native "
        f"session. See {FRESH_START_REFERENCE}."
    )


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


def fresh_start_timeout_seconds() -> int:
    raw = os.environ.get(FRESH_START_TIMEOUT_ENV, "300")
    try:
        value = int(raw)
    except ValueError as error:
        raise ValueError(f"{FRESH_START_TIMEOUT_ENV} must be a whole number.") from error
    if value < 30:
        raise ValueError(f"{FRESH_START_TIMEOUT_ENV} must be at least 30.")
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
        cls.fresh_start_timeout = fresh_start_timeout_seconds()

    def require_provider(self, provider: str) -> None:
        if provider not in self.providers:
            self.skipTest(f"{provider} was not selected in {PROVIDER_ENV}.")

    def require_fresh_start(self, provider: str) -> None:
        self.require_provider(provider)
        if os.environ.get(FRESH_START_FLAG) != "1":
            self.skipTest(
                f"Set {FRESH_START_FLAG}=1 to run the real fresh-start recovery probe."
            )

    @staticmethod
    def seed_antigravity_gemini_dir(gemini_dir: Path, workspace: Path) -> None:
        """Create a clean provider config without inheriting user MCP servers."""
        (gemini_dir / "antigravity-cli" / "cache").mkdir(parents=True, exist_ok=True)
        (gemini_dir / "config").mkdir(parents=True, exist_ok=True)
        (gemini_dir / "antigravity-cli" / "cache" / "onboarding.json").write_text(
            json.dumps(
                {
                    "consumerOnboardingComplete": True,
                    "enterpriseOnboardingComplete": False,
                    "onboardingComplete": True,
                }
            )
            + "\n",
            encoding="utf-8",
        )
        (gemini_dir / "antigravity-cli" / "settings.json").write_text(
            json.dumps(
                {
                    "enableTelemetry": False,
                    "model": "Gemini 3.6 Flash (Low)",
                    "permissions": {
                        "allow": ["mcp(accounts/list)", "mcp(accounts/add)"]
                    },
                    "trustedWorkspaces": [str(workspace)],
                }
            )
            + "\n",
            encoding="utf-8",
        )
        (gemini_dir / "config" / "mcp_config.json").write_text(
            '{"mcpServers": {}}\n',
            encoding="utf-8",
        )
        (gemini_dir / "settings.json").write_text(
            json.dumps(
                {
                    "general": {
                        "sessionRetention": {
                            "enabled": True,
                            "maxAge": "30d",
                            "warningAcknowledged": True,
                        }
                    },
                    "security": {"auth": {"selectedType": "oauth-personal"}},
                    "ide": {"enabled": False},
                    "mcpServers": {},
                }
            )
            + "\n",
            encoding="utf-8",
        )

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
        timeout_seconds: int | None = None,
    ) -> subprocess.CompletedProcess[str]:
        effective_timeout = timeout_seconds or self.timeout
        try:
            result = subprocess.run(
                command,
                cwd=cwd,
                input=input_text,
                text=True,
                capture_output=True,
                errors="replace",
                env=env,
                timeout=effective_timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as error:
            self.fail(f"Timed out after {effective_timeout}s: {command!r}\n{error}")

        if check and result.returncode:
            self.fail(self.command_failure(command, result))
        return result

    def run_command_expected_timeout(
        self,
        command: list[str],
        cwd: Path,
        *,
        timeout_seconds: int,
        env: dict[str, str] | None = None,
    ) -> tuple[subprocess.CompletedProcess[str], bool]:
        """Run a deliberately blocked provider and reap its process group."""
        process = subprocess.Popen(
            command,
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            errors="replace",
            env=env,
            start_new_session=os.name != "nt",
        )
        try:
            stdout, stderr = process.communicate(timeout=timeout_seconds)
            return subprocess.CompletedProcess(command, process.returncode, stdout, stderr), False
        except subprocess.TimeoutExpired:
            if os.name == "nt":
                process.terminate()
            else:
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except OSError:
                    process.terminate()
            try:
                stdout, stderr = process.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                if os.name == "nt":
                    process.kill()
                else:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except OSError:
                        process.kill()
                stdout, stderr = process.communicate(timeout=10)
            return subprocess.CompletedProcess(command, process.returncode, stdout, stderr), True

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

    def test_real_fresh_probe_claude_without_mcp_or_plugins(self) -> None:
        self.require_fresh_start("claude")
        marker = "FRESH-CLAUDE-OK"
        with self.temporary_git_repository() as workspace:
            command = self.preflight("claude")
            empty_mcp = workspace.parent / "fresh-empty-mcp.json"
            empty_mcp.write_text('{"mcpServers":{}}\n', encoding="utf-8")
            result = self.run_command(
                command
                + [
                    "--bare",
                    "--strict-mcp-config",
                    "--mcp-config",
                    str(empty_mcp),
                    "-p",
                    f"Reply exactly {marker}. Do not use tools or modify files.",
                    "--output-format",
                    "json",
                    "--dangerously-skip-permissions",
                    "--max-budget-usd",
                    os.environ.get(CLAUDE_BUDGET_ENV, "1"),
                ],
                workspace,
                timeout_seconds=self.fresh_start_timeout,
            )
            self.assertIn(marker, result.stdout)
            status = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=workspace,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(status.returncode, 0, status.stderr)
            self.assertEqual(status.stdout, "")

    def test_real_fresh_probe_codex_without_mcp_or_plugins(self) -> None:
        self.require_fresh_start("codex")
        marker = "FRESH-CODEX-OK"
        with self.temporary_git_repository() as workspace:
            command = self.preflight("codex")
            environment = self.codex_environment(workspace)
            result = self.run_command(
                command
                + [
                    "exec",
                    "--ignore-user-config",
                    "--ephemeral",
                    "--sandbox",
                    "read-only",
                    "--json",
                    "-C",
                    str(workspace),
                    f"Reply exactly {marker}. Do not use tools or modify files.",
                ],
                workspace,
                env=environment,
                timeout_seconds=self.fresh_start_timeout,
            )
            self.assertIn(marker, result.stdout)
            status = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=workspace,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(status.returncode, 0, status.stderr)
            self.assertEqual(status.stdout, "")

    def test_real_fresh_probe_antigravity_without_mcp_or_plugins(self) -> None:
        self.require_fresh_start("antigravity")
        marker = "FRESH-AGY-OK"
        with self.temporary_git_repository() as workspace:
            gemini_dir = workspace.parent / "fresh-gemini"
            self.seed_antigravity_gemini_dir(gemini_dir, workspace)
            log_path = workspace.parent / "fresh-agy.log"
            result = self.run_command(
                self.resolve_cli("agy")
                + [
                    f"--gemini_dir={gemini_dir}",
                    "--log-file",
                    str(log_path),
                    "--mode",
                    "plan",
                    "--sandbox",
                    "-p",
                    f"Reply exactly {marker}. Do not use tools or modify files.",
                    "--print-timeout",
                    f"{self.fresh_start_timeout}s",
                ],
                workspace,
                timeout_seconds=self.fresh_start_timeout + 30,
            )
            self.assertIn(marker, result.stdout)
            self.assertNotIn("MCP:", log_path.read_text(encoding="utf-8", errors="replace"))
            status = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=workspace,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(status.returncode, 0, status.stderr)
            self.assertEqual(status.stdout, "")

    def test_real_antigravity_stuck_mcp_then_fresh_probe(self) -> None:
        """Prove a blocked MCP attempt is replaced by a clean real probe."""
        self.require_fresh_start("antigravity")
        if os.environ.get("RUN_REAL_CLI_FRESH_START_FAILURE_TESTS") != "1":
            self.skipTest(
                "Set RUN_REAL_CLI_FRESH_START_FAILURE_TESTS=1 to run the blocked-MCP probe."
            )
        try:
            failure_timeout = int(
                os.environ.get("REAL_CLI_FRESH_START_FAILURE_TIMEOUT_SECONDS", "45")
            )
        except ValueError as error:
            self.fail("REAL_CLI_FRESH_START_FAILURE_TIMEOUT_SECONDS must be an integer.")
        if failure_timeout < 30:
            self.fail("REAL_CLI_FRESH_START_FAILURE_TIMEOUT_SECONDS must be at least 30.")

        with self.temporary_git_repository() as workspace:
            blocked_server = workspace.parent / "blocked-mcp.py"
            blocked_server.write_text("import time\ntime.sleep(600)\n", encoding="utf-8")
            blocked_gemini = workspace.parent / "blocked-gemini"
            self.seed_antigravity_gemini_dir(blocked_gemini, workspace)
            (blocked_gemini / "config" / "mcp_config.json").write_text(
                json.dumps(
                    {
                        "mcpServers": {
                            "blocked-mcp": {
                                "command": sys.executable,
                                "args": [str(blocked_server)],
                            }
                        }
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            blocked_log = workspace.parent / "blocked-agy.log"
            blocked, timed_out = self.run_command_expected_timeout(
                self.resolve_cli("agy")
                + [
                    f"--gemini_dir={blocked_gemini}",
                    "--log-file",
                    str(blocked_log),
                    "--mode",
                    "plan",
                    "--sandbox",
                    "-p",
                    "Reply exactly BLOCKED-AGY-OK. Do not use tools or modify files.",
                    "--print-timeout",
                    f"{failure_timeout}s",
                ],
                workspace,
                timeout_seconds=failure_timeout + 30,
            )
            self.assertTrue(timed_out, fresh_start_hint())
            blocked_output = blocked.stdout + blocked.stderr
            blocked_diagnostics = blocked_log.read_text(
                encoding="utf-8", errors="replace"
            )
            self.assertNotIn("BLOCKED-AGY-OK", blocked_output)
            self.assertRegex(
                blocked_diagnostics,
                r"(?i)(MCP:|connecting|timed out|timeout)",
                fresh_start_hint(),
            )

            fresh_gemini = workspace.parent / "recovery-gemini"
            self.seed_antigravity_gemini_dir(fresh_gemini, workspace)
            fresh_log = workspace.parent / "recovery-agy.log"
            fresh = self.run_command(
                self.resolve_cli("agy")
                + [
                    f"--gemini_dir={fresh_gemini}",
                    "--log-file",
                    str(fresh_log),
                    "--mode",
                    "plan",
                    "--sandbox",
                    "-p",
                    "Reply exactly RECOVERED-AGY-OK. Do not use tools or modify files.",
                    "--print-timeout",
                    f"{self.fresh_start_timeout}s",
                ],
                workspace,
                timeout_seconds=self.fresh_start_timeout + 30,
            )
            self.assertIn("RECOVERED-AGY-OK", fresh.stdout)
            self.assertNotIn(
                "MCP:", fresh_log.read_text(encoding="utf-8", errors="replace")
            )

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

    def supervisor_command(
        self,
        runtime: Path,
        *args: str,
        env: dict[str, str] | None = None,
        check: bool = True,
    ) -> dict[str, object]:
        command = [
            sys.executable,
            str(SUPERVISOR_SCRIPT),
            "--runtime-root",
            str(runtime),
            "--json",
            *args,
        ]
        result = self.run_command(
            command,
            Path(__file__).resolve().parents[1],
            env=env,
            check=check,
        )
        try:
            data = json.loads(result.stdout)
        except json.JSONDecodeError as error:
            self.fail(f"Supervisor did not return JSON: {error}\n{result.stdout[-4000:]}")
        self.assertIsInstance(data, dict)
        return data

    def require_supervisor_live(self, provider: str) -> None:
        self.require_provider("orchestrator")
        if os.environ.get(SUPERVISOR_LIVE_FLAG) != "1":
            self.skipTest(
                f"Set {SUPERVISOR_LIVE_FLAG}=1 to run the real {provider} supervisor workflow."
            )
        provider_key = provider.strip().lower()
        if provider_key not in self.providers:
            self.skipTest(f"{provider} must be selected in {PROVIDER_ENV} for supervisor testing.")

    def wait_for_supervisor_status(
        self,
        runtime: Path,
        dispatch_id: str,
        predicate,
        *,
        env: dict[str, str] | None = None,
        timeout_seconds: int | None = None,
    ) -> dict[str, object]:
        deadline = time.monotonic() + (timeout_seconds or self.timeout)
        last: dict[str, object] = {}
        while time.monotonic() < deadline:
            data = self.supervisor_command(runtime, "status", dispatch_id, env=env)
            process = data.get("process")
            self.assertIsInstance(process, dict)
            last = process
            if predicate(process):
                return process
            if process.get("status") in {"worker-error", "live-transport-unavailable"}:
                self.fail(f"Supervisor worker entered {process.get('status')}: {process}")
            time.sleep(0.25)
        self.fail(f"Timed out waiting for supervisor status: {last}\n{fresh_start_hint()}")

    @staticmethod
    def supervisor_log_events(log_path: Path) -> list[dict[str, object]]:
        events: list[dict[str, object]] = []
        try:
            lines = log_path.read_text(encoding="utf-8").splitlines()
        except FileNotFoundError:
            return events
        for line in lines:
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(event, dict):
                events.append(event)
        return events

    def wait_for_supervisor_log(
        self,
        log_path: Path,
        predicate,
        *,
        diagnostic_paths: tuple[Path, ...] = (),
        timeout_seconds: int | None = None,
    ) -> list[dict[str, object]]:
        deadline = time.monotonic() + (timeout_seconds or self.timeout)
        events: list[dict[str, object]] = []
        while time.monotonic() < deadline:
            events = self.supervisor_log_events(log_path)
            if predicate(events):
                return events
            time.sleep(0.25)
        diagnostics: list[str] = []
        for diagnostic_path in diagnostic_paths:
            try:
                lines = diagnostic_path.read_text(encoding="utf-8", errors="replace").splitlines()
            except FileNotFoundError:
                lines = []
            diagnostics.append(f"{diagnostic_path}: {lines[-40:]}")
        suffix = f"\nDiagnostics:\n{chr(10).join(diagnostics)}" if diagnostics else ""
        self.fail(
            f"Timed out waiting for supervisor log {log_path}: {events[-10:]}"
            f"{suffix}\n{fresh_start_hint()}"
        )

    def test_orchestrator_supervisor_real_claude_stream_json(self) -> None:
        self.require_supervisor_live("Claude")
        token_one = self.marker_token("supervisor-claude-one")
        token_two = self.marker_token("supervisor-claude-two")
        dispatch_id = "task-TASK-supervisor-claude-attempt-1"

        with self.temporary_git_repository() as workspace:
            runtime = workspace / ".orchestrator" / "runtime"
            command = self.resolve_cli("claude") + [
                "-p",
                "--input-format",
                "stream-json",
                "--output-format",
                "stream-json",
                "--verbose",
                "--no-session-persistence",
                "--tools",
                "",
                "--permission-mode",
                "plan",
                "--safe-mode",
                "--max-budget-usd",
                os.environ.get(CLAUDE_BUDGET_ENV, "1"),
            ]
            try:
                started = self.supervisor_command(
                    runtime,
                    "start",
                    "--dispatch-id",
                    dispatch_id,
                    "--provider",
                    "claude-cli",
                    "--protocol",
                    "claude-stream-json",
                    "--workspace",
                    str(workspace),
                    "--",
                    *command,
                )
                self.assertTrue(started.get("ok"), started)
                log_path = Path(str(started["log_file"]))
                first_send = self.supervisor_command(
                    runtime,
                    "send",
                    dispatch_id,
                    f"Remember {token_one}. Reply exactly with {token_one}.",
                )
                self.assertTrue(first_send.get("ok"), first_send)
                first_events = self.wait_for_supervisor_log(
                    log_path,
                    lambda events: any(
                        event.get("event") == "stdout"
                        and token_one in str(event.get("line", ""))
                        for event in events
                    ),
                )
                first_status = self.wait_for_supervisor_status(
                    runtime,
                    dispatch_id,
                    lambda process: bool(process.get("native_session_id"))
                    and bool(process.get("live_handle")),
                )
                native_id = str(first_status["native_session_id"])
                pid = first_status["pid"]

                second_send = self.supervisor_command(
                    runtime,
                    "send",
                    dispatch_id,
                    f"Return exactly {token_two}; do not start another process.",
                )
                self.assertTrue(second_send.get("ok"), second_send)
                events = self.wait_for_supervisor_log(
                    log_path,
                    lambda current: any(
                        event.get("event") == "stdout"
                        and token_two in str(event.get("line", ""))
                        for event in current
                    ),
                )
                status = self.supervisor_command(runtime, "status", dispatch_id)
                process = status["process"]
                self.assertEqual(process["pid"], pid)
                self.assertEqual(process["native_session_id"], native_id)
                self.assertTrue(process["live_handle"])
                self.assertEqual(
                    sum(event.get("event") == "prompt-sent" for event in events),
                    2,
                )
                self.assertGreaterEqual(len(first_events), 1)
            finally:
                self.supervisor_command(runtime, "shutdown", check=False)

    def test_orchestrator_supervisor_real_codex_app_server(self) -> None:
        self.require_supervisor_live("Codex")
        token_one = self.marker_token("supervisor-codex-one")
        token_two = self.marker_token("supervisor-codex-two")
        dispatch_id = "task-TASK-supervisor-codex-attempt-1"

        with self.temporary_git_repository() as workspace:
            runtime = workspace / ".orchestrator" / "runtime"
            environment = self.codex_environment(workspace)
            command = self.resolve_cli("codex") + ["app-server", "--stdio"]
            try:
                started = self.supervisor_command(
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
                    *command,
                    env=environment,
                )
                self.assertTrue(started.get("ok"), started)
                log_path = Path(str(started["log_file"]))
                first_send = self.supervisor_command(
                    runtime,
                    "send",
                    dispatch_id,
                    f"Without tools, reply exactly with {token_one}.",
                    env=environment,
                )
                self.assertTrue(first_send.get("ok"), first_send)
                self.wait_for_supervisor_log(
                    log_path,
                    lambda events: any(
                        event.get("event") == "stdout"
                        and token_one in str(event.get("line", ""))
                        for event in events
                    ),
                )
                first_status = self.wait_for_supervisor_status(
                    runtime,
                    dispatch_id,
                    lambda process: bool(process.get("native_session_id"))
                    and bool(process.get("live_handle")),
                    env=environment,
                )
                thread_id = str(first_status["native_session_id"])
                pid = first_status["pid"]

                second_send = self.supervisor_command(
                    runtime,
                    "send",
                    dispatch_id,
                    f"Without tools, reply exactly with {token_two}.",
                    env=environment,
                )
                self.assertTrue(second_send.get("ok"), second_send)
                events = self.wait_for_supervisor_log(
                    log_path,
                    lambda current: any(
                        event.get("event") == "stdout"
                        and token_two in str(event.get("line", ""))
                        for event in current
                    ),
                )
                process = self.wait_for_supervisor_status(
                    runtime,
                    dispatch_id,
                    lambda current: current.get("current_turn") is None
                    and bool(current.get("live_handle")),
                    env=environment,
                )
                self.assertEqual(process["pid"], pid)
                self.assertEqual(process["native_session_id"], thread_id)
                self.assertTrue(process["live_handle"])
                self.assertIsNone(process["current_turn"])
                self.assertGreaterEqual(
                    sum(event.get("event") == "prompt-sent" for event in events),
                    2,
                )
            finally:
                self.supervisor_command(runtime, "shutdown", env=environment, check=False)

    def test_orchestrator_supervisor_real_antigravity_tmux_pty(self) -> None:
        """Use the real agy TUI through the managed macOS tmux PTY route."""
        self.require_supervisor_live("Antigravity")
        if os.environ.get(SUPERVISOR_ANTIGRAVITY_PTY_FLAG) != "1":
            self.skipTest(
                f"Set {SUPERVISOR_ANTIGRAVITY_PTY_FLAG}=1 to run the real Antigravity tmux PTY workflow."
            )
        if sys.platform != "darwin":
            self.skipTest("The real tmux Antigravity supervisor probe is enabled only on macOS.")
        if not shutil.which("tmux"):
            self.skipTest("Install tmux before running the real Antigravity supervisor probe.")
        try:
            configured_timeout = int(
                os.environ.get("REAL_CLI_ANTIGRAVITY_PTY_TIMEOUT_SECONDS", "300")
            )
        except ValueError as error:
            self.fail("REAL_CLI_ANTIGRAVITY_PTY_TIMEOUT_SECONDS must be an integer.")
        if configured_timeout < 30:
            self.fail("REAL_CLI_ANTIGRAVITY_PTY_TIMEOUT_SECONDS must be at least 30.")
        pty_timeout = max(self.timeout, configured_timeout)

        initial = "Reply exactly FIRST-SUPERVISOR-PTY-MARKER. Do not use tools or modify files."
        follow_up = "Reply exactly SECOND-SUPERVISOR-PTY-MARKER. Do not use tools or modify files."
        dispatch_id = "task-TASK-supervisor-antigravity-attempt-1"
        with self.temporary_git_repository() as workspace:
            runtime = workspace / ".orchestrator" / "runtime"
            gemini_dir = workspace / ".agy-gemini"
            self.seed_antigravity_gemini_dir(gemini_dir, workspace)
            agy_log = runtime / "logs" / f"{dispatch_id}.agy.log"
            command = self.resolve_cli("agy") + [
                f"--gemini_dir={gemini_dir}",
                "--log-file",
                str(agy_log),
                "--sandbox",
                "-i",
                initial,
            ]
            try:
                started = self.supervisor_command(
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
                    *command,
                )
                self.assertTrue(started.get("ok"), started)
                log_path = Path(str(started["log_file"]))
                preflight_events = self.wait_for_supervisor_log(
                    log_path,
                    lambda events: any(
                        event.get("event") == "stdout"
                        and (
                            "FIRST-SUPERVISOR-PTY-MARKER" in str(event.get("line", ""))
                            or "Do you trust the contents of this project?" in str(
                                event.get("line", "")
                            )
                        )
                        for event in events
                    ),
                    diagnostic_paths=(agy_log,),
                    timeout_seconds=pty_timeout,
                )
                has_first_marker = any(
                    event.get("event") == "stdout"
                    and "FIRST-SUPERVISOR-PTY-MARKER" in str(event.get("line", ""))
                    for event in preflight_events
                )
                if not has_first_marker:
                    # Confirm the TUI's initial folder-trust screen through the
                    # same retained PTY; an empty prompt is an explicit Enter.
                    trusted = self.supervisor_command(runtime, "send", dispatch_id, "")
                    self.assertTrue(trusted.get("ok"), trusted)
                self.wait_for_supervisor_log(
                    log_path,
                    lambda events: any(
                        event.get("event") == "stdout"
                        and "FIRST-SUPERVISOR-PTY-MARKER" in str(event.get("line", ""))
                        for event in events
                    ),
                    diagnostic_paths=(agy_log,),
                    timeout_seconds=pty_timeout,
                )
                first_status = self.wait_for_supervisor_status(
                    runtime,
                    dispatch_id,
                    lambda process: process.get("transport") == "tmux"
                    and bool(process.get("live_handle")),
                    timeout_seconds=pty_timeout,
                )
                first_pid = first_status["pid"]
                metadata = first_status["transport_meta"]
                self.assertEqual(metadata["backend"], "tmux")

                sent = self.supervisor_command(runtime, "send", dispatch_id, follow_up)
                self.assertTrue(sent.get("ok"), sent)
                events = self.wait_for_supervisor_log(
                    log_path,
                    lambda current: any(
                        event.get("event") == "stdout"
                        and "SECOND-SUPERVISOR-PTY-MARKER" in str(event.get("line", ""))
                        for event in current
                    ),
                    diagnostic_paths=(agy_log,),
                    timeout_seconds=pty_timeout,
                )
                final_status = self.supervisor_command(runtime, "status", dispatch_id)["process"]
                self.assertEqual(final_status["pid"], first_pid)
                self.assertTrue(final_status["live_handle"])
                self.assertEqual(final_status["transport_meta"], metadata)
                self.assertGreaterEqual(
                    sum(event.get("event") == "prompt-sent" for event in events),
                    1,
                )
            finally:
                self.supervisor_command(runtime, "shutdown", check=False)

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

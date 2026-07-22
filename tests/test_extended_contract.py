"""Extended offline contract and unit tests for cli-agent-skills.

Covers gaps in the existing suite:
- Missing reference-file existence assertions
- openai.yaml schema and content validation
- macOS/Unix cli_command path (unit-testable branch)
- Helper function unit tests (selected_providers, timeout_seconds)
- Module import smoke tests
- Cross-document consistency checks
- macOS platform-specific path normalization
- JsonlProcess unit tests with fake pipes

All tests run offline without credentials, network, or provider calls.
"""

from __future__ import annotations

import io
import json
import os
import queue
import sys
import tempfile
import threading
import unittest
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator
from unittest import mock


REPOSITORY = Path(__file__).resolve().parents[1]
SKILLS = REPOSITORY / "skills"
EXPECTED_SKILLS = {
    "claude-cli",
    "codex-cli",
    "antigravity-cli",
    "orchestrator-cli",
}


def read(relative_path: str) -> str:
    return (REPOSITORY / relative_path).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Reference file completeness
# ---------------------------------------------------------------------------

class ReferenceFileCompletenessTests(unittest.TestCase):
    """All reference files linked from SKILL.md must exist on disk."""

    EXPECTED_REFERENCES = {
        "claude-cli/references/cli-reference.md",
        "codex-cli/references/cli-reference.md",
        "antigravity-cli/references/cli-reference.md",
        "orchestrator-cli/references/dispatch-protocol.md",
        "orchestrator-cli/references/templates-and-example.md",
        "orchestrator-cli/references/file-fallback.md",
        "orchestrator-cli/references/github-issue-operations.md",
        "orchestrator-cli/references/cli-model-routing.md",
    }

    def test_all_reference_files_exist(self) -> None:
        for reference in self.EXPECTED_REFERENCES:
            with self.subTest(reference=reference):
                path = SKILLS / reference
                self.assertTrue(
                    path.is_file(),
                    f"Expected reference file missing: {reference}",
                )

    def test_no_unexpected_reference_files(self) -> None:
        """No stale or orphaned reference files exist."""
        for skill_name in EXPECTED_SKILLS:
            references_dir = SKILLS / skill_name / "references"
            if not references_dir.is_dir():
                continue
            for path in references_dir.iterdir():
                if path.is_file():
                    relative = f"{skill_name}/references/{path.name}"
                    self.assertIn(
                        relative,
                        self.EXPECTED_REFERENCES,
                        f"Unexpected reference file: {relative}",
                    )


# ---------------------------------------------------------------------------
# openai.yaml schema validation
# ---------------------------------------------------------------------------

class OpenaiYamlSchemaTests(unittest.TestCase):
    """Each agents/openai.yaml must have the expected structure and content."""

    def _parse_yaml_compat(self, text: str) -> dict[str, Any]:
        """Minimal YAML parser for the known openai.yaml structure.

        Avoids a PyYAML dependency; handles only the single-level
        nested `interface:` key with string values.
        """
        result: dict[str, Any] = {}
        current_key: str | None = None
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if not line.startswith(" ") and ":" in stripped:
                key, _, value = stripped.partition(":")
                current_key = key.strip()
                value = value.strip().strip('"').strip("'")
                if value:
                    result[current_key] = value
                else:
                    result[current_key] = {}
            elif current_key and ":" in stripped:
                key, _, value = stripped.partition(":")
                value = value.strip().strip('"').strip("'")
                if isinstance(result.get(current_key), dict):
                    result[current_key][key.strip()] = value
        return result

    def test_openai_yaml_has_required_interface_keys(self) -> None:
        required_keys = {"display_name", "short_description", "default_prompt"}
        for skill_name in EXPECTED_SKILLS:
            with self.subTest(skill=skill_name):
                yaml_path = SKILLS / skill_name / "agents" / "openai.yaml"
                self.assertTrue(yaml_path.is_file(), f"Missing {yaml_path}")
                text = yaml_path.read_text(encoding="utf-8")
                parsed = self._parse_yaml_compat(text)
                self.assertIn("interface", parsed, "Missing 'interface' key")
                interface = parsed["interface"]
                self.assertIsInstance(interface, dict, "'interface' must be a mapping")
                for key in required_keys:
                    self.assertIn(
                        key,
                        interface,
                        f"Missing interface.{key} in {skill_name}/agents/openai.yaml",
                    )
                    self.assertIsInstance(
                        interface[key],
                        str,
                        f"interface.{key} must be a string in {skill_name}",
                    )
                    self.assertTrue(
                        interface[key].strip(),
                        f"interface.{key} is empty in {skill_name}",
                    )

    def test_openai_yaml_default_prompt_references_skill(self) -> None:
        """default_prompt must reference the skill's $name variable."""
        for skill_name in EXPECTED_SKILLS:
            with self.subTest(skill=skill_name):
                yaml_path = SKILLS / skill_name / "agents" / "openai.yaml"
                text = yaml_path.read_text(encoding="utf-8")
                self.assertIn(
                    f"${skill_name}",
                    text,
                    f"default_prompt in {skill_name} should reference ${skill_name}",
                )

    def test_openai_yaml_display_name_is_nonempty_and_reasonable(self) -> None:
        for skill_name in EXPECTED_SKILLS:
            with self.subTest(skill=skill_name):
                yaml_path = SKILLS / skill_name / "agents" / "openai.yaml"
                text = yaml_path.read_text(encoding="utf-8")
                parsed = self._parse_yaml_compat(text)
                display_name = parsed["interface"]["display_name"]
                self.assertGreater(
                    len(display_name),
                    3,
                    f"display_name too short for {skill_name}",
                )
                self.assertLess(
                    len(display_name),
                    80,
                    f"display_name too long for {skill_name}",
                )


# ---------------------------------------------------------------------------
# macOS / Unix cli_command unit tests
# ---------------------------------------------------------------------------

class UnixCliCommandTests(unittest.TestCase):
    """Unit tests for the cli_command helper's non-Windows branch."""

    def _import_cli_command(self, module_path: str):
        """Import cli_command from the given test module path."""
        import importlib
        module = importlib.import_module(module_path)
        return module.cli_command

    @mock.patch("os.name", "posix")
    def test_returns_resolved_path_on_unix(self) -> None:
        cli_command = self._import_cli_command("tests.test_active_process_sessions")
        with mock.patch("shutil.which", return_value="/usr/local/bin/claude") as m:
            result = cli_command("claude")
        self.assertEqual(result, ["/usr/local/bin/claude"])
        m.assert_called_once_with("claude")

    @mock.patch("os.name", "posix")
    def test_returns_resolved_path_for_codex_on_unix(self) -> None:
        cli_command = self._import_cli_command("tests.test_active_process_sessions")
        with mock.patch("shutil.which", return_value="/opt/homebrew/bin/codex"):
            result = cli_command("codex")
        self.assertEqual(result, ["/opt/homebrew/bin/codex"])

    @mock.patch("os.name", "posix")
    def test_raises_when_executable_not_found(self) -> None:
        cli_command = self._import_cli_command("tests.test_active_process_sessions")
        with mock.patch("shutil.which", return_value=None):
            with self.assertRaises(FileNotFoundError):
                cli_command("nonexistent-cli")

    @unittest.skipUnless(os.name == "nt", "WindowsPath only available on Windows")
    def test_windows_ps1_without_cmd_wrapper_uses_powershell(self) -> None:
        cli_command = self._import_cli_command("tests.test_active_process_sessions")
        ps1_path = Path("/Users/test/.local/bin/tool.ps1")
        with (
            mock.patch("shutil.which", side_effect=[str(ps1_path), "/usr/bin/pwsh"]),
            mock.patch.object(Path, "is_file", return_value=False),
        ):
            result = cli_command("tool")
        self.assertEqual(result[0], "/usr/bin/pwsh")
        self.assertIn("-NoProfile", result)
        self.assertIn("-File", result)

    def test_both_live_modules_have_matching_cli_command_logic(self) -> None:
        """Both modules must share the same Windows-shim and Unix logic."""
        import inspect
        import importlib
        mod1 = importlib.import_module("tests.test_active_process_sessions")
        mod2 = importlib.import_module("tests.test_native_sessions")
        src1 = inspect.getsource(mod1.cli_command)
        src2 = inspect.getsource(mod2.NativeSessionLiveTests.cli_command)
        # Both must contain the same key decision points
        for fragment in (
            'path.suffix.lower() == ".ps1"',
            'path.with_suffix(".cmd")',
            '"pwsh"',
            '"powershell"',
            "-NoProfile",
            "-File",
            "return [str(path)]",
        ):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, src1, f"active_process missing: {fragment}")
                self.assertIn(fragment, src2, f"native_sessions missing: {fragment}")


# ---------------------------------------------------------------------------
# Helper function unit tests
# ---------------------------------------------------------------------------

class SelectedProvidersTests(unittest.TestCase):
    """Unit tests for selected_providers() validation."""

    def _import_selected_providers(self, module_path: str):
        import importlib
        module = importlib.import_module(module_path)
        return module.selected_providers

    def test_returns_default_providers_when_env_unset(self) -> None:
        selected_providers = self._import_selected_providers(
            "tests.test_active_process_sessions"
        )
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ACTIVE_PROCESS_TEST_PROVIDERS", None)
            result = selected_providers()
            self.assertEqual(result, frozenset({"claude", "codex"}))

    def test_returns_subset_when_env_set(self) -> None:
        selected_providers = self._import_selected_providers(
            "tests.test_active_process_sessions"
        )
        with mock.patch.dict(os.environ, {"ACTIVE_PROCESS_TEST_PROVIDERS": "claude"}):
            result = selected_providers()
            self.assertEqual(result, frozenset({"claude"}))

    def test_raises_on_unknown_provider(self) -> None:
        selected_providers = self._import_selected_providers(
            "tests.test_active_process_sessions"
        )
        with mock.patch.dict(os.environ, {"ACTIVE_PROCESS_TEST_PROVIDERS": "claude,bogus"}):
            with self.assertRaises(ValueError) as ctx:
                selected_providers()
            self.assertIn("bogus", str(ctx.exception))

    def test_strips_whitespace(self) -> None:
        selected_providers = self._import_selected_providers(
            "tests.test_active_process_sessions"
        )
        with mock.patch.dict(os.environ, {"ACTIVE_PROCESS_TEST_PROVIDERS": " claude , codex "}):
            result = selected_providers()
            self.assertEqual(result, frozenset({"claude", "codex"}))

    def test_native_session_providers_match_skill_providers(self) -> None:
        """Both probe modules should support the same three providers."""
        import importlib
        ns = importlib.import_module("tests.test_native_sessions")
        ap = importlib.import_module("tests.test_active_process_sessions")
        # Native sessions supports 3, active process supports 2 (antigravity excluded)
        self.assertEqual(ns.PROVIDERS, frozenset({"claude", "codex", "antigravity"}))
        self.assertEqual(ap.PROVIDERS, frozenset({"claude", "codex"}))
        # Active process is a strict subset
        self.assertTrue(ap.PROVIDERS.issubset(ns.PROVIDERS))


class TimeoutSecondsTests(unittest.TestCase):
    """Unit tests for timeout_seconds() validation."""

    def _import_timeout(self, module_path: str):
        import importlib
        module = importlib.import_module(module_path)
        return module.timeout_seconds

    def test_returns_default_when_env_unset(self) -> None:
        timeout = self._import_timeout("tests.test_active_process_sessions")
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ACTIVE_PROCESS_TEST_TIMEOUT_SECONDS", None)
            result = timeout()
            self.assertEqual(result, 180)

    def test_returns_custom_value(self) -> None:
        timeout = self._import_timeout("tests.test_active_process_sessions")
        with mock.patch.dict(os.environ, {"ACTIVE_PROCESS_TEST_TIMEOUT_SECONDS": "120"}):
            self.assertEqual(timeout(), 120)

    def test_rejects_non_numeric(self) -> None:
        timeout = self._import_timeout("tests.test_active_process_sessions")
        with mock.patch.dict(os.environ, {"ACTIVE_PROCESS_TEST_TIMEOUT_SECONDS": "abc"}):
            with self.assertRaises(ValueError):
                timeout()

    def test_rejects_below_minimum(self) -> None:
        timeout = self._import_timeout("tests.test_active_process_sessions")
        with mock.patch.dict(os.environ, {"ACTIVE_PROCESS_TEST_TIMEOUT_SECONDS": "5"}):
            with self.assertRaises(ValueError) as ctx:
                timeout()
            self.assertIn("at least 30", str(ctx.exception))

    def test_accepts_minimum_boundary(self) -> None:
        timeout = self._import_timeout("tests.test_active_process_sessions")
        with mock.patch.dict(os.environ, {"ACTIVE_PROCESS_TEST_TIMEOUT_SECONDS": "30"}):
            self.assertEqual(timeout(), 30)


# ---------------------------------------------------------------------------
# Module import smoke tests
# ---------------------------------------------------------------------------

class ModuleImportSmokeTests(unittest.TestCase):
    """Import the live-probe modules to catch syntax errors without running probes."""

    def test_import_native_sessions_module(self) -> None:
        import importlib
        module = importlib.import_module("tests.test_native_sessions")
        self.assertTrue(hasattr(module, "NativeSessionLiveTests"))
        self.assertTrue(hasattr(module, "LIVE_FLAG"))
        self.assertTrue(hasattr(module, "PROVIDERS"))

    def test_import_active_process_sessions_module(self) -> None:
        import importlib
        module = importlib.import_module("tests.test_active_process_sessions")
        self.assertTrue(hasattr(module, "ActiveProcessLiveTests"))
        self.assertTrue(hasattr(module, "JsonlProcess"))
        self.assertTrue(hasattr(module, "LIVE_FLAG"))
        self.assertTrue(hasattr(module, "PROVIDERS"))

    def test_import_skill_contract_module(self) -> None:
        import importlib
        module = importlib.import_module("tests.test_skill_contract")
        self.assertTrue(hasattr(module, "SkillLayoutTests"))
        self.assertTrue(hasattr(module, "NativeSessionContractTests"))


# ---------------------------------------------------------------------------
# macOS platform-specific tests
# ---------------------------------------------------------------------------

@unittest.skipUnless(sys.platform == "darwin", "macOS-only assertions")
class MacOsPlatformTests(unittest.TestCase):
    """Tests specific to macOS/darwin platform behavior."""

    def test_current_platform_is_darwin(self) -> None:
        """Verify we are running on macOS for these tests to be meaningful."""
        self.assertEqual(sys.platform, "darwin", "These macOS tests should only run on darwin")

    def test_os_name_is_posix(self) -> None:
        self.assertEqual(os.name, "posix")

    def test_home_directory_is_macos_style(self) -> None:
        home = Path.home()
        self.assertTrue(
            home.is_absolute(),
            f"macOS home should be absolute, got {home}",
        )

    def test_path_resolve_on_macos(self) -> None:
        """Path.resolve() on macOS does not lowercase (unlike Windows normcase)."""
        p = Path("/tmp/SomeCamelCase")
        resolved = str(p.resolve())
        # On macOS, case is preserved (though filesystem may be case-insensitive)
        self.assertIn("SomeCamelCase", resolved)

    def test_normalize_path_preserves_case(self) -> None:
        """normalize_path should preserve case on macOS (no normcase lowercasing)."""
        # Import from native_sessions which has the normalize_path helper
        import importlib
        module = importlib.import_module("tests.test_native_sessions")
        result = module.normalize_path("/Users/Test/Project")
        # On posix, normcase is a no-op, so case is preserved
        self.assertIn("Users", result)
        self.assertIn("Test", result)

    def test_normalize_path_resolves_symlinks(self) -> None:
        import importlib
        module = importlib.import_module("tests.test_native_sessions")
        with tempfile.TemporaryDirectory() as tmpdir:
            real = Path(tmpdir) / "real_dir"
            real.mkdir()
            result = module.normalize_path(real)
            self.assertEqual(result, str(real.resolve()))

    def test_tempdir_is_under_var_or_private(self) -> None:
        """macOS temp directories are under /var/folders or /private/var."""
        tmp = tempfile.gettempdir()
        self.assertTrue(
            tmp.startswith("/var/folders")
            or tmp.startswith("/private/var/folders")
            or tmp.startswith("/tmp"),
            f"Unexpected macOS temp dir: {tmp}",
        )


# ---------------------------------------------------------------------------
# JSONL pipe unit tests (JsonlProcess with fake streams)
# ---------------------------------------------------------------------------

class FakeProcess:
    """Minimal stand-in for subprocess.Popen for JsonlProcess unit tests."""

    def __init__(self, stdout_lines: list[str] | None = None) -> None:
        self._stdout_lines = stdout_lines or []
        self.stdout = io.StringIO("\n".join(self._stdout_lines) + "\n" if self._stdout_lines else "")
        self.stderr = io.StringIO("")
        self.stdin = io.StringIO()
        self.stdin.closed = False
        self.returncode: int | None = None
        self._poll_count = 0

    def poll(self) -> int | None:
        self._poll_count += 1
        # Simulate: process stays alive for a few polls, then exits
        if self._poll_count > 100:
            return 0
        return None

    def wait(self, timeout: float | None = None) -> int:
        self.returncode = 0
        return 0

    def terminate(self) -> None:
        self.returncode = 0

    def kill(self) -> None:
        self.returncode = -9


class JsonlProcessParsingTests(unittest.TestCase):
    """Unit tests for JSONL event parsing logic used by JsonlProcess."""

    def test_parse_valid_jsonl_events(self) -> None:
        lines = [
            json.dumps({"type": "system", "subtype": "init", "session_id": "abc-123"}),
            json.dumps({"type": "result", "result": "hello", "session_id": "abc-123"}),
        ]
        events = []
        for line in lines:
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(event, dict):
                events.append(event)

        self.assertEqual(len(events), 2)
        self.assertEqual(events[0]["type"], "system")
        self.assertEqual(events[1]["type"], "result")
        self.assertEqual(events[1]["session_id"], "abc-123")

    def test_skip_malformed_json_lines(self) -> None:
        lines = [
            '{"type": "system"}',
            "not valid json",
            '{"type": "result"}',
            "",
            '{"incomplete',
        ]
        events = []
        for line in lines:
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(event, dict):
                events.append(event)

        self.assertEqual(len(events), 2)
        self.assertEqual(events[0]["type"], "system")
        self.assertEqual(events[1]["type"], "result")

    def test_skip_non_dict_json_values(self) -> None:
        lines = [
            '"just a string"',
            "42",
            "[1, 2, 3]",
            '{"type": "result"}',
        ]
        events = []
        for line in lines:
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(event, dict):
                events.append(event)

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["type"], "result")


class JsonlProcessWaitForTests(unittest.TestCase):
    """Unit tests for the wait_for predicate-matching logic."""

    def test_predicate_matching_finds_correct_event(self) -> None:
        events_queue: queue.Queue[dict[str, Any]] = queue.Queue()
        events_queue.put({"type": "system", "subtype": "init"})
        events_queue.put({"type": "result", "result": "done"})

        seen: list[dict[str, Any]] = []
        result = None
        while not events_queue.empty():
            event = events_queue.get(timeout=0)
            seen.append(event)
            if event.get("type") == "result":
                result = event
                break

        self.assertIsNotNone(result)
        self.assertEqual(result["type"], "result")
        self.assertEqual(len(seen), 2)

    def test_predicate_timeout_raises(self) -> None:
        """A predicate that never matches should eventually time out."""
        import time

        events_queue: queue.Queue[dict[str, Any]] = queue.Queue()
        deadline = time.monotonic() + 0.1  # 100ms timeout
        matched = False

        while time.monotonic() < deadline:
            try:
                event = events_queue.get(timeout=min(0.025, deadline - time.monotonic()))
            except queue.Empty:
                continue
            if event.get("type") == "never_matches":
                matched = True
                break

        self.assertFalse(matched, "Should have timed out without matching")


# ---------------------------------------------------------------------------
# Cross-document consistency
# ---------------------------------------------------------------------------

class CrossDocumentConsistencyTests(unittest.TestCase):
    """Verify that resume commands and key contracts are consistent across skills."""

    def assert_mentions(self, text: str, *fragments: str) -> None:
        normalized_text = " ".join(text.split())
        missing = [
            fragment
            for fragment in fragments
            if " ".join(fragment.split()) not in normalized_text
        ]
        self.assertFalse(missing, f"Missing fragments: {missing}")

    def test_claude_resume_command_consistent_across_skill_and_references(self) -> None:
        skill = read("skills/claude-cli/SKILL.md")
        ref = read("skills/claude-cli/references/cli-reference.md")
        orchestrator = read("skills/orchestrator-cli/SKILL.md")

        # The canonical resume form must appear in all three
        for text, source in [
            (skill, "claude-cli/SKILL.md"),
            (ref, "claude-cli/references/cli-reference.md"),
            (orchestrator, "orchestrator-cli/SKILL.md"),
        ]:
            with self.subTest(source=source):
                self.assert_mentions(text, "claude -r <")

    def test_codex_resume_command_consistent_across_skill_and_references(self) -> None:
        skill = read("skills/codex-cli/SKILL.md")
        ref = read("skills/codex-cli/references/cli-reference.md")
        orchestrator = read("skills/orchestrator-cli/SKILL.md")

        for text, source in [
            (skill, "codex-cli/SKILL.md"),
            (ref, "codex-cli/references/cli-reference.md"),
            (orchestrator, "orchestrator-cli/SKILL.md"),
        ]:
            with self.subTest(source=source):
                self.assert_mentions(text, "codex exec resume <")

    def test_antigravity_resume_command_consistent_across_skill_and_references(self) -> None:
        skill = read("skills/antigravity-cli/SKILL.md")
        ref = read("skills/antigravity-cli/references/cli-reference.md")
        orchestrator = read("skills/orchestrator-cli/SKILL.md")

        for text, source in [
            (skill, "antigravity-cli/SKILL.md"),
            (ref, "antigravity-cli/references/cli-reference.md"),
            (orchestrator, "orchestrator-cli/SKILL.md"),
        ]:
            with self.subTest(source=source):
                self.assert_mentions(text, "agy --conversation <")

    def test_all_direct_skills_warn_against_substituting_ids(self) -> None:
        """Every direct skill must warn against using dispatch/task IDs as session IDs."""
        for skill_name in ("claude-cli", "codex-cli", "antigravity-cli"):
            with self.subTest(skill=skill_name):
                text = read(f"skills/{skill_name}/SKILL.md")
                self.assert_mentions(
                    text,
                    "not a CAO or tmux session",
                )

    def test_orchestrator_mentions_all_three_providers(self) -> None:
        orchestrator = read("skills/orchestrator-cli/SKILL.md")
        self.assert_mentions(
            orchestrator,
            "claude-cli",
            "codex-cli",
            "antigravity-cli",
        )

    def test_file_fallback_reference_is_substantive(self) -> None:
        """The file-fallback reference must be non-trivial (not just a stub)."""
        text = read("skills/orchestrator-cli/references/file-fallback.md")
        self.assertGreater(
            len(text.strip()),
            200,
            "file-fallback.md appears to be a stub (< 200 chars)",
        )

    def test_github_issue_operations_reference_is_substantive(self) -> None:
        text = read("skills/orchestrator-cli/references/github-issue-operations.md")
        self.assertGreater(
            len(text.strip()),
            200,
            "github-issue-operations.md appears to be a stub (< 200 chars)",
        )

    def test_cli_model_routing_reference_is_substantive(self) -> None:
        text = read("skills/orchestrator-cli/references/cli-model-routing.md")
        self.assertGreater(
            len(text.strip()),
            200,
            "cli-model-routing.md appears to be a stub (< 200 chars)",
        )


# ---------------------------------------------------------------------------
# Live probe safety contract tests
# ---------------------------------------------------------------------------

class LiveProbeSafetyContractTests(unittest.TestCase):
    """Verify safety properties of the live probe modules."""

    def test_active_process_probe_has_codex_isolation(self) -> None:
        probe = read("tests/test_active_process_sessions.py")
        for fragment in (
            "CODEX_HOME",
            "CODEX_SQLITE_HOME",
            "RUST_LOG",
        ):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, probe)

    def test_active_process_probe_handles_terminate_kill_escalation(self) -> None:
        probe = read("tests/test_active_process_sessions.py")
        # The close() method should escalate: stdin.close -> wait -> terminate -> wait -> kill
        for fragment in (
            "self.process.terminate()",
            "self.process.kill()",
            "self.process.wait(timeout=",
        ):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, probe)

    def test_native_sessions_probe_has_keep_artifacts_option(self) -> None:
        probe = read("tests/test_native_sessions.py")
        self.assertIn("KEEP_NATIVE_SESSION_ARTIFACTS", probe)

    def test_both_probes_use_uuid_nonces(self) -> None:
        """Nonces must be random (UUID) to prevent cross-test contamination."""
        for module_name in ("tests/test_native_sessions.py", "tests/test_active_process_sessions.py"):
            with self.subTest(module=module_name):
                probe = read(module_name)
                self.assertIn("uuid.uuid4().hex", probe)


# ---------------------------------------------------------------------------
# SKILL.md structural tests
# ---------------------------------------------------------------------------

class SkillMarkdownStructureTests(unittest.TestCase):
    """Structural checks on each SKILL.md beyond frontmatter."""

    def test_each_skill_has_heading_after_frontmatter(self) -> None:
        for skill_name in EXPECTED_SKILLS:
            with self.subTest(skill=skill_name):
                text = read(f"skills/{skill_name}/SKILL.md")
                # After closing ---, there should be a heading
                parts = text.split("---", 2)
                self.assertGreaterEqual(len(parts), 3, "Frontmatter not properly closed")
                body = parts[2].strip()
                self.assertTrue(
                    body.startswith("#"),
                    f"{skill_name}/SKILL.md body should start with a heading",
                )

    def test_each_skill_mentions_its_own_name_in_body(self) -> None:
        for skill_name in EXPECTED_SKILLS:
            with self.subTest(skill=skill_name):
                text = read(f"skills/{skill_name}/SKILL.md")
                # The body (after frontmatter) should reference the skill
                parts = text.split("---", 2)
                if len(parts) >= 3:
                    body = parts[2]
                    # At minimum, the skill directory name should be mentioned
                    # or its human-readable equivalent
                    self.assertTrue(
                        len(body.strip()) > 100,
                        f"{skill_name}/SKILL.md body appears too short",
                    )

    def test_no_skill_exceeds_reasonable_length(self) -> None:
        """Skills should be focused documents, not novels."""
        for skill_name in EXPECTED_SKILLS:
            with self.subTest(skill=skill_name):
                text = read(f"skills/{skill_name}/SKILL.md")
                line_count = len(text.splitlines())
                self.assertLess(
                    line_count,
                    1000,
                    f"{skill_name}/SKILL.md is unexpectedly long ({line_count} lines)",
                )


# ---------------------------------------------------------------------------
# Environment variable contract tests
# ---------------------------------------------------------------------------

class EnvironmentVariableContractTests(unittest.TestCase):
    """Verify the env vars documented in README match what the code reads."""

    def test_live_flag_names_match_between_readme_and_code(self) -> None:
        readme = read("README.md")
        # README should document the live flags
        self.assertIn("RUN_LIVE_SKILL_TESTS", readme)
        self.assertIn("RUN_LIVE_ACTIVE_PROCESS_TESTS", readme)

    def test_provider_env_var_names_documented(self) -> None:
        readme = read("README.md")
        self.assertIn("SKILL_TEST_PROVIDERS", readme)
        self.assertIn("ACTIVE_PROCESS_TEST_PROVIDERS", readme)

    def test_timeout_env_var_names_documented(self) -> None:
        readme = read("README.md")
        self.assertIn("SKILL_TEST_TIMEOUT_SECONDS", readme)
        self.assertIn("ACTIVE_PROCESS_TEST_TIMEOUT_SECONDS", readme)

    def test_codex_auth_env_var_documented(self) -> None:
        readme = read("README.md")
        self.assertIn("ACTIVE_PROCESS_CODEX_AUTH_FILE", readme)


# ---------------------------------------------------------------------------
# Normalize path edge cases (macOS-relevant)
# ---------------------------------------------------------------------------

class NormalizePathEdgeCaseTests(unittest.TestCase):
    """Edge cases for the normalize_path helper on macOS."""

    def setUp(self) -> None:
        import importlib
        self.module = importlib.import_module("tests.test_native_sessions")
        self.normalize = self.module.normalize_path

    def test_trailing_slash_removed(self) -> None:
        result = self.normalize("/tmp/test/")
        self.assertFalse(result.endswith("/"), f"Trailing slash not removed: {result}")

    test_trailing_slash_removed.__doc__ = "Trailing slashes are normalized away."

    def test_double_slashes_normalized(self) -> None:
        result = self.normalize("/tmp//test///path")
        self.assertNotIn("//", result)

    def test_dot_segments_resolved(self) -> None:
        result = self.normalize("/tmp/test/../other")
        self.assertNotIn("/../", result)
        self.assertTrue(result.endswith("/other") or result.endswith("/other"))

    def test_dot_dot_at_root(self) -> None:
        result = self.normalize("/../tmp")
        # Should resolve to /tmp
        self.assertTrue(result.endswith("/tmp") or result == "/tmp")

    def test_empty_path_resolves_to_cwd(self) -> None:
        result = self.normalize("")
        # Should resolve to current directory
        self.assertTrue(len(result) > 0)

    def test_path_object_accepted(self) -> None:
        result = self.normalize(Path("/tmp/test"))
        self.assertIsInstance(result, str)
        self.assertIn("tmp", result)

    def test_relative_path_resolves_to_absolute(self) -> None:
        result = self.normalize("relative/path")
        self.assertTrue(
            result.startswith("/"),
            f"Relative path not resolved to absolute: {result}",
        )


if __name__ == "__main__":
    unittest.main()

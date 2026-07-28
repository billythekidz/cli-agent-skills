"""Fast, dependency-free contract checks for the four skill documents."""

from __future__ import annotations

import re
import unittest
from pathlib import Path


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


class SkillLayoutTests(unittest.TestCase):
    def test_expected_skills_have_valid_frontmatter_and_metadata(self) -> None:
        for skill_name in EXPECTED_SKILLS:
            with self.subTest(skill=skill_name):
                skill_dir = SKILLS / skill_name
                skill_text = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
                match = re.match(
                    r"\A---\nname:\s*([a-z0-9-]+)\n.*?\n---\n",
                    skill_text,
                    re.DOTALL,
                )

                self.assertIsNotNone(match, "SKILL.md must start with frontmatter")
                self.assertEqual(match.group(1), skill_name)
                self.assertTrue(
                    (skill_dir / "agents" / "openai.yaml").is_file(),
                    "each installable skill needs agents/openai.yaml",
                )

    def test_expected_reference_files_exist(self) -> None:
        expected_references = {
            "claude-cli/references/cli-reference.md",
            "codex-cli/references/cli-reference.md",
            "antigravity-cli/references/cli-reference.md",
            "orchestrator-cli/references/dispatch-protocol.md",
            "orchestrator-cli/references/templates-and-example.md",
        }

        for reference in expected_references:
            with self.subTest(reference=reference):
                self.assertTrue((SKILLS / reference).is_file())

    def test_orchestrator_bundles_lightweight_supervisor(self) -> None:
        supervisor = SKILLS / "orchestrator-cli" / "scripts" / "orchestrator_supervisor.py"
        self.assertTrue(supervisor.is_file())
        text = supervisor.read_text(encoding="utf-8")
        for fragment in (
            "subprocess.Popen",
            "socketserver.ThreadingTCPServer",
            "sqlite3",
            "live-transport-unavailable",
            "claude-stream-json",
            "codex-app-server",
        ):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, text)


class NativeSessionContractTests(unittest.TestCase):
    def assert_mentions(self, text: str, *fragments: str) -> None:
        normalized_text = " ".join(text.split())
        missing = [
            fragment
            for fragment in fragments
            if " ".join(fragment.split()) not in normalized_text
        ]
        self.assertFalse(missing, f"Missing session contract fragments: {missing}")

    def test_direct_skills_keep_native_identity_separate_from_cao_or_tmux(self) -> None:
        for skill_name in ("claude-cli", "codex-cli", "antigravity-cli"):
            with self.subTest(skill=skill_name):
                text = read(f"skills/{skill_name}/SKILL.md")
                self.assert_mentions(
                    text,
                    "not a CAO or tmux session",
                    "provider, absolute workspace/worktree",
                    "factual handoff summary",
                )

    def test_claude_uses_an_exact_resumable_session(self) -> None:
        text = (
            read("skills/claude-cli/SKILL.md")
            + read("skills/claude-cli/references/cli-reference.md")
        )
        self.assert_mentions(
            text,
            "claude -r <session-id> -p",
            "Use `-c` only when the user explicitly",
            "--no-session-persistence",
            "--fork-session",
        )

    def test_codex_uses_an_exact_resumable_session(self) -> None:
        text = (
            read("skills/codex-cli/SKILL.md")
            + read("skills/codex-cli/references/cli-reference.md")
        )
        self.assert_mentions(
            text,
            "codex exec resume <session-id>",
            "Use `--last` only when the user explicitly",
            "--ephemeral",
        )

    def test_codex_documents_current_root_command_selection(self) -> None:
        text = (
            read("skills/codex-cli/SKILL.md")
            + read("skills/codex-cli/references/cli-reference.md")
        )
        self.assert_mentions(
            text,
            "codex --help",
            "codex exec",
            "codex review",
            "codex app-server",
            "codex doctor",
            "--cd <workspace>",
            "--add-dir <path>",
            "--ask-for-approval",
            "--search",
            "remote-control",
        )

    def test_antigravity_uses_an_exact_resumable_conversation(self) -> None:
        text = (
            read("skills/antigravity-cli/SKILL.md")
            + read("skills/antigravity-cli/references/cli-reference.md")
        )
        self.assert_mentions(
            text,
            "agy --conversation <id> -p",
            "Use `-c` only when the user explicitly",
            "last_conversations.json",
            "Do not edit or delete this cache",
        )

    def test_orchestrator_records_native_session_envelope_and_exact_resume(self) -> None:
        skill = read("skills/orchestrator-cli/SKILL.md")
        protocol = read("skills/orchestrator-cli/references/dispatch-protocol.md")
        templates = read("skills/orchestrator-cli/references/templates-and-example.md")

        self.assert_mentions(
            skill,
            "Provider: claude-cli | codex-cli | antigravity-cli",
            "Native session: <exact provider ID> | unavailable",
            "Workspace/worktree: <absolute path>",
            "Session action: new | resumed",
            "claude -r <id>",
            "codex exec resume <id>",
            "agy --conversation <id>",
            "Never substitute the dispatch ID, issue number, local task ID, or process",
            "Do not resume a still-running process with a second CLI invocation",
        )
        self.assert_mentions(
            protocol,
            "native-session-unavailable",
            "native-resume-failed",
            'Do not fall back to a "latest" session',
            "Never reuse that ID with a different provider",
        )
        self.assert_mentions(
            templates,
            "do not use a latest-session flag",
            "exact native session ID (or `unavailable`)",
        )


class NativeSessionProbeSafetyTests(unittest.TestCase):
    def test_live_probe_is_opt_in_and_does_not_load_codex_user_configuration(self) -> None:
        probe = read("tests/test_native_sessions.py")
        for fragment in (
            'LIVE_FLAG = "RUN_LIVE_SKILL_TESTS"',
            '"--sandbox"',
            '"read-only"',
            '"--ignore-user-config"',
            '"--ignore-rules"',
            '"--tools"',
        ):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, probe)


class LiveProcessContractTests(unittest.TestCase):
    def assert_mentions(self, text: str, *fragments: str) -> None:
        normalized_text = " ".join(text.split())
        missing = [
            fragment
            for fragment in fragments
            if " ".join(fragment.split()) not in normalized_text
        ]
        self.assertFalse(missing, f"Missing live-process contract fragments: {missing}")

    def test_claude_documents_one_live_jsonl_process(self) -> None:
        text = (
            read("skills/claude-cli/SKILL.md")
            + read("skills/claude-cli/references/cli-reference.md")
        )
        self.assert_mentions(
            text,
            "claude -p --input-format stream-json --output-format stream-json",
            "system/init",
            "result",
            "stdin JSONL",
            "one writer",
            "Two-turn recipe",
            "ALPHA-42",
        )

    def test_claude_documents_pipe_based_structured_output(self) -> None:
        text = (
            read("skills/claude-cli/SKILL.md")
            + read("skills/claude-cli/references/cli-reference.md")
        )
        self.assert_mentions(
            text,
            "--output-format json",
            "--json-schema <schema>",
            "ordinary stdin/stdout/stderr pipes",
            "no PTY is required",
        )

    def test_codex_documents_live_steering_separately_from_exec_resume(self) -> None:
        text = (
            read("skills/codex-cli/SKILL.md")
            + read("skills/codex-cli/references/cli-reference.md")
        )
        self.assert_mentions(
            text,
            "codex exec",
            "one-shot",
            "codex app-server",
            "turn/steer",
            "expectedTurnId",
            "turn/started",
            "turn/completed",
            "activeTurnNotSteerable",
            "In-flight steer recipe",
        )

    def test_antigravity_documents_the_original_interactive_pty(self) -> None:
        text = (
            read("skills/antigravity-cli/SKILL.md")
            + read("skills/antigravity-cli/references/cli-reference.md")
        )
        self.assert_mentions(
            text,
            "Get-Command agy -CommandType Application",
            "& $agy --sandbox -i",
            "original terminal/PTY",
            "agy send <process>",
            "creates a new process",
            "transport, not native",
            "In-flight PTY recipe",
            "SECOND-PTY-MARKER",
        )

    def test_antigravity_documents_pipe_based_print_mode(self) -> None:
        text = (
            read("skills/antigravity-cli/SKILL.md")
            + read("skills/antigravity-cli/references/cli-reference.md")
        )
        self.assert_mentions(
            text,
            "--output-format json",
            "--output-format stream-json",
            "--json-schema",
            "final result",
            "stdout/stderr pipes",
            "Print mode is one-shot",
            "no PTY is required",
        )

    def test_orchestrator_records_live_transport_without_confusing_it_for_identity(self) -> None:
        text = (
            read("skills/orchestrator-cli/SKILL.md")
            + read("skills/orchestrator-cli/references/dispatch-protocol.md")
            + read("skills/orchestrator-cli/references/templates-and-example.md")
        )
        self.assert_mentions(
            text,
            "Process state: active | stopped | unavailable",
            "Live transport: stdin JSONL | app-server stdio | original interactive PTY | unavailable",
            "Headless transport: stdout/stderr pipes | one-shot | unavailable",
            "Current turn: <turn ID> | awaiting result | idle | unavailable",
            "turn/steer",
            "original interactive PTY",
            "operational routing data, not the provider-native session ID",
            "Optional Live Process Supervisor",
            "<orchestrator-cli-skill-dir>/scripts/orchestrator_supervisor.py start",
            "live-transport-unavailable",
            "Active Follow-up Examples",
            "Action: turn/steer expectedTurnId=turn-456",
        )

    def test_orchestrator_prioritizes_headless_dispatch(self) -> None:
        text = (
            read("skills/orchestrator-cli/SKILL.md")
            + read("skills/orchestrator-cli/references/dispatch-protocol.md")
            + read("skills/orchestrator-cli/references/templates-and-example.md")
        )
        self.assert_mentions(
            text,
            "Delegate bounded work headlessly by default",
            "headless-one-shot",
            "headless-live",
            "interactive-live",
            "claude -p --output-format json --dangerously-skip-permissions",
            "codex exec --dangerously-bypass-approvals-and-sandbox --json",
            "agy -p --output-format json --mode accept-edits --dangerously-skip-permissions",
            "Prefer a headless one-shot command",
            "Never allocate a PTY for the default headless route",
        )

    def test_live_probe_is_opt_in_isolates_codex_home_and_handles_windows_shims(self) -> None:
        probe = read("tests/test_active_process_sessions.py")
        for fragment in (
            'LIVE_FLAG = "RUN_LIVE_ACTIVE_PROCESS_TESTS"',
            '"CODEX_HOME"',
            '"CODEX_SQLITE_HOME"',
            '"ACTIVE_PROCESS_CODEX_AUTH_FILE"',
            '"--input-format"',
            'path.suffix.lower() == ".ps1"',
            'path.with_suffix(".cmd")',
            "return [str(path)]",
            '"method": "turn/steer"',
            '"method": "turn/interrupt"',
            '"method") == "turn/started"',
        ):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, probe)

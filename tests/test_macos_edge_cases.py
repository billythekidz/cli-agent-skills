"""Offline macOS/Unix edge-case tests for the live-probe helpers.

The repository is documentation-first, so the executable behavior that can be
verified without credentials lives in the opt-in probe harnesses themselves.
These tests exercise those helpers rather than duplicating their implementation
in the assertions.  They deliberately use paths containing spaces and Unicode,
which catches shell-splitting and path-encoding regressions common on macOS.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import test_active_process_sessions as active_process
from tests import test_native_sessions as native_sessions


@unittest.skipUnless(os.name == "posix", "POSIX/macOS path behavior")
class MacOsPathAndCommandTests(unittest.TestCase):
    """Exercise direct executable resolution on the current POSIX host."""

    def test_active_process_cli_command_keeps_macos_path_verbatim(self) -> None:
        resolved = "/opt/homebrew/bin/Claude Tools/claude"
        with mock.patch.object(active_process.os, "name", "posix"):
            with mock.patch.object(
                active_process.shutil, "which", return_value=resolved
            ) as which:
                with mock.patch.object(Path, "is_file") as is_file:
                    self.assertEqual(
                        active_process.cli_command("claude"),
                        [resolved],
                    )
        which.assert_called_once_with("claude")
        is_file.assert_not_called()

    def test_native_session_cli_command_keeps_unicode_path_verbatim(self) -> None:
        resolved = "/Users/test/Library/工具/codex"
        probe = native_sessions.NativeSessionLiveTests("runTest")
        with mock.patch.object(native_sessions.os, "name", "posix"):
            with mock.patch.object(native_sessions.shutil, "which", return_value=resolved):
                self.assertEqual(probe.cli_command("codex"), [resolved])

    def test_posix_does_not_treat_ps1_suffix_as_a_windows_shim(self) -> None:
        resolved = "/Users/test/bin/tool.ps1"
        with mock.patch.object(active_process.os, "name", "posix"):
            with mock.patch.object(active_process.shutil, "which", return_value=resolved):
                self.assertEqual(active_process.cli_command("tool"), [resolved])

    def test_normalize_path_resolves_macos_symlink_and_preserves_case(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cli agent macOS ") as root:
            root_path = Path(root)
            real = root_path / "Workspace With Spaces" / "CamelCase"
            real.mkdir(parents=True)
            alias = root_path / "workspace-alias"
            alias.symlink_to(real, target_is_directory=True)

            result = native_sessions.normalize_path(alias)

        self.assertEqual(result, str(real.resolve()))
        self.assertIn("Workspace With Spaces", result)
        self.assertIn("CamelCase", result)
        self.assertNotIn("/../", result)

    def test_normalize_path_accepts_nonexistent_unicode_path(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cli agent macOS ") as root:
            candidate = Path(root) / "未作成" / "My Project" / "Tool"
            result = native_sessions.normalize_path(candidate)

        self.assertEqual(result, str(candidate.resolve()))
        self.assertTrue(result.endswith("/未作成/My Project/Tool"))


class JsonlProcessIntegrationTests(unittest.TestCase):
    """Exercise JsonlProcess against a harmless local subprocess."""

    @staticmethod
    def python_jsonl_worker() -> str:
        return (
            "import json, sys, time\n"
            "for line in sys.stdin:\n"
            "    message = json.loads(line)\n"
            "    print('not-json', flush=True)\n"
            "    print(json.dumps([1, 2, 3]), flush=True)\n"
            "    print(json.dumps({'method': 'echo', 'params': message}), flush=True)\n"
            "    time.sleep(1)\n"
        )

    def start_worker(self, root: Path) -> active_process.JsonlProcess:
        command = [sys.executable, "-c", self.python_jsonl_worker()]
        return active_process.JsonlProcess(command, root)

    def test_send_and_wait_for_ignore_non_event_jsonl(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cli agent macOS worker ") as root:
            process = self.start_worker(Path(root))
            try:
                message = {
                    "cwd": "/Users/test/My Project/工具",
                    "text": "é résumé",
                }
                process.send(message)
                event = process.wait_for(
                    lambda item: item.get("method") == "echo",
                    timeout=3,
                    label="echo event",
                )
                self.assertIsNone(process.process.poll())
            finally:
                process.close()

        self.assertEqual(event["params"], message)
        self.assertEqual(process.seen, [event])
        self.assertIsNotNone(process.process.poll())

    def test_wait_for_reports_early_nonzero_exit(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cli agent macOS worker ") as root:
            command = [sys.executable, "-c", "import sys; sys.exit(17)"]
            process = active_process.JsonlProcess(command, Path(root))
            try:
                with self.assertRaisesRegex(RuntimeError, r"code 17"):
                    process.wait_for(lambda _: True, timeout=2, label="event")
            finally:
                process.close()


class TemporaryWorkspaceAndIsolationTests(unittest.TestCase):
    """Verify macOS temporary workspaces and Codex environment isolation."""

    def test_active_process_workspace_is_a_clean_git_repo_and_is_removed(self) -> None:
        probe = active_process.ActiveProcessLiveTests("runTest")
        with probe.temporary_git_repository() as workspace:
            root = workspace.parent
            self.assertTrue(workspace.is_dir())
            status = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=workspace,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(status.returncode, 0)
            self.assertEqual(status.stdout, "")

        self.assertFalse(root.exists())

    def test_codex_home_is_copied_to_a_temp_path_with_unicode(self) -> None:
        probe = active_process.ActiveProcessLiveTests("runTest")
        with tempfile.TemporaryDirectory(prefix="cli agent macOS ") as root:
            workspace = Path(root) / "Рабочая папка"
            workspace.mkdir()
            auth_source = Path(root) / "auth source.json"
            auth_source.write_text('{"access_token":"test-only"}\n', encoding="utf-8")

            with mock.patch.dict(
                os.environ,
                {"ACTIVE_PROCESS_CODEX_AUTH_FILE": str(auth_source)},
            ):
                environment = probe.isolated_codex_environment(workspace)

            codex_home = Path(environment["CODEX_HOME"])
            self.assertEqual(codex_home.parent, workspace.parent)
            self.assertEqual(
                Path(environment["CODEX_SQLITE_HOME"]),
                codex_home / "sqlite",
            )
            self.assertEqual(
                (codex_home / "auth.json").read_text(encoding="utf-8"),
                auth_source.read_text(encoding="utf-8"),
            )
            self.assertNotEqual(environment["CODEX_HOME"], os.environ.get("CODEX_HOME"))


class NativeSessionParsingTests(unittest.TestCase):
    """Cover provider output parsing without starting a provider CLI."""

    def setUp(self) -> None:
        self.probe = native_sessions.NativeSessionLiveTests("runTest")

    def test_parse_claude_accepts_json_object_or_event_list(self) -> None:
        session_id = "11111111-1111-4111-8111-111111111111"
        payload = [
            {"type": "system", "subtype": "init", "session_id": session_id},
            {"type": "assistant", "content": []},
            {"type": "result", "session_id": session_id, "result": "ACK token"},
        ]
        self.assertEqual(
            self.probe.parse_claude_result(json.dumps(payload)),
            (session_id, "ACK token"),
        )
        self.assertEqual(
            self.probe.parse_claude_result(
                json.dumps({"type": "result", "session_id": session_id, "result": "done"})
            ),
            (session_id, "done"),
        )

    def test_parse_codex_ignores_noise_and_malformed_lines(self) -> None:
        session_id = "22222222-2222-4222-8222-222222222222"
        stdout = "\n".join(
            [
                "provider banner",
                "{not json}",
                json.dumps({"type": "turn.started", "id": "turn-1"}),
                json.dumps({"type": "thread.started", "thread_id": session_id}),
            ]
        )
        self.assertEqual(self.probe.parse_codex_thread_id(stdout), session_id)

    def test_require_uuid_normalizes_valid_uuid_and_rejects_other_ids(self) -> None:
        self.assertEqual(
            self.probe.require_uuid("33333333-3333-4333-8333-333333333333".upper(), "test"),
            "33333333-3333-4333-8333-333333333333",
        )
        with self.assertRaisesRegex(AssertionError, "non-UUID"):
            self.probe.require_uuid("dispatch-123", "test")

    def test_antigravity_cache_matches_resolved_workspace_not_dictionary_order(self) -> None:
        session_id = "44444444-4444-4444-8444-444444444444"
        other_session_id = "55555555-5555-4555-8555-555555555555"
        with tempfile.TemporaryDirectory(prefix="cli agent macOS cache ") as root:
            root_path = Path(root)
            home = root_path / "home"
            cache_path = home / ".gemini" / "antigravity-cli" / "cache" / "last_conversations.json"
            cache_path.parent.mkdir(parents=True)
            workspace = root_path / "Workspace With Spaces"
            workspace.mkdir()
            alias = root_path / "workspace-alias"
            alias.symlink_to(workspace, target_is_directory=True)
            other = root_path / "Other Workspace"
            other.mkdir()
            cache_path.write_text(
                json.dumps({str(other): other_session_id, str(alias): session_id}),
                encoding="utf-8",
            )

            with mock.patch.object(native_sessions.Path, "home", return_value=home):
                result = self.probe.read_antigravity_workspace_session(workspace)

        self.assertEqual(result, session_id)


if __name__ == "__main__":
    unittest.main()

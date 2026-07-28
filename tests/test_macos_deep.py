"""Deeper macOS/Unix edge-case tests for the live-probe helpers.

test_macos_edge_cases covers the broad strokes; this module drills into the
behaviors most likely to regress on a real macOS host and that the contract
documents depend on:

- Unicode decomposition (NFC vs NFD) on APFS, which affects the Antigravity
  workspace->conversation cache key and any exact-resume lookup.
- Temporary-workspace symlink resolution under /private/var (macOS symlinks
  /tmp -> /private/tmp and /var/folders -> /private/var/folders).
- JsonlProcess send/wait/close internals (closed stdin, non-dict JSON, early
  exit) without starting a provider.
- Provider/timeout parsing corner cases (empty, whitespace-only, duplicates,
  case folding) that selected_providers/timeout_seconds must tolerate.
- Codex isolation edge cases (missing auth file, missing token).
- The parser failure modes each provider output contract relies on.
- remove_tree_with_retry idempotency on already-removed trees.

All tests run offline; no credentials, network, or provider calls are required.
"""

from __future__ import annotations

import io
import json
import os
import queue
import shutil
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest import mock

from tests import test_active_process_sessions as active_process
from tests import test_native_sessions as native_sessions


# ---------------------------------------------------------------------------
# Unicode decomposition (APFS/HFS+ realism)
# ---------------------------------------------------------------------------

class UnicodeNormalizationTests(unittest.TestCase):
    """macOS APFS decomposes many combining-character sequences (NFD).

    The Antigravity cache maps *resolved* absolute workspace paths to IDs, so
    a lookup must not depend on whether the caller typed a precomposed (NFC) or
    decomposed (NFD) form.  normalize_path must be deterministic and stable.
    """

    def test_normalize_path_is_stable_for_repeated_calls(self) -> None:
        path = Path("/Users/test/My Project/工具")
        self.assertEqual(native_sessions.normalize_path(path), native_sessions.normalize_path(path))

    def test_normalize_path_accepts_string_or_path(self) -> None:
        path_str = "/tmp/CamelCase Project"
        path_obj = Path(path_str)
        self.assertEqual(native_sessions.normalize_path(path_str), native_sessions.normalize_path(path_obj))

    def test_apfs_nfc_directory_is_resolvable_from_nfc_path(self) -> None:
        """A directory created under an NFC name resolves back to a café form."""
        import unicodedata

        nfc = unicodedata.normalize("NFC", "café")
        with tempfile.TemporaryDirectory(prefix="cli-agent-nfc ") as root:
            directory = Path(root) / nfc
            directory.mkdir()
            resolved = native_sessions.normalize_path(directory)
            # The resolved path must contain the directory's name in some
            # café-equivalent form (the filesystem may store it decomposed).
            self.assertTrue(
                "café" in resolved or unicodedata.normalize("NFD", "café") in resolved,
                f"Resolved path lost the unicode name: {resolved}",
            )

    def test_antigravity_cache_lookup_matches_resolved_workspace(self) -> None:
        """A cache keyed by a resolved workspace must be found by normalize_path."""
        session_id = str(uuid.uuid4())
        with tempfile.TemporaryDirectory(prefix="cli-agent-cache ") as root:
            root_path = Path(root)
            home = root_path / "home"
            cache_path = home / ".gemini" / "antigravity-cli" / "cache" / "last_conversations.json"
            cache_path.parent.mkdir(parents=True)
            workspace = root_path / "Pröject Ünïcode"
            workspace.mkdir()
            cache_path.write_text(
                json.dumps({native_sessions.normalize_path(workspace): session_id}),
                encoding="utf-8",
            )

            probe = native_sessions.NativeSessionLiveTests("runTest")
            with mock.patch.object(native_sessions.Path, "home", return_value=home):
                result = probe.read_antigravity_workspace_session(workspace)

        self.assertEqual(result, session_id)


# ---------------------------------------------------------------------------
# macOS symlink resolution under /private/var
# ---------------------------------------------------------------------------

class MacOsPrivateVarSymlinkTests(unittest.TestCase):
    """macOS symlinks /tmp -> /private/tmp and /var/folders -> /private/var.

    normalize_path must follow these so two callers referencing the same tree
    through different prefixes compare equal.
    """

    def test_tempdir_resolves_to_private_var(self) -> None:
        import os.path

        tmp = str(Path(tempfile.gettempdir()).resolve())
        # On macOS the canonical tempdir lives under /private/var/folders.
        self.assertTrue(
            tmp.startswith("/private/var/folders") or tmp.startswith("/var/folders"),
            f"Unexpected resolved tempdir on macOS: {tmp}",
        )

    def test_normalize_path_resolves_tmp_alias_to_private(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cli-agent-symlink ") as root:
            real = Path(root) / "target"
            real.mkdir()
            alias = Path(root) / "alias"
            alias.symlink_to(real, target_is_directory=True)

            normalized_alias = native_sessions.normalize_path(alias)
            normalized_real = native_sessions.normalize_path(real)

        self.assertEqual(
            normalized_alias,
            normalized_real,
            "Symlink was not resolved to the same canonical path as its target.",
        )


# ---------------------------------------------------------------------------
# selected_providers / timeout_seconds parsing corner cases
# ---------------------------------------------------------------------------

class SelectedProvidersCornerCaseTests(unittest.TestCase):
    """selected_providers must tolerate degenerate env values gracefully."""

    def test_empty_value_yields_empty_set(self) -> None:
        with mock.patch.dict(os.environ, {"ACTIVE_PROCESS_TEST_PROVIDERS": ""}):
            self.assertEqual(active_process.selected_providers(), frozenset())

    def test_whitespace_and_commas_only_yields_empty_set(self) -> None:
        with mock.patch.dict(os.environ, {"ACTIVE_PROCESS_TEST_PROVIDERS": ", , ,"}):
            self.assertEqual(active_process.selected_providers(), frozenset())

    def test_duplicate_and_case_folded_providers_dedupe(self) -> None:
        with mock.patch.dict(os.environ, {"ACTIVE_PROCESS_TEST_PROVIDERS": "CLAUDE, Claude, codex"}):
            self.assertEqual(active_process.selected_providers(), frozenset({"claude", "codex"}))

    def test_unknown_provider_among_known_is_rejected(self) -> None:
        with mock.patch.dict(os.environ, {"ACTIVE_PROCESS_TEST_PROVIDERS": "claude,bogus"}):
            with self.assertRaises(ValueError) as ctx:
                active_process.selected_providers()
            self.assertIn("bogus", str(ctx.exception))

    def test_native_sessions_supports_all_three_providers(self) -> None:
        with mock.patch.dict(os.environ, {"SKILL_TEST_PROVIDERS": "claude,codex,antigravity"}):
            self.assertEqual(
                native_sessions.selected_providers(),
                frozenset({"claude", "codex", "antigravity"}),
            )

    def test_active_process_rejects_antigravity(self) -> None:
        """Antigravity has no unattended JSONL transport, so it is not supported."""
        with mock.patch.dict(os.environ, {"ACTIVE_PROCESS_TEST_PROVIDERS": "antigravity"}):
            with self.assertRaises(ValueError):
                active_process.selected_providers()


class TimeoutSecondsCornerCaseTests(unittest.TestCase):
    """timeout_seconds boundary and rejection behavior."""

    def test_below_minimum_rejected(self) -> None:
        with mock.patch.dict(os.environ, {"ACTIVE_PROCESS_TEST_TIMEOUT_SECONDS": "29"}):
            with self.assertRaises(ValueError) as ctx:
                active_process.timeout_seconds()
            self.assertIn("at least 30", str(ctx.exception))

    def test_minimum_boundary_accepted(self) -> None:
        with mock.patch.dict(os.environ, {"ACTIVE_PROCESS_TEST_TIMEOUT_SECONDS": "30"}):
            self.assertEqual(active_process.timeout_seconds(), 30)

    def test_negative_rejected(self) -> None:
        with mock.patch.dict(os.environ, {"ACTIVE_PROCESS_TEST_TIMEOUT_SECONDS": "-60"}):
            with self.assertRaises(ValueError):
                active_process.timeout_seconds()

    def test_float_string_rejected(self) -> None:
        with mock.patch.dict(os.environ, {"ACTIVE_PROCESS_TEST_TIMEOUT_SECONDS": "30.5"}):
            with self.assertRaises(ValueError):
                active_process.timeout_seconds()

    def test_native_sessions_has_longer_default_than_active_process(self) -> None:
        os.environ.pop("SKILL_TEST_TIMEOUT_SECONDS", None)
        os.environ.pop("ACTIVE_PROCESS_TEST_TIMEOUT_SECONDS", None)
        try:
            self.assertGreater(
                native_sessions.timeout_seconds(),
                active_process.timeout_seconds(),
            )
        finally:
            os.environ.pop("SKILL_TEST_TIMEOUT_SECONDS", None)
            os.environ.pop("ACTIVE_PROCESS_TEST_TIMEOUT_SECONDS", None)


# ---------------------------------------------------------------------------
# JsonlProcess internals (no real subprocess)
# ---------------------------------------------------------------------------

class JsonlProcessSendTests(unittest.TestCase):
    """send() must fail loudly when the process has no writable stdin."""

    def _bare_process(self, stdin) -> "active_process.JsonlProcess":
        proc = active_process.JsonlProcess.__new__(active_process.JsonlProcess)

        class _Fake:
            pass

        proc.process = _Fake()
        proc.process.stdin = stdin
        return proc

    def test_send_raises_when_stdin_is_none(self) -> None:
        proc = self._bare_process(stdin=None)
        with self.assertRaisesRegex(RuntimeError, "no writable stdin"):
            proc.send({"type": "user"})

    def test_send_writes_utf8_jsonl_line_and_flushes(self) -> None:
        captured: list[str] = []

        class _Stdin:
            closed = False

            def write(self, data: str) -> int:
                captured.append(data)
                return len(data)

            def flush(self) -> None:
                captured.append("<flush>")

        proc = self._bare_process(stdin=_Stdin())
        proc.send({"text": "é résumé 工具"})

        self.assertEqual(len(captured), 2)
        self.assertTrue(captured[0].endswith("\n"))
        decoded = json.loads(captured[0])
        self.assertEqual(decoded["text"], "é résumé 工具")
        self.assertNotIn("\\u", captured[0], "Non-ASCII must be written as UTF-8, not escaped.")
        self.assertEqual(captured[1], "<flush>")


class JsonlProcessReadStdoutTests(unittest.TestCase):
    """_read_stdout must skip non-JSON and non-dict lines, queueing only dicts."""

    def _make(self, stdout_text: str) -> "active_process.JsonlProcess":
        proc = active_process.JsonlProcess.__new__(active_process.JsonlProcess)
        proc.events = queue.Queue()
        proc.seen = []

        class _Fake:
            pass

        proc.process = _Fake()
        proc.process.stdout = io.StringIO(stdout_text)
        return proc

    def test_queues_only_dict_events(self) -> None:
        stdout = "\n".join([
            json.dumps([1, 2, 3]),          # list -> skip
            "not json",                      # malformed -> skip
            json.dumps({"method": "echo"}),  # dict -> queue
            json.dumps("a string"),          # str -> skip
            json.dumps({"type": "result"}),  # dict -> queue
        ])
        proc = self._make(stdout + "\n")
        proc._read_stdout()

        events = []
        while not proc.events.empty():
            events.append(proc.events.get_nowait())
        self.assertEqual(events, [{"method": "echo"}, {"type": "result"}])

    def test_empty_stdout_produces_no_events(self) -> None:
        proc = self._make("")
        proc._read_stdout()
        self.assertTrue(proc.events.empty())


class JsonlProcessWaitForEarlyExitTests(unittest.TestCase):
    """wait_for must surface an early process exit as a RuntimeError, not hang."""

    def _make(self, returncode: int | None) -> "active_process.JsonlProcess":
        proc = active_process.JsonlProcess.__new__(active_process.JsonlProcess)
        proc.events = queue.Queue()
        proc.seen = []

        class _Fake:
            pass

        proc.process = _Fake()
        proc.process.returncode = returncode

        def _poll() -> int | None:
            return returncode

        proc.process.poll = _poll
        return proc

    def test_wait_for_raises_on_already_exited_process(self) -> None:
        proc = self._make(returncode=42)
        with self.assertRaisesRegex(RuntimeError, r"code 42"):
            proc.wait_for(lambda _: True, timeout=2, label="event")


# ---------------------------------------------------------------------------
# remove_tree_with_retry robustness
# ---------------------------------------------------------------------------

class RemoveTreeWithRetryTests(unittest.TestCase):
    """remove_tree_with_retry must be a no-op on already-absent paths."""

    def test_nonexistent_path_returns_without_error(self) -> None:
        missing = Path(tempfile.gettempdir()) / f"does-not-exist-{uuid.uuid4().hex}"
        self.assertFalse(missing.exists())
        # Must not raise.
        active_process.ActiveProcessLiveTests.remove_tree_with_retry(missing)

    def test_native_variant_also_handles_missing_path(self) -> None:
        missing = Path(tempfile.gettempdir()) / f"does-not-exist-{uuid.uuid4().hex}"
        native_sessions.NativeSessionLiveTests.remove_tree_with_retry(missing)

    def test_real_tree_is_removed(self) -> None:
        root = Path(tempfile.mkdtemp(prefix="cli-agent-rmtree-"))
        (root / "nested" / "file.txt").parent.mkdir(parents=True)
        (root / "nested" / "file.txt").write_text("payload")
        active_process.ActiveProcessLiveTests.remove_tree_with_retry(root)
        self.assertFalse(root.exists())


# ---------------------------------------------------------------------------
# Codex isolation edge cases
# ---------------------------------------------------------------------------

class CodexIsolationEdgeCaseTests(unittest.TestCase):
    """isolated_codex_environment handling of missing auth sources."""

    def setUp(self) -> None:
        self.probe = active_process.ActiveProcessLiveTests("runTest")

    def test_missing_auth_file_and_token_skips(self) -> None:
        """Without an auth file or token, the probe must skip, not crash."""
        with tempfile.TemporaryDirectory(prefix="cli-agent-codex-") as root:
            workspace = Path(root) / "workspace"
            workspace.mkdir()
            env_without_codex = {
                k: v for k, v in os.environ.items()
                if k not in ("CODEX_ACCESS_TOKEN", "ACTIVE_PROCESS_CODEX_AUTH_FILE")
            }
            with mock.patch.dict(os.environ, env_without_codex, clear=True):
                with self.assertRaises(unittest.SkipTest):
                    self.probe.isolated_codex_environment(workspace)

    def test_auth_file_pointing_at_missing_file_fails(self) -> None:
        """A configured auth file path that does not exist must fail loudly."""
        with tempfile.TemporaryDirectory(prefix="cli-agent-codex-") as root:
            workspace = Path(root) / "workspace"
            workspace.mkdir()
            bogus = Path(root) / "missing-auth.json"
            with mock.patch.dict(os.environ, {"ACTIVE_PROCESS_CODEX_AUTH_FILE": str(bogus)}):
                with self.assertRaises(AssertionError):
                    self.probe.isolated_codex_environment(workspace)

    def test_codex_home_is_isolated_from_environment(self) -> None:
        """CODEX_HOME must not equal any ambient CODEX_HOME."""
        with tempfile.TemporaryDirectory(prefix="cli-agent-codex-") as root:
            workspace = Path(root) / "workspace"
            workspace.mkdir()
            auth_source = Path(root) / "auth.json"
            auth_source.write_text('{"access_token":"test-only"}\n', encoding="utf-8")
            with mock.patch.dict(os.environ, {"ACTIVE_PROCESS_CODEX_AUTH_FILE": str(auth_source)}):
                environment = self.probe.isolated_codex_environment(workspace)

        self.assertNotEqual(environment["CODEX_HOME"], os.environ.get("CODEX_HOME"))
        self.assertEqual(environment["RUST_LOG"], "error")
        self.assertTrue(environment["CODEX_HOME"].endswith("codex-home"))


# ---------------------------------------------------------------------------
# Provider output parser failure modes
# ---------------------------------------------------------------------------

class ParserFailureModeTests(unittest.TestCase):
    """Each provider parser must fail loudly (not return junk) on bad input."""

    def setUp(self) -> None:
        self.probe = native_sessions.NativeSessionLiveTests("runTest")

    def test_parse_codex_thread_id_returns_first_match(self) -> None:
        stdout = "\n".join([
            json.dumps({"type": "thread.started", "thread_id": "first-uuid"}),
            json.dumps({"type": "thread.started", "thread_id": "second-uuid"}),
        ])
        self.assertEqual(self.probe.parse_codex_thread_id(stdout), "first-uuid")

    def test_parse_codex_thread_id_fails_without_thread_started(self) -> None:
        with self.assertRaises(AssertionError):
            self.probe.parse_codex_thread_id("noise\n{}\nmore noise")

    def test_parse_codex_thread_id_ignores_non_string_thread_id(self) -> None:
        stdout = "\n".join([
            json.dumps({"type": "thread.started", "thread_id": 12345}),
            json.dumps({"type": "thread.started", "thread_id": "real-uuid"}),
        ])
        # The integer must be skipped; only a string thread_id qualifies.
        self.assertEqual(self.probe.parse_codex_thread_id(stdout), "real-uuid")

    def test_parse_claude_fails_without_session_id(self) -> None:
        with self.assertRaises(AssertionError):
            self.probe.parse_claude_result(json.dumps({"type": "result", "result": "hi"}))

    def test_parse_claude_fails_without_result(self) -> None:
        payload = [{"type": "system", "subtype": "init", "session_id": "abc"}]
        with self.assertRaises(AssertionError):
            self.probe.parse_claude_result(json.dumps(payload))

    def test_parse_claude_prefers_result_session_id(self) -> None:
        """The result event's session_id wins over the init event's."""
        session_id = str(uuid.uuid4())
        payload = [
            {"type": "system", "subtype": "init", "session_id": "init-id"},
            {"type": "result", "session_id": session_id, "result": "done"},
        ]
        parsed_id, parsed_text = self.probe.parse_claude_result(json.dumps(payload))
        self.assertEqual(parsed_id, session_id)
        self.assertEqual(parsed_text, "done")

    def test_require_uuid_rejects_non_string(self) -> None:
        with self.assertRaises(AssertionError):
            self.probe.require_uuid(12345, "test")

    def test_require_uuid_rejects_none(self) -> None:
        with self.assertRaises(AssertionError):
            self.probe.require_uuid(None, "test")

    def test_require_uuid_accepts_braced_uuid(self) -> None:
        valid = uuid.uuid4()
        self.assertEqual(self.probe.require_uuid(f"{{{valid}}}", "test"), str(valid))


# ---------------------------------------------------------------------------
# Native session prompt construction contracts
# ---------------------------------------------------------------------------

class NativeSessionPromptContractTests(unittest.TestCase):
    """The probe's non-invasive prompts must keep the nonce out of the resume."""

    def setUp(self) -> None:
        self.probe = native_sessions.NativeSessionLiveTests("runTest")

    def test_resume_prompt_does_not_embed_nonce(self) -> None:
        nonce = f"probe-{uuid.uuid4().hex}"
        self.assertNotIn(nonce, self.probe.resume_prompt())

    def test_initial_prompt_instructs_no_tools_or_network(self) -> None:
        nonce = f"probe-{uuid.uuid4().hex}"
        prompt = self.probe.initial_prompt(nonce)
        # The probe must tell the model not to use tools, run commands, or
        # touch the network -- it is a non-invasive continuity probe.
        self.assertRegex(prompt, r"(?i)(do not use tools)")
        self.assertRegex(prompt, r"(?i)(access the network|no.*network)")
        self.assertIn(nonce, prompt)


# ---------------------------------------------------------------------------
# Orchestrator live-process supervisor (pure functions, offline)
# ---------------------------------------------------------------------------

REPOSITORY = Path(__file__).resolve().parents[1]
SUPERVISOR_SCRIPT = (
    REPOSITORY / "skills" / "orchestrator-cli" / "scripts" / "orchestrator_supervisor.py"
)


def _load_supervisor_module():
    """Import the supervisor script as a module without starting its server."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "orchestrator_supervisor_under_test", SUPERVISOR_SCRIPT
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class SupervisorEncodePromptTests(unittest.TestCase):
    """encode_prompt must produce the exact per-protocol injection payloads.

    These are the contracts the SKILL.md protocol table documents:
    text -> prompt + newline; jsonl -> {type:user,text}; claude-stream-json ->
    a JSONL user message; codex-app-server -> turn/steer for a known turn else
    turn/start, both requiring a retained thread ID.
    """

    def setUp(self) -> None:
        self.supervisor = _load_supervisor_module()

    def test_text_protocol_appends_newline(self) -> None:
        self.assertEqual(
            self.supervisor.encode_prompt("text", "hello world", None),
            "hello world\n",
        )

    def test_jsonl_protocol_emits_user_text_object(self) -> None:
        payload = json.loads(self.supervisor.encode_prompt("jsonl", "hi", None))
        self.assertEqual(payload, {"type": "user", "text": "hi"})

    def test_claude_stream_json_emits_user_message_envelope(self) -> None:
        payload = json.loads(
            self.supervisor.encode_prompt("claude-stream-json", "hi", None)
        )
        self.assertEqual(payload["type"], "user")
        self.assertEqual(payload["message"]["role"], "user")
        self.assertEqual(
            payload["message"]["content"],
            [{"type": "text", "text": "hi"}],
        )

    def test_codex_protocol_requires_thread_id(self) -> None:
        """Without a retained thread ID, codex injection cannot be encoded."""
        with self.assertRaises(ValueError):
            self.supervisor.encode_prompt("codex-app-server", "hi", None, None)

    def test_codex_protocol_uses_turn_start_when_no_active_turn(self) -> None:
        payload = json.loads(
            self.supervisor.encode_prompt("codex-app-server", "hi", None, "thread-7")
        )
        self.assertEqual(payload["method"], "turn/start")
        self.assertEqual(payload["params"]["threadId"], "thread-7")
        self.assertEqual(
            payload["params"]["input"], [{"type": "text", "text": "hi"}]
        )

    def test_codex_protocol_uses_turn_steer_for_active_turn(self) -> None:
        payload = json.loads(
            self.supervisor.encode_prompt(
                "codex-app-server", "hi", "turn-9", "thread-7"
            )
        )
        self.assertEqual(payload["method"], "turn/steer")
        self.assertEqual(payload["params"]["expectedTurnId"], "turn-9")
        self.assertEqual(payload["params"]["threadId"], "thread-7")
        self.assertEqual(
            payload["params"]["input"], [{"type": "text", "text": "hi"}]
        )

    def test_unsupported_protocol_raises(self) -> None:
        with self.assertRaises(ValueError):
            self.supervisor.encode_prompt("bogus", "x", None)

    def test_all_payloads_are_single_line_jsonl(self) -> None:
        """JSON protocols emit one valid JSON line; text emits one plain line.

        The supervisor writes each payload as exactly one stdin line, so the
        body before the trailing newline must contain no embedded newline.
        text is plain stdin input (not JSON); the others must parse as JSON.
        """
        cases = [
            ("text", "p", None, None),
            ("jsonl", "p", None, None),
            ("claude-stream-json", "p", None, None),
            ("codex-app-server", "p", None, "thread"),
            ("codex-app-server", "p", "turn", "thread"),
        ]
        for protocol, prompt, turn, thread in cases:
            with self.subTest(protocol=protocol, turn=turn):
                encoded = self.supervisor.encode_prompt(protocol, prompt, turn, thread)
                self.assertTrue(encoded.endswith("\n"))
                # Exactly one line: no newline before the trailing one.
                body = encoded[:-1]
                self.assertNotIn("\n", body)
                if protocol != "text":
                    json.loads(body)
        for protocol, prompt, turn, thread in cases:
            with self.subTest(protocol=protocol, turn=turn):
                encoded = self.supervisor.encode_prompt(protocol, prompt, turn, thread)
                self.assertTrue(encoded.endswith("\n"))
                body = encoded[:-1]
                self.assertNotIn("\n", body)
                if protocol == "text":
                    self.assertEqual(body, prompt)
                else:
                    json.loads(body)


class SupervisorHelperTests(unittest.TestCase):
    """safe_filename, first_string, and RuntimePaths behavior on macOS."""

    def setUp(self) -> None:
        self.supervisor = _load_supervisor_module()

    def test_safe_filename_preserves_safe_chars_and_replaces_others(self) -> None:
        fn = self.supervisor.safe_filename("task-TASK_12.attempt-1/spaces here")
        self.assertNotIn(" ", fn)
        self.assertNotIn("/", fn)
        # Alphanumerics, dot, underscore, and dash are preserved verbatim.
        self.assertIn("task-TASK_12.attempt-1", fn)

    def test_first_string_returns_first_present_string(self) -> None:
        self.assertEqual(
            self.supervisor.first_string({"a": "1", "b": "2"}, "a", "b"),
            "1",
        )
        self.assertEqual(
            self.supervisor.first_string({"a": None, "b": "2"}, "a", "b"),
            "2",
        )
        self.assertIsNone(self.supervisor.first_string({"a": 123}, "a"))

    def test_first_string_skips_empty_strings(self) -> None:
        self.assertIsNone(self.supervisor.first_string({"a": ""}, "a"))

    def test_runtime_paths_resolve_under_orchestrator_runtime(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cli-agent-runtime-") as root:
            paths = self.supervisor.RuntimePaths(Path(root))
            self.assertEqual(paths.db, Path(root) / "supervisor.sqlite3")
            self.assertEqual(paths.server_info, Path(root) / "server.json")
            self.assertEqual(paths.logs, Path(root) / "logs")
            # ensure() creates both the root and logs directories.
            paths.ensure()
            self.assertTrue(paths.logs.is_dir())

    def test_default_runtime_root_is_dot_orchestrator_runtime(self) -> None:
        root = self.supervisor.default_runtime_root(Path("/tmp/sample-repo"))
        self.assertEqual(root, Path("/tmp/sample-repo/.orchestrator/runtime"))


class SupervisorResponseShapeTests(unittest.TestCase):
    """ok()/err() helpers produce the documented JSON envelope."""

    def setUp(self) -> None:
        self.supervisor = _load_supervisor_module()

    def test_ok_envelope_is_truthy_with_extra_fields(self) -> None:
        payload = self.supervisor.ok(pid=123, log_file="/x.jsonl")
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["pid"], 123)
        self.assertEqual(payload["log_file"], "/x.jsonl")

    def test_err_envelope_carries_code_and_message(self) -> None:
        payload = self.supervisor.err("live-transport-unavailable", "no handle")
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["code"], "live-transport-unavailable")
        self.assertEqual(payload["error"]["message"], "no handle")


if __name__ == "__main__":
    unittest.main()

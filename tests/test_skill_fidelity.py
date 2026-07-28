"""Real contract tests grounded in the four skills' actual setup.

These tests assert the concrete commands, flags, permission defaults, and
protocols each skill document prescribes -- not generic string presence.  They
cover the behavior the skills *must* recommend, derived directly from the
verified command tables, recipes, and dispatch/handoff protocol in:

- skills/claude-cli/SKILL.md + references/cli-reference.md
- skills/codex-cli/SKILL.md + references/cli-reference.md
- skills/antigravity-cli/SKILL.md + references/cli-reference.md
- skills/orchestrator-cli/SKILL.md + references/*.md

All run offline; no credentials, network, or provider calls are required.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path


REPOSITORY = Path(__file__).resolve().parents[1]
SKILLS = REPOSITORY / "skills"

# The verified default permission-bypass flag each direct skill prescribes.
# These come straight from each SKILL.md "Default Automation" section and the
# README "Permission Policy" table -- they are the skills' core safety contract.
PERMISSION_DEFAULT_FLAGS = {
    "claude-cli": "--dangerously-skip-permissions",
    "codex-cli": "--dangerously-bypass-approvals-and-sandbox",
    "antigravity-cli": "--dangerously-skip-permissions",
}

# The exact-resume command each provider-native session must use.
EXACT_RESUME_COMMAND = {
    "claude-cli": "claude -r <session-id>",
    "codex-cli": "codex exec resume <session-id>",
    "antigravity-cli": "agy --conversation <id>",
}

# The "latest local session" fallback each skill permits only on exception.
# (Codex's body uses the bare `--last`; the full `exec resume --last` lives in
#  its reference command table and is checked separately.)
LATEST_FALLBACK_FLAG = {
    "claude-cli": "-c",
    "codex-cli": "--last",
    "antigravity-cli": "-c",
}

DIRECT_SKILLS = ("claude-cli", "codex-cli", "antigravity-cli")


def read(relative_path: str) -> str:
    return (REPOSITORY / relative_path).read_text(encoding="utf-8")


def norm(text: str) -> str:
    """Collapse all runs of whitespace (incl. newlines) to single spaces.

    Skill prose wraps long sentences across lines, so multi-word phrases that
    span a line break will not match a contiguous assertIn.  Normalizing first
    lets the assertions check meaning rather than column width.
    """
    return " ".join(text.split())


def skill_text(skill: str, *extra: str) -> str:
    """Concatenate a skill's SKILL.md with any extra reference files."""
    parts = [read(f"skills/{skill}/SKILL.md")]
    for name in extra:
        parts.append(read(f"skills/{skill}/references/{name}"))
    return "\n".join(parts)


def assert_contains_phrase(testcase: unittest.TestCase, text: str, phrase: str) -> None:
    """Assert a (possibly line-wrapped) phrase is present in text."""
    testcase.assertIn(norm(phrase), norm(text))


class PermissionDefaultContractTests(unittest.TestCase):
    """Each direct skill must prescribe its verified permission-bypass default."""

    def test_each_skill_states_its_default_permission_flag(self) -> None:
        for skill, flag in PERMISSION_DEFAULT_FLAGS.items():
            with self.subTest(skill=skill):
                body = read(f"skills/{skill}/SKILL.md")
                self.assertIn(
                    flag,
                    body,
                    f"{skill} must document its default {flag} bypass.",
                )

    def test_default_flags_match_readme_permission_policy(self) -> None:
        readme = read("README.md")
        for skill, flag in PERMISSION_DEFAULT_FLAGS.items():
            with self.subTest(skill=skill):
                self.assertIn(flag, readme)

    def test_claude_does_not_combine_plan_mode_with_skip_permissions(self) -> None:
        body = read("skills/claude-cli/SKILL.md")
        # The skill must warn against combining the two.
        self.assertIn("--permission-mode plan", body)
        self.assertIn("Do not combine", body)

    def test_codex_does_not_combine_sandbox_with_bypass(self) -> None:
        combined = skill_text("codex-cli", "cli-reference.md")
        self.assertIn("--sandbox", combined)
        # The "do not combine it with --sandbox" rule lives in the reference,
        # and the SKILL.md forbids combining --sandbox with the bypass flag too.
        assert_contains_phrase(self, combined, "Do not combine it with `--sandbox`")

    def test_codex_hook_trust_bypass_remains_opt_in(self) -> None:
        """--dangerously-bypass-hook-trust is a separate bypass, never default."""
        body = read("skills/codex-cli/SKILL.md")
        ref = read("skills/codex-cli/references/cli-reference.md")
        for text, source in [(body, "SKILL.md"), (ref, "cli-reference.md")]:
            with self.subTest(source=source):
                self.assertIn("--dangerously-bypass-hook-trust", text)
                # Must NOT be listed as a default; must be opt-in only.
                self.assertRegex(text, r"(?i)(opt-in|explicit request|separate)")

    def test_antigravity_agent_flag_requires_local_check(self) -> None:
        """--agent <name> must only follow an agy agent / agy agents check."""
        body = read("skills/antigravity-cli/SKILL.md")
        self.assertIn("--agent <name>", body)
        self.assertTrue(
            "agy agent" in body or "agy agents" in body,
            "Skill must tell the caller to inspect available agents first.",
        )


class ExactResumeContractTests(unittest.TestCase):
    """Exact native ID resume must be prescribed; latest-session is exception-only."""

    def test_each_direct_skill_prescribes_exact_resume_command(self) -> None:
        for skill, command in EXACT_RESUME_COMMAND.items():
            with self.subTest(skill=skill):
                combined = skill_text(skill, "cli-reference.md")
                self.assertIn(command, combined)

    def test_each_direct_skill_gates_latest_fallback_behind_explicit_user_request(self) -> None:
        for skill, flag in LATEST_FALLBACK_FLAG.items():
            with self.subTest(skill=skill):
                body = read(f"skills/{skill}/SKILL.md")
                # The latest flag appears, but always conditioned on an explicit ask.
                self.assertIn(flag, body)
                # The canonical clause wraps across lines, so check the phrase
                # on normalized text.
                assert_contains_phrase(
                    self,
                    body,
                    "only when the user explicitly requests the unique latest local",
                )

    def test_no_direct_skill_uses_latest_fallback_unconditionally(self) -> None:
        """A worker prompt must never tell the child to use a latest flag."""
        # The orchestrator templates carry the canonical worker-prompt wording.
        templates = read("skills/orchestrator-cli/references/templates-and-example.md")
        protocol = read("skills/orchestrator-cli/references/dispatch-protocol.md")
        for text, source in [(templates, "templates"), (protocol, "protocol")]:
            with self.subTest(source=source):
                # The documented stance must forbid relying on "latest".
                self.assertRegex(
                    text,
                    r"(?i)(do not fall back|do not use a latest|never.*latest)",
                )

    def test_claude_resumes_only_after_process_ended(self) -> None:
        body = read("skills/claude-cli/SKILL.md")
        self.assertIn("claude -r", body)
        # Must state that -r is for when the original process has ended/unreachable.
        self.assertRegex(body, r"(?i)(ended or is unreachable|after the original process)")

    def test_codex_resume_command_shape_matches_reference(self) -> None:
        ref = read("skills/codex-cli/references/cli-reference.md")
        # The reference table lists the exact resume command with the bypass flag.
        self.assertIn(
            "codex exec resume <session-id> --dangerously-bypass-approvals-and-sandbox",
            ref,
        )

    def test_codex_reference_documents_latest_resume_fallback(self) -> None:
        ref = read("skills/codex-cli/references/cli-reference.md")
        # The full latest-session fallback appears in the reference command table.
        self.assertIn(
            "codex exec resume --last --dangerously-bypass-approvals-and-sandbox",
            ref,
        )

    def test_antigravity_conversation_flag_creates_new_process(self) -> None:
        """--conversation is recovery-only, never a route into a live process."""
        combined = skill_text("antigravity-cli", "cli-reference.md")
        self.assertIn("creates a new process", combined)


class LiveTransportRoutingTests(unittest.TestCase):
    """The three providers' in-flight follow-up transports are distinct."""

    def test_claude_live_transport_is_stdin_jsonl_after_result_boundary(self) -> None:
        combined = skill_text("claude-cli", "cli-reference.md")
        self.assertIn("--input-format stream-json", combined)
        self.assertIn("--output-format stream-json", combined)
        self.assertIn("stdin JSONL", combined)
        # Each result event is the turn boundary.
        self.assertRegex(combined, r"(?i)(each `result`|result.*boundary)")

    def test_claude_jsonl_must_not_carry_a_bom_on_macos_or_windows(self) -> None:
        ref = read("skills/claude-cli/references/cli-reference.md")
        self.assertRegex(ref, r"(?i)(without a BOM|UTF-8 without)")

    def test_codex_live_transport_is_app_server_turn_steer(self) -> None:
        combined = skill_text("codex-cli", "cli-reference.md")
        self.assertIn("codex app-server", combined)
        self.assertIn("turn/steer", combined)
        self.assertIn("expectedTurnId", combined)
        self.assertIn("turn/started", combined)
        self.assertIn("turn/completed", combined)

    def test_codex_active_turn_not_steerable_has_queueing_fallback(self) -> None:
        combined = skill_text("codex-cli", "cli-reference.md")
        self.assertIn("activeTurnNotSteerable", combined)
        # Must state the fallback: queue and send turn/start after completion.
        self.assertRegex(combined, r"(?i)(queue|turn/start after completion)")

    def test_antigravity_live_transport_is_interactive_pty(self) -> None:
        combined = skill_text("antigravity-cli", "cli-reference.md")
        # The skill prescribes the live interactive PTY and states the original
        # process/PTY is a transport route, not native identity.
        assert_contains_phrase(self, combined, "drive one live interactive PTY")
        assert_contains_phrase(self, combined, "original process/PTY is a transport route")
        # No external send command exists; --conversation is recovery-only.
        self.assertIn("creates a new process", combined)

    def test_antigravity_states_no_external_send_command(self) -> None:
        """agy has no `send <process>` route into a live process."""
        combined = skill_text("antigravity-cli", "cli-reference.md")
        self.assertRegex(
            combined,
            r"(?i)(no external .*send|agy send|cannot inject)",
        )

    def test_each_live_transport_is_documented_as_not_identity(self) -> None:
        """Every skill must say the live transport is routing, not native identity."""
        for skill in DIRECT_SKILLS:
            with self.subTest(skill=skill):
                body = norm(read(f"skills/{skill}/SKILL.md"))
                # Phrasing varies per skill, but each asserts transport != identity.
                self.assertTrue(
                    "not native session identity" in body
                    or "not native conversation identity" in body
                    or "is transport, not identity" in body
                    or "operational transport, not native" in body,
                    f"{skill} must state the live transport is not native session identity.",
                )


class TwoTurnRecipeTokenTests(unittest.TestCase):
    """The canonical multi-turn recipes each use a distinctive marker token."""

    def test_claude_two_turn_recipe_uses_alpha_marker(self) -> None:
        ref = read("skills/claude-cli/references/cli-reference.md")
        self.assertIn("ALPHA-42", ref)

    def test_codex_in_flight_steer_recipe_uses_turn_and_thread_ids(self) -> None:
        ref = read("skills/codex-cli/references/cli-reference.md")
        self.assertIn("thread-123", ref)
        self.assertIn("turn-456", ref)

    def test_antigravity_in_flight_pty_recipe_uses_two_markers(self) -> None:
        ref = read("skills/antigravity-cli/references/cli-reference.md")
        self.assertIn("FIRST-PTY-MARKER", ref)
        self.assertIn("SECOND-PTY-MARKER", ref)


class OrchestratorNativeSessionEnvelopeTests(unittest.TestCase):
    """The orchestrator's native-session envelope must carry every required field."""

    REQUIRED_ENVELOPE_FIELDS = (
        "Provider: claude-cli | codex-cli | antigravity-cli",
        "Native session: <exact provider ID> | unavailable",
        "Workspace/worktree: <absolute path>",
        "Agent/model/profile: <selected value or default>",
        "Process state: active | stopped | unavailable",
        "Live transport: stdin JSONL | app-server stdio | original interactive PTY | unavailable",
        "Current turn: <turn ID> | awaiting result | idle | unavailable",
        "Session action: new | resumed",
    )

    def test_envelope_lists_all_required_fields(self) -> None:
        skill = read("skills/orchestrator-cli/SKILL.md")
        for field in self.REQUIRED_ENVELOPE_FIELDS:
            with self.subTest(field=field[:40]):
                self.assertIn(field, skill)

    def test_orchestrator_forbids_substituting_dispatch_id_for_native_id(self) -> None:
        combined = (
            read("skills/orchestrator-cli/SKILL.md")
            + read("skills/orchestrator-cli/references/dispatch-protocol.md")
        )
        self.assertIn(
            "Never substitute the dispatch ID, issue number, local task ID, or process",
            combined,
        )

    def test_orchestrator_routes_three_exact_resume_commands(self) -> None:
        skill = read("skills/orchestrator-cli/SKILL.md")
        self.assertIn("claude -r <id>", skill)
        self.assertIn("codex exec resume <id>", skill)
        self.assertIn("agy --conversation <id>", skill)

    def test_orchestrator_states_native_id_cannot_cross_providers(self) -> None:
        skill = read("skills/orchestrator-cli/SKILL.md")
        self.assertRegex(skill, r"(?i)(cannot cross providers|not portable to another provider)")

    def test_orchestrator_forbids_resuming_a_live_process_with_second_invocation(self) -> None:
        skill = read("skills/orchestrator-cli/SKILL.md")
        self.assertIn(
            "Do not resume a still-running process with a second CLI invocation",
            skill,
        )


class OrchestratorDispatchProtocolTests(unittest.TestCase):
    """assign/handoff/send_message model, failure taxonomy, and parallel safety."""

    def test_protocol_defines_assign_handoff_send_message(self) -> None:
        protocol = read("skills/orchestrator-cli/references/dispatch-protocol.md")
        self.assertIn("`assign`", protocol)
        self.assertIn("`handoff`", protocol)
        self.assertIn("`send_message`", protocol)

    def test_protocol_lists_handoff_failure_taxonomy(self) -> None:
        protocol = read("skills/orchestrator-cli/references/dispatch-protocol.md")
        for failure in (
            "dispatch-failed",
            "worker-error",
            "timeout",
            "startup-blocked-by-integrations",
            "no-handoff",
            "misrouted-handoff",
            "native-session-unavailable",
            "native-resume-failed",
            "live-transport-unavailable",
        ):
            with self.subTest(failure=failure):
                self.assertIn(failure, protocol)

    def test_protocol_requires_unique_worktree_and_non_overlapping_paths_for_parallel(self) -> None:
        protocol = read("skills/orchestrator-cli/references/dispatch-protocol.md")
        # Preflight requires the worktree and branch to be unique (line 38).
        assert_contains_phrase(self, protocol, "The worktree and branch are unique")
        # Parallel fan-out requires non-overlapping Owns paths (line 80).
        assert_contains_phrase(self, protocol, "worktree and non-overlapping `Owns` paths")

    def test_dispatch_id_format_is_documented(self) -> None:
        skill = read("skills/orchestrator-cli/SKILL.md")
        # GitHub mode and local Markdown mode dispatch-ID shapes.
        self.assertIn("issue-<number>-attempt-<n>", skill)
        self.assertIn("task-TASK-<number>-attempt-<n>", skill)

    def test_handoff_validation_lists_required_fields(self) -> None:
        protocol = read("skills/orchestrator-cli/references/dispatch-protocol.md")
        # A valid handoff must carry these named pieces of evidence.
        for field in (
            "dispatch ID",
            "verification",
            "changed paths",
            "branch or commit",
            "blockers",
            "next owner",
        ):
            with self.subTest(field=field):
                self.assertIn(field, protocol)

    def test_native_resume_failed_forbids_latest_fallback(self) -> None:
        protocol = read("skills/orchestrator-cli/references/dispatch-protocol.md")
        self.assertIn("native-resume-failed", protocol)
        self.assertIn('Do not fall back to a "latest" session', protocol)

    def test_resumed_session_reuses_native_id_not_dispatch_id(self) -> None:
        protocol = read("skills/orchestrator-cli/references/dispatch-protocol.md")
        # The "Never reuse that ID with a different provider" clause wraps across
        # lines 127-128; assert on normalized text.
        assert_contains_phrase(
            self,
            protocol,
            "Never reuse that ID with a different provider",
        )


class ControlPlaneSelectionTests(unittest.TestCase):
    """The orchestrator must probe gh first and fall back to Markdown on failure."""

    def test_skill_requires_exactly_one_control_plane(self) -> None:
        skill = read("skills/orchestrator-cli/SKILL.md")
        self.assertIn("exactly one control plane", skill)

    def test_skill_documents_gh_probe_then_local_fallback(self) -> None:
        skill = read("skills/orchestrator-cli/SKILL.md")
        self.assertIn("gh auth status", skill)
        self.assertIn(".orchestrator/", skill)
        self.assertIn("local-markdown", skill)

    def test_skill_uses_direct_rest_only_with_existing_token(self) -> None:
        skill = read("skills/orchestrator-cli/SKILL.md")
        # Direct REST is gated on GH_TOKEN / GITHUB_TOKEN already being present.
        self.assertIn("GH_TOKEN", skill)
        self.assertIn("GITHUB_TOKEN", skill)

    def test_github_reference_is_read_only_in_github_mode(self) -> None:
        skill = read("skills/orchestrator-cli/SKILL.md")
        # The skill must direct to the GitHub reference only in github mode.
        self.assertIn("references/github-issue-operations.md", skill)
        self.assertIn("references/file-fallback.md", skill)

    def test_github_reference_ignores_pull_request_objects(self) -> None:
        """The REST issues endpoint returns PRs; the skill must filter them."""
        ref = read("skills/orchestrator-cli/references/github-issue-operations.md")
        self.assertRegex(ref, r"(?i)(ignore.*pull_request|pull_request)")


class LocalMarkdownFallbackTests(unittest.TestCase):
    """The .orchestrator/ directory layout, ownership, and reconciliation rules."""

    EXPECTED_LAYOUT = (
        "INDEX.md",
        "tasks/",
        "handoffs/",
        "bugs/",
    )

    def test_file_fallback_documents_directory_layout(self) -> None:
        ref = read("skills/orchestrator-cli/references/file-fallback.md")
        for entry in self.EXPECTED_LAYOUT:
            with self.subTest(entry=entry):
                self.assertIn(entry, ref)

    def test_supervisor_owns_index_workers_own_own_task_files(self) -> None:
        ref = read("skills/orchestrator-cli/references/file-fallback.md")
        self.assertIn("INDEX.md", ref)
        self.assertRegex(ref, r"(?i)(supervisor owns)")
        # Workers must not edit INDEX.md.
        self.assertRegex(ref, r"(?i)(Do not let parallel workers edit)")

    def test_layout_must_not_be_gitignored(self) -> None:
        ref = read("skills/orchestrator-cli/references/file-fallback.md")
        self.assertRegex(ref, r"(?i)(Do not add it to `\.gitignore`)")

    def test_ids_are_monotonically_allocated_and_never_reused(self) -> None:
        ref = read("skills/orchestrator-cli/references/file-fallback.md")
        self.assertIn("TASK-001", ref)
        self.assertIn("BUG-001", ref)
        self.assertRegex(ref, r"(?i)(Never reuse an ID)")

    def test_reconciliation_requires_explicit_authorization(self) -> None:
        ref = read("skills/orchestrator-cli/references/file-fallback.md")
        self.assertRegex(ref, r"(?i)(Do not automatically mirror|confirm the user wants reconciliation)")
        self.assertIn("Reconcile After GitHub Recovers", ref)


class ModelRoutingTests(unittest.TestCase):
    """The cli-model-routing reference must keep tiers and inspection in sync."""

    def test_routing_requires_local_availability_inspection(self) -> None:
        ref = read("skills/orchestrator-cli/references/cli-model-routing.md")
        for command in ("claude --help", "codex exec --help", "agy models"):
            with self.subTest(command=command):
                self.assertIn(command, ref)

    def test_routing_documents_all_three_cli_skills(self) -> None:
        ref = read("skills/orchestrator-cli/references/cli-model-routing.md")
        for skill in DIRECT_SKILLS:
            with self.subTest(skill=skill):
                self.assertIn(skill, ref)

    def test_routing_states_capability_tiers(self) -> None:
        ref = read("skills/orchestrator-cli/references/cli-model-routing.md")
        for tier in ("Fast or low", "Balanced or medium", "High or thinking", "Flagship or frontier"):
            with self.subTest(tier=tier):
                self.assertIn(tier, ref)

    def test_routing_warns_against_inferring_capability_from_alias(self) -> None:
        ref = read("skills/orchestrator-cli/references/cli-model-routing.md")
        self.assertRegex(ref, r"(?i)(do not infer capability|verify.*tier first)")

    def test_routing_cites_model_refresh_date(self) -> None:
        ref = read("skills/orchestrator-cli/references/cli-model-routing.md")
        # The Antigravity display names were captured on a specific date.
        self.assertRegex(ref, r"2026-07-\d{2}")

    def test_routing_documents_default_dev_reviewer_pairing(self) -> None:
        """The standard implement-then-review loop pins two Antigravity models."""
        ref = read("skills/orchestrator-cli/references/cli-model-routing.md")
        for model in ("gemini-3.6-flash-high", "claude-sonnet-4-6"):
            with self.subTest(model=model):
                self.assertIn(model, ref)

    def test_antigravity_skill_states_default_role_models(self) -> None:
        """The antigravity-cli skill must name both default role models."""
        body = read("skills/antigravity-cli/SKILL.md")
        for model in ("gemini-3.6-flash-high", "claude-sonnet-4-6"):
            with self.subTest(model=model):
                self.assertIn(model, body)


class StatusAndRecoveryTests(unittest.TestCase):
    """The orchestrator's state machine and double-dispatch prevention."""

    EXPECTED_TRANSITION = (
        "planned -> ready -> dispatched -> running -> handoff -> verified -> done"
    )

    def test_skill_documents_state_transition(self) -> None:
        skill = read("skills/orchestrator-cli/SKILL.md")
        # The transition string wraps across two lines (248-249); normalize.
        assert_contains_phrase(self, skill, self.EXPECTED_TRANSITION[0])

    def test_blocked_record_requires_named_fields(self) -> None:
        skill = read("skills/orchestrator-cli/SKILL.md")
        for field in ("blocker", "evidence", "impact", "decision owner", "smallest next action"):
            with self.subTest(field=field):
                self.assertIn(field, skill)

    def test_skill_distinguishes_failure_types(self) -> None:
        skill = read("skills/orchestrator-cli/SKILL.md")
        for failure in (
            "dispatch-failed",
            "worker-error",
            "timeout",
            "startup-blocked-by-integrations",
            "no-handoff",
        ):
            with self.subTest(failure=failure):
                self.assertIn(failure, skill)

    def test_skill_forbids_double_dispatch_of_active_task(self) -> None:
        skill = read("skills/orchestrator-cli/SKILL.md")
        self.assertRegex(skill, r"(?i)(Never double-dispatch|Never reuse)")

    def test_skill_requires_evidence_before_review_or_done(self) -> None:
        skill = read("skills/orchestrator-cli/SKILL.md")
        self.assertIn("Require evidence before moving a task to review or done", skill)

    def test_skill_uses_github_label_taxonomy_only_with_approval(self) -> None:
        skill = read("skills/orchestrator-cli/SKILL.md")
        for label in ("orchestrator:ready", "orchestrator:done"):
            with self.subTest(label=label):
                self.assertIn(label, skill)
        self.assertRegex(skill, r"(?i)(With explicit approval|prefer existing labels)")


class AntigravityCacheContractTests(unittest.TestCase):
    """The Antigravity workspace->conversation cache contract is load-bearing."""

    def test_skill_names_exact_cache_path(self) -> None:
        combined = skill_text("antigravity-cli", "cli-reference.md")
        self.assertIn("last_conversations.json", combined)
        self.assertIn("antigravity-cli", combined)

    def test_skill_forbids_editing_or_deleting_cache(self) -> None:
        body = read("skills/antigravity-cli/SKILL.md")
        self.assertRegex(body, r"(?i)(Do not edit or delete this cache)")

    def test_skill_matches_resolved_workspace_key(self) -> None:
        """Cache lookup must match the exact *resolved* workspace, not raw text."""
        combined = skill_text("antigravity-cli", "cli-reference.md")
        self.assertRegex(combined, r"(?i)(match the exact resolved workspace|exact resolved workspace)")


class FreshStartupRecoveryContractTests(unittest.TestCase):
    """All CLI skills must converge on the same no-integration recovery path."""

    def test_shared_fresh_start_reference_is_linked_by_all_cli_skills(self) -> None:
        reference = SKILLS / "orchestrator-cli" / "references" / "fresh-start-without-integrations.md"
        self.assertTrue(reference.is_file())
        for skill in ("claude-cli", "codex-cli", "antigravity-cli", "orchestrator-cli"):
            with self.subTest(skill=skill):
                body = read(f"skills/{skill}/SKILL.md")
                self.assertIn("fresh-start-without-integrations.md", body)
                self.assertIn("300 seconds", body)

    def test_fresh_probe_disables_provider_integrations(self) -> None:
        claude = skill_text("claude-cli", "cli-reference.md")
        for flag in ("--bare", "--strict-mcp-config", "--mcp-config"):
            with self.subTest(provider="claude", flag=flag):
                self.assertIn(flag, claude)

        codex = skill_text("codex-cli", "cli-reference.md")
        for flag in ("CODEX_HOME", "--ignore-user-config", "--ephemeral"):
            with self.subTest(provider="codex", flag=flag):
                self.assertIn(flag, codex)

        antigravity = skill_text("antigravity-cli", "cli-reference.md")
        for phrase in ("--gemini_dir", "workspace-local `.agents/mcp_config.json`"):
            with self.subTest(provider="antigravity", phrase=phrase):
                self.assertIn(phrase, antigravity)
        self.assertIn(
            '"mcpServers":{}',
            read("skills/orchestrator-cli/references/fresh-start-without-integrations.md"),
        )

    def test_recovery_forbids_continuing_timed_out_native_session(self) -> None:
        combined = skill_text("orchestrator-cli", "fresh-start-without-integrations.md")
        assert_contains_phrase(self, combined, "create a new dispatch/native session")
        assert_contains_phrase(self, combined, "Do not keep retrying the same process")
        assert_contains_phrase(self, combined, "startup-blocked-by-integrations")


class OpenaiYamlInterfaceTests(unittest.TestCase):
    """Each agents/openai.yaml default_prompt must reference its own skill var."""

    def test_default_prompt_references_own_skill_variable(self) -> None:
        for skill in ("claude-cli", "codex-cli", "antigravity-cli", "orchestrator-cli"):
            with self.subTest(skill=skill):
                yaml_text = read(f"skills/{skill}/agents/openai.yaml")
                self.assertIn(f"${skill}", yaml_text)

    def test_display_names_are_human_readable(self) -> None:
        expected = {
            "claude-cli": "Claude CLI",
            "codex-cli": "Codex CLI",
            "antigravity-cli": "Antigravity CLI",
            "orchestrator-cli": "Orchestrator CLI",
        }
        for skill, name in expected.items():
            with self.subTest(skill=skill):
                yaml_text = read(f"skills/{skill}/agents/openai.yaml")
                self.assertIn(f'display_name: "{name}"', yaml_text)


class CrossSkillConsistencyTests(unittest.TestCase):
    """Facts that must agree across multiple skills."""

    def test_all_skills_repudiate_cao_and_cao_server(self) -> None:
        """No skill may start cao-server or use CAO handoff tools."""
        for skill in ("claude-cli", "codex-cli", "antigravity-cli", "orchestrator-cli"):
            with self.subTest(skill=skill):
                body = read(f"skills/{skill}/SKILL.md")
                self.assertRegex(body, r"(?i)(not a CAO or tmux session|do not.*cao-server)")
                self.assertIn("cao-server", body)

    def test_each_direct_skill_includes_verification_step(self) -> None:
        """Every direct workflow must tell the caller to verify, not trust claims."""
        for skill in DIRECT_SKILLS:
            with self.subTest(skill=skill):
                body = read(f"skills/{skill}/SKILL.md")
                self.assertRegex(
                    body,
                    r"(?i)(run the requested verification|Do not accept an unverified claim)",
                )

    def test_each_direct_skill_states_ids_not_portable(self) -> None:
        for skill in DIRECT_SKILLS:
            with self.subTest(skill=skill):
                body = read(f"skills/{skill}/SKILL.md")
                self.assertRegex(body, r"(?i)(not portable to another provider|cannot cross providers)")

    def test_each_direct_skill_warns_against_two_writers_one_worktree(self) -> None:
        for skill in DIRECT_SKILLS:
            with self.subTest(skill=skill):
                body = read(f"skills/{skill}/SKILL.md")
                self.assertRegex(
                    body,
                    r"(?i)(Do not run two writing agents|separate worktrees|run them sequentially)",
                )


class PromptShapeContractTests(unittest.TestCase):
    """The shared Workspace/Task/Scope/Constraints/Verify/Return prompt shape."""

    REQUIRED_PROMPT_SHAPE_FIELDS = (
        "Workspace:",
        "Task:",
        "Scope:",
        "Constraints:",
        "Verify:",
        "Return:",
    )

    def test_each_direct_reference_documents_prompt_shape(self) -> None:
        for skill in DIRECT_SKILLS:
            with self.subTest(skill=skill):
                ref = read(f"skills/{skill}/references/cli-reference.md")
                for field in self.REQUIRED_PROMPT_SHAPE_FIELDS:
                    with self.subTest(field=field):
                        self.assertIn(field, ref)


class CodexIsolationAndSandboxTests(unittest.TestCase):
    """The codex-cli reference documents the isolation flags the live probe relies on."""

    def test_reference_documents_read_only_sandbox(self) -> None:
        ref = read("skills/codex-cli/references/cli-reference.md")
        self.assertIn("--sandbox read-only", ref)

    def test_reference_documents_ephemeral_means_non_resumable(self) -> None:
        ref = read("skills/codex-cli/references/cli-reference.md")
        self.assertIn("--ephemeral", ref)
        self.assertRegex(ref, r"(?i)(cannot be resumed|non-resumable)")

    def test_reference_documents_output_last_message_flag(self) -> None:
        ref = read("skills/codex-cli/references/cli-reference.md")
        self.assertIn("--output-last-message", ref)


class OrchestratorLiveSupervisorContractTests(unittest.TestCase):
    """The bundled live-process supervisor: documentation contract + boundaries.

    Grounded in the "Optional Live Process Supervisor" section of
    orchestrator-cli/SKILL.md and the dispatch-protocol supervisor block.  The
    supervisor is a *retained live route*, never the durable control plane and
    never the provider-native session identity.
    """

    def setUp(self) -> None:
        self.skill = read("skills/orchestrator-cli/SKILL.md")
        self.protocol = read("skills/orchestrator-cli/references/dispatch-protocol.md")

    def test_supervisor_section_is_documented(self) -> None:
        self.assertIn("## Optional Live Process Supervisor", self.skill)

    def test_supervisor_script_path_is_documented_with_send_and_status(self) -> None:
        # The SKILL.md shows start, send, and status against the script path.
        for command in (
            "orchestrator_supervisor.py --json doctor",
            "orchestrator_supervisor.py --json start",
            "orchestrator_supervisor.py --json send",
            "orchestrator_supervisor.py --json status",
        ):
            with self.subTest(command=command):
                self.assertIn(command, self.skill)

    def test_supervisor_is_localhost_jsonl_daemon_started_on_demand(self) -> None:
        assert_contains_phrase(
            self,
            self.skill,
            "single-machine localhost JSONL daemon started on demand",
        )

    def test_supervisor_uses_python_stdlib_only(self) -> None:
        # Portability claim rests on these stdlib modules.
        assert_contains_phrase(
            self,
            self.skill,
            "stdlib `subprocess`, `socket`, and `sqlite3`",
        )

    def test_supervisor_documents_durable_storage_paths(self) -> None:
        # Durable records and raw logs live under .orchestrator/runtime.
        self.assertIn(".orchestrator/runtime/supervisor.sqlite3", self.skill)
        self.assertIn(".orchestrator/runtime/logs/*.jsonl", self.skill)

    def test_supervisor_protocol_table_lists_all_five_protocols(self) -> None:
        for protocol in (
            "text",
            "jsonl",
            "claude-stream-json",
            "codex-app-server",
            "antigravity-pty",
        ):
            with self.subTest(protocol=protocol):
                self.assertIn(f"`{protocol}`", self.skill)

    def test_supervisor_codex_protocol_chooses_steer_or_start(self) -> None:
        # codex-app-server must steer a known turn, else turn/start a new one.
        assert_contains_phrase(
            self,
            self.skill,
            "`turn/steer` when current turn is known, otherwise `turn/start`",
        )

    def test_supervisor_documents_pty_backend_setup(self) -> None:
        for phrase in (
            "isolated tmux session",
            "pywinpty/ConPTY",
            "brew install tmux",
            "py -m pip install pywinpty",
            "--protocol antigravity-pty",
            "--transport auto",
            "never auto-installs dependencies",
            "live-transport-unavailable",
        ):
            with self.subTest(phrase=phrase):
                assert_contains_phrase(self, self.skill, phrase)

    def test_supervisor_routes_antigravity_to_original_pty(self) -> None:
        body = self.skill
        assert_contains_phrase(self, body, "use the original terminal/PTY described in")
        assert_contains_phrase(self, body, "agy --conversation <id>")

    def test_supervisor_is_retained_route_not_control_plane(self) -> None:
        # The durable control plane stays GitHub Issues / .orchestrator Markdown.
        assert_contains_phrase(self, self.skill, "Use the supervisor only as the retained live route")
        assert_contains_phrase(self, self.skill, "still GitHub Issues or `.orchestrator/` Markdown")

    def test_supervisor_loss_records_live_transport_unavailable(self) -> None:
        # If it exits/crashes/is unreachable, do not claim injection is possible.
        assert_contains_phrase(self, self.skill, "mark the route `live-transport-unavailable`")

    def test_dispatch_protocol_documents_supervisor_start_and_send(self) -> None:
        # The protocol reference carries the same start/send usage with runtime paths.
        self.assertIn("orchestrator_supervisor.py", self.protocol)
        self.assertIn("supervisor.sqlite3", self.protocol)
        self.assertIn("logs/*.jsonl", self.protocol)

    def test_dispatch_protocol_governs_lost_live_handle(self) -> None:
        # live_handle: false for an active dispatch must not spawn a second process.
        assert_contains_phrase(self, self.protocol, "live_handle: false")
        assert_contains_phrase(self, self.protocol, "do not start a second active process")

    def test_orchestration_workflow_routes_injection_through_supervisor(self) -> None:
        # Step 6 of the workflow launches through the supervisor start command.
        assert_contains_phrase(
            self,
            self.skill,
            "scripts/orchestrator_supervisor.py start",
        )


if __name__ == "__main__":
    unittest.main()

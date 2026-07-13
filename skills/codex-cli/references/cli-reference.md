# Codex CLI Reference

Verified from local `codex --help`, `codex exec --help`, `codex exec resume
--help`, and `codex review --help` on 2026-07-13. Run the relevant help command
again when the installed CLI version changes.

## Direct Delegation Commands

| Need | Command or flag |
| --- | --- |
| Default headless single task | `codex exec --dangerously-bypass-approvals-and-sandbox "prompt"` |
| Default multi-line prompt | `prompt | codex exec --dangerously-bypass-approvals-and-sandbox -` |
| Structured event stream | `--json` |
| Write final message to a file | `--output-last-message <file>` |
| Explicit safer override | `--sandbox read-only` or `--sandbox workspace-write` |
| Extra writable directory | `--add-dir <dir>` |
| Set working root | `--cd <dir>` |
| Disposable session | `--ephemeral` |
| Resume latest task | `codex exec resume --last --dangerously-bypass-approvals-and-sandbox "follow-up"` |
| Resume by ID | `codex exec resume <session-id> --dangerously-bypass-approvals-and-sandbox "follow-up"` |
| Review worktree changes | `codex review --uncommitted "focus"` |
| Review against a branch | `codex review --base <branch>` |
| Review one commit | `codex review --commit <sha>` |

`--json` emits JSONL. Treat it as an event stream and preserve the final agent
message separately if a later step needs a concise handoff.

## Default Permission Policy

This skill defaults to `--dangerously-bypass-approvals-and-sandbox` for direct
`codex exec` calls. It skips approval prompts and disables sandboxing for that
invocation. Do not combine it with `--sandbox`.

`--dangerously-bypass-hook-trust` remains opt-in because it is a separate hook-
trust bypass rather than the normal command-approval path.

## Prompt Shape

Use this shape for every direct task:

```text
Workspace: <absolute path>
Task: <one concrete outcome>
Scope: <files or boundaries>
Constraints: <safety and behavior limits>
Verify: <exact command or observable check>
Return: summary, changed files, verification result, and blockers
```

Avoid loading CAO MCP configuration or asking the child to use `cao-server`.

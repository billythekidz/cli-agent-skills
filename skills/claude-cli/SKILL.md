---
name: claude-cli
description: "Delegate a bounded coding, analysis, review, or implementation task directly to the local Claude Code CLI (`claude`) without CAO or cao-server. Choose efficient print, structured-stream, review, or live JSONL delivery, preserve and resume an exact native session ID, and verify the result."
---

# Direct Claude CLI Delegation

Use the local `claude` CLI to hand one bounded task to Claude Code. A session in
this skill is a Claude-native conversation, not a CAO or tmux session. Do not
start `cao-server`, call `cao`, configure `cao-mcp-server`, or use CAO handoff
tools.

## Workflow

1. Confirm the local CLI and authentication state before delegating:

```powershell
Get-Command claude -ErrorAction Stop
claude --help
claude auth status
```

2. Give the child agent a complete, single-task prompt. Include the workspace,
expected outcome, allowed files, constraints, and a verification command. Ask it
to inspect first when the task is ambiguous or risky.

3. Use `--print` with the configured permission bypass for every direct
   delegation. Use `--output-format json` for one machine-readable result and
   add `--json-schema <schema>` when the caller needs validated structured
   output. Do not add `--no-session-persistence` when the task may need a
   follow-up:

```powershell
$prompt = @'
Work in D:\path\to\repo. Inspect the failing test, make the smallest in-scope
fix, run the named test, and report changed files plus the verification result.
'@

claude -p $prompt --output-format json --dangerously-skip-permissions
```

4. Capture the native session ID reported by the CLI. Keep it with the provider,
absolute workspace/worktree, selected agent/model, and a short task summary in
the caller's task state or handoff. When this skill runs under
`orchestrator-cli`, record it in that task's dispatch and handoff records. Do not
add a state file to the target repository unless the user asks.

5. Read the result, inspect the diff, and run the requested verification yourself.
Do not accept an unverified claim that tests passed.

## Default Automation And Coordination

- This skill defaults to `--dangerously-skip-permissions`. It auto-approves all
  Claude Code permission checks for the invocation.
- Use `--permission-mode plan` or `--permission-mode acceptEdits` only when a
  task explicitly requests a safer override. Do not combine either mode with
  `--dangerously-skip-permissions`.
- Prefer `--add-dir <path>` for an explicitly shared workspace boundary, and
  use `--max-budget-usd <amount>` when the task needs a hard print-mode spend
  cap.
- Use `--fallback-model <model>` only for print-mode availability fallback. Load
  `--mcp-config`, `--chrome`, or other external integrations only when the task
  actually needs them.
- `-p/--print` with `json` or `stream-json` uses ordinary stdin/stdout/stderr
  pipes; it does not need a PTY. Reserve a console/PTY for the human
  interactive `claude [prompt]` UI.
- Do not run two writing agents against the same worktree. Use separate
  worktrees or run them sequentially.
- If Claude Code reports a nested-session problem, use a separate shell or a
  different client; do not weaken safeguards just to launch the child session.

## Live Process Prompt Delivery

Use this only when one still-running Claude process must receive more than one
prompt. `claude -p` becomes a JSONL input stream when paired with
`--input-format stream-json` and `--output-format stream-json`:

```text
claude -p --input-format stream-json --output-format stream-json \
  --no-session-persistence --tools "" --permission-mode plan --safe-mode
{"type":"user","message":{"role":"user","content":"first prompt"}}
{"type":"user","message":{"role":"user","content":"follow-up"}}
```

- Keep the original process and its stdin open. Send UTF-8 JSON lines without a
  BOM; its initial `system/init` event provides the native session ID.
- Keep stdin, stdout, and stderr as ordinary pipes. A PTY is not required for
  this stream-json process and can make JSONL capture less reliable.
- Treat each `result` event as the completed-turn boundary. Use one writer and
  a FIFO; do not assume a later input interrupts or runs in parallel with an
  in-flight turn.
- The command above is a harmless probe. Remove `--no-session-persistence`
  only when the same native session must also survive process exit for a later
  resume.
- Record the process handle, `stdin JSONL` transport, and current turn state
  beside the native session ID. A process handle is transport, not identity.

## Native Session Consistency

- Keep one native Claude session per direct agent task. Native session IDs are
  not portable to another provider.
- Before resuming, confirm that the recorded provider and workspace/worktree
  still match the follow-up.
- Resume by the exact recorded ID. Use `-c` only when the user explicitly
  requests the unique latest local session and no concurrent session can be
  selected by mistake.
- Do not add `--fork-session` when the requirement is to continue the same
  native conversation.
- If the ID is missing or the resume fails, start a new Claude session with a
  factual handoff summary. Identify it as a new session; do not imply that it
  retained the old conversation.

## Continuation

Use `claude -r <session-id> -p "follow-up" --dangerously-skip-permissions` for
the recorded session only after the original process has ended or is
unreachable. Preserve the same workspace and restate any changed success
criteria. While the original stream process is alive, inject the follow-up into
its stdin instead of starting `-r`.

See [references/cli-reference.md](references/cli-reference.md) for the local
help-derived command reference and prompt template.

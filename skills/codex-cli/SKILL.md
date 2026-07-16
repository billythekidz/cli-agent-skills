---
name: codex-cli
description: "Delegate a bounded coding, analysis, review, or implementation task directly to the local Codex CLI (`codex`) without CAO or cao-server. Use to run Codex headlessly, steer or queue prompts in an active native process, preserve and resume an exact native session ID, review changes, and verify the result."
---

# Direct Codex CLI Delegation

Use the local `codex` CLI to hand one bounded task to Codex. A session in this
skill is a Codex-native conversation, not a CAO or tmux session. Do not start
`cao-server`, call `cao`, configure `cao-mcp-server`, or use CAO handoff tools.

## Workflow

1. Confirm the local CLI before delegating:

```powershell
Get-Command codex -ErrorAction Stop
codex exec --help
```

2. Give the child agent a complete, single-task prompt. Include the workspace,
expected outcome, allowed files, constraints, and a verification command. Ask it
to inspect first when the task is ambiguous or risky.

3. Pipe multi-line prompts to `codex exec -` with the configured approval-and-
sandbox bypass for every direct delegation. Omit `--ephemeral` when the task
may need a follow-up because it does not persist a resumable session:

```powershell
$prompt = @'
Work in D:\path\to\repo. Diagnose the first failing test. Touch only the
necessary files, run the named test, and report changed files plus the result.
'@

$prompt | codex exec --dangerously-bypass-approvals-and-sandbox --json -
```

4. Capture the native session ID reported in the JSONL or CLI output. Keep it
with the provider, absolute workspace/worktree, selected profile/model, and a
short task summary in the caller's task state or handoff. When this skill runs
under `orchestrator-cli`, record it in that task's dispatch and handoff records.
Do not add a state file to the target repository unless the user asks.

5. Read the JSONL events, inspect the diff, and run the requested verification
yourself. Do not accept an unverified claim that tests passed.

## Default Automation And Coordination

- This skill defaults to `--dangerously-bypass-approvals-and-sandbox`. It skips
  approval prompts and runs without Codex sandboxing for the invocation.
- Use `--sandbox` only when a task explicitly requests a safer override. Do not
  combine it with `--dangerously-bypass-approvals-and-sandbox`.
- Do not add `--dangerously-bypass-hook-trust` by default. It is a separate
  hook-trust bypass and requires an explicit request.
- Do not run two writing agents against the same worktree. Use separate
  worktrees or run them sequentially.
- Keep all paths inside the intended workspace unless the user explicitly adds
  another writable directory with `--add-dir`.

## Active Process Prompt Delivery

`codex exec` is a one-shot non-interactive run. Its stdin supplies initial
prompt/context only; it is not a prompt stream for an already-running `exec`
process.

For a human-operated persistent session, start `codex` without `exec`. While
Codex is working, press Enter to steer the current turn or Tab to queue a
prompt for the next turn.

For programmatic delivery, `codex app-server` is experimental. Keep one
app-server process and one JSONL writer. Complete `initialize` -> `initialized`
-> `thread/start` -> `turn/start`, then capture the returned `threadId` and
the active `turnId` from the `turn/started` notification. Inject input into
that running turn with `turn/steer`:

```json
{
  "threadId": "<thread-id>",
  "expectedTurnId": "<active-turn-id>",
  "input": [{ "type": "text", "text": "<new prompt>" }]
}
```

Track `turn/started` and `turn/completed`. Serialize writes to the transport;
do not send competing writers or treat it as an unbounded prompt queue. If the
server reports `activeTurnNotSteerable`, or the turn has completed, queue the
prompt in the caller and send it with `turn/start` after completion. The
app-server connection or stdio transport is not native conversation identity;
record the returned `threadId` with the normal task handoff.

## Native Session Consistency

- Keep one native Codex session per direct agent task. Native session IDs are
  not portable to another provider.
- Before resuming, confirm that the recorded provider and workspace/worktree
  still match the follow-up.
- Resume by the exact recorded ID. Use `--last` only when the user explicitly
  requests the unique latest local session and no concurrent session can be
  selected by mistake.
- If the ID is missing or the resume fails, start a new Codex session with a
  factual handoff summary. Identify it as a new session; do not imply that it
  retained the old conversation.

## Review And Continuation

Use `codex review --uncommitted "focus on regressions"` for a direct review of
the current worktree. Continue a saved non-interactive task with
`codex exec resume <session-id> --dangerously-bypass-approvals-and-sandbox "follow-up"`.

See [references/cli-reference.md](references/cli-reference.md) for the local
help-derived command reference and prompt template.

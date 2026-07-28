---
name: antigravity-cli
description: "Delegate a bounded coding, analysis, review, or implementation task directly to the local Google Antigravity CLI (`agy`) without CAO or cao-server. Choose headless text/JSON/stream output or same-process interactive delivery, preserve and resume an exact native conversation ID, and verify the result."
---

# Direct Antigravity CLI Delegation

Use the local `agy` CLI to hand one bounded task to Antigravity. A session in
this skill is an Antigravity-native conversation, not a CAO or tmux session. Do
not start `cao-server`, call `cao`, configure `cao-mcp-server`, or use CAO
handoff tools.

## Workflow

1. Confirm the local CLI before delegating. In Windows PowerShell, resolve the
   application rather than a user-defined `agy` function or wrapper, which can
   silently change permission flags:

```powershell
$agy = Get-Command agy -CommandType Application | Select-Object -First 1 -ExpandProperty Source
if (-not $agy) { throw "agy application was not found on PATH." }
& $agy --help
```

   On macOS or Linux, invoke the `agy` executable directly. Keep the resolved
   Windows application path for every invocation in this workflow; replace
   PowerShell's `& $agy` below with `agy` on macOS or Linux.

2. Give the child agent a complete, single-task prompt. Include the workspace,
expected outcome, allowed files, constraints, and a verification command. Ask it
to inspect first when the task is ambiguous or risky.

3. Use print mode with the configured auto-approval for every direct
delegation:

```powershell
$prompt = @'
Work in D:\path\to\repo. Inspect the failing test, make the smallest in-scope
fix, run the named test, and report changed files plus the verification result.
'@

& $agy -p $prompt --mode accept-edits --dangerously-skip-permissions --print-timeout 15m
```

   For unattended machine-readable output, use `--output-format json` for one
   result or `--output-format stream-json` for events. Add `--json-schema` when
   the result must satisfy a schema; in `stream-json` mode the schema applies
   only to the final result. These print-mode commands use ordinary stdout and
   stderr pipes and do not need a PTY:

```powershell
$schemaPath = 'D:\path\to\result.schema.json'
& $agy --print $prompt --output-format stream-json --json-schema $schemaPath `
  --mode accept-edits --dangerously-skip-permissions --print-timeout 15m
```

4. Capture the native conversation ID. Current `agy -p` versions can finish
without printing it. After the process exits, its native cache at
`~/.gemini/antigravity-cli/cache/last_conversations.json` maps absolute
workspaces to conversation UUIDs; match the exact resolved workspace key,
validate the UUID, and record it. This is provider-native state, not an
orchestration session. Do not edit or delete this cache. Keep the ID with the
provider, absolute workspace/worktree, selected agent/model, and a short task
summary in the caller's task state or handoff. When this skill runs under
`orchestrator-cli`, record it in that task's dispatch and handoff records. Do not
add a state file to the target repository unless the user asks.

5. Read the response, inspect the diff, and run the requested verification
yourself. Do not accept an unverified claim that tests passed.

## Default Automation And Coordination

- This skill defaults to `--dangerously-skip-permissions`. It auto-approves all
  Antigravity tool permission requests for the invocation.
- Use `--sandbox` or `--mode plan` only when a task explicitly requests a safer
  override.
- Prefer `--add-dir <path>` for an explicit workspace boundary and
  `--print-timeout <duration>` for bounded automation. Use `--effort`,
  `--model`, or `--agent` only when the caller has selected a valid local value.
- Do not run two writing agents against the same worktree. Use separate
  worktrees or run them sequentially.
- Pass `--agent <name>` only after checking the locally available names with
  `agy agent` or `agy agents`.

## Live Process Prompt Delivery

Use a live interactive process when a follow-up must enter the same running
conversation, rather than recover it after process exit. On Windows PowerShell:

```powershell
& $agy --sandbox -i "initial prompt"
```

- If a human launches `-i` from an attached PowerShell console, that inherited
  console is enough. If an external supervisor must drive the interactive UI,
  retain that original terminal/PTY and process handle; send each follow-up to
  the same PTY followed by carriage return (`CR`, the Enter key). Antigravity's
  interactive UI can queue prompt lines while a turn is running.
- Do not allocate a PTY for `-p/--print`, `--output-format json`, or
  `--output-format stream-json`; capture stdout and stderr as pipes instead.
- Serialize writes through one owner. Do not assume an injected line interrupts
  the current turn, and wait for the transcript or completed turn when order
  matters.
- `agy -p` is one-shot. There is no external `agy send <process>` command for
  injecting into a live process. `--conversation <id>` creates a new process to
  resume a conversation; it is not a route into the still-running process.
- The process handle and PTY are operational transport, not native session identity.
  If the original PTY is lost, do not claim live injection succeeded;
  wait for or deliberately stop the process, then use native recovery.

## Native Session Consistency

- Keep one native Antigravity conversation per direct agent task. Conversation
  IDs are not portable to another provider.
- Before resuming, confirm that the recorded provider and workspace/worktree
  still match the follow-up.
- While the original interactive process and PTY are available, deliver the
  follow-up through that live transport instead of starting `--conversation`.
- Resume by the exact recorded conversation ID. Use `-c` only when the user
  explicitly requests the unique latest local conversation and no concurrent
  conversation can be selected by mistake.
- If the CLI does not yield a stable conversation ID, or the resume fails,
  start a new conversation with a factual handoff summary. Identify it as new;
  do not imply that it retained the old conversation.

## Continuation And Diagnostics

Only after the original process has stopped or its PTY is unavailable, use
`& $agy --conversation <id> -p "follow-up" --mode accept-edits --dangerously-skip-permissions`
for the recorded conversation. For a stuck headless run, add `--log-file <path>`
and a deliberate `--print-timeout <duration>`.

See [references/cli-reference.md](references/cli-reference.md) for the local
help-derived command reference and prompt template.

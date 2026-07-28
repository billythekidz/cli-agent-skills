# Antigravity CLI Reference

Verified from the local `agy -h` help capture, `agy help`, and `agy agent
--help` on 2026-07-28. Run the relevant help command again when the installed
CLI version changes.

## Direct Delegation Commands

| Need | Command or flag |
| --- | --- |
| Inspect the installed CLI | `agy --help` or `agy -h` |
| Interactive session | `agy` |
| Initial interactive prompt | `agy -i "prompt"` |
| Live multi-prompt interactive process (Windows) | `& $agy --sandbox -i "initial prompt"` |
| Default headless single task | `agy -p "prompt" --mode accept-edits --dangerously-skip-permissions` |
| Structured single result | `agy -p "prompt" --output-format json ...` |
| Streamed events and final result | `agy -p "prompt" --output-format stream-json ...` |
| Constrain the final structured result | `--json-schema <schema string or path>` |
| Resume exact conversation after process exit | `agy --conversation <id> -p "follow-up" --mode accept-edits --dangerously-skip-permissions` |
| Continue latest conversation (exception only) | `agy -c -p "follow-up" --mode accept-edits --dangerously-skip-permissions` |
| Start a new project context | `--new-project` or `--project <id>` |
| Explicit safer override | `--mode plan` or `--sandbox` |
| Select reasoning effort | `--effort low|medium|high` |
| Choose an agent | `--agent <name>` |
| Choose a model | `--model <model>` |
| Add a workspace | `--add-dir <path>` |
| Restrict terminal execution | `--sandbox` |
| Bound print-mode wait | `--print-timeout <duration>` |
| Save diagnostics | `--log-file <path>` |

Root help also lists `agent`, `agents`, `changelog`, `install`, `models`,
`plugin`, `plugins`, and `update` subcommands. Prefer `agy plugin help` for
plugin command discovery; some plugin subcommands interpret `--help` as an
argument.

## Command Selection

| Requirement | Preferred command | Why |
| --- | --- | --- |
| One bounded worker task | `agy -p ... --output-format json` | One machine-readable result and ordinary pipe capture |
| Progress/events plus final result | `agy -p ... --output-format stream-json` | Stream events without a PTY |
| Schema-constrained final result | `agy -p ... --output-format stream-json --json-schema <schema>` | Structured final-result contract |
| Same-process interactive follow-up | `agy -i ...` | The native UI can queue input in the original console |
| Post-exit exact continuation | `agy --conversation <id> -p ...` | Creates a new process with the recorded native conversation |
| Agent/model discovery | `agy agent` / `agy models` | Confirm local names before passing `--agent` or `--model` |
| Plugin or installation lifecycle | `agy plugin ...` / `agy install` | Explicit environment operations, not worker delegation |

## Print Mode And Structured Output

`-p/--print` runs one prompt and exits. Use ordinary stdout/stderr pipes; no PTY
is required for these forms:

```powershell
& $agy --print $prompt --output-format json `
  --mode accept-edits --dangerously-skip-permissions --print-timeout 15m

& $agy --print $prompt --output-format stream-json `
  --json-schema 'D:\path\to\result.schema.json' `
  --mode accept-edits --dangerously-skip-permissions --print-timeout 15m
```

Use `json` when the caller needs one machine-readable result and
`stream-json` when it needs progress/events. `--json-schema` accepts a schema
string or a schema-file path. For `stream-json`, it validates/enforces only the
final result, not every event. Print mode is one-shot: it cannot accept a later
prompt through the same process. After exit, recover with the exact
`--conversation <id>` command if a follow-up is needed.

## Live Interactive Prompt Delivery

On Windows PowerShell, resolve the actual application before starting a live
interactive process. A PowerShell function named `agy` can add or change flags.

```powershell
$agy = Get-Command agy -CommandType Application | Select-Object -First 1 -ExpandProperty Source
if (-not $agy) { throw "agy application was not found on PATH." }
& $agy --sandbox -i "initial prompt"
```

When a human starts this from an attached PowerShell console, the inherited
console is sufficient. An external supervisor needs a PTY only when it must
drive that interactive console programmatically.

Keep that original process and terminal/PTY alive. Write each follow-up prompt
to the same PTY and terminate it with carriage return (`CR`, the Enter key).
The interactive UI can queue prompt lines while a turn is running. Serialize
writes through one owner and wait for turn completion whenever ordering matters;
do not assume a line interrupts the active turn.

`agy -p` is a one-shot process. There is no external `agy send <process>`
command for a live prompt injection. `agy --conversation <id>` resumes through
a new process and is only for recovery after the original process stopped or
its PTY became unavailable. The original process/PTY is a transport route, not native conversation identity; retain the native ID for recovery.

### In-flight PTY recipe

Use a task whose first response takes long enough to observe the queue, then
type the second message into that same terminal while the first turn is still
running. Both lines are terminal input, not two `agy` processes.

```text
terminal $ agy --sandbox -i "Begin a long explanation, then reply FIRST-PTY-MARKER."
... Antigravity is working ...
terminal input> Stop the long explanation and reply SECOND-PTY-MARKER instead.
<press Enter in the same terminal>
... UI shows the follow-up queued ...
assistant> SECOND-PTY-MARKER
```

Do not automate this by piping to `agy -p`, and do not substitute
`agy --conversation <id>`: both create a different process. A PTY automation
adapter is required for unattended testing; on Windows a broken `winpty`
installation is a test-environment failure, not evidence that the CLI lacks
interactive queueing.

## Native Session Continuity

Record the native conversation ID with its provider, absolute
workspace/worktree, selected agent/model, and a short task summary. In current
headless `agy -p` versions, the ID may not appear in stdout. After the process
exits, `~/.gemini/antigravity-cli/cache/last_conversations.json` maps absolute
workspaces to conversation UUIDs. Match the exact resolved workspace key,
validate the UUID, and record it. This is provider-native state, not an
orchestration session; do not edit or delete this cache. Resume with that exact
ID after verifying the recorded workspace still matches.

`-c` is only safe when the original process is no longer usable, the user
explicitly asks for the unique latest local conversation, and no concurrent task
can be selected. If the CLI does not yield a stable conversation ID, or resume
fails, begin a new task with a factual handoff summary and label it as new.

## Default Permission Policy

This skill defaults to `--dangerously-skip-permissions` for every direct task.
It auto-approves Antigravity tool permission requests for that invocation. Use
`--sandbox` or `--mode plan` only when a task explicitly requests a safer
override.

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

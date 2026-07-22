# Antigravity CLI Reference

Verified from local `agy --help`, `agy help`, and `agy agent --help` on
2026-07-14. The model list (including `gemini-3.6-flash-high` and
`claude-sonnet-4-6`) was re-verified against `agy models` on 2026-07-22. Run the
relevant help command again when the installed CLI version changes.

## Direct Delegation Commands

| Need | Command or flag |
| --- | --- |
| Interactive session | `agy` |
| Initial interactive prompt | `agy -i "prompt"` |
| Live multi-prompt interactive process (Windows) | `& $agy --sandbox -i "initial prompt"` |
| Default headless single task | `agy -p "prompt" --mode accept-edits --dangerously-skip-permissions` |
| Resume exact conversation after process exit | `agy --conversation <id> -p "follow-up" --mode accept-edits --dangerously-skip-permissions` |
| Continue latest conversation (exception only) | `agy -c -p "follow-up" --mode accept-edits --dangerously-skip-permissions` |
| Explicit safer override | `--mode plan` or `--sandbox` |
| Choose an agent | `--agent <name>` |
| Choose a model | `--model <model>` (list available IDs with `agy models`) |
| Default developer model | `--model gemini-3.6-flash-high` |
| Default reviewer model | `--model claude-sonnet-4-6` |
| Add a workspace | `--add-dir <path>` |
| Restrict terminal execution | `--sandbox` |
| Bound print-mode wait | `--print-timeout <duration>` |
| Save diagnostics | `--log-file <path>` |

Root help also lists `agent`, `agents`, `changelog`, `install`, `models`,
`plugin`, and `update` subcommands. Prefer `agy plugin help` for plugin command
discovery; some plugin subcommands interpret `--help` as an argument.

## Live Interactive Prompt Delivery

On Windows PowerShell, resolve the actual application before starting a live
interactive process. A PowerShell function named `agy` can add or change flags.

```powershell
$agy = Get-Command agy -CommandType Application | Select-Object -First 1 -ExpandProperty Source
if (-not $agy) { throw "agy application was not found on PATH." }
& $agy --sandbox -i "initial prompt"
```

### Managed PTY prerequisites

For unattended same-process follow-ups, `orchestrator-cli` uses
`antigravity-pty`. On macOS it creates an isolated tmux session around the
interactive process. On Windows it uses the optional pywinpty package, backed
by the available Windows PTY implementation (ConPTY/WinPTY).

Run the supervisor doctor first:

```bash
python <orchestrator-cli-skill-dir>/scripts/orchestrator_supervisor.py --json doctor
```

If tmux is missing on macOS, install it manually:

```bash
brew install tmux
tmux -V
```

If the Windows backend is missing, install pywinpty from PowerShell:

```powershell
py -m pip install pywinpty
```

The skill never auto-installs host dependencies. Re-run `doctor` after either
installation. If the dependency is unavailable, report
`live-transport-unavailable`; do not substitute `agy --conversation <id>` while
the original process is still running.

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
adapter is required for unattended testing; a broken tmux or pywinpty
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

## Startup Timeout Recovery

After 300 seconds without a usable prompt, stop the process and preserve
`--log-file` output. Do not assume a `Loading` MCP server will be ignored. Run
the [fresh-start-without-integrations](../../orchestrator-cli/references/fresh-start-without-integrations.md)
procedure with a temporary `--gemini_dir`, an empty `mcp_config.json`, and no
workspace-local `.agents/mcp_config.json` or imported plugins. A successful
probe is a new native session, not a continuation of the timed-out process.

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

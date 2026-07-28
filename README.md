# Direct CLI Skills

Portable Agent Skills for delegating bounded tasks directly to local Claude
Code, Codex CLI, and Google Antigravity CLI processes. These skills do not
require `cao-server` or CAO MCP handoff tools.

## Included Skills

- [claude-cli](skills/claude-cli/SKILL.md) delegates with `claude`.
- [codex-cli](skills/codex-cli/SKILL.md) delegates with `codex exec`.
- [antigravity-cli](skills/antigravity-cli/SKILL.md) delegates with `agy`.
- [orchestrator-cli](skills/orchestrator-cli/SKILL.md) coordinates GitHub
  Issues or offline Markdown records and direct CLI workers with CAO-inspired
  assign/handoff semantics, preferring headless workers without starting
  `cao-server`.

## Install With `npx skills`

Prerequisite: Node.js with `npx` available.

Project scope is the default. It keeps the skills in the current project and
shares them with the selected clients:

```bash
npx skills add billythekidz/cli-agent-skills --skill claude-cli --skill codex-cli --skill antigravity-cli --skill orchestrator-cli --agent claude-code --agent codex --agent antigravity-cli --yes
```

Use `--global` to make them available across projects:

```bash
npx skills add billythekidz/cli-agent-skills --skill claude-cli --skill codex-cli --skill antigravity-cli --skill orchestrator-cli --agent claude-code --agent codex --agent antigravity-cli --global --yes
```

Install only one skill by keeping the matching `--skill` argument and removing
the others. Use `npx skills list` to inspect installed skills.

## Local Checkout

From a clone of this repository, use `.` instead of the GitHub source:

```bash
npx skills add . --skill claude-cli --skill codex-cli --skill antigravity-cli --skill orchestrator-cli --agent claude-code --agent codex --agent antigravity-cli --yes
```

## Tests

Run the fast documentation contract suite without credentials, a network
connection, or provider calls:

```powershell
python -m unittest tests.test_skill_contract tests.test_orchestrator_supervisor -v
```

`tests.test_orchestrator_supervisor` starts the bundled lightweight supervisor
with a fixture worker, sends two prompts into the same retained process, and
checks the shared PID, live handle, native-session capture, and JSONL log. It
uses only Python stdlib and runs on Windows, macOS, and Linux.

The macOS PTY integration test exercises the real tmux backend with an isolated
socket and one retained pane (no provider quota):

```bash
python3 -m unittest tests.test_antigravity_pty -v
```

For Antigravity live routing, run supervisor `doctor` first. Install tmux with
`brew install tmux` on macOS or pywinpty with `py -m pip install pywinpty` in
Windows PowerShell when the doctor reports the backend missing. Tests and the
supervisor never auto-install these optional dependencies.

Run the default suite on the current macOS. It includes local CLI
`--version`/`--help` smoke checks, but does not send provider prompts:

```bash
python3 -m unittest discover -s tests -p 'test_*.py' -v
```

The offline suite also includes two focused contract/edge-case modules that
do not invoke any CLI:

```bash
# Real contract checks grounded in each skill's actual commands, flags,
# permission defaults, dispatch protocol, and model routing.
python3 -m unittest tests.test_skill_fidelity -v

# Deeper macOS/Unix edge cases: APFS Unicode (NFC/NFD) decomposition,
# /private/var symlink resolution, JsonlProcess send/wait/close internals,
# provider/timeout parsing corner cases, and parser failure modes.
python3 -m unittest tests.test_macos_deep -v
```

Run the real provider workflow suite. It creates temporary Git workspaces,
asks each selected CLI to make one bounded change, captures the real native
session ID, and resumes that exact ID. The orchestrator case uses local
`.orchestrator/` Markdown records and dispatches a real worker; it does not
write GitHub issues:

```bash
RUN_REAL_CLI_SKILL_TESTS=1 \
REAL_CLI_SKILL_PROVIDERS=claude,codex,antigravity,orchestrator \
python3 -m unittest tests.test_real_cli_workflows -v
```

Enable the optional Antigravity same-process PTY probe separately:

```bash
RUN_REAL_CLI_SKILL_TESTS=1 \
RUN_REAL_CLI_ANTIGRAVITY_PTY=1 \
REAL_CLI_SKILL_PROVIDERS=antigravity \
python3 -m unittest tests.test_real_cli_workflows -v
```

These real workflow tests require authenticated local CLIs, consume provider
quota, persist normal native session history/cache, and are intentionally
never run by default.

Run the real `orchestrator-cli` supervisor against Claude stream-JSON and Codex
app-server transports. This verifies the retained PID/live handle, native
session capture, current turn, JSONL log, and same-process follow-up route:

```bash
RUN_REAL_CLI_SKILL_TESTS=1 \
RUN_REAL_ORCHESTRATOR_SUPERVISOR_TESTS=1 \
REAL_CLI_SKILL_PROVIDERS=claude,codex,orchestrator \
python3 -m unittest tests.test_real_cli_workflows -v
```

Enable the real Antigravity supervisor PTY probe separately on macOS. It starts
`agy` inside the isolated tmux backend, sends a follow-up through the supervisor,
and verifies the same pane/PID and tmux metadata:

```bash
RUN_REAL_CLI_SKILL_TESTS=1 \
RUN_REAL_ORCHESTRATOR_SUPERVISOR_TESTS=1 \
RUN_REAL_ORCHESTRATOR_ANTIGRAVITY_PTY=1 \
REAL_CLI_ANTIGRAVITY_PTY_TIMEOUT_SECONDS=300 \
REAL_CLI_SKILL_PROVIDERS=antigravity,orchestrator \
python3 -m unittest tests.test_real_cli_workflows -v
```

This opt-in probe requires an authenticated `agy` installation and consumes
provider quota. On Windows, install `pywinpty` first; the macOS integration
test remains the no-quota coverage for the tmux backend. The probe gives `agy`
an isolated temporary `--gemini_dir` and `--log-file`, so unrelated user MCP
servers (for example a slow `unity-mcp`) cannot delay the model request. The
PTY timeout defaults to 300 seconds and can be increased with
`REAL_CLI_ANTIGRAVITY_PTY_TIMEOUT_SECONDS`; a timeout failure includes the
provider log tail for diagnosis.

### Fresh startup recovery without MCP or plugins

If any provider has not reached its first usable prompt after 300 seconds,
record the timeout and stop that process. Do not retry the same native session.
Use [fresh-start-without-integrations](skills/orchestrator-cli/references/fresh-start-without-integrations.md)
to launch a disposable probe:

- Antigravity: temporary `--gemini_dir` with an empty `mcp_config.json` and no
  workspace-local `.agents/mcp_config.json` or imported plugins.
- Claude: `--bare --strict-mcp-config --mcp-config <empty-json>`.
- Codex: disposable `CODEX_HOME --ignore-user-config --ephemeral`; copy only
  the auth file when preserving the existing login is necessary.

If the fresh probe succeeds, classify the original failure as
`startup-blocked-by-integrations` and create a new dispatch/native session with
a factual handoff. If it fails too, investigate authentication, network,
binary/runtime, or PTY health instead of increasing retries indefinitely.

Run the real fresh-start probes explicitly; they use the authenticated local
CLIs, consume quota, and default to a 300-second provider budget:

```bash
RUN_REAL_CLI_SKILL_TESTS=1 \
RUN_REAL_CLI_FRESH_START_TESTS=1 \
REAL_CLI_FRESH_START_TIMEOUT_SECONDS=300 \
REAL_CLI_SKILL_PROVIDERS=claude,codex,antigravity \
python3 -m unittest \
  tests.test_real_cli_workflows.RealCliWorkflowTests.test_real_fresh_probe_claude_without_mcp_or_plugins \
  tests.test_real_cli_workflows.RealCliWorkflowTests.test_real_fresh_probe_codex_without_mcp_or_plugins \
  tests.test_real_cli_workflows.RealCliWorkflowTests.test_real_fresh_probe_antigravity_without_mcp_or_plugins -v
```

To reproduce the blocked-MCP-to-fresh recovery path for Antigravity, also set
`RUN_REAL_CLI_FRESH_START_FAILURE_TESTS=1`; that case uses a shortened local
failure budget by default and then runs the clean probe.

The native-session probes are deliberately opt-in. They create an isolated
temporary Git repository for each selected CLI, start one harmless conversation,
capture its native ID, resume that exact ID, and require the follow-up to return
a random token from the first turn. They use disabled tools for Claude,
read-only sandboxing plus ignored user config/rules for Codex, and sandboxing
for Antigravity; they do not test the skills' permission-bypass defaults.

```powershell
$env:RUN_LIVE_SKILL_TESTS = "1"
$env:SKILL_TEST_PROVIDERS = "claude,codex,antigravity"
$env:SKILL_TEST_TIMEOUT_SECONDS = "600"
python -m unittest tests.test_native_sessions -v
```

Live probes require each locally authenticated CLI and consume provider quota.
They persist normal provider-native conversation history. The Antigravity probe
also leaves its normal workspace-to-conversation cache entry under
`~/.gemini/antigravity-cli`; it never edits or deletes provider-owned history.
Set `NATIVE_SESSION_CODEX_MODEL` when the local Codex default model is not
available, `NATIVE_SESSION_CLAUDE_BUDGET_USD` to change Claude's per-call cap
(default `1`), or `KEEP_NATIVE_SESSION_ARTIFACTS=1` to retain the temporary
repositories for diagnosis.

### Live-process delivery probes

The preceding suite tests **recovery after a process exits**. It does not prove
that a still-running process accepts another prompt. Run this separate opt-in
suite to keep one process alive, send a second prompt through its original
transport, and verify the same native session/thread retains the first token:

```powershell
$env:RUN_LIVE_ACTIVE_PROCESS_TESTS = "1"
$env:ACTIVE_PROCESS_TEST_PROVIDERS = "claude,codex"
$env:ACTIVE_PROCESS_TEST_TIMEOUT_SECONDS = "180"

# Required for the isolated Codex app-server probe unless CODEX_ACCESS_TOKEN
# is already present. The file is copied only into a temporary CODEX_HOME and
# is removed with that temporary directory; its contents are never printed.
$env:ACTIVE_PROCESS_CODEX_AUTH_FILE = Join-Path $env:USERPROFILE ".codex\auth.json"

python -m unittest tests.test_active_process_sessions -v
```

The Claude probe uses one `stream-json` stdin process and waits for each
`result`. The Codex probe uses one isolated `codex app-server` JSONL process,
one `threadId`, starts a deliberately active turn, then sends a real
`turn/steer` request before that turn completes, verifies that the response
names the same active turn, then interrupts and observes its completion. It
intentionally does not load the normal user `CODEX_HOME`, so it cannot launch
unrelated MCP servers.

Antigravity's same-process follow-up behavior is interactive-console/PTY-only,
so it has a manual provider probe rather than an unattended JSONL test. The
offline suite also verifies the supervisor's tmux/pywinpty transport boundary
with a fixture PTY worker; the provider smoke remains opt-in because it requires
an authenticated interactive `agy` process. On macOS, the managed route is an
isolated tmux pane. On Windows, use the installed pywinpty/ConPTY bridge.
Its headless `-p/--print` `json` and `stream-json` modes are pipe-friendly, but
one-shot and cannot receive a later prompt in that same process. For a direct
manual probe, start the real interactive application and, while its first turn
still shows as running, type a follow-up into that **same** terminal and press
Enter. Do not use `--conversation` for this in-flight test.

```powershell
$agy = Get-Command agy -CommandType Application | Select-Object -First 1 -ExpandProperty Source
& $agy --sandbox -i "Start a long, harmless explanation and wait for my next instruction."
```

The test harness maps a Windows `*.ps1` CLI shim to its sibling `*.cmd` wrapper
when available. On macOS and Linux it invokes the resolved CLI executable
directly.

## Offline Coordination

When GitHub is offline, unavailable, or returns an error, `orchestrator-cli`
uses tracked Markdown records in the target repository's `.orchestrator/`
directory instead of losing plans, tasks, handoffs, or bug reports. It does not
automatically mirror those records to GitHub after connectivity returns.

## Permission Policy

These skills default to unattended permission bypass for direct delegation:

- Claude Code: `--dangerously-skip-permissions`
- Codex CLI: `--dangerously-bypass-approvals-and-sandbox`
- Antigravity CLI: `--dangerously-skip-permissions`

`orchestrator-cli` coordinates these direct workers. It keeps GitHub Issue
writes explicitly scoped to the requested repository and issue numbers. Its
Codex `app-server` live route uses the equivalent config overrides
`-c 'approval_policy="never"' -c 'sandbox_mode="danger-full-access"'` because
the app-server subcommand does not expose the one-shot bypass flag in its own
options.

This allows child agents to edit files and run commands without approval
prompts. Install and use these skills only in workspaces and environments you
trust. Each skill documents the safer override flags when a task needs them.

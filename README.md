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
python -m unittest tests.test_skill_contract -v
```

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
so it has a manual probe rather than an unattended JSONL test. Its headless
`-p/--print` `json` and `stream-json` modes are pipe-friendly, but one-shot and
cannot receive a later prompt in that same process. In one terminal, start the
real interactive application and, while its first turn still shows as running,
type a follow-up into that **same** terminal and press Enter. The UI should
queue it; do not use `--conversation` for this test.

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
writes explicitly scoped to the requested repository and issue numbers.

This allows child agents to edit files and run commands without approval
prompts. Install and use these skills only in workspaces and environments you
trust. Each skill documents the safer override flags when a task needs them.

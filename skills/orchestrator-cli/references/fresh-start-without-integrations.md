# Fresh Start Without MCP Or Plugins

Use this recovery procedure when a provider has not reached its first usable
prompt within the 300-second startup budget. Do not keep retrying the same
process or PTY: record the timeout, preserve the provider log, stop the failed
route, and launch a new native session with integrations isolated.

## Recovery contract

1. Wait at most 300 seconds for the provider to become usable. A provider that
   is still `Loading`, `connecting`, or `Initializing` at that point is a
   startup failure, not a successful dispatch.
2. Capture the supervisor JSONL log, provider log, command, workspace, and
   `doctor` output. Never print auth tokens or copy provider-owned history into
   the repository.
3. Stop the failed process. Do not send another prompt through a route that
   never reached a usable prompt boundary.
4. Run a fresh probe from a temporary configuration and a clean probe
   workspace. The probe must load no MCP server, plugin, hook, or user config
   unless the provider requires a credential file explicitly copied into the
   temporary home.
5. If the fresh probe succeeds, record `startup-blocked-by-integrations`, then
   retry the task with a new dispatch/native session and a factual handoff. Do
   not claim continuity with the timed-out native session.
6. If the fresh probe also fails, classify the issue as authentication,
   network, binary/runtime, or PTY failure and stop for investigation. Do not
   re-enable integrations or increase the timeout indefinitely.

The 300-second budget is a provider startup budget. The supervisor's own
readiness setting can be overridden independently with
`ORCHESTRATOR_SUPERVISOR_CONNECT_TIMEOUT=300` when a cold machine needs it.

## macOS/Linux fresh probes

Create a disposable root and a clean workspace. Do not point these commands at
the user's real `~/.gemini`, `~/.claude`, or `~/.codex` directories:

```bash
fresh_root="$(mktemp -d "${TMPDIR:-/tmp}/cli-fresh.XXXXXX")"
fresh_workspace="$fresh_root/workspace"
mkdir -p "$fresh_workspace"
git -C "$fresh_workspace" init --quiet
trap 'rm -rf "$fresh_root"' EXIT
```

### Antigravity

`agy` does not reliably fail-open when an MCP server remains in `connecting`.
Use a fresh `--gemini_dir` whose MCP profile is empty. The first invocation may
show the normal onboarding/trust prompts; complete those prompts in the same
fresh PTY, or seed the temporary onboarding state in an automated test.

```bash
fresh_gemini="$fresh_root/gemini"
mkdir -p "$fresh_gemini/config"
printf '%s\n' '{"mcpServers":{}}' > "$fresh_gemini/config/mcp_config.json"

agy --gemini_dir="$fresh_gemini" \
  --log-file "$fresh_root/agy.log" \
  --sandbox \
  --print-timeout 300s \
  -p 'Reply exactly FRESH-AGY-OK. Do not use tools or modify files.'
```

Use the same `--gemini_dir`, clean workspace, and `--log-file` when starting
the fresh Antigravity PTY through `orchestrator-cli`. On macOS the supervisor
must use its isolated tmux backend; on Windows use pywinpty/ConPTY. A fresh
`--gemini_dir` does not override a workspace-local `.agents/mcp_config.json`,
so the probe workspace must not contain one.

For the macOS supervisor route, use a new runtime root and dispatch ID:

```bash
fresh_runtime="$fresh_root/runtime"
fresh_agy_log="$fresh_runtime/logs/fresh-antigravity.agy.log"
ORCHESTRATOR_SUPERVISOR_CONNECT_TIMEOUT=300 \
python skills/orchestrator-cli/scripts/orchestrator_supervisor.py \
  --runtime-root "$fresh_runtime" --json start \
  --dispatch-id "fresh-antigravity-$(date +%s)" \
  --provider antigravity-cli \
  --protocol antigravity-pty \
  --transport tmux \
  --workspace "$fresh_workspace" -- \
  agy --gemini_dir="$fresh_gemini" \
    --log-file "$fresh_agy_log" \
    --sandbox -i 'Reply exactly FRESH-AGY-PTY-OK. Do not use tools.'
```

Wait for `FRESH-AGY-PTY-OK` in the supervisor log before routing the real
task. If the first run displays onboarding or folder trust, complete it in
this same fresh PTY and rerun the probe if needed; do not press through an
unknown provider prompt in the original timed-out dispatch.

### Claude Code

Claude's `--bare` skips hooks, LSP, and plugins. Combine it with an empty MCP
file and `--strict-mcp-config` so no other MCP configuration is merged:

```bash
empty_mcp="$fresh_root/empty-mcp.json"
printf '%s\n' '{"mcpServers":{}}' > "$empty_mcp"

claude --bare \
  --strict-mcp-config \
  --mcp-config "$empty_mcp" \
  -p 'Reply exactly FRESH-CLAUDE-OK. Do not use tools or modify files.' \
  --output-format json \
  --dangerously-skip-permissions
```

### Codex

Use a disposable `CODEX_HOME`, ignore the user config, and make the probe
ephemeral. If the existing account is needed, copy only the local auth file
into the disposable home; never print it or commit it:

```bash
fresh_codex_home="$fresh_root/codex-home"
mkdir -p "$fresh_codex_home"
if [ -f "$HOME/.codex/auth.json" ]; then
  cp "$HOME/.codex/auth.json" "$fresh_codex_home/auth.json"
fi

CODEX_HOME="$fresh_codex_home" \
codex exec \
  --ignore-user-config \
  --ephemeral \
  --sandbox read-only \
  --json \
  'Reply exactly FRESH-CODEX-OK. Do not use tools or modify files.'
```

An empty disposable home also excludes user-installed Codex plugins. The clean
probe workspace excludes workspace-local integrations; do not run the probe
from a repository containing a local MCP/plugin configuration if the goal is a
strict no-integrations diagnosis.

## Windows PowerShell equivalents

Use a disposable root and empty MCP file, then apply the provider-specific
commands above with PowerShell paths:

```powershell
$freshRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("cli-fresh-" + [guid]::NewGuid())
$freshWorkspace = Join-Path $freshRoot "workspace"
New-Item -ItemType Directory -Force $freshWorkspace | Out-Null
git -C $freshWorkspace init --quiet
$emptyMcp = Join-Path $freshRoot "empty-mcp.json"
'{"mcpServers":{}}' | Set-Content -Encoding utf8 $emptyMcp
```

For Antigravity install the PTY dependency first when `doctor` reports it
missing:

```powershell
py -m pip install pywinpty
```

For `claude`, use `--bare --strict-mcp-config --mcp-config $emptyMcp`. For
`codex`, set a disposable `$env:CODEX_HOME`, pass `--ignore-user-config
--ephemeral`, and copy `%USERPROFILE%\.codex\auth.json` only when preserving
the existing login is required.

## Orchestrator handling

Before retrying, record the failed dispatch as `timeout` or
`startup-blocked-by-integrations`, attach the log tail, and create a new
dispatch ID. Run `doctor` again, launch the fresh provider probe, then route a
new task only if the probe reaches its first response. A successful fresh probe
proves that the original startup path was polluted by configuration or
integration state; it does not prove the old native conversation is resumable.

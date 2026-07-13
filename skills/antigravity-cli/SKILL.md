---
name: antigravity-cli
description: "Delegate a bounded coding, analysis, review, or implementation task directly to the local Google Antigravity CLI (`agy`) without CAO or cao-server. Use when an agent should run agy headlessly or interactively with the skill's default auto-approved permissions, resume a CLI conversation, and verify the result."
---

# Direct Antigravity CLI Delegation

Use the local `agy` CLI to hand one bounded task to Antigravity. Do not start
`cao-server`, call `cao`, configure `cao-mcp-server`, or use CAO handoff tools.

## Workflow

1. Confirm the local CLI before delegating:

```powershell
Get-Command agy -ErrorAction Stop
agy --help
```

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

agy -p $prompt --mode accept-edits --dangerously-skip-permissions --print-timeout 15m
```

4. Read the response, inspect the diff, and run the requested verification
yourself. Do not accept an unverified claim that tests passed.

## Default Automation And Coordination

- This skill defaults to `--dangerously-skip-permissions`. It auto-approves all
  Antigravity tool permission requests for the invocation.
- Use `--sandbox` or `--mode plan` only when a task explicitly requests a safer
  override.
- Do not run two writing agents against the same worktree. Use separate
  worktrees or run them sequentially.
- Pass `--agent <name>` only after checking the locally available names with
  `agy agent` or `agy agents`.

## Continuation And Diagnostics

Use `agy -c -p "follow-up" --mode accept-edits --dangerously-skip-permissions`
to continue the latest conversation, or add `--conversation <id>` for a known
conversation. For a stuck headless run, add `--log-file <path>` and a deliberate
`--print-timeout <duration>`.

See [references/cli-reference.md](references/cli-reference.md) for the local
help-derived command reference and prompt template.

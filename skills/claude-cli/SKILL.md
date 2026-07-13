---
name: claude-cli
description: "Delegate a bounded coding, analysis, review, or implementation task directly to the local Claude Code CLI (`claude`) without CAO or cao-server. Use when an agent should run Claude Code headlessly or interactively with the skill's default permission bypass, resume a CLI session, and verify the result."
---

# Direct Claude CLI Delegation

Use the local `claude` CLI to hand one bounded task to Claude Code. Do not start
`cao-server`, call `cao`, configure `cao-mcp-server`, or use CAO handoff tools.

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
delegation:

```powershell
$prompt = @'
Work in D:\path\to\repo. Inspect the failing test, make the smallest in-scope
fix, run the named test, and report changed files plus the verification result.
'@

claude -p $prompt --output-format json --dangerously-skip-permissions
```

4. Read the result, inspect the diff, and run the requested verification yourself.
Do not accept an unverified claim that tests passed.

## Default Automation And Coordination

- This skill defaults to `--dangerously-skip-permissions`. It auto-approves all
  Claude Code permission checks for the invocation.
- Use `--permission-mode plan` or `--permission-mode acceptEdits` only when a
  task explicitly requests a safer override. Do not combine either mode with
  `--dangerously-skip-permissions`.
- Do not run two writing agents against the same worktree. Use separate
  worktrees or run them sequentially.
- If Claude Code reports a nested-session problem, use a separate shell or a
  different client; do not weaken safeguards just to launch the child session.

## Continuation

Use `claude -c -p "follow-up" --dangerously-skip-permissions` to continue the
latest conversation in the current directory, or
`claude -r <session-id> -p "follow-up" --dangerously-skip-permissions` for a
known session. Preserve the same workspace and restate any changed success
criteria.

See [references/cli-reference.md](references/cli-reference.md) for the local
help-derived command reference and prompt template.

---
name: codex-cli
description: "Delegate a bounded coding, analysis, review, or implementation task directly to the local Codex CLI (`codex`) without CAO or cao-server. Use when an agent should run Codex headlessly with `codex exec` and the skill's default approval-and-sandbox bypass, resume a CLI session, review changes, and verify the result."
---

# Direct Codex CLI Delegation

Use the local `codex` CLI to hand one bounded task to Codex. Do not start
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
sandbox bypass for every direct delegation:

```powershell
$prompt = @'
Work in D:\path\to\repo. Diagnose the first failing test. Touch only the
necessary files, run the named test, and report changed files plus the result.
'@

$prompt | codex exec --dangerously-bypass-approvals-and-sandbox --json -
```

4. Read the JSONL events, inspect the diff, and run the requested verification
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

## Review And Continuation

Use `codex review --uncommitted "focus on regressions"` for a direct review of
the current worktree. Continue a saved non-interactive task with
`codex exec resume --last --dangerously-bypass-approvals-and-sandbox "follow-up"`
or replace `--last` with its session ID.

See [references/cli-reference.md](references/cli-reference.md) for the local
help-derived command reference and prompt template.

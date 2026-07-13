# Claude CLI Reference

Verified from local `claude --help` and `claude auth --help` on 2026-07-13.
Run the relevant help command again when the installed CLI version changes.

## Direct Delegation Commands

| Need | Command or flag |
| --- | --- |
| Interactive session | `claude [prompt]` |
| Default headless single task | `claude -p "prompt" --dangerously-skip-permissions` |
| Structured result | `--output-format json` with `-p` |
| Streamed events | `--output-format stream-json` with `-p` |
| Continue latest local session | `claude -c -p "follow-up" --dangerously-skip-permissions` |
| Resume a known session | `claude -r <session-id> -p "follow-up" --dangerously-skip-permissions` |
| Choose a model | `--model <model>` |
| Bound API spend | `--max-budget-usd <amount>` with `-p` |
| Add an allowed workspace | `--add-dir <path>` |
| Check auth | `claude auth status` |

## Default Permission Policy

Local help lists `default`, `acceptEdits`, `plan`, `auto`, `dontAsk`, and
`bypassPermissions` for `--permission-mode`.

This skill defaults to `--dangerously-skip-permissions` for every direct task.
It auto-approves Claude Code permission checks for that invocation. Use `plan`
or `acceptEdits` only when a task explicitly requests a safer override.

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

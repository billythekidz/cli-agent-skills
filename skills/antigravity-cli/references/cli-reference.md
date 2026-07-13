# Antigravity CLI Reference

Verified from local `agy --help`, `agy help`, and `agy agent --help` on
2026-07-13. Run the relevant help command again when the installed CLI version
changes.

## Direct Delegation Commands

| Need | Command or flag |
| --- | --- |
| Interactive session | `agy` |
| Initial interactive prompt | `agy -i "prompt"` |
| Default headless single task | `agy -p "prompt" --mode accept-edits --dangerously-skip-permissions` |
| Continue latest conversation | `agy -c -p "follow-up" --mode accept-edits --dangerously-skip-permissions` |
| Resume known conversation | `agy --conversation <id> -p "follow-up" --mode accept-edits --dangerously-skip-permissions` |
| Explicit safer override | `--mode plan` or `--sandbox` |
| Choose an agent | `--agent <name>` |
| Choose a model | `--model <model>` |
| Add a workspace | `--add-dir <path>` |
| Restrict terminal execution | `--sandbox` |
| Bound print-mode wait | `--print-timeout <duration>` |
| Save diagnostics | `--log-file <path>` |

Root help also lists `agent`, `agents`, `changelog`, `install`, `models`,
`plugin`, and `update` subcommands. Prefer `agy plugin help` for plugin command
discovery; some plugin subcommands interpret `--help` as an argument.

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

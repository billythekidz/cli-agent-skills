# Claude CLI Reference

Verified from the local `claude -h` help capture and `claude auth --help` on
2026-07-28.
Run the relevant help command again when the installed CLI version changes.

## Direct Delegation Commands

| Need | Command or flag |
| --- | --- |
| Inspect the installed CLI | `claude --help` or `claude -h` |
| Interactive session | `claude [prompt]` |
| Default headless single task | `claude -p "prompt" --dangerously-skip-permissions` |
| Structured result | `--output-format json` with `-p` |
| Schema-constrained structured result | `--json-schema <schema>` with `-p` |
| Streamed events | `--output-format stream-json` with `-p` |
| Live multi-prompt process | `claude -p --input-format stream-json --output-format stream-json --verbose` |
| Resume exact session | `claude -r <session-id> -p "follow-up" --dangerously-skip-permissions` |
| Continue latest local session (exception only) | `claude -c -p "follow-up" --dangerously-skip-permissions` |
| Disable persistence (not resumable) | `--no-session-persistence` |
| Choose a model | `--model <model>` |
| Bound API spend | `--max-budget-usd <amount>` with `-p` |
| Print-mode model fallback | `--fallback-model <model>` |
| Add an allowed workspace | `--add-dir <path>` |
| Restrict built-in tools | `--tools ""` or `--tools <names...>` |
| Check auth | `claude auth status` |

## Command Selection

| Requirement | Preferred command | Why |
| --- | --- | --- |
| One bounded worker task | `claude -p ... --output-format json` | One result, easy to capture and verify |
| Validated structured result | `claude -p ... --json-schema <schema>` | Enforces the caller's result shape |
| Same-process programmatic follow-up | `claude -p --input-format stream-json --output-format stream-json` | One native session with stdin JSONL delivery |
| Human interactive work | `claude [prompt]` | Uses the native terminal UI |
| Post-exit exact continuation | `claude -r <session-id> -p ...` | Resumes the recorded native session |
| Cloud multi-agent review | `claude ultrareview ...` | Explicit optional review command; not the default worker route |
| Installation/configuration diagnosis | `claude doctor` or `claude auth status` | Diagnose before spending a worker turn |

## Live Process Prompt Delivery

For multiple prompts sent to one still-running process, keep one `claude -p`
process open and write newline-delimited JSON user messages to its stdin:

```text
claude -p --input-format stream-json --output-format stream-json --verbose \
  --no-session-persistence --tools "" --permission-mode plan --safe-mode
{"type":"user","message":{"role":"user","content":"first prompt"}}
{"type":"user","message":{"role":"user","content":"follow-up"}}
```

The `-p/--print` JSON and `stream-json` modes are pipe-based. Keep stdin,
stdout, and stderr as ordinary pipes; no PTY is required for machine-readable
automation. Use a console/PTY only for the interactive `claude [prompt]` UI.

### Two-turn recipe

Use the first `result` as the handoff point, then write the next JSON line to
the same open stdin. This keeps one Claude process and native conversation; it
does not start `claude -r`.

```text
stdin -> {"type":"user","message":{"role":"user","content":"Remember token ALPHA-42. Reply only with it."}}
stdout <- {"type":"system","subtype":"init","session_id":"<native-id>",...}
stdout <- {"type":"result","result":"ALPHA-42",...}
stdin -> {"type":"user","message":{"role":"user","content":"Return the token from the prior turn."}}
stdout <- {"type":"result","result":"ALPHA-42",...}
```

For a prompt that arrives while Claude is still working, place it in the
single-writer FIFO and send it after the active turn's `result`. Claude can
accept stream input, but this skill does not treat a second line as an
interrupt or parallel execution request.

Write UTF-8 without a BOM on Windows. The `system/init` event reports the
native `session_id`; treat each `result` event as the turn boundary. Keep one
writer and use FIFO delivery: do not assume a follow-up interrupts an in-flight
turn or runs in parallel.

The flags above are for a harmless probe. Omit `--no-session-persistence` only
when post-exit resume will be needed. The process handle and stdin stream are
the live transport, not the session identity. Use `claude -r <session-id>` only
after that original process has ended or is unreachable.

## Native Session Continuity

Record the native session ID reported by the CLI with its provider, absolute
workspace/worktree, selected agent/model, and a short task summary. Resume with
that exact ID after verifying the recorded workspace still matches.

`-c` is only safe when the user explicitly asks for the unique latest local
session and no concurrent task can be selected. Do not use
`--no-session-persistence` for a task that needs continuity, or
`--fork-session` when the goal is the same native conversation. If the ID is
unavailable or resume fails, begin a new task with a factual handoff summary and
label it as new.

## Default Permission Policy

Local help lists `default`, `acceptEdits`, `plan`, `auto`, `dontAsk`, and
`bypassPermissions` for `--permission-mode`.

This skill defaults to `--dangerously-skip-permissions` for every direct task.
It auto-approves Claude Code permission checks for that invocation. Use `plan`
or `acceptEdits` only when a task explicitly requests a safer override.

## Startup Timeout Recovery

After 300 seconds without a usable prompt, stop the process and preserve its
diagnostic output. Run the [fresh-start-without-integrations](../../orchestrator-cli/references/fresh-start-without-integrations.md)
procedure with `--bare`, an empty `--mcp-config`, and
`--strict-mcp-config`. A successful probe is a new native session, not a retry
of the timed-out process.

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

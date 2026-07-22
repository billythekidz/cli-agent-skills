# Codex CLI Reference

Verified from local `codex --help`, `codex exec --help`, `codex exec resume
--help`, `codex review --help`, and `codex app-server --help` on 2026-07-14.
Run the relevant help command again when the installed CLI version changes.

## Direct Delegation Commands

| Need | Command or flag |
| --- | --- |
| Default headless single task | `codex exec --dangerously-bypass-approvals-and-sandbox "prompt"` |
| Default multi-line prompt | `prompt | codex exec --dangerously-bypass-approvals-and-sandbox -` |
| Structured event stream | `--json` |
| Write final message to a file | `--output-last-message <file>` |
| Explicit safer override | `--sandbox read-only` or `--sandbox workspace-write` |
| Extra writable directory | `--add-dir <dir>` |
| Set working root | `--cd <dir>` |
| Disposable, non-resumable session | `--ephemeral` |
| Resume exact task | `codex exec resume <session-id> --dangerously-bypass-approvals-and-sandbox "follow-up"` |
| Resume latest task (exception only) | `codex exec resume --last --dangerously-bypass-approvals-and-sandbox "follow-up"` |
| Review worktree changes | `codex review --uncommitted "focus"` |
| Review against a branch | `codex review --base <branch>` |
| Review one commit | `codex review --commit <sha>` |

`--json` emits JSONL. Treat it as an event stream and preserve the final agent
message separately if a later step needs a concise handoff.

## Active Process Prompt Delivery

`codex exec` is a one-shot non-interactive command. Piped stdin supplies the
initial prompt/context only; it cannot inject later prompts into a running
`exec` process.

### Interactive TUI

Start `codex` without `exec` for a persistent terminal UI. While a turn is
running:

| Input | Result |
| --- | --- |
| Enter | Steer the current turn with the composed prompt |
| Tab | Queue the composed prompt for the next turn |

### App Server

`codex app-server` is experimental. Its default `stdio://` transport is JSONL
JSON-RPC. Keep one process and one writer, then use this lifecycle:

1. Send `initialize`.
2. Send the `initialized` notification.
3. Send `thread/start` and record the returned `threadId`.
4. Send `turn/start` and record the active `turnId` from its `turn/started`
   notification; do not wait for the request response to steer.
5. Send `turn/steer` to inject prompt input into that in-flight turn.

Use this `turn/steer` request shape:

```json
{
  "method": "turn/steer",
  "id": 4,
  "params": {
    "threadId": "<thread-id>",
    "expectedTurnId": "<active-turn-id>",
    "input": [{ "type": "text", "text": "<new prompt>" }]
  }
}
```

### In-flight steer recipe

Start a turn that remains active, retain its returned turn ID, then send this
request through the same app-server stdin before `turn/completed`. A successful
response proves delivery to the active process; the final `agentMessage` should
reflect the injected instruction.

```text
stdin -> {"method":"thread/start","id":2,"params":{}}
stdout <- {"id":2,"result":{"thread":{"id":"thread-123"}}}
stdin -> {"method":"turn/start","id":3,"params":{"threadId":"thread-123","input":[{"type":"text","text":"Begin a long explanation and end with INITIAL."}]}}
stdout <- {"method":"turn/started","params":{"threadId":"thread-123","turn":{"id":"turn-456"}}}
stdin -> {"method":"turn/steer","id":4,"params":{"threadId":"thread-123","expectedTurnId":"turn-456","input":[{"type":"text","text":"Stop the long explanation and report STEERED instead."}]}}
stdout <- {"id":4,"result":{...}}
stdout <- {"method":"turn/completed","params":{"turn":{"id":"turn-456","status":"completed"}}}
```

The `threadId` remains the same; `turn/steer` uses the same active `turnId`.
After `turn/completed`, do not send another steer for that ID. Retain the next
prompt and create a fresh `turn/start` on the same thread.

Track `turn/started` and `turn/completed` notifications. Serialize all writes;
the transport is not an unbounded prompt queue and the connection itself is not
the native conversation identity. If `turn/steer` returns
`activeTurnNotSteerable`, or the active turn completes before injection, retain
the prompt in the caller queue and send it with `turn/start` after completion.

## Native Session Continuity

Record the native session ID emitted by the CLI with its provider, absolute
workspace/worktree, selected profile/model, and a short task summary. Resume
with that exact ID after verifying the recorded workspace still matches.

`--last` is only safe when the user explicitly asks for the unique latest local
session and no concurrent task can be selected. `--ephemeral` intentionally
creates a session that cannot be resumed. If the ID is unavailable or resume
fails, begin a new task with a factual handoff summary and label it as new.

## Default Permission Policy

This skill defaults to `--dangerously-bypass-approvals-and-sandbox` for direct
`codex exec` calls. It skips approval prompts and disables sandboxing for that
invocation. Do not combine it with `--sandbox`.

`--dangerously-bypass-hook-trust` remains opt-in because it is a separate hook-
trust bypass rather than the normal command-approval path.

## Startup Timeout Recovery

After 300 seconds without a usable prompt, stop the process and preserve its
JSONL diagnostics. Run the [fresh-start-without-integrations](../../orchestrator-cli/references/fresh-start-without-integrations.md)
procedure with a disposable `CODEX_HOME`, `--ignore-user-config`, and
`--ephemeral`. Copy only the auth file when preserving the existing login is
required. A successful probe is a new native session, not a continuation of
the timed-out process.

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

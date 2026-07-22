# Dispatch Protocol

This protocol adapts the implemented `assign`, `handoff`, and `send_message`
contracts in [CLI Agent Orchestrator](https://github.com/awslabs/cli-agent-orchestrator)
to direct local CLIs with GitHub Issues or `.orchestrator/` Markdown records.
It does not start CAO and does not depend on CAO terminal IDs or an inbox
service.

The durable Issue or Markdown record tracks orchestration; the provider-native
session ID tracks one CLI conversation. Keep those identities separate.

CAO's source uses non-blocking `assign` with a callback, blocking `handoff`
with structured output, caller-aware delivery, fail-fast preflight, and clear
timeout/error handling. Its workflow service currently marks `parallel` as
reserved rather than silently running it sequentially. Mirror those guarantees
with an Issue DAG and worktree isolation instead of assuming a hidden scheduler.

## Modes

| Mode | Use it for | Completion rule |
| --- | --- | --- |
| `assign` | Independent research, isolated tests, docs, or non-overlapping code changes | The worker produces a valid handoff for its own dispatch ID; process start is not completion. |
| `handoff` | A design decision, dependency gate, integration, review, or any task whose result unlocks another | Wait for a valid handoff and verify its evidence before dispatching the dependent task. |

Do not dispatch a task as `assign` merely to avoid waiting when its result is a
required input. Do not run a `handoff` in a worktree another writer owns.

## Preflight And Dispatch Ledger

Before any write worker starts, verify all of the following:

1. The repository, parent record, and child record are exact and authorized.
2. The child has an objective, inputs, exclusive allowed paths, excluded paths,
   verification, CLI, and model tier.
3. Every named dependency is complete or its output is explicitly available.
4. The dependency graph has no cycle. Break ties by the order recorded in the
   parent plan, then by issue number, so re-planning is deterministic.
5. The worktree and branch are unique, and no active marker already claims the
   same issue or file scope.
6. The result location, timeout policy, and supervisor callback target are
   known before launch.
7. The native-session action and process state are explicit: `new`, a live
   transport for an active task, or an exact recorded provider-native ID for a
   stopped task that genuinely needs a follow-up.

Write a dispatch record before launching a child. It is the durable equivalent
of a CAO terminal record: use an issue comment in GitHub mode, or the task file
plus an `INDEX.md` event in local Markdown mode.

```markdown
<!-- orchestrator-cli:dispatch:issue-124-attempt-1 -->
## Dispatch
Dispatch: `issue-124-attempt-1`
Mode: `assign`
CLI/model: `codex-cli` / `high`
Worktree: `<absolute path>`
Owns: `src/webhooks/*`
Depends on: `#121`, `#122`
Result location: `<temporary supervisor-owned path>`
Timeout: `<duration or policy>`
Native session: `pending capture` | `provider / exact ID`
Session action: `new` | `resumed`
Process state: `active` | `stopped` | `unavailable`
Live transport: `stdin JSONL` | `app-server stdio` | `original interactive PTY` | `unavailable`
Current turn: `<turn ID>` | `awaiting result` | `queued` | `idle` | `unavailable`
State: `dispatched`
```

The supervisor, not the worker, owns the issue comment or `INDEX.md` event. A
worker's return must name the same dispatch ID; otherwise treat it as misrouted
and do not attach it to the task record. After launch, update the record with
the exact provider-native ID from the worker result, or `unavailable` when the
CLI did not report a stable ID. While the worker remains active, update the
same record with its transport and current-turn state before accepting another
prompt.

When a task needs prompt injection into the same live process, start it through
the bundled lightweight supervisor:

```bash
python <orchestrator-cli-skill-dir>/scripts/orchestrator_supervisor.py --json start \
  --dispatch-id task-TASK-12-attempt-1 \
  --provider claude-cli \
  --protocol claude-stream-json \
  --workspace /absolute/worktree \
  -- claude -p --input-format stream-json --output-format stream-json

python <orchestrator-cli-skill-dir>/scripts/orchestrator_supervisor.py --json send \
  task-TASK-12-attempt-1 "Continue in the same live process."
```

The supervisor records process status in
`.orchestrator/runtime/supervisor.sqlite3` and raw output in
`.orchestrator/runtime/logs/*.jsonl`. Claude and Codex retain stdio/JSONL or
app-server handles. Antigravity uses `antigravity-pty`: an isolated tmux PTY on
macOS and a pywinpty/ConPTY PTY on Windows. If `status` shows
`live_handle: false` for an active dispatch, or `send` returns
`live-transport-unavailable`, do not start a second active process. Record the
lost route, then wait, stop, or recover by exact provider-native session ID
according to the task state.

Before an Antigravity PTY dispatch, run the bundled `doctor`. Install missing
host dependencies manually: `brew install tmux` on macOS, or
`py -m pip install pywinpty` from Windows PowerShell. The supervisor never
auto-installs either dependency.

## Parallel Fan-Out

Run a batch only when all child issues are ready and every writer has a unique
worktree and non-overlapping `Owns` paths. Start the batch, retain each process
handle and raw output, then write one handoff record per child after review.
Each worker has its own provider-native session envelope; never select a
continuation through a provider's "latest" option in a concurrent batch.

Do not use an unbounded fan-out. Limit the batch to the number of independent
tasks that can be reviewed and integrated without losing track of their output.
If capacity is uncertain, start with the smallest useful batch and unlock more
only after the first handoffs are valid.

Never silently downgrade a requested parallel batch into writers sharing one
worktree, nor silently continue after a dependency cycle or file-scope overlap.
Record the planning conflict as a blocker and revise the issue graph.

## Handoff Validation And Recovery

A valid handoff contains all of: dispatch ID, status, native-session action and
provider/ID (or `unavailable`), changed paths, branch or commit, verification
command and result, evidence, blockers, and next owner.

| Failure | Required response |
| --- | --- |
| `dispatch-failed` | Record the launch error. Do not claim the worker began. |
| `worker-error` | Record exit/error evidence and preserve the worktree for inspection when useful. |
| `timeout` | Record the timeout and process state. Do not assume the worker made no changes. |
| `startup-blocked-by-integrations` | After 300 seconds of `Loading`/`connecting`, preserve log tails, stop the route, run `fresh-start-without-integrations.md`, and use a new dispatch only if the no-integration probe succeeds. |
| `no-handoff` | Record that the process ended without the required payload; inspect output before retrying. |
| `misrouted-handoff` | Record the received ID, keep it out of the target issue, and locate or re-request the correct handoff. |
| `native-session-unavailable` | Record that no stable provider ID was returned. A later follow-up starts a new session with a factual handoff. |
| `native-resume-failed` | Record the exact resume command and error. Do not fall back to a "latest" session; start a labeled new session only after preserving the prior evidence. |
| `live-transport-unavailable` | Record that the original process/PTY/stdio route is unavailable. Do not start a second live worker; wait for or deliberately stop the original process, then use exact native recovery if needed. |

Never replace a failed attempt's evidence. A retry gets a new attempt number
and a durable record explaining what changed: input, scope, CLI/model tier,
timeout, or environmental repair. Do not launch a second process for an active
dispatch unless the user explicitly instructs a takeover and the original
worker is stopped or isolated.

For an active task with a retained route, use that route instead: Claude
receives a JSONL user message on its original stdin; Codex app-server records
the active turn ID from `turn/started`, then receives `turn/steer` with that
`expectedTurnId` (or a queued `turn/start` after
completion); Antigravity receives prompt text plus Enter on its original
interactive PTY. The handle and transport are operational routing data, not
the provider-native session ID.

For a stopped task, an attempt may resume its own exact provider-native session.
That does not reuse a dispatch ID: link the new attempt to the prior one and
record the same native ID plus `Session action: resumed`. Never reuse that ID
with a different provider.

## Sequential Integration Gate

At a dependency gate, the integration owner must:

1. Read the exact child issue or local task handoffs and their evidence.
2. Confirm required inputs are merged or available in its worktree.
3. Resolve conflicts without starting another writing worker in that worktree.
4. Run the parent acceptance checks.
5. Post one parent summary listing each child dispatch ID and final outcome.

This retains CAO's separation between asynchronous work and the blocking
handoff that synthesizes it, while the selected control plane provides durable
history.

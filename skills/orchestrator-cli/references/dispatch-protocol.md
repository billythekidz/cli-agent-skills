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
2. The child has an objective, inputs, allowed paths, excluded paths,
   verification, CLI, model tier, and an explicit workspace mode. Use
   `current` for one sequential writer; use `dedicated-worktree` for parallel
   or isolation-required work.
3. Every named dependency is complete or its output is explicitly available.
4. The dependency graph has no cycle. Break ties by the order recorded in the
   parent plan, then by issue number, so re-planning is deterministic.
5. For `dedicated-worktree`, the worktree and branch are unique. For
   `current`, confirm no other active writer claims the same workspace or file
   scope. In both cases, reject an already-active dispatch ID.
6. The result location, timeout policy, and supervisor callback target are
   known before launch.
7. The native-session action and process state are explicit: `new`, a live
   transport for an active task, or an exact recorded provider-native ID for a
   stopped task that genuinely needs a follow-up.
8. The selected route has passed the short `READY` availability probe from
   [availability-probe.md](availability-probe.md) within the probe budget.
   Do not send the real task prompt before this gate passes.
9. A progress-hash scope is defined before launch. Hash only the task's owned
   paths, task output paths, and explicit handoff/evidence files; do not hash
   the whole repository when unrelated changes could create false progress.

Write a dispatch record before launching a child. It is the durable equivalent
of a CAO terminal record: use an issue comment in GitHub mode, or the task file
plus an `INDEX.md` event in local Markdown mode.

```markdown
<!-- orchestrator-cli:dispatch:issue-124-attempt-1 -->
## Dispatch
Dispatch: `issue-124-attempt-1`
Mode: `assign`
Task type: `coding` | `review` | `plan`
CLI/model: `<one permitted CLI/model pair from cli-model-routing.md>`
Fallback chain: `<exact chain for the task type>`
Fallback cursor: `1` for a new task; advance only within this task's attempts
Availability probe: `passed READY` | `failed` | `timed out`
Probe evidence: `<command, duration, parsed response/log tail>`
Workspace mode: `current` | `dedicated-worktree`
Worktree: `<absolute current workspace or dedicated path>`
Branch: `<current branch or dedicated branch>`
Cleanup: `not-applicable` | `pending` | `complete` | `cleanup-blocked`
Main-repo evidence: `<control-plane record and persisted artifact paths>`
Progress hash scope: `<owned paths and task artifacts>`
Progress hash snapshot: `<hash and timestamp>`
Timeout streak: `0` | `1` | `2+`
Owns: `src/webhooks/*`
Depends on: `#121`, `#122`
Result location: `<temporary supervisor-owned path>`
Timeout: `<duration or policy>`
Native session: `pending capture` | `provider / exact ID`
Session action: `new` | `resumed`
Process state: `active` | `stopped` | `unavailable`
Execution mode: `headless-one-shot` | `headless-live` | `interactive-live` | `unavailable`
Live transport: `stdin JSONL` | `app-server stdio` | `original interactive PTY` | `unavailable`
Headless transport: `stdout/stderr pipes` | `one-shot` | `unavailable`
Headless live transport: `stdin JSONL` | `app-server stdio` | `unavailable`
Current turn: `<turn ID>` | `awaiting result` | `queued` | `idle` | `unavailable`
State: `dispatched`
```

The supervisor, not the worker, owns the issue comment or `INDEX.md` event. A
worker's return must name the same dispatch ID; otherwise treat it as misrouted
and do not attach it to the task record. After launch, update the record with
the exact provider-native ID from the worker result, or `unavailable` when the
CLI did not report a stable ID. While the worker remains active, update the
same record with its execution mode, transport, and current-turn state before
accepting another prompt.

## Headless-First Dispatch

Use headless one-shot execution as the default for `assign` and `handoff`:

| Provider | Default command | Record as |
| --- | --- | --- |
| Claude | `claude -p --output-format json --dangerously-skip-permissions ...` | `headless-one-shot`, stdout/stderr pipes |
| Codex | `codex exec --dangerously-bypass-approvals-and-sandbox --json ...` | `headless-one-shot`, stdout/stderr pipes |
| Antigravity | `agy -p --output-format json --mode accept-edits --dangerously-skip-permissions ...` or `stream-json` | `headless-one-shot`, stdout/stderr pipes |

These are complete default worker commands. Do not omit the provider's
unattended flag when converting a row into a real process invocation. For the
Codex app-server live route, use its config overrides because the subcommand
does not expose the one-shot bypass flag in its own options:
`codex app-server -c 'approval_policy="never"' -c 'sandbox_mode="danger-full-access"'`.
The app-server remains JSONL over stdio and does not need a PTY.

Select a headless-live route only when the supervisor must send another prompt
before the process exits: Claude `stream-json` stdin with
`--dangerously-skip-permissions`, or Codex app-server stdio with the config
overrides above.
Select interactive-live only for a requested/native UI workflow; use the
original console or an externally controlled PTY for that UI. Do not create a
PTY for a default headless worker.

When a task needs prompt injection into the same live process, start it through
the bundled lightweight supervisor:

```bash
python <orchestrator-cli-skill-dir>/scripts/orchestrator_supervisor.py --json start \
  --dispatch-id task-TASK-12-attempt-1 \
  --provider claude-cli \
  --protocol claude-stream-json \
  --workspace /absolute/worktree \
  -- claude -p --input-format stream-json --output-format stream-json \
     --dangerously-skip-permissions

python <orchestrator-cli-skill-dir>/scripts/orchestrator_supervisor.py --json send \
  task-TASK-12-attempt-1 "Continue in the same live process."
```

For a Codex live route, keep the same unattended policy on the app-server
process:

```bash
python <orchestrator-cli-skill-dir>/scripts/orchestrator_supervisor.py --json start \
  --dispatch-id task-TASK-13-attempt-1 \
  --provider codex-cli \
  --protocol codex-app-server \
  --workspace /absolute/worktree \
  -- codex app-server -c 'approval_policy="never"' -c 'sandbox_mode="danger-full-access"'
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

## Workspace Selection

Use `workspace=current` for a simple or sequential task with one writer. Do not
create a worktree solely because the task is delegated to a CLI. Record the
current absolute workspace and branch, and verify that no other active writer
owns it.

Use `workspace=dedicated-worktree` for parallel writers, overlapping/uncertain
file scopes, explicit isolation, or a separate integration owner that must not
touch a dirty current workspace. Record its absolute path and branch before
launch.

## Parallel Fan-Out

Run a batch only when all child issues are ready and every writer has a unique
dedicated worktree and non-overlapping `Owns` paths. Start the batch, retain each process
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
| `worker-error` | Record exit/error evidence and preserve the worktree for inspection when useful; clean it after the disposition is recorded. |
| `timeout` | Record the timeout and process state. Do not assume the worker made no changes. |
| `startup-blocked-by-integrations` | After 300 seconds of `Loading`/`connecting`, preserve log tails, stop the route, run `fresh-start-without-integrations.md`, and use a new dispatch only if the no-integration probe succeeds. |
| `no-handoff` | Record that the process ended without the required payload; inspect output before retrying. |
| `misrouted-handoff` | Record the received ID, keep it out of the target issue, and locate or re-request the correct handoff. |
| `native-session-unavailable` | Record that no stable provider ID was returned. A later follow-up starts a new session with a factual handoff. |
| `native-resume-failed` | Record the exact resume command and error. Do not fall back to a "latest" session; start a labeled new session only after preserving the prior evidence. |
| `live-transport-unavailable` | Record that the original process/PTY/stdio route is unavailable. Do not start a second live worker; wait for or deliberately stop the original process, then use exact native recovery if needed. A completed Antigravity print process is not a live-transport failure; use its exact native conversation ID for recovery. |

Never replace a failed attempt's evidence. A retry gets a new attempt number
and a durable record explaining what changed: input, scope, CLI/model tier,
timeout, or environmental repair. Do not launch a second process for an active
dispatch unless the user explicitly instructs a takeover and the original
worker is stopped or isolated.

## Timeout Progress Hash

Treat a task timeout as an observation point, not an automatic stop signal.
Compute a progress hash over the task's owned paths and explicit task artifacts.
For a dedicated worktree, this usually means the owned files plus handoff/docs
produced by that task. For `workspace=current`, hash only the owned paths and
task artifact paths, never the whole repository, so unrelated edits do not look
like task progress.

The scope is task-owned, not provider-owned: use the same owned/artifact paths
for Claude, Codex, and Antigravity within one attempt. Exclude provider-private
state such as `.claude`, `.codex`, `.gemini`, raw session logs, and process
metadata, because those may change without task progress. If a retry reduces or
otherwise changes the task scope, create a new attempt and progress-hash
baseline; do not compare it with the prior scope's hash.

Use the bundled read-only helper to produce a deterministic JSON snapshot:

```powershell
python <orchestrator-cli-skill-dir>/scripts/task_progress_hash.py --root <workspace-path> --path <owned-path> --path <task-artifact-path>
```

Record the returned `sha256`, timestamp, selected paths, and file/missing-path
lists in the dispatch record. Reuse exactly the same `--root` and `--path`
scope for the next timeout comparison.

Recommended timeout handling:

1. On the first timeout, record the process state, current progress hash, and
   timestamp. If there is no previous hash for this attempt, keep the CLI
   running and schedule another timeout check.
2. On the next timeout, recompute the progress hash for the same scope.
3. If the hash changed, record `Timeout action: keep-running`, reset the
   unchanged streak, and continue waiting.
4. If the hash is unchanged across consecutive timeout checks, stop the CLI,
   record `Timeout action: stop-and-probe`, run the short `READY` availability
   probe for the same model/CLI, and only then decide whether to retry the same
   route with a smaller task.
5. Fall back to another model/CLI only when the probe or provider error shows
   the current route is actually unavailable (for example quota, auth, startup,
   or classified provider failure). Mere slowness with changing hashes is not a
   fallback trigger.

When a task reaches `verified` or `done`, close its fallback sequence and reset
the fallback cursor to `1`. The next task must probe the first route in its own
task-type chain, even if the previous task completed successfully on a fallback
provider. If the first route fails again, continue through that chain and record
the new attempts; do not reuse the previous task's selected provider as a
sticky default.

## Mandatory Dedicated-Worktree Cleanup

After a dedicated-worktree task has a verified handoff:

1. Stop and verify the CLI process, supervisor route, and any child processes.
2. Confirm the task commit/handoff and inspect `git status --short` in the
   dedicated path. Do not discard uncommitted changes silently.
3. Persist the commit reference, handoff, evidence/log summary, and required
   docs into the main workspace/main repository or active GitHub control plane.
   Record the destination paths and verify they exist; the worktree must not be
   the only copy.
4. If the path is clean and the disposition is recorded, run from the main
   repository:

   ```powershell
   git worktree remove <dedicated-path>
   git worktree prune
   ```

5. Remove the dedicated branch only after it is merged or its disposition is
   recorded. Record removed path, branch disposition, cleanup timestamp,
   persisted artifact paths, and result in the task journal.

If a process is still active, required artifacts are not persisted, the path is
dirty without a recorded disposition, or `git worktree remove` fails, set
`Cleanup: cleanup-blocked`, preserve the worktree, and do not mark the task
fully closed. Never replace worktree removal with recursive filesystem deletion.

For an active task with a retained route, use that route instead: Claude
receives a JSONL user message on its original stdin; Codex app-server records
the active turn ID from `turn/started`, then receives `turn/steer` with that
`expectedTurnId` (or a queued `turn/start` after completion); Antigravity
interactive mode receives prompt text plus Enter on its original terminal/PTY.
Antigravity print mode uses stdout/stderr pipes only for its one-shot result and
has no same-process follow-up route. The handle and transport are operational
routing data, not the provider-native session ID.

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

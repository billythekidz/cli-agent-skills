# Dispatch Protocol

This protocol adapts the implemented `assign`, `handoff`, and `send_message`
contracts in [CLI Agent Orchestrator](https://github.com/awslabs/cli-agent-orchestrator)
to direct local CLIs and GitHub Issues. It does not start CAO and does not
depend on CAO terminal IDs or an inbox service.

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

1. The repository, parent issue, and child issue are exact and authorized.
2. The child has an objective, inputs, exclusive allowed paths, excluded paths,
   verification, CLI, and model tier.
3. Every named dependency is complete or its output is explicitly available.
4. The dependency graph has no cycle. Break ties by the order recorded in the
   parent plan, then by issue number, so re-planning is deterministic.
5. The worktree and branch are unique, and no active marker already claims the
   same issue or file scope.
6. The result location, timeout policy, and supervisor callback target are
   known before launch.

Write a dispatch comment before launching a child. It is the durable equivalent
of a CAO terminal record:

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
State: `dispatched`
```

The supervisor, not the worker, owns this comment. A worker's return must name
the same dispatch ID; otherwise treat it as misrouted and do not attach it to
the issue.

## Parallel Fan-Out

Run a batch only when all child issues are ready and every writer has a unique
worktree and non-overlapping `Owns` paths. Start the batch, retain each process
handle and raw output, then post one handoff comment per child after review.

Do not use an unbounded fan-out. Limit the batch to the number of independent
tasks that can be reviewed and integrated without losing track of their output.
If capacity is uncertain, start with the smallest useful batch and unlock more
only after the first handoffs are valid.

Never silently downgrade a requested parallel batch into writers sharing one
worktree, nor silently continue after a dependency cycle or file-scope overlap.
Record the planning conflict as a blocker and revise the issue graph.

## Handoff Validation And Recovery

A valid handoff contains all of: dispatch ID, status, changed paths, branch or
commit, verification command and result, evidence, blockers, and next owner.

| Failure | Required response |
| --- | --- |
| `dispatch-failed` | Record the launch error. Do not claim the worker began. |
| `worker-error` | Record exit/error evidence and preserve the worktree for inspection when useful. |
| `timeout` | Record the timeout and process state. Do not assume the worker made no changes. |
| `no-handoff` | Record that the process ended without the required payload; inspect output before retrying. |
| `misrouted-handoff` | Record the received ID, keep it out of the target issue, and locate or re-request the correct handoff. |

Never replace a failed attempt's evidence. A retry gets a new attempt number
and a comment explaining what changed: input, scope, CLI/model tier, timeout,
or environmental repair. Do not launch a second process for an active dispatch
unless the user explicitly instructs a takeover and the original worker is
stopped or isolated.

## Sequential Integration Gate

At a dependency gate, the integration owner must:

1. Read the exact child issue handoffs and their evidence.
2. Confirm required inputs are merged or available in its worktree.
3. Resolve conflicts without starting another writing worker in that worktree.
4. Run the parent acceptance checks.
5. Post one parent summary listing each child dispatch ID and final outcome.

This retains CAO's separation between asynchronous work and the blocking
handoff that synthesizes it, while GitHub Issues provide the durable history.

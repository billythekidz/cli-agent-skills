# Templates And Example

Replace angle-bracket fields. Keep issue bodies short enough that a worker can
act without reading unrelated discussion.

## Parent Plan Record

Use this as a parent Issue body in GitHub mode or as the plan section of
`.orchestrator/INDEX.md` in local Markdown mode.

```markdown
## Outcome
<user-visible or system outcome>

## Acceptance checks
- [ ] <observable check>
- [ ] <test or verification command>

## Boundaries
- In scope: <paths or components>
- Out of scope: <paths, services, or behavior>

## Coordination
- Integration owner: <CLI and model tier>
- Parent branch/worktree: <location>
- Child tasks:
  - [ ] #<number> | TASK-<number> <title>
  - [ ] #<number> | TASK-<number> <title>

## Decisions and risks
<confirmed assumptions, dependency order, and escalation points>
```

## Child Task Record

```markdown
Parent: #<parent-number> | `.orchestrator/INDEX.md`
Control plane: `github` | `local-markdown`

## Objective
<one observable outcome>

## Inputs and dependencies
- Depends on: #<number> or none
- Read: <commits, docs, or paths>

## Ownership
- CLI / model tier: <for example: codex-cli / balanced>
- Mode: `assign` | `handoff`
- Dispatch ID: `issue-<number>-attempt-<n>`
- Worktree / branch: <absolute path and branch>
- May change: <exclusive paths>
- Must not change: <excluded paths and control-plane state>

## Acceptance checks
- <verification command>
- <observable behavior>

## Required handoff
Return changed files, branch/commit, verification output, blockers, and a
proposed handoff record. Do not update the control plane directly.
```

## Parent Dispatch Ledger

Post this as a parent-issue comment in GitHub mode or keep it in `INDEX.md` in
local Markdown mode. Update it only after a reviewed handoff; child handoffs
remain the detailed event history.

```markdown
| Record | Dispatch | Mode | CLI / tier | Owns | Depends on | State |
| --- | --- | --- | --- | --- | --- | --- |
| #121 / TASK-001 | `issue-121-attempt-1` | `assign` | claude-cli / balanced | read-only | none | dispatched |
| #122 / TASK-002 | `issue-122-attempt-1` | `assign` | codex-cli / balanced | `tests/webhook-retry.*` | none | dispatched |
| #124 / TASK-004 | not dispatched | `handoff` | codex-cli / high | `src/webhooks/*` | #121, #122 | blocked |
```

## Worker Prompt

```text
Task record: #<number> <URL> | `.orchestrator/tasks/TASK-<number>.md`
Dispatch ID: issue-<number>-attempt-<n> | task-TASK-<number>-attempt-<n>
Mode: assign | handoff
Workspace: <absolute dedicated worktree>
Objective: <one outcome>
Own: <paths>
Do not change: <paths, control-plane state, deployment state>
Inputs: <dependency evidence>
Verify: <exact command>

Inspect before editing. Make the smallest in-scope change. Do not use GitHub
CLI or modify task/index/handoff records. Return: summary; changed files;
branch/commit; verification output; blockers; and a concise proposed handoff
record.
```

## Handoff Record

Post this as an Issue comment in GitHub mode or save it as
`.orchestrator/handoffs/TASK-<number>-attempt-<n>.md` in local Markdown mode.

```markdown
## Handoff
Dispatch: `issue-<number>-attempt-<n>`
Status: `ready for review` | `blocked` | `complete`

Changed: `<paths>`
Branch/commit: `<branch>` / `<sha or none>`
Verification: `<command>` -> `<result>`
Evidence: <key observation or link>
Blocker: <none or concrete blocker>
Next owner: <CLI/model tier and one next action>
```

## Bug Record

```markdown
## Summary
<one-sentence failure>

## Reproduction
1. <precondition>
2. <command or UI action>
3. <observed result>

## Expected vs actual
- Expected: <behavior>
- Actual: <behavior and exact error>

## Evidence
- Commit/environment: <value>
- Logs, screenshots, or test output: <link or excerpt>

## Scope and suggested route
- Suspected paths: <paths>
- Suggested CLI/model tier: <route and why>
```

## Local Markdown Layout

When GitHub is unavailable, initialize this tracked layout before parallel
dispatch. The supervisor owns `INDEX.md`; each worker only owns its assigned
task/handoff files.

```text
.orchestrator/
  INDEX.md
  tasks/TASK-001.md
  handoffs/TASK-001-attempt-1.md
  bugs/BUG-001.md
```

Use [file-fallback.md](file-fallback.md) for the full templates and the
authorized reconciliation procedure after GitHub recovers.

## Concrete Parallel-Then-Sequential Case

**Goal:** Prevent duplicate webhook processing after a retry. In GitHub mode,
create parent issue `#120` only after authorization, then use these child
issues:

| Issue | Scope | Route | Dependency |
| --- | --- | --- | --- |
| `#121` Reproduce and map retry path | Read-only investigation; return logs and likely owner paths | `assign`: `claude-cli` with a balanced or high reasoning tier | None |
| `#122` Define regression test | Only `tests/webhook-retry.*` | `assign`: `codex-cli` with a balanced tier | None |
| `#123` Document metric and alert expectation | Only `docs/observability/*` | `assign`: `antigravity-cli` with a fast or medium tier | None |
| `#124` Implement idempotency guard | Only `src/webhooks/*`; consume `#121` and merged `#122` | `handoff`: `codex-cli` with high or flagship tier | `#121`, `#122` |
| `#125` Integrate and verify | Integration worktree; no parallel writers | `handoff`: `claude-cli` or `codex-cli` with flagship tier | `#123`, `#124` |

1. Inspect existing issues and labels. Post the parent plan and dispatch ledger
   with the five linked tasks, their file boundaries, and attempt IDs.
2. Run `#121`, `#122`, and `#123` in separate worktrees at the same time. They
   have no shared writable path. Post each dispatch marker before launch.
3. Review the three handoffs. Merge or otherwise make the regression test from
   `#122` available before dispatching `#124`.
4. Dispatch `#124` with the exact reproduction evidence from `#121` and the
   newly available test. It owns only the implementation path.
5. After `#124` passes its check, dispatch `#125` sequentially. The integrator
   runs the full verification, resolves conflicts, and posts the parent summary.
6. Close only the children with recorded acceptance evidence, then close `#120`.

If any parallel worker exits without its dispatch ID and required handoff
fields, mark it `no-handoff`, preserve its output, and do not unlock `#124`.

In local Markdown mode, replace parent `#120` with `INDEX.md` and child issues
`#121` through `#125` with `TASK-001` through `TASK-005`. Save their handoffs
under `.orchestrator/handoffs/`; do not create GitHub issues until a user
authorizes reconciliation.

Example blocker handoff for `#124`:

```markdown
## Handoff
Dispatch: `issue-124-attempt-1`
Status: `blocked`

Changed: none
Branch/commit: `orchestrator/issue-124` / none
Verification: `npm test -- webhook-retry` -> test fixture cannot reproduce a
second delivery without the queue adapter from #121.
Evidence: retry path enters `src/webhooks/queue.ts` before the idempotency key
is read.
Blocker: #121 must confirm whether the queue adapter preserves event IDs.
Next owner: `claude-cli` / high reasoning tier to inspect the adapter contract.
```

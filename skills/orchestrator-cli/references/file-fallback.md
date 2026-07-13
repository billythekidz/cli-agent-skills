# Local Markdown Fallback

Use this fallback when the user requests offline work, `gh` is missing, OAuth
cannot finish, or a non-mutating GitHub/API probe fails. It is a control plane,
not a temporary scratchpad: keep its records readable, reviewable, and in the
target repository.

## Layout And Ownership

```text
.orchestrator/
  INDEX.md
  tasks/
    TASK-001.md
  handoffs/
    TASK-001-attempt-1.md
  bugs/
    BUG-001.md
```

- The supervisor owns `INDEX.md`: plan, task ledger, mode, and append-only
  events. Do not let parallel workers edit it.
- A worker owns only its `tasks/TASK-<number>.md` and its matching
  `handoffs/TASK-<number>-attempt-<n>.md`, or returns the handoff for the
  supervisor to write.
- Give each bug its own `bugs/BUG-<number>.md`. Link it from the relevant task
  and the index.
- Keep the directory tracked when multiple worktrees or agents need it. Commit
  the initial index before fan-out, following the repository's normal commit
  policy. Do not add it to `.gitignore`.

## Initialize The Index

Create the folders, then initialize `INDEX.md` from this template. Record the
actual GitHub failure so a later reconciliation has evidence.

```markdown
# Orchestration Index

## Control Plane
- Mode: `local-markdown`
- GitHub status: `unavailable`
- Last probe: `<timestamp>`
- Failure: `<gh/API error or offline reason>`
- Repository remote: `<origin URL or none>`

## Outcome
<user-visible or system outcome>

## Acceptance Checks
- [ ] <observable check>
- [ ] <verification command>

## Task Ledger
| Task | Dispatch | Mode | CLI / tier | Owns | Depends on | State |
| --- | --- | --- | --- | --- | --- | --- |
| TASK-001 | not dispatched | assign | codex-cli / balanced | `<paths>` | none | ready |

## Append-Only Events
- `<timestamp>` Initialized local fallback because `<failure>`.
```

Allocate monotonically increasing `TASK-001`, `TASK-002`, and `BUG-001` IDs.
Never reuse an ID after a task is cancelled or merged into another task.

## Task, Handoff, And Bug Records

Create one task file per executable unit:

```markdown
# TASK-001: <title>

Parent: `INDEX.md`
Status: `ready`
Mode: `assign`
Dispatch: `task-TASK-001-attempt-1`

## Objective
<one observable outcome>

## Inputs And Dependencies
- Depends on: `none` | `TASK-<number>`
- Read: <paths, commits, or handoff files>

## Ownership
- CLI / model tier: `<route>`
- Worktree / branch: `<absolute path>` / `<branch>`
- May change: `<exclusive paths>`
- Must not change: `<excluded paths and external state>`

## Acceptance Checks
- `<command>`
- <observable behavior>

## Handoff
- Expected file: `../handoffs/TASK-001-attempt-1.md`
```

Write a handoff as a separate file so it cannot conflict with the task plan:

```markdown
# Handoff: TASK-001

Dispatch: `task-TASK-001-attempt-1`
Status: `ready for review` | `blocked` | `complete`
Changed: `<paths>`
Branch/commit: `<branch>` / `<sha or none>`
Verification: `<command>` -> `<result>`
Evidence: <key observation, log path, or test output>
Blocker: <none or concrete blocker>
Next owner: <CLI/model tier and one next action>
```

Use the same field structure for a bug record as the GitHub bug template, with
`BUG-<number>` as its stable ID and links to affected `TASK-<number>` files.

## Parallel And Sequential Work

1. The supervisor writes task files and the initial index before dispatching.
2. Give each parallel writer a unique worktree and one task file. Do not let
   them edit `INDEX.md` or another worker's task/handoff file.
3. The supervisor verifies each handoff, appends an event to `INDEX.md`, and
   changes the relevant task row only after evidence exists.
4. Dispatch a dependent task only after its required handoff file is present
   and its output is merged or otherwise available in the dependent worktree.
5. Let the integration owner update the final task state and parent acceptance
   checks after the full verification succeeds.

An offline example maps the online webhook case as follows:

| Online record | Offline record |
| --- | --- |
| Parent issue `#120` | `.orchestrator/INDEX.md` |
| Child issue `#121` | `tasks/TASK-001.md` |
| Child comment | `handoffs/TASK-001-attempt-1.md` |
| Bug issue | `bugs/BUG-001.md` |

## Reconcile After GitHub Recovers

Do not automatically mirror local records back to GitHub. First confirm the
user wants reconciliation and inspect whether matching issues already exist.

1. Re-run `gh auth status` and the non-mutating issue-list probe.
2. Read `INDEX.md`, all task files, handoffs, and bug records. Identify the
   exact local IDs that need a GitHub counterpart.
3. Search existing GitHub issues before creating anything. Reuse a match only
   when title, scope, and acceptance checks agree.
4. With authorization, create or update one parent issue and child issues.
   Include `Local record: TASK-001` or `BUG-001` in each body and copy only the
   reviewed handoff summary, not raw transient logs.
5. Append a reconciliation event to `INDEX.md` with the GitHub URLs and the
   time. Keep local files as the audit trail until the user explicitly chooses
   to retire them.

If reconciliation is not authorized, remain in local Markdown mode even after
network access returns.

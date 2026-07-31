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
| Task | Dispatch | Mode | Task type / CLI / model | Owns | Depends on | State |
| --- | --- | --- | --- | --- | --- | --- |
| TASK-001 | not dispatched | assign | coding / antigravity-cli / gemini-3.6-flash-high | `<paths>` | none | ready |

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
- Fallback cursor: `1` for a new task; reset to `1` after the prior task is done
- Context budget: `standard` | `gpt-oss-131k`
- Agent-controlled cap: `60k tokens` | `not-applicable`
- Reserved buffer: `at least 60k tokens plus 11k slack` | `not-applicable`
- Evidence controls: `not-applicable` | `<bounded query/excerpt plan>`
- Token telemetry: `<provider-reported count>` | `unavailable`
- Slice: `<n/m>` | `not-applicable`
- Parent phase/task: `<parent record>` | `not-applicable`
- GPT-OSS micro-slice: `not-applicable` | `<parent dispatch ID>/gpt-oss-s<n>: one bounded outcome`
- Resource readiness: `ready` | `blocked-resource-unavailable`
- Required resources: `<name, scope, capability, check command, timeout, JSON result>`
- Availability probe: `pending` | `passed READY` | `failed` | `timed out`
- Probe evidence: `<command, duration, raw parsed response, normalized result, log tail>`
- Workspace mode: `current` | `dedicated-worktree` (explicit user approval only)
- Worktree authorization: `prohibited-by-default` | `user-approved <source>`
- Worktree disk preflight: `not-applicable` | `<existing worktrees and free-space evidence>`
- Worktree checkout: `not-applicable` | `<required sparse read/owned paths>`
- Shared cache policy: `not-applicable` | `<external safe cache names and paths>`
- Worktree size cap: `not-applicable` | `500000000 bytes`
- Worktree size measurement: `not-applicable` | `<JSON result from worktree_size.py>`
- Write access: `single current-workspace integrator` | `read-only parallel worker`
- Worktree / branch: `<current workspace or approved dedicated path>` / `<branch>`
- Cleanup: `not-applicable` | `pending` | `complete` | `cleanup-blocked`
- Main-repo evidence: `<control-plane record and persisted artifact paths>`
- Progress hash scope: `<owned paths and task artifacts>`
- Progress hash snapshot: `<hash and timestamp>`
- Timeout streak: `0` | `1` | `2+`
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
2. Run writer tasks sequentially in the current workspace. Parallel work is
   read-only by default. A parallel writer needs explicit user approval for a
   dedicated worktree and recorded disk preflight; do not let it edit `INDEX.md`
   or another worker's task/handoff file.
   Read-only parallel workers return evidence or a proposed patch; one
   integrator applies selected changes sequentially after the handoffs.
3. The supervisor verifies each handoff, appends an event to `INDEX.md`, and
   changes the relevant task row only after evidence exists.
4. Dispatch a dependent task only after its required handoff file is present
   and its output is merged or otherwise available in the dependent worktree.
5. Let the integration owner update the final task state and parent acceptance
   checks after the full verification succeeds. Use the current workspace for
   this sequential owner unless isolation is required.
6. After each dedicated-worktree task is verified, stop its process, confirm its
   commit/handoff/evidence/log summary/docs are persisted into the main
   workspace/main repository or local control-plane records, then remove it
   with `git worktree remove`, run `git worktree prune`, and record the cleanup
   result and destination paths. A cleanup failure blocks closure; do not delete
   the directory recursively.
7. For a timed-out task, compare the progress hash across timeout checks before
   stopping the CLI. If the hash still changes, keep the same route running. If
   it stays unchanged across consecutive checks, stop the CLI, run the short
   probe for the same route, and only then retry smaller or fall back.
   Use `scripts/task_progress_hash.py --root <workspace> --path <owned-path>`
   with the same selected paths for every comparison.

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

---
name: orchestrator-cli
description: "Coordinate parallel and sequential engineering work directly through GitHub Issues and local Claude Code, Codex, and Antigravity CLIs. Use when an agent needs to create a plan, split and route tasks, track handoffs, report bugs, recover blocked work, or synthesize results with `gh` first and a GitHub API fallback."
---

# GitHub CLI Task Orchestration

Use GitHub Issues as the source of truth for a multi-agent task. Delegate
bounded work directly through `claude`, `codex exec`, and `agy`; do not start
`cao-server`, call CAO, or use CAO handoff tools.

## Operating Boundaries

- Separate read actions from external writes. Inspect issues, labels, and code
  first. Create, edit, comment on, label, assign, or close an issue only when
  the user explicitly requests that workflow or has already authorized it.
- Keep each write scoped to the named repository and issue numbers. Do not
  change unrelated issues, labels, projects, branches, or pull requests.
- Give every writing worker a unique worktree, branch, and file ownership
  boundary. Run work in parallel only when both dependencies and write scopes
  are independent.
- Treat direct CLI permission-bypass defaults as local execution policy, not
  authorization to make arbitrary GitHub writes. Use them only in trusted,
  externally sandboxed environments.
- Require evidence before moving a task to review or done: changed files,
  commit or branch, verification output, and known blockers.

## CAO-Derived Dispatch Model

Adapt CAO's implemented `assign`, `handoff`, and `send_message` contracts
without starting `cao-server` or relying on CAO terminal IDs.

| CAO primitive | Direct CLI and GitHub Issue equivalent |
| --- | --- |
| `assign` | Launch an independent child task asynchronously in its own worktree. Record a unique dispatch ID before launch and require a structured handoff result. |
| `handoff` | Run a blocking gate and wait for its structured result before unlocking a dependent task. |
| `send_message` | The supervisor posts the reviewed child result as a durable comment on the exact task issue and links it to the parent. |

Use `issue-<number>-attempt-<n>` as the dispatch ID. It replaces CAO's worker
terminal ID and prevents results from being attached to the wrong task.

Before an `assign`, fail fast if the issue, parent, dependency inputs,
worktree, result location, or callback target is missing. Do not report a task
as successful merely because its child process launched or exited. A result
without the required handoff fields is `no-handoff`, not success.

CAO's workflow service currently reserves rather than implements its own
`parallel` mode. This skill therefore implements parallelism explicitly with a
GitHub Issue dependency graph, separate worktrees, durable dispatch comments,
and a single integration gate. See
[references/dispatch-protocol.md](references/dispatch-protocol.md).

## Bootstrap

Run this in the target repository before planning or dispatching. If GitHub
authentication is absent, start the OAuth browser flow yourself and wait for
the user to finish it before continuing.

```powershell
Get-Command gh -ErrorAction Stop
gh auth status
if ($LASTEXITCODE -ne 0) {
  gh auth login --web --git-protocol https
  if ($LASTEXITCODE -ne 0) { throw "GitHub OAuth login did not complete." }
  gh auth status
}

$repo = gh repo view --json nameWithOwner -q .nameWithOwner
if ($LASTEXITCODE -ne 0) { throw "Run inside the intended GitHub repository." }
```

If `gh` is unavailable, do not ask the user to paste a token. Install GitHub
CLI and use the OAuth flow above. Use direct GitHub REST or GraphQL only when a
token is already provided through `GH_TOKEN` or `GITHUB_TOKEN`.

## Orchestration Workflow

1. **Preflight**: Confirm authentication, repository, parent issue, current
   issue state, dependency inputs, and a unique worktree for every writer.
   Reject dependency cycles, overlapping writers, and an already-active
   dispatch ID before launching any child.
2. **Discover**: Read the repository, open issues, target issue comments, and
   existing labels. Reuse the repository's taxonomy instead of inventing one.
3. **Plan**: Create one parent issue only when authorized. State the outcome,
   acceptance checks, dependencies, file ownership, and a checklist linking
   each child task. Add a dispatch ledger comment so task IDs, modes, attempts,
   owners, and gates are durable.
4. **Split**: Make each child issue independently actionable. Record its
   parent, inputs, blocked-by issues, output, verification, owner CLI, model
   tier, and exclusive file scope. Use `Depends on #123` and `Unblocks #456`
   in the issue body when native hierarchy is unavailable.
5. **Route**: Select a CLI and model tier from
   [references/cli-model-routing.md](references/cli-model-routing.md). Route
   hard design decisions to a strong reasoning tier; route mechanical,
   evidence-only work to a faster tier.
6. **Dispatch**: Mark independent tasks as `assign` and launch them in parallel
   only after their preflight checks pass. Mark dependency gates as `handoff`;
   wait for their structured results before the next task. Give every worker
   the issue URL/number, dispatch ID, absolute worktree, allowed paths,
   prohibited paths, verification command, and required handoff fields. Use
   the matching direct CLI skill; workers should not update GitHub metadata
   unless that is explicitly part of their task.
7. **Track**: Post a concise handoff comment after reviewing the worker result.
   Mark blockers with evidence and the next decision needed. Start dependent
   tasks only after their inputs are merged or otherwise made available.
8. **Integrate**: Reserve one sequential owner for conflict resolution, final
   verification, and the parent-issue summary. Do not let multiple workers edit
   the integration worktree.
9. **Close**: Close only the exact child issue whose acceptance checks are met.
   Close the parent only after every required child is complete and the final
   verification is recorded.

Read [references/github-issue-operations.md](references/github-issue-operations.md)
before issuing GitHub writes or using the API fallback. Read
[references/dispatch-protocol.md](references/dispatch-protocol.md) before
launching a parallel worker or retrying one. Read
[references/templates-and-example.md](references/templates-and-example.md) for
copy-ready issue, handoff, bug-report, and worker-prompt templates plus a
parallel-then-sequential example.

## Dispatch Contract

Use this minimum prompt shape with every child agent:

```text
GitHub issue: <URL or #number>
Dispatch ID: issue-<number>-attempt-<n>
Mode: assign | handoff
Workspace: <absolute, dedicated worktree>
Objective: <one observable outcome>
Own: <allowed paths>
Do not change: <paths and external state>
Inputs: <dependency issues, commits, or documents>
Verify: <exact commands or checks>
Return: summary, changed files, branch/commit, verification evidence,
blockers, and a proposed GitHub handoff comment. Do not edit GitHub Issues.
```

For direct execution, follow the corresponding `claude-cli`, `codex-cli`, or
`antigravity-cli` skill. Their default unattended flags are intentionally
dangerous; keep the task one issue wide and inspect the result before the next
GitHub update.

For `assign`, capture the process handle and raw output in a temporary result
location owned by the supervisor. The handoff becomes durable only after the
supervisor verifies it and posts the structured comment. For `handoff`, wait
for the required fields before dispatching the dependent task.

## Status And Recovery

- Prefer existing status labels. With explicit approval, a small compatible
  taxonomy is `orchestrator:ready`, `orchestrator:active`,
  `orchestrator:blocked`, `orchestrator:review`, and `orchestrator:done`.
- Treat issue comments as an append-only execution journal. The normal
  transition is `planned -> ready -> dispatched -> running -> handoff ->
  verified -> done`; a failure goes to `blocked` with a reason, not directly to
  done.
- A blocked issue comment must state: blocker, evidence, impact, owner of the
  decision, and the smallest next action. Do not silently retry a failed task
  with a different model or CLI.
- Distinguish `dispatch-failed`, `worker-error`, `timeout`, and `no-handoff`.
  Preserve the previous dispatch ID and evidence; create a new attempt ID only
  after recording why a retry is justified.
- Never double-dispatch an active issue. Inspect its latest dispatch marker,
  process handle, branch, and worktree first. If those are unavailable, mark it
  blocked and re-plan instead of guessing whether it completed.
- If a worker overlaps another worker's scope, stop the later writer, preserve
  its evidence, and re-plan the ownership boundary or run it after integration.
- Use GitHub API fallback only for a missing high-level `gh` operation; retain
  the same repository and issue scope, then verify the API response.

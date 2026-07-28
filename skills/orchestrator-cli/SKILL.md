---
name: orchestrator-cli
description: "Coordinate parallel and sequential engineering work directly through GitHub Issues when available, or durable local Markdown records when GitHub is offline or failing, with local Claude Code, Codex, and Antigravity CLIs. Preserve provider-native session identity while planning, splitting, routing, tracking handoffs, recovering blocked work, or synthesizing results."
---

# Direct CLI Task Orchestration

Use GitHub Issues as the control plane when `gh` can read the target repository.
When GitHub is unavailable, use `.orchestrator/` Markdown records in the target
repository instead. Delegate bounded work headlessly by default through
`claude -p`, `codex exec`, and `agy -p`; do not start `cao-server`, call CAO, or
use CAO handoff tools.

## Operating Boundaries

- Choose exactly one control plane for an active run: GitHub Issues/API or
  `.orchestrator/` Markdown. Do not write both until an authorized reconcile.
- Separate read actions from external writes. Create, edit, comment on, label,
  assign, or close GitHub issues only when the user authorizes that workflow.
- Keep GitHub writes scoped to the named repository and issue numbers. Keep
  local writes scoped to the active repository's `.orchestrator/` directory.
- Give every writing worker a unique worktree, branch, and file ownership
  boundary. Run work in parallel only when both dependencies and write scopes
  are independent.
- Treat direct CLI permission-bypass defaults as local execution policy, not
  authorization to make arbitrary GitHub writes. Use them only in trusted,
  externally sandboxed environments.
- Require evidence before moving a task to review or done: changed files,
  commit or branch, verification output, and known blockers.
- Treat the provider-native session or conversation ID as the only identity for
  a continued CLI conversation. A dispatch ID, GitHub issue, local task file,
  process handle, worktree, and tmux session are related records, not native
  session IDs.

## CAO-Derived Dispatch Model

Adapt CAO's implemented `assign`, `handoff`, and `send_message` contracts
without starting `cao-server` or relying on CAO terminal IDs.

| CAO primitive | Direct CLI equivalent |
| --- | --- |
| `assign` | Launch an independent child task asynchronously in its own worktree. Record a unique dispatch ID before launch and require a structured handoff result. |
| `handoff` | Run a blocking gate and wait for its structured result before unlocking a dependent task. |
| `send_message` | The supervisor writes the reviewed child result to an exact GitHub issue comment or local handoff file, then links it to the parent record. |

Use `issue-<number>-attempt-<n>` in GitHub mode and
`task-TASK-<number>-attempt-<n>` in local Markdown mode. A result without the
matching dispatch ID and required handoff fields is `no-handoff`, not success.

CAO's workflow service currently reserves rather than implements its own
`parallel` mode. Implement parallelism explicitly with a dependency graph,
separate worktrees, durable task records, and a single integration gate. See
[references/dispatch-protocol.md](references/dispatch-protocol.md).

## Native Session Continuity

For every direct CLI task, keep a native session envelope in the active control
plane:

```text
Provider: claude-cli | codex-cli | antigravity-cli
Native session: <exact provider ID> | unavailable
Workspace/worktree: <absolute path>
Agent/model/profile: <selected value or default>
Process state: active | stopped | unavailable
Execution mode: headless-one-shot | headless-live | interactive-live | unavailable
Live transport: stdin JSONL | app-server stdio | original interactive PTY | unavailable
Headless transport: stdout/stderr pipes | one-shot | unavailable
Headless live transport: stdin JSONL | app-server stdio | unavailable
Current turn: <turn ID> | awaiting result | idle | unavailable
Session action: new | resumed
```

- Capture the ID after initial launch and include it in the reviewed handoff.
  Never substitute the dispatch ID, issue number, local task ID, or process
  handle.
- Prefer `headless-one-shot` for every independent `assign` and blocking
  `handoff`: Claude `-p --output-format json`, Codex `exec --json`, or
  Antigravity `-p --output-format json`/`stream-json`. Capture stdout/stderr,
  inspect the result, and let the process exit before writing the handoff.
- Use `headless-live` only when a follow-up must arrive before process exit:
  Claude `-p --input-format stream-json --output-format stream-json` or Codex
  `app-server` JSONL. These are still pipe-based and do not need a PTY.
- Use `interactive-live` only when the user requests a native UI or the
  provider has no suitable headless-live route. Antigravity `-i` and Codex TUI
  use the original console; an external controller needs a PTY only for that
  interactive UI. Never allocate a PTY for the default headless route.
- A process handle, PTY, and live transport are operational routes, not native
  session identity. Retain both the route and the native ID while a process is
  active.
- For an active task, inject the follow-up through its recorded live transport:
  Claude's `stdin JSONL` stream waits for a `result` boundary; Codex app-server
  gets its exact active turn ID from `turn/started`, then uses `turn/steer`, or queues a later
  `turn/start`; Antigravity interactive mode writes the prompt plus Enter to its
  original terminal/PTY. Use one writer per transport and record whether a
  prompt is queued or has completed.
- Antigravity `-p/--print` with `--output-format json` or `stream-json` is a
  pipe-based, one-shot run. It does not accept a later prompt in the same
  process; capture stdout/stderr and use exact `agy --conversation <id>` only
  after the process exits. A PTY is not required for this headless route.
- Antigravity `-i/--prompt-interactive` needs the original attached console for
  same-process follow-up. Allocate a PTY only when an external supervisor must
  drive that console; do not allocate one for print-mode JSON capture.
- Only after the original process is stopped or its live transport is lost,
  route a follow-up through the matching direct CLI skill using that exact ID:
  `claude -r <id>`, `codex exec resume <id>`, or
  `agy --conversation <id>`. A provider-native ID cannot cross providers.
- Do not use `claude -c`, `codex exec resume --last`, or `agy -c` for a routed
  task unless the user explicitly asks for the unique latest local conversation
  and no concurrent task can be selected by mistake.
- Do not resume a still-running process with a second CLI invocation. If no
  compatible live transport was retained, wait for or deliberately stop that
  process through the authorized task workflow first.
- If the CLI reports no stable ID, or exact resume fails, record the fact and
  start a new native session only with a factual handoff of the prior result.
  Mark the action `new`; never claim session continuity that did not occur.

## Select The Control Plane

If the user says the work must stay offline, enter local Markdown mode without
attempting GitHub. Otherwise, probe `gh` first. When `gh` lacks authentication,
start the OAuth browser flow. If `gh` cannot complete the probe, try direct
REST only when `GH_TOKEN` or `GITHUB_TOKEN` is already configured; otherwise
immediately use the local fallback.

```powershell
$mode = "local-markdown"
$repo = $null
$githubFailure = "GitHub was not probed."
$apiToken = if ($env:GH_TOKEN) { $env:GH_TOKEN } else { $env:GITHUB_TOKEN }
$gh = Get-Command gh -ErrorAction SilentlyContinue

if ($null -ne $gh) {
  gh auth status
  if ($LASTEXITCODE -ne 0) {
    gh auth login --web --git-protocol https
    gh auth status
  }

  if ($LASTEXITCODE -eq 0) {
    $repo = gh repo view --json nameWithOwner -q .nameWithOwner 2>$null
    if ($LASTEXITCODE -eq 0) {
      $probe = gh issue list --repo $repo --state open --limit 1 --json number 2>&1
      if ($LASTEXITCODE -eq 0) {
        $mode = "github"
      } else {
        $githubFailure = ($probe | Out-String).Trim()
      }
    } else {
      $githubFailure = "The target repository could not be resolved through gh."
    }
  } else {
    $githubFailure = "GitHub OAuth login did not complete."
  }
} else {
  $githubFailure = "gh is not installed."
}

# Direct REST is a last GitHub route. Never print or persist the token.
if ($mode -eq "local-markdown" -and $apiToken) {
  $origin = git remote get-url origin 2>$null
  if ($origin -match 'github\.com[/:]([^/]+/[^/]+?)(?:\.git)?$') {
    $apiRepo = $matches[1] -replace '\.git$', ''
    try {
      $headers = @{ Authorization = "Bearer $apiToken"; Accept = "application/vnd.github+json" }
      Invoke-RestMethod -Uri "https://api.github.com/repos/$apiRepo/issues?state=open&per_page=1" -Headers $headers -ErrorAction Stop | Out-Null
      $repo = $apiRepo
      $mode = "github-api"
    } catch {
      $githubFailure = "GitHub REST probe failed: $($_.Exception.Message)"
    }
  }
}

if ($mode -eq "local-markdown") {
  $orchestratorRoot = Join-Path (Get-Location) ".orchestrator"
  @($orchestratorRoot, "$orchestratorRoot\tasks", "$orchestratorRoot\handoffs", "$orchestratorRoot\bugs") |
    ForEach-Object { New-Item -ItemType Directory -Force -Path $_ | Out-Null }
  Write-Host "Using local Markdown orchestration: $githubFailure"
}
```

Use `gh api` only when `$mode` is `github` but a high-level `gh` command lacks
the operation. Use direct REST only when `$mode` is `github-api`. If either API
route fails, record the failure and switch to local Markdown mode; do not keep
retrying external writes.

Read [references/github-issue-operations.md](references/github-issue-operations.md)
only in `github` or `github-api` mode. Read
[references/file-fallback.md](references/file-fallback.md) before creating or
changing local task records.

## Orchestration Workflow

1. **Preflight**: Confirm the control plane, parent record, current task state,
   dependency inputs, and a unique worktree for every writer. Reject dependency
   cycles, overlapping writers, and an already-active dispatch ID.
2. **Discover**: In GitHub mode, read issues, comments, and labels. In local
   mode, read `.orchestrator/INDEX.md`, the target task files, handoffs, and
   bugs. Reuse the active control plane's taxonomy and IDs.
3. **Plan**: Create one parent GitHub issue or initialize the local index only
   when authorized. State the outcome, acceptance checks, dependencies, file
   ownership, and a linked child-task ledger.
4. **Split**: Make each child issue or `TASK-<number>.md` independently
   actionable. Record its parent, inputs, blocked-by tasks, output,
   verification, owner CLI, model tier, and exclusive file scope.
5. **Route**: Select a CLI and model tier from
   [references/cli-model-routing.md](references/cli-model-routing.md). Route
   hard design decisions to a strong reasoning tier; route mechanical,
   evidence-only work to a faster tier.
6. **Dispatch**: Mark independent tasks as `assign` and launch them in parallel
   only after preflight passes. Mark dependency gates as `handoff`; wait for
   their structured results before the next task. Prefer a headless one-shot
   command for both modes; select headless-live only when same-process follow-up
   is required, and interactive-live only as an explicit fallback. Give every
   worker the task record, dispatch ID, absolute worktree, allowed paths,
   prohibited paths, verification command, native-session action,
   execution/transport state, and required handoff fields.
7. **Track**: The supervisor reviews every worker result, then posts the
   handoff comment in GitHub mode or writes the matching handoff Markdown file
   in local mode. Record the native session envelope, mark blockers with
   evidence, and state the next decision needed.
8. **Integrate**: Reserve one sequential owner for conflict resolution, final
   verification, and the parent-record summary. Do not let multiple workers
   edit the integration worktree.
9. **Close**: Close only the exact GitHub child issue or mark only the exact
   local task as done when its acceptance checks are recorded. Do not reconcile
   the two control planes without explicit authorization.

Read [references/dispatch-protocol.md](references/dispatch-protocol.md) before
launching a parallel worker or retrying one. Read
[references/templates-and-example.md](references/templates-and-example.md) for
copy-ready issue, local-record, handoff, bug-report, and worker-prompt
templates plus a parallel-then-sequential example.

## Dispatch Contract

Use this minimum prompt shape with every child agent:

```text
Task record: <GitHub URL/#number or .orchestrator/tasks/TASK-<number>.md>
Dispatch ID: issue-<number>-attempt-<n> | task-TASK-<number>-attempt-<n>
Mode: assign | handoff
Workspace: <absolute, dedicated worktree>
Native session: new, then return provider and exact native ID
Process state: active | stopped
Execution mode: headless-one-shot | headless-live | interactive-live
Headless transport: <route or unavailable>
Live transport: <route or unavailable>
Current turn: <turn ID, result boundary, queued prompt, or unavailable>
Objective: <one observable outcome>
Own: <allowed paths>
Do not change: <paths and external state>
Inputs: <dependency records, commits, or documents>
Verify: <exact commands or checks>
Return: summary, changed files, branch/commit, verification evidence,
blockers, and a proposed handoff record. Do not change the control plane.
```

For direct execution, follow the corresponding `claude-cli`, `codex-cli`, or
`antigravity-cli` skill. Select its headless command first and record the
execution mode. Their default unattended flags are intentionally dangerous;
keep the task one record wide and inspect the result before the next
control-plane update.

For `assign`, capture the process handle and raw output in a temporary result
location owned by the supervisor. The handoff becomes durable only after the
supervisor verifies it and writes the GitHub comment or local handoff file. For
`handoff`, wait for the required fields before dispatching the dependent task.

## Status And Recovery

- Treat GitHub comments or local Markdown records as an append-only execution
  journal. The normal transition is `planned -> ready -> dispatched -> running
  -> handoff -> verified -> done`; a failure goes to `blocked` with a reason.
- In GitHub mode, prefer existing labels. With explicit approval, a compatible
  taxonomy is `orchestrator:ready`, `orchestrator:active`,
  `orchestrator:blocked`, `orchestrator:review`, and `orchestrator:done`.
- In local mode, the supervisor alone updates `INDEX.md`; each worker owns only
  its task/handoff file. Commit the initial index before parallel workers need
  it, following the repository's normal commit policy.
- A blocked record must state blocker, evidence, impact, decision owner, and
  smallest next action. Do not silently retry a failed task with a different
  model or CLI.
- Distinguish `dispatch-failed`, `worker-error`, `timeout`, and `no-handoff`.
  Preserve previous evidence; create a new attempt ID only after recording why
  a retry is justified.
- Never double-dispatch an active task. Inspect its latest dispatch marker,
  process handle, branch, worktree, and native session envelope first. If those
  are unavailable, mark it blocked and re-plan instead of guessing whether it
  completed.
- For an active task with a compatible retained transport, inject the follow-up
  into that same process and update its queued/current-turn state. Do not make
  a second CLI process merely to deliver the prompt.
- For a stopped task that needs a follow-up, resume its exact recorded native
  session before considering a new attempt. If that ID is unavailable or resume
  fails, preserve the error and prior evidence, then launch a clearly labeled
  new session with a factual handoff.
- If GitHub returns, follow the reconciliation procedure in
  [references/file-fallback.md](references/file-fallback.md). Do not
  automatically create duplicate issues, comments, or labels.

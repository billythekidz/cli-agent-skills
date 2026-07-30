---
name: orchestrator-cli
description: "Coordinate parallel and sequential engineering work directly through GitHub Issues when available, or durable local Markdown records when GitHub is offline or failing, with local Claude Code, Codex, and Antigravity CLIs. Preserve provider-native session identity while planning, splitting, routing, tracking handoffs, recovering blocked work, or synthesizing results."
---

# Direct CLI Task Orchestration

Use GitHub Issues as the control plane when `gh` can read the target repository.
When GitHub is unavailable, use `.orchestrator/` Markdown records in the target
repository instead. Delegate bounded work headlessly by default through
`claude -p --output-format json --dangerously-skip-permissions`,
`codex exec --dangerously-bypass-approvals-and-sandbox --json`, and
`agy -p --output-format json --mode accept-edits --dangerously-skip-permissions`;
do not start `cao-server`, call CAO, or use CAO handoff tools.

## Operating Boundaries

- Choose exactly one control plane for an active run: GitHub Issues/API or
  `.orchestrator/` Markdown. Do not write both until an authorized reconcile.
- Separate read actions from external writes. Create, edit, comment on, label,
  assign, or close GitHub issues only when the user authorizes that workflow.
- Keep GitHub writes scoped to the named repository and issue numbers. Keep
  local writes scoped to the active repository's `.orchestrator/` directory.
- Use `workspace=current` by default and prohibit creating a dedicated
  worktree unless the user explicitly authorizes one for the named task.
  Parallel writers, uncertain ownership, a dirty workspace, or an integration
  gate are not implicit exceptions: serialize writers through `handoff`
  records instead. Run parallel work only when every concurrent task is
  read-only or the user has approved the required dedicated worktrees.
- For parallel work without worktrees, designate exactly one sequential
  integrator as the source-tree writer. Other concurrent workers are
  read-only: they inspect, analyze, review, or run only non-mutating checks and
  return findings or a proposed patch in their handoff. They must not apply a
  patch, run formatters, update lockfiles, or write generated artifacts inside
  the repository. The integrator applies selected proposals and verifies them
  one at a time in the current workspace.
- After a dedicated-worktree task reaches verified/done, stop its CLI process,
  persist its commit, handoff, evidence, log summary, and required docs into
  the main workspace/main repository or active GitHub control plane, verify the
  destination, then remove the worktree and prune its branch/metadata. Never
  leave completed CLI worktrees behind. If cleanup is unsafe because a process
  is active, artifacts are not persisted, or uncommitted changes lack a
  recorded disposition, keep it and mark the task blocked instead of deleting
  user work.
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
| `assign` | Launch an independent child task asynchronously in the current workspace or, when isolation is required, its own worktree. Record the workspace decision before launch and require a structured handoff result. |
| `handoff` | Run a blocking gate and wait for its structured result before unlocking a dependent task. |
| `send_message` | The supervisor writes the reviewed child result to an exact GitHub issue comment or local handoff file, then links it to the parent record. |

Use `issue-<number>-attempt-<n>` in GitHub mode and
`task-TASK-<number>-attempt-<n>` in local Markdown mode. A result without the
matching dispatch ID and required handoff fields is `no-handoff`, not success.

CAO's workflow service currently reserves rather than implements its own
`parallel` mode. Implement parallelism explicitly with a dependency graph,
separate worktrees, durable task records, and a single integration gate. See
[references/dispatch-protocol.md](references/dispatch-protocol.md).

## Workspace And Cleanup Policy

Choose `workspace=current` for a simple, sequential, one-writer task. Reuse the
current branch/workspace when no other worker is active and the task's allowed
paths do not overlap another active writer. This avoids the cost of creating
and indexing another Git worktree.

`workspace=dedicated-worktree` is prohibited by default. It may be used only
after the user explicitly authorizes a dedicated worktree for the named task.
Parallel writers, uncertain ownership, and a dirty current workspace require a
sequential handoff plan; they do not grant an exception. Before an approved
exception, inspect existing worktrees and available disk space, record the
authorization and preflight result, and confirm the main workspace has enough
capacity for the selected sparse checkout and its generated dependencies. A
full-source worktree is never permitted. If the task cannot be bounded to
sparse paths, block the worktree request and run it sequentially in the current
workspace. Every approved dedicated worktree gets an explicit path, branch,
owner, sparse path list, disk preflight, authorization reference, cache policy,
size measurement, and cleanup record. A sparse worktree's allocated filesystem
size must not exceed 500 MB (500,000,000 bytes), excluding external symlink
targets.

For an approved thin worktree, create it without an initial checkout, enable
per-worktree Git configuration, configure sparse-checkout for only the task's
read/owned paths, verify the configured sparse path list, then perform its
first checkout. `git worktree add` without `--no-checkout` is forbidden in an
orchestrated run. Keep dependency caches outside the repository and share only
caches designed for concurrent reuse (such as the package manager's
content-addressed store or Unity Accelerator). Never junction, symlink, or
otherwise share a mutable repo-local `node_modules`, Unity `Library`, build
output, or tool cache between concurrent writers. Unity editor tasks normally
remain sequential in the current workspace because `Library` is a mutable
imported-asset database.

Immediately after sparse checkout and before launching the CLI, run
`python <orchestrator-cli-skill-dir>/scripts/worktree_size.py --path <path>
--max-bytes 500000000`. Record its JSON result. If the command reports
`allocated_bytes` over 500 MB, do not dispatch the worker; remove that newly created, untouched
over 500 MB, do not dispatch the worker; remove that newly created, untouched
worktree with `git worktree remove <path>`, run `git worktree prune`, and use a
sequential current-workspace handoff instead.

Cleanup is mandatory for a completed dedicated worktree:

1. Wait for the CLI process and supervisor route to stop; never remove an
   active process's workspace.
2. Verify the handoff, changed files, commit/branch, tests, and blocker state.
3. Persist the commit reference, handoff, evidence/log summary, and required
   documentation into the main workspace/main repository or active GitHub
   control plane. Do not leave the only copy inside the worktree.
4. Confirm the destination paths exist and `git status --short` is empty, or
   record the exact disposition of every remaining change before cleanup.
5. Run `git worktree remove <dedicated-path>` and then `git worktree prune` from
   the main repository. Remove the task branch only when it is merged or its
   disposition is explicitly recorded.
6. Record cleanup status, removed path, branch disposition, persisted artifact
   paths, and any failure in the task journal.

Do not use recursive filesystem deletion as a substitute for `git worktree
remove`. If cleanup fails, preserve the path and mark `cleanup-blocked`; do not
claim the task is fully closed.

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
  `handoff`: Claude `-p --output-format json --dangerously-skip-permissions`,
  Codex `exec --dangerously-bypass-approvals-and-sandbox --json`, or
  Antigravity `-p --output-format json`/`stream-json` with
  `--mode accept-edits --dangerously-skip-permissions`. Capture stdout/stderr,
  inspect the result, and let the process exit before writing the handoff.
- Use `headless-live` only when a follow-up must arrive before process exit:
  Claude `-p --input-format stream-json --output-format stream-json
  --dangerously-skip-permissions` or Codex `app-server` JSONL started with
  `-c 'approval_policy="never"' -c 'sandbox_mode="danger-full-access"'`.
  These are still pipe-based and do not need a PTY.
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

## Optional Live Process Supervisor

For unattended local runs that need follow-up prompts injected into an already
running process, use the bundled lightweight supervisor instead of CAO:

```bash
python <orchestrator-cli-skill-dir>/scripts/orchestrator_supervisor.py --json doctor

python <orchestrator-cli-skill-dir>/scripts/orchestrator_supervisor.py --json start \
  --dispatch-id task-TASK-12-attempt-1 \
  --provider claude-cli \
  --protocol claude-stream-json \
  --workspace /absolute/worktree \
  -- claude -p --input-format stream-json --output-format stream-json \
     --dangerously-skip-permissions

python <orchestrator-cli-skill-dir>/scripts/orchestrator_supervisor.py --json send \
  task-TASK-12-attempt-1 "Use the same live session and add this evidence."

python <orchestrator-cli-skill-dir>/scripts/orchestrator_supervisor.py --json status \
  task-TASK-12-attempt-1
```

The supervisor is a single-machine localhost JSONL daemon started on demand. It
stores durable records in `.orchestrator/runtime/supervisor.sqlite3` and raw
process logs in `.orchestrator/runtime/logs/*.jsonl`. Claude and Codex remain
on their protocol-native stdio transports. Antigravity's interactive PTY route
uses an isolated tmux session on macOS or the optional pywinpty/ConPTY bridge
on Windows; the supervisor still uses stdlib `subprocess`, `socket`, and
`sqlite3` for its daemon, database, and JSONL control protocol.

Use the supervisor only as the retained live route. The durable control plane is
still GitHub Issues or `.orchestrator/` Markdown, and the provider-native
session ID is still the provider's ID. A macOS tmux route can be reattached
from its recorded socket after a supervisor restart; if reattachment fails, or
the supervisor is not reachable, mark the route `live-transport-unavailable`.
Do not claim prompt injection into a route that was not reattached.

Supported prompt protocols:

| Protocol | Use for | Injection shape |
| --- | --- | --- |
| `text` | Test workers or CLIs that accept plain stdin lines | `prompt + "\n"` |
| `jsonl` | Generic JSONL workers | `{"type":"user","text":...}` |
| `claude-stream-json` | One retained Claude stream-json process | JSONL user message |
| `codex-app-server` | One retained Codex app-server process | `turn/steer` when current turn is known, otherwise `turn/start` |
| `antigravity-pty` | One retained Antigravity interactive process | prompt text plus PTY Enter (`CR`) |

### Antigravity PTY prerequisites

The PTY backend is selected with `--protocol antigravity-pty`. Leave
`--transport` as `stdio` (the default) for Claude and Codex. For Antigravity,
`stdio` is automatically resolved to `auto`: macOS selects an isolated tmux
session and Windows selects pywinpty/ConPTY.

Check prerequisites before starting a live route:

```bash
python <orchestrator-cli-skill-dir>/scripts/orchestrator_supervisor.py --json doctor
```

On macOS, the managed PTY requires tmux. If `doctor` reports it missing, install
it with Homebrew and verify it:

```bash
brew install tmux
tmux -V
```

On Windows PowerShell, install the optional Python PTY bridge if `doctor`
reports it missing:

```powershell
py -m pip install pywinpty
```

Run `doctor` again after installation. The supervisor never auto-installs
dependencies. If the selected backend is unavailable, start returns
`live-transport-unavailable`; do not claim that a prompt entered Antigravity.
The durable native conversation ID remains separate from the tmux session,
Windows PTY handle, dispatch ID, and PID.

Example Antigravity route:

```bash
python <orchestrator-cli-skill-dir>/scripts/orchestrator_supervisor.py --json start \
  --dispatch-id task-TASK-12-attempt-1 \
  --provider antigravity-cli \
  --protocol antigravity-pty \
  --transport auto \
  --workspace /absolute/worktree \
  -- agy --sandbox -i "Inspect the failing test and wait for follow-up."
```

If the supervisor handle or PTY is lost, record
`live-transport-unavailable`, stop or wait for the original process, then use
the original terminal/PTY described in `antigravity-cli` or the exact
`agy --conversation <id>` recovery route from that skill.

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
   dependency inputs, and the workspace decision. Use `current` unless the
   user explicitly authorized a named `dedicated-worktree`; otherwise serialize
   writers through handoffs. For an approved worktree, record free-space and
   existing-worktree evidence before creation. Reject dependency cycles,
   overlapping writers, and an already-active dispatch ID.
2. **Discover**: In GitHub mode, read issues, comments, and labels. In local
   mode, read `.orchestrator/INDEX.md`, the target task files, handoffs, and
   bugs. Reuse the active control plane's taxonomy and IDs.
3. **Plan**: Create one parent GitHub issue or initialize the local index only
   when authorized. State the outcome, acceptance checks, dependencies, file
   ownership, and a linked child-task ledger.
4. **Split**: Make each child issue or `TASK-<number>.md` independently
   actionable. Record its parent, inputs, blocked-by tasks, output,
   verification, owner CLI, model tier, and exclusive file scope.
5. **Route**: Classify the task as coding/development, review, or planning and
   select the ordered fallback chain from
   [references/cli-model-routing.md](references/cli-model-routing.md). Probe
   availability with the short `READY` check in
   [references/availability-probe.md](references/availability-probe.md) before
   dispatching the real task prompt. If the selected route is
   `antigravity-cli / gpt-oss-120b-medium`, enforce its 131k context budget:
   submit at most 80k input tokens, reserve at least 40k tokens for system,
   tool, and output context, and split any larger task into sequential slices
   before dispatch. Only models in the classified task's chain
   are permitted; do not route to any other model or tier. If the probe fails,
   times out, reports quota/rate-limit/capacity/authentication failure, or the
   CLI cannot start, record the probe evidence and move to the next route before
   sending the real task prompt. If the real task later fails, record that task
   failure separately and create the next attempt with a factual handoff; never
   silently downgrade or double-dispatch an active task.
   The fallback cursor is task-scoped: after a task reaches `verified` or `done`,
   reset it to the first route for the next task. A successful fallback must
   never become a sticky provider/model default.
6. **Dispatch**: Mark independent tasks as `assign` and launch writer tasks
   sequentially in the current workspace by default. Parallelize only read-only
   tasks, or writers with user-approved dedicated worktrees whose disk
   preflight passed. Mark dependency gates as `handoff`; wait for their
   structured results before the next task. Prefer a headless one-shot
   command for both modes; select headless-live only when same-process follow-up
   is required, and interactive-live only as an explicit fallback. Give every
   worker the task record, dispatch ID, workspace mode/path, allowed paths,
   prohibited paths, verification command, native-session action,
   execution/transport state, and required handoff fields.
   When follow-up injection may be needed, select headless-live first and launch
   through `<orchestrator-cli-skill-dir>/scripts/orchestrator_supervisor.py start`
   when a managed process is required; record the runtime log path in the
   dispatch record. For a long-running real-task prompt, record a progress hash
   over the task's owned paths and re-check it on timeout before stopping the
   process. Use interactive-live/PTY only as the explicit fallback.
7. **Track**: The supervisor reviews every worker result, then posts the
   handoff comment in GitHub mode or writes the matching handoff Markdown file
   in local mode. Record the native session envelope, mark blockers with
   evidence, and state the next decision needed. A timeout is a progress check:
   if the progress hash is still changing, keep the same CLI running; only when
   the hash is unchanged across consecutive timeout checks may the supervisor
   stop the CLI, run the short availability probe, and consider breaking the
   task into a smaller retry. Fallback to another model/CLI only after probe
   evidence or a classified quota/unavailable failure shows the current route
   cannot continue.
8. **Integrate**: Reserve one sequential owner for conflict resolution, final
   verification, and the parent-record summary. Reuse the current workspace for
   this owner unless isolation is required; do not let multiple workers edit
   the same workspace.
9. **Close**: Close only the exact GitHub child issue or mark only the exact
   local task as done when its acceptance checks and dedicated-worktree cleanup
   status are recorded. Do not reconcile the two control planes without
   explicit authorization.

Read [references/dispatch-protocol.md](references/dispatch-protocol.md) before
launching a parallel worker or retrying one. Read
[references/templates-and-example.md](references/templates-and-example.md) for
copy-ready issue, local-record, handoff, bug-report, and worker-prompt
templates plus a parallel-then-sequential example.

Read [references/fresh-start-without-integrations.md](references/fresh-start-without-integrations.md)
whenever provider startup reaches the 300-second timeout.

## Dispatch Contract

Use this minimum prompt shape with every child agent:

```text
Task record: <GitHub URL/#number or .orchestrator/tasks/TASK-<number>.md>
Dispatch ID: issue-<number>-attempt-<n> | task-TASK-<number>-attempt-<n>
Mode: assign | handoff
Workspace mode: current | dedicated-worktree
Workspace: <absolute current workspace or dedicated worktree>
Worktree authorization: prohibited-by-default | user-approved <source>
Worktree disk preflight: <not-applicable | existing worktrees and free-space evidence>
Worktree checkout: <not-applicable | required sparse paths>
Shared cache policy: <not-applicable | external safe cache names and paths>
Worktree size cap: <not-applicable | 500000000 bytes>
Worktree size measurement: <not-applicable | JSON result from worktree_size.py>
Write access: <single current-workspace integrator | read-only parallel worker>
Cleanup: required for dedicated-worktree; pending | complete | cleanup-blocked
Main-repo evidence: <control-plane record and persisted artifact paths>
Native session: new, then return provider and exact native ID
Process state: active | stopped
Execution mode: headless-one-shot | headless-live | interactive-live
Headless transport: <route or unavailable>
Live transport: <route or unavailable>
Current turn: <turn ID, result boundary, queued prompt, or unavailable>
Task type: coding | review | plan
Fallback chain: <ordered CLI/model routes>
Fallback cursor: <1-based route position for this task>
Context budget: standard | gpt-oss-131k
Input cap: <80k tokens or not-applicable>
Reserved buffer: <at least 40k tokens or not-applicable>
Slice: <n/m or not-applicable>
Availability probe: <pending | passed READY | failed with evidence>
Probe budget: <short wall-clock limit, recommended 30s>
Progress hash: <owned-path hash snapshot and timestamp>
Timeout action: <keep-running | stop-and-probe | fallback-after-quota>
Selected CLI/model: <route used for this attempt>
Previous fallback attempts: <none or recorded attempt summaries>
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
the flags are part of the executable command, not optional prose. For
Codex `app-server`, pass the same unattended policy as config overrides:
`-c 'approval_policy="never"' -c 'sandbox_mode="danger-full-access"'`.
Keep the task one record wide and inspect the result before the next
control-plane update.

For `assign`, capture the process handle and raw output in a temporary result
location owned by the supervisor. The handoff becomes durable only after the
supervisor verifies it and writes the GitHub comment or local handoff file. For
`handoff`, wait for the required fields before dispatching the dependent task.

### 300-second startup timeout and fresh recovery

Give a provider at most 300 seconds to reach its first usable prompt. If it is
still `Loading`, `connecting`, or `Initializing`, classify the attempt as
`timeout` / `startup-blocked-by-integrations`, preserve the supervisor and
provider log tails, stop the process, and do not send another prompt through
that route. Run
[fresh-start-without-integrations.md](references/fresh-start-without-integrations.md)
with an empty MCP/plugin configuration. Only after that probe succeeds may you
create a new dispatch/native session with a factual handoff; never claim that
the timed-out native session continued.

The supervisor readiness wait is separately configurable with
`ORCHESTRATOR_SUPERVISOR_CONNECT_TIMEOUT=300`. This changes daemon readiness,
not the provider's own startup behavior. A timeout is not permission to retry
indefinitely or to silently re-enable a failing integration.

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
  smallest next action. The routing fallback chain is an explicit exception to
  the normal no-silent-retry rule: use it only after recording the failed
  attempt's CLI/model, native-session state, exact error, and reason.
- A dedicated worktree is not complete until its CLI process is stopped, its
  commit/handoff/evidence/log summary/docs are persisted to the main workspace,
  main repository, or active GitHub control plane, and cleanup status is
  recorded. A simple/sequential task using the current workspace has no
  worktree to remove.
- A timeout alone is not proof of a stuck CLI. Compare progress hashes over the
  task's owned paths between timeout checks. Keep the CLI running while the hash
  changes; only stop it after unchanged consecutive timeout checks or an
  explicit provider failure such as quota exhaustion.
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

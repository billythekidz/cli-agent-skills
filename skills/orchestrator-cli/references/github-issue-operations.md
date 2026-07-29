# GitHub Issue Operations

Use this reference only after the control-plane probe selected `github` or
`github-api` mode. Use the local Markdown fallback when `gh` is missing, OAuth
cannot complete, or a non-mutating GitHub request fails. The `gh` commands
below were verified against GitHub CLI 2.85.0 on 2026-07-13. Re-run the
relevant `--help` command if the installed CLI changes.

## GitHub CLI Mode

Use this section only when the control plane is `github`. If it is
`github-api`, skip directly to [Direct REST Fallback](#direct-rest-fallback).

### Authenticate And Inspect

```powershell
Get-Command gh -ErrorAction Stop
gh auth status
if ($LASTEXITCODE -ne 0) {
  gh auth login --web --git-protocol https
  if ($LASTEXITCODE -ne 0) { throw "Complete OAuth login before continuing." }
  gh auth status
}

$repo = gh repo view --json nameWithOwner -q .nameWithOwner
$number = 123 # Replace with the confirmed issue number.
gh repo view --repo $repo --json nameWithOwner,url,defaultBranchRef,isPrivate
gh issue list --repo $repo --state open --limit 100 --json number,title,body,labels,assignees,state,updatedAt,url
gh label list --repo $repo --limit 100 --json name,description,color
gh issue view $number --repo $repo --comments
```

If `gh auth login` or the issue-list request fails, do not issue a create,
edit, comment, label, or close command. Record the failure in
`.orchestrator/INDEX.md` and follow
[file-fallback.md](file-fallback.md).

Use a parent issue for the outcome and linked child issues for executable work.
Before creating a label, inspect existing labels. Reuse them when possible.

### Authorized GitHub CLI Writes

Only run these after confirming the exact repository and issue scope with the
user. Keep the parent/child relationship, dependency, acceptance checks, and
file boundary in every task issue.

```powershell
$body = @'
<issue body from the template>
'@
$number = 123 # Replace with the confirmed issue number.

gh issue create --repo $repo --title "<title>" --body $body --label "<existing-label>"
gh issue edit $number --repo $repo --add-label "<existing-label>" --remove-label "<existing-label>"
gh issue comment $number --repo $repo --body "<handoff or blocker update>"
gh issue close $number --repo $repo --reason completed --comment "<final evidence>"
```

If the user authorizes a new label taxonomy, create only the required labels:

```powershell
gh label create "orchestrator:ready" --repo $repo --color "1D76DB" --description "Ready for a delegated task"
```

### Dispatch Journal Comments

Use comments as an append-only execution journal. Post a unique marker before
launching a direct CLI worker, then post the reviewed handoff with the same ID.
This prevents duplicate workers and misrouted results.

```powershell
$dispatch = @'
<!-- orchestrator-cli:dispatch:issue-123-attempt-1 -->
## Dispatch
Dispatch: `issue-123-attempt-1`
Mode: `assign`
CLI/model: `antigravity-cli` / `gemini-3.6-flash-high`
Task type: `coding`
Fallback chain: `antigravity-cli / gemini-3.6-flash-high` -> `antigravity-cli / gpt-oss-120b-medium` -> `claude-cli / sonnet` -> `codex-cli / gpt-5.6-luna-medium` -> `claude-cli / haiku`
Worktree: `<absolute path>`
Owns: `<exclusive paths>`
Depends on: `<issue numbers or none>`
State: `dispatched`
'@

gh issue comment 123 --repo $repo --body $dispatch
gh issue view 123 --repo $repo --comments
```

Before retrying an issue, read its comments and confirm there is no active
dispatch marker. Use a new attempt number only after recording the previous
attempt's error, timeout, or missing handoff.

### `gh api` Fallback

Use `gh api` when the high-level command lacks the needed operation. It uses
the same authenticated `gh` session and expands `{owner}` and `{repo}` for the
current repository. The REST issues endpoint also returns pull requests, so
ignore objects with a `pull_request` field when listing issues.

```powershell
# Read open issues through REST. Filter pull_request objects in the result.
gh api --paginate --slurp "repos/{owner}/{repo}/issues?state=open&per_page=100"

# Create a scoped issue or comment.
gh api "repos/{owner}/{repo}/issues" -f title="<title>" -f body="<body>"
gh api "repos/{owner}/{repo}/issues/<number>/comments" -f body="<handoff>"

# Update only the named issue after confirming the target.
gh api --method PATCH "repos/{owner}/{repo}/issues/<number>" -f state=closed
```

## Direct REST Fallback

Use this section only when the control plane is `github-api`: `gh` is
unavailable or broken, but `GH_TOKEN` or `GITHUB_TOKEN` already passed the
non-mutating REST probe. Do not ask the user to paste a token or write it to a
file. `$repo` must be the exact `owner/repository` selected by that probe.

```powershell
$token = if ($env:GH_TOKEN) { $env:GH_TOKEN } else { $env:GITHUB_TOKEN }
$headers = @{ Authorization = "Bearer $token"; Accept = "application/vnd.github+json" }
$apiBase = "https://api.github.com/repos/$repo"

# Read. The REST issues endpoint includes pull requests; ignore pull_request objects.
Invoke-RestMethod -Uri "$apiBase/issues?state=open&per_page=100" -Headers $headers

# Authorized writes only.
$newIssue = @{ title = "<title>"; body = "<body>" } | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri "$apiBase/issues" -Headers $headers -ContentType "application/json" -Body $newIssue

$comment = @{ body = "<handoff>" } | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri "$apiBase/issues/<number>/comments" -Headers $headers -ContentType "application/json" -Body $comment

$close = @{ state = "closed" } | ConvertTo-Json
Invoke-RestMethod -Method Patch -Uri "$apiBase/issues/<number>" -Headers $headers -ContentType "application/json" -Body $close
```

Prefer installing `gh` and using `gh auth login --web` when possible so OAuth
is handled by the system credential store.

If `gh api` itself fails because GitHub is unavailable, stop external retries
and enter local Markdown mode. Do not treat a partial GitHub response as proof
that the matching issue/comment write succeeded.

## Coordination Rules

- Use issue comments for durable decisions, handoffs, blockers, and final
  evidence. Do not rely on terminal output or ephemeral chat messages.
- Use a child issue checklist in the parent body for progress; update it only
  after the corresponding child has evidence.
- Do not assign a GitHub user merely to represent a local CLI unless that user
  explicitly accepts the assignment. Store the selected CLI/model in the issue
  body or handoff comment instead.
- Do not use closing keywords such as `Fixes #123` in a worker commit until the
  integration owner has confirmed the intended close behavior.
- Do not reconcile local Markdown records to GitHub automatically after an
  outage. Follow the authorized reconciliation procedure in
  [file-fallback.md](file-fallback.md).

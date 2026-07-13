# GitHub Issue Operations

Use `gh` first. The commands below were verified against GitHub CLI 2.85.0 on
2026-07-13. Re-run the relevant `--help` command if the installed CLI changes.

## Authenticate And Inspect

```powershell
Get-Command gh -ErrorAction Stop
gh auth status
if ($LASTEXITCODE -ne 0) {
  gh auth login --web --git-protocol https
  if ($LASTEXITCODE -ne 0) { throw "Complete OAuth login before continuing." }
  gh auth status
}

$repo = gh repo view --json nameWithOwner -q .nameWithOwner
gh repo view --repo $repo --json nameWithOwner,url,defaultBranchRef,isPrivate
gh issue list --repo $repo --state open --limit 100 --json number,title,body,labels,assignees,state,updatedAt,url
gh label list --repo $repo --limit 100 --json name,description,color
gh issue view <number> --repo $repo --comments
```

Use a parent issue for the outcome and linked child issues for executable work.
Before creating a label, inspect existing labels. Reuse them when possible.

## Authorized Writes

Only run these after confirming the exact repository and issue scope with the
user. Keep the parent/child relationship, dependency, acceptance checks, and
file boundary in every task issue.

```powershell
$body = @'
<issue body from the template>
'@

gh issue create --repo $repo --title "<title>" --body $body --label "<existing-label>"
gh issue edit <number> --repo $repo --add-label "<existing-label>" --remove-label "<existing-label>"
gh issue comment <number> --repo $repo --body "<handoff or blocker update>"
gh issue close <number> --repo $repo --reason completed --comment "<final evidence>"
```

If the user authorizes a new label taxonomy, create only the required labels:

```powershell
gh label create "orchestrator:ready" --repo $repo --color "1D76DB" --description "Ready for a delegated task"
```

## Dispatch Journal Comments

Use comments as an append-only execution journal. Post a unique marker before
launching a direct CLI worker, then post the reviewed handoff with the same ID.
This prevents duplicate workers and misrouted results.

```powershell
$dispatch = @'
<!-- orchestrator-cli:dispatch:issue-123-attempt-1 -->
## Dispatch
Dispatch: `issue-123-attempt-1`
Mode: `assign`
CLI/model: `codex-cli` / `balanced`
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

## GitHub API Fallback

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

When `gh` is not available, use the direct GitHub API only if `GH_TOKEN` or
`GITHUB_TOKEN` is already configured. Do not request a token in chat or add one
to a file. Prefer installing `gh` and using `gh auth login --web` so OAuth is
handled by the system credential store.

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

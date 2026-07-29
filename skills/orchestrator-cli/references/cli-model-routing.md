# CLI And Model Routing

Choose by task shape, not brand loyalty. Model names and account access change,
so inspect local availability before every dispatch:

```powershell
claude --help
codex exec --help
agy models
```

## CLI Selection

| CLI skill | Prefer for | Avoid as the sole owner of |
| --- | --- | --- |
| `claude-cli` | Architecture options, broad codebase reconnaissance, high-context reviews, written handoffs, and ambiguous diagnosis | Mechanical parallel edits that would conflict with another writer |
| `codex-cli` | Repo-local implementation, test-driven bug fixes, surgical refactors, diff review, and final verification in a worktree | A large unscoped research task with no concrete repository outcome |
| `antigravity-cli` | Independent exploration, alternate-model opinions, bounded implementation, and parallel evidence gathering | The integration worktree when another agent is already writing it |

Use one strong reasoning agent to decide uncertain architecture or high-impact
risk. Use one bounded coding agent per non-overlapping implementation issue.
Reserve the final integration and verification for a single owner.

## Capability Tiers

| Tier | Suitable work | Do not use as the final authority for |
| --- | --- | --- |
| Fast or low | Issue inventory, duplicate triage, reproduction notes, narrow documentation, test discovery, and structured handoff drafts | Architecture, migrations, security decisions, or changes spanning uncertain ownership |
| Balanced or medium | A self-contained code change, unit tests, routine review, implementation of an approved plan, and deterministic verification | An unresolved cross-cutting design dispute |
| High or thinking | Multi-file diagnosis, difficult review, implementation with tricky invariants, and plan critique | High-volume clerical work where latency matters more than depth |
| Flagship or frontier | Architecture, risky migrations, security-sensitive analysis, final integration review, and conflicts between prior agents | Routine issue administration or simple mechanical changes |

## Strict Model Allowlist

The task-type table above is the complete allowlist. These are not examples.
Once a task is classified, use only the models in that task's chain. Do not
substitute another model family, tier, alias, or provider, even if it is
available locally. If none of the listed routes is available, mark the task
`blocked` with `invalid-route`/`model-unavailable` evidence and request a new
routing decision; do not invent a fourth fallback.

The currently permitted model labels are exactly:

| Task type | Permitted CLI/model pairs |
| --- | --- |
| Coding or development | `antigravity-cli / gemini-3.6-flash-high`; `antigravity-cli / gpt-oss-120b-medium`; `claude-cli / sonnet`; `codex-cli / gpt-5.6-luna-medium`; `claude-cli / haiku` |
| Review | `antigravity-cli / claude-sonnet-4-6`; `claude-cli / opus`; `codex-cli / gpt-5.6-terra-high` |
| Planning | `claude-cli / opus`; `codex-cli / gpt-5.6-sol-high`; `antigravity-cli / claude-sonnet-4-6` |

The listed labels must still be checked against local account availability, but
availability checks never authorize using a model outside this allowlist.

## Task-Type Fallback Order

Classify the task before dispatch. The first available route is preferred; move
to the next route only when the current route is unavailable or the attempt has
failed. This explicitly includes model quota exhaustion, rate limits, provider
overload/capacity errors, authentication/configuration failures that prevent
launch, CLI-not-found errors, startup timeouts, and worker errors. Model labels
below are the requested routing aliases; verify the exact IDs exposed by each
local CLI before launch.

| Task type | Priority order (CLI / model) |
| --- | --- |
| Coding or development | `antigravity-cli / gemini-3.6-flash-high` → `antigravity-cli / gpt-oss-120b-medium` → `claude-cli / sonnet` → `codex-cli / gpt-5.6-luna-medium` → `claude-cli / haiku` |
| Review | `antigravity-cli / claude-sonnet-4-6` → `claude-cli / opus` → `codex-cli / gpt-5.6-terra-high` |
| Planning | `claude-cli / opus` → `codex-cli / gpt-5.6-sol-high` → `antigravity-cli / claude-sonnet-4-6` |

For coding/development, use the first row for both implementation and bounded
development work. For review, use a separate reviewer conversation with the
developer's changed files, branch/commit, and verification command. For plan,
prefer the stronger reasoning route even when no code will be changed.

### Fallback protocol

Before dispatch, run the provider's availability check (`agy models`,
`claude --help`/configured model aliases, and `codex exec --help` or the local
profile list). If a route cannot start, the model has no remaining quota, or it
returns a classified capacity/rate-limit/provider/worker failure, record the
attempted CLI/model, native-session state, exact error, and reason for fallback
in the task record, then try the next entry. Do not skip entries silently,
retry an active process in a second CLI, or use a provider's "latest"
conversation selector. A fallback is a new attempt with a new dispatch ID; it
must carry a factual handoff of the previous attempt's evidence.

### Reset after a completed task

The fallback cursor is scoped to one task only. A successful fallback does not
change the preferred route for later work. After the task reaches `verified` or
`done`, reset the cursor to position 1 and probe the first provider/model in the
chain for the next task. If that first route is still unavailable, rate-limited,
over quota, overloaded, or fails, walk the same chain from the beginning and
record the new fallback attempts. Never make the last successful fallback a
sticky default.

The reviewer is a separate invocation, not the developer's session continued.
Confirm the selected model and native session ID before dispatch, and record the
final selected route in the handoff.

## Prompt And Model Match

- Coding/development routes receive one issue, one worktree, exact paths, and a
  verification command.
- Review routes receive the changed files, branch/commit, acceptance checks, and
  an approve/request-changes verdict.
- Planning routes receive uncertainty, constraints, alternatives, and a request
  for explicit assumptions and evidence.
- Do not replace any of these routes with a different model tier or provider.

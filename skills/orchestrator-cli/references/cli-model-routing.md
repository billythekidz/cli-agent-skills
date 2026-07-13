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

## Known Model Families

These are routing examples, not an availability guarantee. Pass only model IDs
or labels that the local CLI and account expose.

| CLI | Model or class | Route to |
| --- | --- | --- |
| Claude Code | `opus` class | Architecture, security or migration review, hard root-cause analysis, and final decision memos |
| Claude Code | `sonnet` class | Bounded implementation, normal code review, test additions, and clear issue handoffs |
| Claude Code | `fable` or another configured alias | Verify the provider's documented tier first; do not infer capability from an alias alone |
| Codex CLI | `gpt-5.6` or `gpt-5.6-sol`, if available | Most difficult code changes, integration review, and quality-first reasoning |
| Codex CLI | `gpt-5.6-terra`, if available | Balanced implementation and routine review |
| Codex CLI | `gpt-5.6-luna`, if available | High-volume, well-specified triage, documentation, and mechanical checks |
| Antigravity | `Gemini 3.5 Flash (Low)` | Fast repository or issue inventory and concise evidence collection |
| Antigravity | `Gemini 3.5 Flash (Medium)` | Bounded tests, documentation, and independent small changes |
| Antigravity | `Gemini 3.5 Flash (High)` | Complex but isolated implementation or review |
| Antigravity | `Gemini 3.1 Pro (Low)` | Fast plan decomposition and broad discovery when a lighter pass is enough |
| Antigravity | `Gemini 3.1 Pro (High)` | Multi-file diagnosis, plan review, and difficult implementation |
| Antigravity | `Claude Sonnet 4.6 (Thinking)` | Careful implementation planning and critical code review |
| Antigravity | `Claude Opus 4.6 (Thinking)` | Architecture, high-risk review, and final synthesis |
| Antigravity | `GPT-OSS 120B (Medium)` | A bounded second opinion, tests, or a medium-complexity independent task; verify critical conclusions with a stronger owner |

The Codex examples follow the OpenAI capability tiers documented in the
[current model guide](https://developers.openai.com/api/docs/guides/latest-model.md).
They do not guarantee that a particular Codex CLI account can select those
models. The Antigravity display names above were returned by `agy models` on
2026-07-13; refresh the list before relying on them.

## Prompt And Model Match

- Give fast-tier agents evidence-gathering tasks with a fixed output schema.
- Give balanced-tier agents one issue, one worktree, exact paths, and a test.
- Give high-tier agents uncertainty, constraints, alternatives, and a request
  for explicit assumptions and evidence.
- Give a flagship-tier integration owner the child handoffs, merged diff,
  verification command, and authority boundary. Do not ask it to redo every
  completed task without a specific conflict or gap.

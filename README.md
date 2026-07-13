# Direct CLI Skills

Portable Agent Skills for delegating bounded tasks directly to local Claude
Code, Codex CLI, and Google Antigravity CLI processes. These skills do not
require `cao-server` or CAO MCP handoff tools.

## Included Skills

- [claude-cli](skills/claude-cli/SKILL.md) delegates with `claude`.
- [codex-cli](skills/codex-cli/SKILL.md) delegates with `codex exec`.
- [antigravity-cli](skills/antigravity-cli/SKILL.md) delegates with `agy`.
- [orchestrator-cli](skills/orchestrator-cli/SKILL.md) coordinates GitHub
  Issues and direct CLI workers with CAO-inspired assign/handoff semantics,
  without starting `cao-server`.

## Install With `npx skills`

Prerequisite: Node.js with `npx` available.

Project scope is the default. It keeps the skills in the current project and
shares them with the selected clients:

```bash
npx skills add billythekidz/cli-agent-skills --skill claude-cli --skill codex-cli --skill antigravity-cli --skill orchestrator-cli --agent claude-code --agent codex --agent antigravity-cli --yes
```

Use `--global` to make them available across projects:

```bash
npx skills add billythekidz/cli-agent-skills --skill claude-cli --skill codex-cli --skill antigravity-cli --skill orchestrator-cli --agent claude-code --agent codex --agent antigravity-cli --global --yes
```

Install only one skill by keeping the matching `--skill` argument and removing
the others. Use `npx skills list` to inspect installed skills.

## Local Checkout

From a clone of this repository, use `.` instead of the GitHub source:

```bash
npx skills add . --skill claude-cli --skill codex-cli --skill antigravity-cli --skill orchestrator-cli --agent claude-code --agent codex --agent antigravity-cli --yes
```

## Permission Policy

These skills default to unattended permission bypass for direct delegation:

- Claude Code: `--dangerously-skip-permissions`
- Codex CLI: `--dangerously-bypass-approvals-and-sandbox`
- Antigravity CLI: `--dangerously-skip-permissions`

`orchestrator-cli` coordinates these direct workers. It keeps GitHub Issue
writes explicitly scoped to the requested repository and issue numbers.

This allows child agents to edit files and run commands without approval
prompts. Install and use these skills only in workspaces and environments you
trust. Each skill documents the safer override flags when a task needs them.

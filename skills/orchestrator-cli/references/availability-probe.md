# Fast Model Availability Probe

Run this probe before sending the real task prompt. It is a disposable,
one-shot check for the selected CLI/model route; it must not read the target
repository, call tools, modify files, or resume a previous task session.

## Probe contract

Use this exact prompt:

```text
Reply exactly READY. Do not use tools, inspect files, or modify anything.
```

The probe passes only when all of the following are true:

- the selected CLI starts within the short probe budget (recommended: 30s);
- the process exits successfully and returns machine-readable output when the
  route supports it; and
- the final response is exactly `READY` after normal output-envelope parsing.

The probe is not task work and is not acceptance evidence. After a pass, start
the real task using the normal dispatch command and record a new native session
when the CLI is one-shot. Do not inject the real task prompt into a probe
process.

## Provider command shapes

Replace `<model>` with the already selected model from the task-type allowlist.
Use the same dedicated worktree or a clean disposable probe workspace, with no
task files supplied as prompt context.

### Antigravity

```powershell
agy -p 'Reply exactly READY. Do not use tools, inspect files, or modify anything.' `
  --model <model> --output-format json --mode accept-edits `
  --dangerously-skip-permissions --print-timeout 30s
```

### Claude Code

```powershell
claude -p 'Reply exactly READY. Do not use tools, inspect files, or modify anything.' `
  --model <model> --output-format json --dangerously-skip-permissions
```

Enforce the same short 30-second wall-clock budget from the orchestrator
supervisor when the CLI has no equivalent print-timeout flag.

### Codex CLI

```powershell
$probePrompt = 'Reply exactly READY. Do not use tools, inspect files, or modify anything.'
$probePrompt | codex exec --model <model> `
  --dangerously-bypass-approvals-and-sandbox --json -
```

Enforce the same short 30-second wall-clock budget from the orchestrator
supervisor. Do not use the probe as a substitute for the full task timeout.

## Routing after the probe

For a new task, probe position 1 of its task-type fallback chain. If the probe
fails, times out, reports quota/rate-limit/capacity/authentication failure, or
the CLI cannot launch, record the probe command, exit state, parsed output/log
tail, and failure classification, then probe the next permitted route. Do not
send the large task prompt to a route whose probe has failed.

When a probe passes, dispatch the real task on that selected route. If the real
task later fails, record the task failure separately and continue with the next
fallback route; a passing probe does not guarantee task completion.

If the real task times out, do not fall back immediately. First compare the
task's progress hash across timeout checks. Only after unchanged consecutive
timeout hashes and a failed/unavailable post-stop probe should the orchestrator
move to a smaller retry or the next fallback route.

After the task reaches `verified` or `done`, reset the probe position to 1 for
the next task. A fallback route that passed a prior probe is never a sticky
default.

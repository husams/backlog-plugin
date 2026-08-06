---
name: claude-review-backlog-item
description: Ask Claude Code to independently review an implemented Backlog task, feature, story, bug, or subtask from the exact Git worktree where the implementation was performed. Use when the user asks Claude to review, re-review, inspect, or provide Backlog feedback on a work item identified by a key such as F-001, S-004, B-002, or T-009.
---

# Claude Review Backlog Item

Treat every relative path below as relative to this skill directory.

Launch a Claude Code review in the implementation worktree and have Claude use
the Backlog skill to record its findings.

Never create, clone, switch, or provision a worktree for this review. Use the
user-supplied checkout or the already established current checkout in place.

## Workflow

1. Extract one Backlog item key and any user-supplied review focus.
2. Resolve the exact Git worktree where that item's implementation was done,
   using this precedence:
   - Use a worktree path explicitly supplied by the user.
   - Otherwise use the implementation worktree already established in the
     current conversation or progress record.
   - Otherwise use the current Git worktree only when the Backlog item can be
     confirmed from that worktree.
3. Do not guess, scan unrelated repositories, or use this plugin's repository
   merely because the launcher lives here. If the worktree remains ambiguous,
   ask the user for its path and stop.
4. Build a focused review prompt. Include the item key, name the
   independent-reviewer role so the
   `backlog-reviewer` skill and its guardrails apply, require comparison of the
   implementation and tests with the Backlog description, acceptance criteria,
   and checklist, and ask Claude to post findings as Backlog review threads.
   Preserve any additional review focus supplied by the user. This skill only
   launches the review; `backlog-reviewer` owns every reviewer rule and this
   file must not relax or weaken one.
5. Run the bundled launcher with the resolved worktree, item key, and prompt:

   ```bash
   scripts/review-with-claude.sh --workdir <implementation-worktree> <item-key> '<review prompt>'
   ```

6. Wait for the command to finish. Report its exit status and a concise review
   outcome. Do not reinterpret a failed launch as a completed review.

## Worktree Requirements

Treat the launcher validation as mandatory. It normalizes the supplied path to
its Git worktree root and confirms that the Backlog item is visible there
before starting Claude. A validation failure means the selected directory is
wrong or not configured for that Backlog project; resolve the directory rather
than bypassing the check.

The launcher executes Claude from the validated worktree root in non-interactive
read-only review mode. It disables browser access, file-editing tools, session
persistence, and permission bypass. Shell access is allowlisted to read-only Git
inspection, Backlog review/criterion operations, and common test runners; any
other command is denied rather than prompting:

```bash
claude --permission-mode dontAsk \
  --tools 'Read,Grep,Glob,Bash' --allowedTools '<review allowlist>' \
  --no-chrome --no-session-persistence -n <item-key> \
  -p '/backlog-reviewer <review prompt>'
```

Use `scripts/review-with-claude.sh --check --workdir <path> <item-key>` only to
validate directory selection without launching Claude.

## Default Review Prompt

When the user gives no narrower focus, pass this prompt explicitly:

> Use the `backlog-reviewer` skill to review `<item-key>` as its assigned
> independent reviewer. Keep the implementation worktree read-only. Compare
> the implementation and tests in this
> worktree with the Backlog item description, acceptance criteria, and
> checklist. Run only non-mutating relevant checks. Post every specific finding as a Backlog
> review thread with accurate severity and file/line evidence when available.
> Decide every pending implementer response. Record a `bl.verify_criterion`
> verdict with concrete evidence for every entry returned by
> `bl.acceptance_criteria`, and refuse to approve if that list is empty, if any
> criterion is unverified, stale, or unmet, or if `bl.todos` reports an open
> todo. Finish with the configured acceptance or changes-requested action; do
> not leave the review ambiguous.

The launcher fallback carries the same minimum contract, but pass the focused
prompt above so any user-supplied review emphasis is retained.

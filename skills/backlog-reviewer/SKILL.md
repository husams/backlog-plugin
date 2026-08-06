---
name: backlog-reviewer
description: Independently review Backlog Features, Stories, Bugs, and Iterations through the documented Backlog APIs. Use only when explicitly assigned the independent reviewer role to inspect incremental review feedback, decide implementer responses, request changes, approve reviewed work, or verify merge readiness. Do not trigger for generic Backlog lookups, what to work on, implementation, coordination, or one-off Backlog commands; use the generic backlog skill for those.
---

# Backlog Reviewer

Use the existing `backlog` skill and its documented public `backlog_cli` API as
the source of truth. Read references progressively:

- Read `../backlog/references/review.md` for review-thread lifecycle and
  incremental reads.
- Read `../backlog/references/api.md` for computed or multi-thread work.
- Read `../backlog/references/workflow.md` when checking semantic actions or
  gates.
- Read local `references/review-api.md` for reviewer-specific call sequences,
  including the acceptance-verdict sequence.
- Use `references/decision-checklist.md` before a verdict and
  `references/failure-modes.md` when a guardrail is at risk.

## Use Backlog evidence safely

Use the documented Python API for multi-step or computed work. Reserve the CLI
for one simple documented command. Never build shell workflows or scratch
files. Filter before reducing. Never decide from truncated, incomplete, or
arbitrarily limited evidence.

## Preserve independence

1. Open every API session with the real reviewer identity.
2. Resolve the task and confirm that the reviewer differs from both its
   creator and implementer. If any identity matches, refuse to review and
   report the conflict.
3. Never implement a fix while acting as reviewer. Never substitute the
   implementer identity, accept another actor's response, or merge on behalf
   of the designated merger.
4. Inspect only the implementation evidence needed for the assigned review;
   do not read implementation under `bin/`, `tool/`, or `scripts/`. Use the
   documented Markdown references for those interfaces.

## Keep the implementation worktree read-only

- Review only the exact implementation worktree assigned to the task. Do not
  traverse into another checkout, the user home, sibling repositories, secret
  stores, credential files, or unrelated mounted paths.
- Capture the worktree's changed-file list before validation. Read only the
  task contract, its declared evidence, the diff, and files needed to verify a
  criterion or finding. Do not scan unrelated project content for context.
- Never edit implementation, tests, snapshots, fixtures, generated files,
  manifests, lockfiles, configuration, or policy. Do not run formatters,
  fixers, installers, generators, migrations, or tests known to rewrite tracked
  files. A reviewer records findings; the assigned implementer makes fixes.
- After validation, compare the changed-file list with the initial snapshot.
  If a check created or modified files, do not conceal the side effect or
  discard pre-existing work. Remove only disposable output proven to have been
  created by this review; otherwise report the path and stop before a verdict.
- Write only through the documented Backlog review, criterion-verdict, and
  semantic-action APIs. Never write task implementation files, `.backlog/`
  storage, scratch evidence files, or ad hoc reports from the reviewer session.

## Approve only on recorded acceptance evidence

Approval is conditional on deterministic evidence, never on judgement alone.
These rules bind every approval path and none of them has a waiver.

1. Call `bl.acceptance_criteria(key)` before any approval action. An empty list
   means the contract is unspecified: refuse to approve, report the missing
   criteria, and return the work. `acceptance_criteria_verified` fails on a
   task with zero criteria.
2. Evaluate every criterion individually against the diff, tests, validator
   output, and review record, then record each one with
   `bl.verify_criterion(item_id, met=..., evidence=...)`. The evidence must
   name what was inspected — file and line, test name, command, or output. A
   verdict without specific evidence is a defect, not a shortcut.
   The API session actor must match the task's assigned reviewer, and verdicts
   may be recorded only while the task is in a review-category state.
3. Never approve while any criterion is `unverified`, `unmet`, or `stale`, or
   while `bl.todos(key)` returns an open todo. Open a typed review root for the
   gap and return the work through the changes-requested action instead.
4. When a criterion cannot be verified from the available evidence, record
   `met=False` with that reason and request changes. Never leave a criterion
   unverified and approve anyway.
5. Never record a verdict for a criterion this actor implemented, and never
   substitute another identity to pass the independence check; the API rejects
   the task's implementer and its creator.
6. Never waive `acceptance_criteria_verified` — no waiver exists for it, and
   searching for one is itself a guardrail violation. Never waive
   `todos_closed`, and never reach a done-category status by any route that
   skips either gate.
7. Re-check `bl.can(key, target="accepted")` immediately before the final
   action. If it fails, report every `gate.failures` entry verbatim and do not
   approve. Never claim an approval the gate did not permit.

## Run one complete review loop

1. Resolve the assigned task with `bl.task(key)`, read its criteria contract
   with `bl.acceptance_criteria(key)`, and inspect dependencies,
   artifacts/evidence, open `bl.todos(key)`, current status, and
   `bl.actions(key)`. Do not request or invent a destination status.
2. Make one narrowly filtered initial inbox read for the task and reviewer
   role. Retain a separate `root -> LAST_SEEN` mapping for every returned
   thread, where `LAST_SEEN` is that thread's `reply_to` key. Do not use one
   task-level cursor.
3. Inspect the diff, tests, validator output, and relevant gates. Treat
   truncated or incomplete evidence as unusable; narrow the query or report
   that no verdict is possible. Record a `bl.verify_criterion` verdict for each
   criterion as its evidence is established, never in one unexamined batch.
4. Open one concrete typed review root per finding with severity, evidence,
   expected behavior, rationale, and location when available. Use only
   `blocker`, `nice_to_have`, or `info`.
5. For every known root, call `bl.review_updates(root, after=LAST_SEEN)`.
   Process every returned comment oldest-first, then advance only that root's
   cursor. Never poll known roots by re-reading an inbox or full thread.
6. Reply to every implementer response at every severity. Use `accept` only
   as the opening reviewer; use `reject` when evidence is insufficient. Each
   decision must have a substantive explanation. A `nice_to_have` or `info`
   response is not optional.
7. If an accepted finding regresses, call `bl.review_reopen(root,
   author=reviewer, body=reason)` with the causal reason. Do not create an
   unlinked replacement root.
8. Before the final verdict, make exactly one final semantically filtered
   discovery read for previously unseen roots. Process each new root through the same
   loop and retain its cursor.
9. Before approving, re-read `bl.acceptance_criteria(key)` and `bl.todos(key)`
   and apply *Approve only on recorded acceptance evidence* in full: every
   criterion `met` and not `stale`, zero open todos, and
   `bl.can(key, target="accepted")` green. Any failure means
   changes-requested, not approval.
10. Recheck `bl.actions(key)` and end decisively through the configured
    approval or changes-requested semantic action. Leave no response awaiting a
    reviewer and no root ambiguously open. Do not submit `feedback.*` actions;
    review APIs emit managed feedback events.

## Iteration reviews

Treat an Iteration as a reviewable container, not as deliverable Story work.
An Iteration carries no acceptance-criteria contract of its own, so
`acceptance_criteria_verified` does not apply to it and no criterion verdict is
recorded on it; each member Story or Bug carries that gate separately.
Every blocker, `nice_to_have`, and `info` root still requires an implementer
response and an opening-reviewer `accept` or `reject`. Disposition alone does
not close a root. Verify every root is closed before allowing
`iteration.closed`; the closure gate is `iteration_comments_closed`, which
blocks on any open severity. Feedback events on an Iteration are lifecycle
no-ops. Also check the `iteration_members_finished` gate and use only the
currently allowed `iteration.closed` action. The repository-owned evaluation
under `evals/` exercises this behavior through the public API.

## Merge handoff

Verify `bl.can(key, target="merge")` or the documented `$BL gate <KEY> --for
merge` command immediately before the handoff. Hand off only when the API
succeeds or the CLI exits `0`. Treat CLI exit `2` as **do not merge**, report
every `gate.failures` entry verbatim, and leave the actual merge to the
designated merger. Never bypass a gate with a waiver just to obtain approval —
`acceptance_criteria_verified` and `todos_closed` are never waived — and never
claim a PR was merged from a reviewer session.

Use public APIs only: no direct SQL, database access, direct status mutation,
identity substitution, or undocumented commands. Keep review work scoped to
the assigned task and leave concise evidence in the Backlog review record.

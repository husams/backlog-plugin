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
- Read local `references/review-api.md` for reviewer-specific call sequences.
- Use `references/decision-checklist.md` before a verdict and
  `references/failure-modes.md` when a guardrail is at risk.

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

## Run one complete review loop

1. Resolve the assigned task with `bl.task(key)`, inspect its acceptance
   criteria, dependencies, artifacts/evidence, current status, and
   `bl.actions(key)`. Do not request or invent a destination status.
2. Make one narrowly filtered initial inbox read for the task and reviewer
   role. Retain a separate `root -> LAST_SEEN` mapping for every returned
   thread, where `LAST_SEEN` is that thread's `reply_to` key. Do not use one
   task-level cursor.
3. Inspect the diff, tests, validator output, and relevant gates. Treat
   truncated or incomplete evidence as unusable; narrow the query or report
   that no verdict is possible.
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
9. Recheck `bl.actions(key)` and end decisively through the configured
   approval or changes-requested semantic action. Leave no response awaiting a
   reviewer and no root ambiguously open. Do not submit `feedback.*` actions;
   review APIs emit managed feedback events.

## Iteration reviews

Treat an Iteration as a reviewable container, not as deliverable Story work.
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
merge` command. Hand off only when the API succeeds or the CLI exits `0`.
Treat CLI exit `2` as **do not merge**, report `gate.failures`, and leave the
actual merge to the designated merger. Never bypass a gate with a waiver just
to obtain approval, and never claim a PR was merged from a reviewer session.

Use public APIs only: no direct SQL, database access, direct status mutation,
identity substitution, or undocumented commands. Keep review work scoped to
the assigned task and leave concise evidence in the Backlog review record.

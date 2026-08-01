---
name: backlog-coordinator
description: Coordinate Backlog Features and Iterations through the documented Backlog APIs. Use only when explicitly coordinating decomposition, independent role assignments, dependencies, iteration membership, child-derived PR state, lifecycle gates, handoffs, or retrospective actions. Do not trigger for generic Backlog lookups, one task's implementation or review, status/next/action queries, or one-off Backlog commands; use the generic backlog skill for those.
---

# Backlog Coordinator

Use the sibling `backlog` skill and its public `backlog_cli` API as the only
workflow authority. Read references progressively:

- `../backlog/references/api.md` for computed or multi-task queries and the
  Feature/Iteration API;
- `../backlog/references/workflow.md` for semantic actions and gates;
- `../backlog/references/review.md` for review handoffs and disposition;
- `../backlog/references/retrospectives.md` for retrospective-action lifecycle.

## Preserve role separation

- Open every API session with the real coordinator identity.
- Preserve immutable `created_by`. Assign only the documented implementer and
  reviewer slots with `bl.assign(to=..., reviewer=...)`.
- Route `refinement.accepted` to a named actor who is independent from the
  creator and upcoming implementer. The acceptance actor is attribution on the
  event, not a persisted `refiner` field.
- Never impersonate a creator, refinement actor, implementer, reviewer, or
  merger, and never perform a role-owned acceptance for another actor.
- Surface missing independent actors as blockers rather than weakening the
  handoff.

## Coordinate Features and Iterations

1. Resolve the Feature or Iteration and inspect children, dependencies,
   assignments, review roots, task items, and `bl.actions(key)`.
2. Decompose Feature outcomes into independently verifiable Stories or
   standalone Bugs with explicit acceptance criteria. Record ordering with
   public dependency operations and verify startability through the API.
3. Assign distinct implementer and opening-reviewer identities. Recheck the
   assignment and allowed actions immediately before each handoff.
4. Open an Iteration only through its configured `iteration.opened` action. Add
   only a Ready Story or standalone Ready Bug to an Open Iteration through
   `bl.add_iteration_member(iteration, member)`. Refuse Features, subtasks,
   Iterations, parented Bugs, non-Ready tasks, and members already retained by
   another Open Iteration.
5. Monitor member readiness, dependencies, review inboxes, all review
   severities, PR state, and merge/acceptance gates without making the
   implementer's or reviewer's decisions.
6. Close an Iteration only with the currently allowed semantic action and only
   when `iteration_members_finished` and `iteration_comments_closed` pass.
   Closure requires every blocker, nice-to-have, and info thread to be closed.
   Reopening is allowed only through the configured action and must recheck
   retained-member conflicts with other Open Iterations.
7. Derive Feature and Iteration PR summaries from child Stories/Bugs. Do not
   create or assign a PR to a Feature or Iteration; those containers have no PR
   of their own.
8. Track retrospective actions through public creation, independent
   acceptance, reasoned rejection, or closure against an addressing project
   and exactly one Feature or Bug.

## Apply coordination guardrails

- Use public Backlog APIs only. Never write status fields or the database,
  execute SQL, read implementation under `bin/`, `tool/`, or `scripts/`, or
  invent a private state machine or lifecycle action.
- Before every state-changing call, inspect `bl.actions(key)` and submit only
  an allowed semantic action. Never request a destination status.
- Treat `bl.can(key, target=...)` or the documented gate command as evidence;
  do not waive a dependency, review, PR, or iteration gate merely to advance
  work.
- Use incremental review reads: retain one `root_key -> reply_to` cursor per
  root, call `bl.review_updates(root, after=cursor)`, process all returned
  comments, then advance that root's cursor. Make one final filtered discovery
  read for unseen roots before handoff.
- Keep coordination evidence concise: scope, assignments, blocked handoffs,
  unresolved retrospective actions, and next allowed decisions. Record
  review feedback with the review API, not as an artifact.

## Report the result

Return the task or container key, decomposition and dependency scope, named
role assignments, iteration membership and gate results, blocked handoffs,
unresolved retrospective actions, and the next allowed semantic decisions.

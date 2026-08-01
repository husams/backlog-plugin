---
name: backlog-implementer
description: Implement an independently refined Backlog Story or Bug through the documented Backlog APIs. Use only when explicitly assigned as implementer to deliver scoped code or documentation, verify dependencies and readiness, record validation evidence, answer every review severity, and hand work back for independent review. Do not trigger for generic Backlog lookups, planning, reviewer decisions, coordination, what-to-work-on requests, or one-off Backlog commands; use the generic backlog skill for those.
---

# Backlog Implementer

Implement one assigned Backlog Story or standalone Bug from an accepted,
independently refined contract through review handoff. Use the sibling
`backlog` skill and its public `backlog_cli` API as the source of truth; do not
duplicate lifecycle rules in this skill.

## Boundaries and routing

- Trigger only when the request identifies an assigned implementation task and
  this actor is its implementer. A request to inspect the board, choose work,
  query status, plan, refine, review, or coordinate belongs to `backlog` (and
  reviewer work belongs to the reviewer workflow), not this skill.
- Open every API session with the real implementer identity. Confirm the task's
  assignee, independent reviewer, current status, acceptance criteria, items,
  dependencies, and allowed semantic actions.
- Refinement must already be accepted by a distinct named actor. Never submit
  `refinement.accepted` for work this actor will implement, even when this
  actor created the task. If refinement is not independently attributed, stop
  and report the missing refiner.
- Never approve a review response, perform reviewer-owned final acceptance, or
  merge this actor's own work. Preserve unrelated changes and the accepted
  scope.

## Delivery workflow

1. Resolve the task with `bl.task(key)`. Read `bl.actions(key)`, inspect
   `bl.dependencies(key)`, and evaluate `bl.can(key, target="start")` or the
   documented dependency check. If dependencies or review threads block the
   task, refuse to start and report the exact blocker.
2. Start only with the currently allowed `Action.WORK_STARTED` semantic action
   through `bl.trigger(...)`. Never request or assign a destination status.
   Re-read actions before every later state change and submit only the action
   the configured workflow exposes.
3. Implement every acceptance criterion within the task's scope. Use normal
   repository inspection and repository-native tests; do not inspect or copy
   lifecycle implementation from the Backlog launchers, runtime internals, or
   operational scripts.
4. Run required executable items with `bl.run_task(key, project_root, ...)` or
   `bl.run_item(...)`. A required validation passes only when its latest result
   is a current `pass` for the current execution-spec fingerprint. A stale,
   pending, failed, errored, or skipped required item does not pass. If a
   validation cannot run, use `bl.waive_validation(item_id, reason=...)` only
   with a concrete, audited reason; never manufacture a pass. Advisory items
   remain visible but do not satisfy the required gate. See
   [validation-and-gates.md](references/validation-and-gates.md).
5. Record a concise implementation/validation summary as a Backlog task note
   (`bl.add_item(key, "note", body)`). Do not turn review feedback into a note
   or artifact. Add an artifact only when the user explicitly requests a
   durable file, report, patch, or other attachment.
6. Before handoff, recheck actions, validation state, and the merge/acceptance
   gate. Use the allowed review-submission semantic action; do not infer its
   destination. Follow [delivery-api.md](references/delivery-api.md).

## Independent review loop

Maintain a small in-context mapping of each known review root to its latest
comment key, `root -> LAST_SEEN`.

- Make one narrowly scoped initial inbox read for this task and implementer
  role. Retain each root and returned `reply_to`; do not repeatedly reload
  full threads or inbox summaries.
- For known roots, call `bl.review_updates(root, after=LAST_SEEN)`. Process
  every returned comment in order, then advance that root's cursor to the last
  processed comment. Do not advance early.
- Answer every thread awaiting this implementer at every severity:
  `blocker`, `nice_to_have`, and `info`. Use `fix` when changing the work,
  `comment` for a concrete implementation explanation, or `reject` with
  evidence when the finding is incorrect. Each response must state the
  disposition and concrete evidence; never reply with only “fixed”.
- Before handing work back, make exactly one semantically filtered discovery
  read to find unseen roots, then process them with the same cursor rules.
- Never use `accept` as the implementer and never submit reviewer-owned final
  approval. Return the work through the configured semantic action and let the
  opening reviewer decide every response.

Load the references progressively:

- [delivery-api.md](references/delivery-api.md) for task, action, dependency,
  evidence, and handoff API patterns.
- [review-response.md](references/review-response.md) for cursor handling and
  all-severity responses.
- [validation-and-gates.md](references/validation-and-gates.md) for current
  executable evidence, waivers, CI validation, and gate semantics.

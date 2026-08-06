# Decision checklist

## Hard pre-approval gate

Never submit an approval action until every box below is true. A single
unchecked box means changes-requested, not approval. None of these has a
waiver, and none may be satisfied by judgement instead of a recorded call.

- [ ] `bl.acceptance_criteria(key)` returned a non-empty list. An empty
      contract is refused and reported, never approved.
- [ ] Every criterion was evaluated individually against inspected evidence,
      not as a batch and not from the implementer's summary alone.
- [ ] Every criterion holds a `bl.verify_criterion` verdict recorded by this
      reviewer with concrete evidence naming file, test, command, or output.
- [ ] This actor is the task's assigned reviewer, the task is currently in
      review, and no substitute identity was used for any verdict or action.
- [ ] No criterion is `unverified`, `unmet`, or `stale`; an unverifiable
      criterion was recorded `met=False` and returned for changes.
- [ ] No verdict covers a criterion this actor implemented, and no identity was
      substituted to pass the independence check.
- [ ] `bl.todos(key)` returns zero open todos.
- [ ] `bl.can(key, target="accepted")` is green, checked immediately before the
      action; `acceptance_criteria_verified` and `todos_closed` were not waived
      and no waiver was sought for them.
- [ ] The done-category status is reached only through the configured approval
      action, never through a route that skips these gates.

An Iteration has no acceptance-criteria contract: skip the criteria boxes for
it and use the Iteration line below instead.

## Full review checklist

Use this checklist before handing a reviewed task back:

- [ ] The API session actor is the real opening reviewer.
- [ ] Reviewer, creator, and implementer identities are distinct.
- [ ] The task, `bl.acceptance_criteria(key)`, dependencies, evidence, open
      todos, current state, and currently allowed semantic actions were
      inspected.
- [ ] One filtered initial inbox read created a per-root `LAST_SEEN` map.
- [ ] Every known root was checked with `review_updates` and every update was
      processed before its cursor advanced.
- [ ] Every finding has a typed severity and concrete evidence.
- [ ] Every implementer response at blocker, `nice_to_have`, and info severity
      has an opening-reviewer accept/reject decision.
- [ ] Regressed accepted findings were reopened through `review_reopen` with a
      causal reason.
- [ ] One final filtered discovery found and processed any unseen roots.
- [ ] No root remains ambiguously open and the configured approval or
      changes-requested action was used.
- [ ] For an Iteration, all severity roots are closed and
      `iteration_comments_closed` passes; `iteration_members_finished` also
      passes; disposition alone was not counted as closure.
- [ ] Merge readiness is green through `bl.can(..., target="merge")` or the
      documented gate command; the reviewer did not merge.

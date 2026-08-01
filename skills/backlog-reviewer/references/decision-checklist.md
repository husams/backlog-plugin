# Decision checklist

Use this checklist before handing a reviewed task back:

- [ ] The API session actor is the real opening reviewer.
- [ ] Reviewer, creator, and implementer identities are distinct.
- [ ] The task, acceptance criteria, dependencies, evidence, current state,
      and currently allowed semantic actions were inspected.
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
      `iteration_comments_closed` passes; disposition alone was not counted as
      closure.
- [ ] Merge readiness is green through `bl.can(..., target="merge")` or the
      documented gate command; the reviewer did not merge.

# Validation and gates

Required validation evidence belongs to the task's executable acceptance or
checklist items. The local checkout policy controls whether an item may run;
the shared Backlog store records the auditable result.

## Required result semantics

Run `bl.run_task(key, project_root, fail_fast=False)` or an individual
`bl.run_item`. The acceptance gate `required_validations_pass` passes only when
every required executable item has a current `pass` whose spec fingerprint
matches the declaration currently on the item. The following do not pass:

- no result (`pending`);
- a result from an older execution specification (`stale`);
- `fail`, `error`, `timeout`, or `skipped`;
- an advisory result when a required item is still unresolved.

Inspect the public `item_details`, `executable_items`, and execution-history
views rather than hidden implementation state. Run again after changing a
command, expectation, timeout, executor, or requirement.

## Waivers

When a required check cannot run for a defensible reason, record an audited
waiver with `bl.waive_validation(item_id, reason=...)`. A waiver can satisfy
the configured gate, but it is not a validation pass and must be reported as a
waiver in the task note and handoff. Never edit a result or status directly,
and never use a waiver to hide an available failing check.

## Repository-owned CI validation

Skill validation must be runnable from the checkout without a user-home skill,
machine-specific validator, database credentials, or network service. Use the
repository's own test command and checkout-relative paths. Evaluation fixtures
create isolated temporary workspaces, invoke only documented Backlog entry
points/public APIs, and never connect to the shared store. The fixtures cover
both refused and successful start paths, three realistic with-skill versus
without-skill cases, fresh-context dependency/evidence/review behavior, and
bidirectional routing with the generic `backlog` skill as distractor.

## Todo closure

`todos_closed` passes only when every flat implementation todo on the task is
closed; its failures list each open todo. It gates the move into review and
both later transitions, `in_review -> accepted` and `accepted -> done`. Check
`bl.todos(key)` before every submission and treat an open todo as a hard stop:
it is never waived, and no other gate compensates for it. Closing a todo is an
assertion that its work is complete — deferring work means renegotiating scope
with the coordinator, not closing or re-adding todos to move past the gate.

## Criteria verification

`acceptance_criteria_verified` is reviewer-owned and has no waiver. It passes
only when every acceptance criterion holds a current `met` verdict from an
assigned independent reviewer, recorded while the task is in review, and it
fails outright when the task has zero criteria.
The implementer's obligation is to leave evidence the reviewer can check
without re-deriving the work: a named test, a file and line, or a current
validation result per criterion. Never call `bl.verify_criterion`; the API
rejects this actor. A criterion left unimplemented becomes an `unmet` verdict
and returns the work.

## Delivery gates

Before review handoff, check the live actions and relevant gate. An independent
reviewer must close every review root, record every acceptance-criterion
verdict, approve the PR, and run `gate <KEY> --for merge`; merging while it is
blocked is forbidden. The
implementer does not approve the PR or merge. After the reviewer merges, record
the merged PR through the documented Backlog PR operation, then use only the
remaining allowed semantic actions to reach the terminal Story state.

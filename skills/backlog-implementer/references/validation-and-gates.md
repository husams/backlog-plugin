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

## Delivery gates

Before review handoff, check the live actions and relevant gate. An independent
reviewer must close every review root, approve the PR, and run
`gate <KEY> --for merge`; merging while it is blocked is forbidden. The
implementer does not approve the PR or merge. After the reviewer merges, record
the merged PR through the documented Backlog PR operation, then use only the
remaining allowed semantic actions to reach the terminal Story state.

# How to run executable acceptance criteria before Done

Backlog separates executing a validation from enforcing its result:

- `Backlog.run_item()` and `Backlog.run_task()` execute declared acceptance
  criteria and checklist validations and record their results;
- the `required_validations_pass` workflow gate decides whether a transition
  may proceed.

Keeping those responsibilities separate matters. A hook should start the
validations, while the workflow gate remains the authoritative check for
required versus advisory items, current execution fingerprints, freshness,
and audited waivers.

## Recommended project configuration

Add a `pre_transition` function to the trusted project hooks package:

```text
<project>/.backlog/hooks/__init__.py
```

The following hook runs all executable items whenever a transition targets the
`done` state:

```python
from pathlib import Path


def pre_transition(action, trigger, current_state, new_state, backlog):
    if new_state != "done":
        return new_state

    project_root = Path(__file__).resolve().parents[2]
    backlog.run_task(
        trigger["task_key"],
        project_root,
        fail_fast=False,
        actor=trigger.get("actor"),
    )
    return new_state
```

State slugs are project-defined. Replace `done` if the target workflow uses a
different slug. If several terminal states need validation, test membership in
a set of their slugs instead.

Do not decide whether the transition passes by reducing the returned results
inside the hook. That can accidentally make advisory validations blocking or
ignore freshness and waivers. Configure `required_validations_pass` on the
transition instead:

```yaml
transitions:
  - task_types: [feature, story, bug, subtask]
    from: accepted
    action: pr.merged
    to: done
    gates: [pr_merged, required_validations_pass]
```

The exact source state, action, task types, and other gates must match the
project's workflow. A project `.backlog/workflow.yaml` replaces the bundled
workflow completely; it is not merged with it.

Also allow every declared executor in the trusted local execution policy:

```yaml
# <project>/.backlog/execution.yaml
shell_enabled: true
allowed_commands: ["python3"]
allowed_working_directories: ["."]
allowed_environment_variables: ["CI"]
allowed_hooks: ["tests.unit", "lint.python"]
max_timeout_seconds: 120
max_output_bytes: 1048576
max_batch_seconds: 600
```

With no local execution policy, shell execution is disabled and hooks are not
allowed. Denied validations are recorded as `skipped`, so required validations
will not satisfy the gate unless they have an active audited waiver.

## What happens during the transition

When an action resolves to `done`, Backlog performs this sequence:

1. Resolve the semantic action to the workflow's destination state.
2. Call `pre_transition`.
3. Run every executable item in declaration order through `run_task()`.
4. Record `pass`, `fail`, `error`, or `skipped` for each attempted item.
5. Return from the hook with the original destination.
6. Evaluate transition gates, including `required_validations_pass`.
7. Commit the transition only when every gate passes.

`fail_fast=False` is recommended here because it produces evidence for every
declared validation in one attempt. The local `max_batch_seconds` policy still
bounds the complete batch. Items that cannot fit in the remaining batch budget
are recorded as `skipped/batch_budget_exhausted`.

The validation runner also emits `check.started` and one terminal check action
for each started validation. Projects should inspect their workflow mappings
for `check.passed`, `check.failed`, and `check.timed_out`: those actions can
cause nested workflow processing while the outer transition is still in its
pre-transition hook. In particular, a `check.failed` mapping may move the task
to a needs-work state before the original transition reaches its gates.

## Current limitation and preferred future API

This approach works with the current public API, but executing validations from
a general-purpose transition hook makes transition processing re-entrant.
Validation result rows and check actions are committed before the outer
transition completes.

A dedicated transition-preflight option would make that behavior explicit:

```python
backlog.trigger(
    key,
    Action.PR_MERGED,
    validations="run-required",
    project_root=".",
)
```

Such an API should execute validations after resolving the destination but
before project hooks and gates:

1. resolve the destination;
2. run the validation preflight;
3. call `pre_transition`;
4. evaluate gates;
5. commit the transition;
6. call `post_transition`.

Until that API exists, use the small `pre_transition` hook above and keep
`required_validations_pass` as the final authority.

## Related documentation

- [Executable item contract](executable-items.md)
- [Python transition hooks](python-extensions.md)
- [Workflow status flow and gates](../skills/backlog/references/workflow.md)

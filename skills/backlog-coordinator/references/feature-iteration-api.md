# Feature and Iteration coordination

Use the sibling API reference for method signatures. This page records the
coordinator's call sequence and the eligibility rules that must remain visible
in a handoff.

## Feature decomposition

Open one public session with the coordinator actor and resolve the container:

```python
from backlog_cli import api

with api.open(actor="coordinator") as bl:
    feature = bl.task("F-007")
    children = feature.children
    actions = bl.actions(feature.key)
```

Create independently verifiable delivery slices with
`bl.create_story(...)` or standalone defects with `bl.create_bug(...)`. Supply
an actor and explicit acceptance criteria. Record ordering with the documented
`dep add` command; the current public Python API reads dependencies but does
not create them. Then use `bl.dependencies(key)` and
`bl.startable(actor=...)`/`dep check` to verify the result. A Feature is a
planning container; its children carry delivery work and PRs.

## Independent assignment and handoff

Use `bl.assign(key, to=implementer, reviewer=reviewer)`. The coordinator must
verify that the creator, refinement actor, implementer, opening reviewer, and
merger are not being silently substituted for one another. The actor on
`refinement.accepted` is supplied to the semantic action and is not stored as
a new role column. Recheck `bl.actions(key)` before refinement, start, review,
approval, or handoff actions.

## Iteration membership

An Iteration is a parallel grouping, not a parent task and not a PR owner:

```python
with api.open(actor="coordinator") as bl:
    iteration = bl.task("I-001")
    bl.trigger(iteration.key, api.Action.ITERATION_OPENED)
    bl.add_iteration_member(iteration.key, "S-001")
```

`add_iteration_member` admits only a Ready Story or a standalone Ready Bug to
an Open Iteration. It rejects Features, subtasks, Iterations, parented Bugs,
non-Ready members, and a Story/Bug already retained by another Open Iteration.
Once admitted, membership remains while the member moves through its own
workflow. Use `task.iteration_members` and `task.iterations` to inspect both
sides. Reopening a Closed Iteration must recheck retained-member conflicts.

The Iteration row is excluded from unscoped `startable()` results. For member
work, call `bl.startable(actor=..., iteration="I-001")` and require the
Iteration to be Open.

## Child-derived PR and closure state

Features and Iterations have no PR of their own. Summarize PR state from their
child Stories/Bugs or retained members; never call `set_pr` for the container.
Before closure or final handoff, evaluate the project's actual allowed actions
and gates. Iteration closure requires both `iteration_members_finished` and
`iteration_comments_closed`; the latter includes blocker, nice-to-have, and
info roots.

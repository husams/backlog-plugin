# Delivery API

Use only the public API documented by the sibling `backlog` skill. Open one
session with the implementer identity and let the workflow choose destinations.

```python
from backlog_cli import api
from backlog_cli.api import Action

with api.open(actor=implementer) as bl:
    task = bl.task(key)
    if task.assignee != implementer:
        raise RuntimeError("task is not assigned to this implementer")
    if task.reviewer in (None, implementer):
        raise RuntimeError("independent reviewer is required")

    allowed = bl.actions(key)
    dependency_gate = bl.can(key, target="start")
    if not dependency_gate.ok:
        raise RuntimeError("refuse to start: " + "; ".join(dependency_gate.failures))
    if Action.WORK_STARTED not in allowed:
        raise RuntimeError("work.started is not allowed from the current state")
    bl.trigger(key, Action.WORK_STARTED, operation="implementer.start")
```

## Attribution and refinement

Task creation stores `created_by`, but creation and refinement acceptance are
separate responsibilities. The actor implementing the task must not submit
`Action.REFINEMENT_ACCEPTED`; confirm that a distinct named refiner already
submitted it. The API rejects self-acceptance, but the skill must check and
explain the boundary before attempting a write.

Open a fresh `api.open(actor=implementer)` session for every write sequence.
The actor is an attribution assertion, not authentication, but the API uses it
to reject mismatched review authors and to preserve an auditable trail.

## Actions and gates

`bl.actions(key)` is the live action set for the current state. Check it before
`bl.trigger`; do not derive a status or pass a destination. Use `bl.dependencies`
and `bl.can` for dependency/start evidence. Before handoff, re-read actions and
use the configured review-submission action. Before merge, the independent
reviewer must run the configured merge gate; the implementer must not merge.

## Todos and criteria evidence

Todos are flat implementation steps on the task. Append them with
`bl.add_todo(key, content)` or `bl.add_todos(key, contents)`, reorder with
`bl.move_todo(id, position)`, and close each one with `bl.close_todo(id)` as
its work lands. Before the review-submission action, the open set must be
empty:

```python
open_todos = [todo for todo in bl.todos(key) if todo["state"] == "open"]
if open_todos:
    raise RuntimeError("refuse to submit for review: open todos " +
                       ", ".join(str(todo["id"]) for todo in open_todos))

gate = bl.can(key, target="in_review")
if not gate.ok:
    raise RuntimeError("refuse to submit: " + "; ".join(gate.failures))
```

`todos_closed` gates the move into review and the later `in_review -> accepted`
and `accepted -> done` transitions, so an open todo blocks the rest of the
lifecycle. Closing a todo asserts the work is done; deferring work means
raising the scope change with the coordinator and removing the todo's premise,
never closing it falsely and never reopening the deferral as a new todo.

Acceptance criteria are proven, not ticked. Read them with
`bl.acceptance_criteria(key)` to confirm nothing is unimplemented, and name in
the handoff note which evidence proves which criterion — file and line, test
name, or validation result. `bl.verify_criterion` is reviewer-owned: the API
accepts only the assigned reviewer while the task is in review and rejects the
task's implementer and creator, so never call it and never open a session under
another identity to do so. Expect verdicts to be cleared when the criteria are
rewritten, either delivery role changes, or the task moves backwards into
active work.

## Evidence boundaries

Use `bl.add_item(key, "note", summary)` for a short task-local summary. Keep
review requests and responses in review threads. Use `bl.artifact...` only for
an explicitly requested durable artifact. Do not write database rows, status
fields, or ad hoc evidence files.

For a final handoff, report only the task key, changed scope, the evidence
proving each acceptance criterion, validation result, review roots still
awaiting the reviewer, and current gate state. The Backlog record remains the
detailed audit trail.

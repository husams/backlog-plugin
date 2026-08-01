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

## Evidence boundaries

Use `bl.add_item(key, "note", summary)` for a short task-local summary. Keep
review requests and responses in review threads. Use `bl.artifact...` only for
an explicitly requested durable artifact. Do not write database rows, status
fields, or ad hoc evidence files.

For a final handoff, report only the task key, changed scope, validation
result, review roots still awaiting the reviewer, and current gate state. The
Backlog record remains the detailed audit trail.

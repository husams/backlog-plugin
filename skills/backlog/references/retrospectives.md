# Retrospective improvement actions

Retrospective actions capture repeated workflow problems and the improvement
proposed to prevent them. They are project-owned records with `R-` keys, kept
separate from delivery tasks because their lifecycle is fixed:

```text
Created --accept--> Ready --close(project + Feature or Bug)--> Done
Created --reject(reason)-------------------------------------> Rejected
Ready   --reject(reason)-------------------------------------> Rejected
```

Every action references an Iteration in its owning project. Rejection is
allowed from Created or Ready and always requires a non-empty reason. Done and
Rejected are terminal.

Closing means the improvement has been translated into tracked delivery work.
It requires an explicit project plus exactly one Feature or Bug. That target
may live in another project in the same store; the target task does not need to
be finished before the retrospective action is closed.

## Agent API

Use the Python API for multi-step action handling:

```python
from backlog_cli import api

with api.open(actor="facilitator") as bl:
    action = bl.create_retrospective_action(
        iteration="I-007",
        repeated_issue="Release checks were skipped in three handoffs.",
        proposed_solution="Add a reusable release-check skill and CI gate.",
        title="Automate release handoff checks",
    )
    action_key = action.key

with api.open(actor="product-manager") as bl:
    action = bl.accept_retrospective_action(action_key)
    action = bl.close_retrospective_action(
        action.key,
        resolution_project="agent-tooling",
        feature="F-003",
    )
```

For a rejected proposal:

```python
action = bl.reject_retrospective_action(
    "R-004",
    reason="The repeated issue came from a retired deployment path.",
)
```

Query with `bl.retrospective_action(key)` or
`bl.retrospective_actions(status=api.RetrospectiveStatus.READY,
iteration="I-007")`. The public status filter is an enum; arbitrary strings are
rejected.

## CLI

```bash
$BL retrospective add --iteration I-007 --actor facilitator \
  --issue "Release checks were skipped in three handoffs." \
  --solution "Add a reusable release-check skill and CI gate." \
  --title "Automate release handoff checks"

$BL retrospective list [--status created|ready|done|rejected] [--iteration I-007]
$BL retrospective show R-001
$BL retrospective accept R-001 --actor product-manager
$BL retrospective reject R-002 --reason "Superseded by the platform migration" \
  --actor product-manager
$BL retrospective close R-001 --resolution-project agent-tooling \
  --feature F-003 --actor product-manager
$BL retrospective close R-004 --resolution-project runtime --bug B-012
$BL retrospective history R-001
```

Creation requires an actor and persists it as `created_by`. The creator cannot
accept the action, and an attributed action cannot be accepted without an
actor. These refusals happen before the update and audit event. Only migrated
v15 rows created without an actor retain legacy actor-less acceptance.

`--feature` and `--bug` are mutually exclusive. A missing project, a target of
another task type, or an illegal lifecycle operation is rejected before the
action changes.

## Recorded fields

A retrospective action retains its owning project, Iteration, title, repeated
issue, proposed solution, status, actors and timestamps. Rejected actions keep
their reason. Done actions keep the resolution project, task key, and whether
that task is a Feature or Bug. Create, accept, reject, and close operations are
also written to the shared event audit trail.

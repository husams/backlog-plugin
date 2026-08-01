# Retrospective action coordination

Retrospective actions are project-owned `R-` records tied to an Iteration and
follow a fixed lifecycle:

```text
Created --accept--> Ready --close(project + Feature or Bug)--> Done
Created --reject(reason)-------------------------------------> Rejected
Ready   --reject(reason)-------------------------------------> Rejected
```

## Public API sequence

Create with an owning Iteration and a named actor:

```python
with api.open(actor="facilitator") as bl:
    action = bl.create_retrospective_action(
        iteration="I-007",
        repeated_issue="Release checks were skipped in three handoffs.",
        proposed_solution="Add a reusable release-validation skill and gate.",
    )
```

Acceptance must be performed by a named actor other than `created_by`:

```python
with api.open(actor="product-manager") as bl:
    bl.accept_retrospective_action(action.key)
```

The creator cannot accept its own action. A rejection requires a non-empty
reason. A close requires `resolution_project` and exactly one of `feature` or
`bug`; the target may be in another project and must be that project's Feature
or standalone Bug. Use `bl.reject_retrospective_action(...)` or
`bl.close_retrospective_action(...)`; do not mutate lifecycle status.

## Coordinator checks

Before each lifecycle operation, inspect the action and current allowed
operation through the documented API. Keep open actions visible in the
coordination report, including their iteration, owner, acceptance actor, and
resolution target. Never accept or reject an action on behalf of another
actor, and never add a `refiner` field to represent refinement or retrospective
attribution.

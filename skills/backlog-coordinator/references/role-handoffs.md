# Role handoffs and identity boundaries

Backlog records authorship and assignments as audit data. Treat each identity
as a separate authority even when the same person operates multiple tools.

## Required identities

- `created_by` is immutable and is recorded when a task is created with
  `actor=...`.
- `to` is the documented implementer assignment.
- `reviewer` is the opening-reviewer assignment.
- The actor on `refinement.accepted` is an independent named actor, distinct
  from the creator and upcoming implementer. It is an event attribution, not a
  persisted role field.

Assign both delivery roles explicitly:

```python
with api.open(actor="coordinator") as bl:
    bl.assign("S-001", to="codex", reviewer="claude")
```

Do not accept refinement for the creator, implement work as the reviewer, make
review decisions as the implementer, or merge on behalf of a designated
merger. A missing or conflicting identity is a blocked handoff, not a reason to
invent a fallback role.

## Handoff contract

Before handing work to an implementer, verify the task is assigned, Ready, and
startable. The implementer starts only with an allowed `work.started` action,
records current validation evidence, and answers every review root at blocker,
nice-to-have, and info severity. The coordinator may inspect those responses
and the opening reviewer's decisions but must not supply either decision.

Before handing work to the reviewer, use one narrowly filtered inbox read for
the task and reviewer role. Retain `root_key -> reply_to`; use
`bl.review_updates(root, after=last_seen)` for known roots, advancing only
after all updates are processed. Make one final filtered discovery read for
previously unseen roots. The opening reviewer accepts or rejects every
implementer response and ends through the configured approval or
changes-requested action.

## Evidence and gates

Use `bl.can(key, target="merge")` or the documented gate command as a query;
an unsuccessful result is a stop condition. Do not submit a destination status
or a `feedback.*` action directly. Review feedback belongs in review threads;
durable artifacts and task notes are separate documented operations. Keep the
handoff summary limited to changed scope, validation/gate result, unresolved
roots, and the next allowed action.

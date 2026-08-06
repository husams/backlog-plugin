# Reviewer call sequences

The sibling `backlog` skill owns the API contract. Read its canonical
references for full semantics: [review](../../backlog/references/review.md),
[API](../../backlog/references/api.md), and
[workflow](../../backlog/references/workflow.md).

## Initial and incremental reads

Open a reviewer-attributed session and filter the first inbox read by every
known scope:

```python
from backlog_cli import api

with api.open(actor=reviewer) as bl:
    task = bl.task(key)
    roots = bl.inbox(actor=reviewer, role="reviewer")
    cursors = {thread.root_key: thread.reply_to for thread in roots
               if thread.task_key == task.key}
```

For a known root, use only its retained cursor:

```python
updates = bl.review_updates(root, after=cursors[root])
for comment in updates:  # oldest to newest
    process(comment)
if updates:
    cursors[root] = updates[-1].key
```

Advance a cursor only after every update for that root has been processed.
Before a verdict, perform one final filtered inbox discovery and retain only
roots not already in `cursors`. Do not use a truncated result for a decision.

## Thread writes

Use `ReviewSeverity` for typed roots and reply to the current `reply_to`, not
the root key:

```python
from backlog_cli.api import ReviewSeverity

root = bl.review_open(key, author=reviewer, severity=ReviewSeverity.BLOCKER,
                      body=body, file=path, line=line)
bl.review_reply(reply_to, author=implementer, action="fix", body=evidence)
bl.review_reply(reply_to, author=reviewer, action="accept", body=decision)
```

The opening reviewer owns every `accept` or `reject` decision. Implementers
must answer blocker, `nice_to_have`, and `info` roots with `fix`, `comment`,
or `reject`; reviewers must then accept or reject each response. Reopen an
accepted regression with `bl.review_reopen(root, author=reviewer, body=reason)`.

## Acceptance verdicts

Every criterion carries its own reviewer verdict. Read the contract first and
refuse to approve an empty one:

```python
criteria = bl.acceptance_criteria(key)
if not criteria:
    raise RuntimeError("no acceptance criteria: refuse to approve and report the gap")
```

Each entry exposes `id`, `task_key`, `position`, `content`, `state`
(`unverified` / `met` / `unmet`), `verdict_by`, `verdict_at`, `evidence`, and
`stale`. A `stale` entry means the criterion text changed after the verdict was
recorded; treat it as `unverified` and verify it again.

Record one attributed verdict per criterion, each with the evidence that was
actually inspected:

```python
bl.verify_criterion(criterion["id"], met=True,
                    evidence="src/router.py:118 rejects expired links; "
                             "tests/test_router.py::test_expired_link passes")
bl.verify_criterion(other["id"], met=False,
                    evidence="no test covers the expiry boundary; "
                             "run_task shows T-104 pending")
```

`verify_criterion` requires a substantive evidence string, a task currently in
review, and the assigned reviewer identity. The task's assignee/implementer,
creator, and any substitute third actor are rejected. Re-verifying the same
criterion overwrites the previous verdict. Verdicts are cleared automatically
when the criteria are rewritten, either delivery role is reassigned, or the
task moves backwards out of review or done into active work. When returning
work for rework, drop the whole set explicitly with
`bl.clear_criterion_verdicts(key, reason=...)`.

The `acceptance_criteria_verified` gate passes only when every criterion holds
a current `met` verdict from the assigned independent reviewer, and it fails
outright on a task with zero criteria. It has no waiver.

## Verdict and gate

Inspect `bl.actions(key)` immediately before a semantic verdict and submit only
an allowed action through `bl.trigger`. Before an approval action, confirm with
one final read that every criterion is `met` and not `stale`, that `bl.todos(key)`
holds no open todo, and that `bl.can(key, target="accepted")` is green:

```python
gate = bl.can(key, target="accepted")
if not gate.ok:
    raise RuntimeError("refuse to approve: " + "; ".join(gate.failures))
```

Report each failure verbatim and request changes; do not retry the approval
with a waiver. For merge readiness, use
`bl.can(key, target="merge")`; report every `gate.failures` entry when false.
The CLI equivalent returns `0` for allowed, `2` for blocked, and must never be
treated as a merge operation. The reviewer hands off; the designated merger
performs the merge.

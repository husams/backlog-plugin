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

## Verdict and gate

Inspect `bl.actions(key)` immediately before a semantic verdict and submit only
an allowed action through `bl.trigger`. For merge readiness, use
`bl.can(key, target="merge")`; report every `gate.failures` entry when false.
The CLI equivalent returns `0` for allowed, `2` for blocked, and must never be
treated as a merge operation. The reviewer hands off; the designated merger
performs the merge.

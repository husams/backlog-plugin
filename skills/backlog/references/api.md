# The Python API

Reach for this when the CLI has no flag for the question — anything that needs
counting, filtering, joining or comparing across many tasks. One process, one
connection, however many operations.

```bash
backlog-py <<'PY'
from backlog_cli import api

with api.open(actor="claude") as bl:
    stale = [t for t in bl.tasks(status="in_review") if t.idle_days > 3]
    print(f"{len(stale)} stale: {', '.join(t.key for t in stale)}")
PY
```

## Three rules

1. **Feed the snippet on stdin.** Never write a `.py` file, a temp file or any
   other artifact to answer a question. The heredoc above leaves nothing behind.
2. **Print the answer, not the data.** Reduce inside Python — count it, sort it,
   pick the top three — and print a sentence or a short table. Never print a
   task list you intend to read and re-summarise, and never print JSON.
3. **The rules still apply.** `trigger` submits a typed action and lets this
   project's workflow choose the destination; `can` runs the real gates and
   every write is logged. There is no direct-status or SQL escape hatch.

## Opening a session

```python
api.open(project=None, actor=None)     # context manager -> Backlog
```

Resolves the same store the CLI would (`.backlog/`, `BACKLOG_DB`, …). Commits on
a clean exit, closes the connection either way. `actor` is the default attribution
for writes made through the session.

## Backlog — the session

| | |
| --- | --- |
| `bl.store` | `Store(backend, scope, project, location)`; `str()` is one line |
| `bl.projects()` | every project slug in the store |
| `bl.task(key)` | one `Task`; raises `BacklogError` if absent |
| `bl.find(key)` | one `Task` or `None` |
| `bl.tasks(status=, task_type=, assignee=, reviewer=, parent=, open_only=)` | filtered `list[Task]`, priority then key |
| `bl.counts()` | `{status: n}` across the project |
| `bl.statuses(task_type="story")` | the statuses this project's flow defines |
| `bl.flow(task_type="story")` | the `Workflow`: `.allows(a,b)`, `.next_from(s)`, `.display(s)`, `.initial`, `.terminal` |
| `bl.actions(key)` | the `list[Action]` configured for this task's current state |
| `bl.startable(actor=None)` | open tasks with no unfinished blockers |
| `bl.blocked()` | `[(Task, [blocking keys])]` for every blocked open task |
| `bl.cycles()` | dependency cycles as key lists; empty when sane |
| `bl.can(key, target="merge", **waivers)` | `Gate` — evaluates, never moves |
| `bl.inbox(actor=None, role=None, severity=None)` | `list[Thread]` waiting on someone, optionally filtered by `ReviewSeverity` |
| `bl.threads(key, state="open", severity=None)` | `list[Thread]` on one task, optionally filtered by `ReviewSeverity` |
| `bl.trigger(key, action: Action, actor=None, operation="api.trigger", parameters=None, **waivers)` | submit a typed `Action`; the workflow selects and enforces the destination |
| `bl.set_pr(key, url=, number=, repo=, state=, review_state=, actor=)` | record PR data and emit the matching `pr.*` action |
| `bl.review_open(key, author=, body=, severity=ReviewSeverity.BLOCKER, role=, title=, file=, line=)` | reviewer opens a typed thread; task status is unchanged |
| `bl.review_reply(comment, author=, action=, body=, role=)` | advance the thread workflow; only reviewer acceptance closes it |
| `bl.review_set_severity(root, severity=ReviewSeverity.*, author=)` | auditably reclassify a review thread |
| `bl.assign(key, to=None, reviewer=None)` | reassign |
| `bl.commit()` | flush early; `open()` commits for you on exit |

Waivers match the CLI flags: `allow_blocked`, `no_pr`, `allow_open_children`.

## Actions and transition hooks

Import the standard action enum from the public API:

```python
from backlog_cli.api import Action
```

Submit facts rather than destination states:

```python
with api.open(actor="github-actions") as bl:
    task = bl.trigger(
        "S-004",
        Action.PR_MERGED,
        operation="github.pull_request.closed",
        parameters={"pull_request": 91},
    )
```

The Python API requires an `Action` enum member and rejects arbitrary strings.
The CLI serializes the same enum values for shell and automation callers.
`feedback.*` members are review-managed and are rejected by `bl.trigger`;
`feedback.resolved` is emitted internally only when every blocker has reviewer
acceptance.

Backlog loads `.backlog/workflow.yaml` when present, otherwise the bundled
`assets/default-workflow.yaml`. It resolves `(task type, current state,
action)` to a destination, imports `pre_transition` and `post_transition` from
`.backlog/hooks/__init__.py`, enforces the normal transition and gates,
commits, then runs the post hook.

Both hooks receive the active public `Backlog` session as their fifth
argument. They may call documented methods such as `task`, `tasks`, `can`,
`actions`, `threads`, `trigger`, `set_pr`, `review_open`, and `review_reply`. They must not
use private attributes or access database tables.

Review severity is also a public enum, not a free-form string:

```python
from backlog_cli.api import ReviewSeverity

bl.review_open(
    "F-001",
    author="senior-developer",
    body="The failure behavior is unspecified.",
    severity=ReviewSeverity.BLOCKER,
)
```

Its fixed members are `BLOCKER`, `NICE_TO_HAVE`, and `INFO`. Passing a string
to the Python API is a type error. CLI values use the enum's lowercase
serialized value.

No public API accepts a destination status. `trigger` is the only general
state-transition entry point; project hooks may override the action's proposed
state through `pre_transition`.

## Task

Any column is an attribute: `key`, `title`, `status`, `task_type`, `priority`,
`assignee`, `reviewer`, `parent_key`, `pr_url`, `pr_review_state`, `created_at`,
`updated_at`, `closed_at`. Plus:

| | |
| --- | --- |
| `t.age_days` / `t.idle_days` | days since created / since last change |
| `t.is_open` | not closed |
| `t.children` | `list[Task]` |
| `t.blockers` | unfinished blockers as `{other_key, other_status}` |
| `t.items(kind=None)` | criteria / checklist / notes as strings |
| `t.open_threads` | root keys of open review threads |

`str(task)` is `KEY  status  title`.

## Gate

`gate.ok`, `gate.failures` (list of `"check: detail"`), `gate.checks` as
`(name, passed, detail)`. `str(gate)` is one line, `READY` or `BLOCKED` with the
reasons — print it directly.

## Thread

`root_key`, `task_key`, `task_title`, `opened_by`, `state`, `awaiting_role`,
`awaiting_actor`, `body` (root comment, first line), `latest`, `latest_author`,
`file`, `line`, `where` (`file:line`), `hidden_comments`, `reply_to`, `age_days`.

Only ever the root comment and the latest reply. If that is genuinely not
enough, `backlog review show <root> --full`.

## Worked examples

Which stories would be unblocked if S-002 landed:

```bash
backlog-py <<'PY'
from backlog_cli import api
with api.open() as bl:
    freed = [t.key for t in bl.tasks(open_only=True)
             if any(b["other_key"] == "S-002" for b in t.blockers)]
    print(f"landing S-002 unblocks {len(freed)}: {', '.join(freed) or 'nothing'}")
PY
```

Everything ready to merge right now, without printing what is not:

```bash
backlog-py <<'PY'
from backlog_cli import api
with api.open() as bl:
    ready = [g.key for g in (bl.can(t.key) for t in bl.tasks(status="in_review")) if g.ok]
    print(", ".join(ready) if ready else "nothing is merge-ready")
PY
```

Submit one semantic action to a batch, reporting only the refusals:

```bash
backlog-py <<'PY'
from backlog_cli import api
with api.open(actor="claude") as bl:
    completed, refused = [], []
    for t in bl.tasks(status="accepted"):
        try:
            bl.trigger(t.key, api.Action.DELIVERY_RELEASED)
            completed.append(t.key)
        except api.BacklogError as exc:
            refused.append(f"{t.key}: {exc}")
    print(f"{len(completed)} completed" + (f"; refused {'; '.join(refused)}" if refused else ""))
PY
```

## When not to use this

A single fact the CLI already prints — `backlog show S-004`, `backlog next`,
`backlog gate S-004 --for merge` — is one command and one process. Use the CLI.
See [cli.md](cli.md) for the full surface and [scripts.md](scripts.md) for the
common requests that are already written.

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

## Retrieval and processing rules

1. **Feed the snippet on stdin.** Never write a `.py` file, a temp file or any
   other artifact to answer a question. The heredoc above leaves nothing behind.
2. **Filter before reducing.** Pass every semantic filter the public method
   supports (`key`, `actor`, `role`, `state`, `severity`, `status`, `type`,
   `after`, and so on). Do not fetch all records and ask the model to locate the
   relevant ones.
3. **Reduce before printing.** Use Python to count, group, compare, validate,
   or select the relevant records. Print only the conclusion and the minimum
   keys or fields needed to support it. Never print JSON or a dataset for the
   model to re-read and summarise.
4. **Paginate only to establish completeness after filtering.** The public
   collection methods documented below return complete lists, so reduce those
   lists directly in-process. If another documented method reports a
   continuation cursor or bounded batch, first apply its semantic filters, then
   consume every matching batch while retaining only the aggregate or relevant
   matches. Do not print each batch or invent a hard result limit.
5. **Reject incomplete evidence.** A truncated response, `budget.max_results`,
   output clipping, or an unconsumed continuation cursor cannot support a
   conclusion, review, or feedback response. Establish completeness or report
   that no conclusion can be made.
6. **The workflow rules still apply.** `trigger` submits a typed action and lets this
   project's workflow choose the destination; `can` runs the real gates and
   every write is logged. There is no direct-status or SQL escape hatch.

## Opening a session

```python
api.open(project=None, actor=None)     # context manager -> Backlog
```

Resolves the same store the CLI would (`.backlog/`, `BACKLOG_DB`, …). Commits on
a clean exit, closes the connection either way. `actor` is the default
attribution for writes and is mandatory in practice for review writes: when it
is set, `review_*` rejects any different `author=` value. It is still a caller
assertion, not cryptographic authentication.

## Backlog — the session

| | |
| --- | --- |
| `bl.store` | `Store(backend, scope, project, location)`; `str()` is one line |
| `bl.projects()` | every project slug in the store |
| `bl.task(key)` | one `Task`; raises `BacklogError` if absent |
| `bl.find(key)` | one `Task` or `None` |
| `bl.retrospective_action(key)` | one `RetrospectiveAction`; raises `BacklogError` if absent |
| `bl.retrospective_actions(status=, iteration=)` | filtered `list[RetrospectiveAction]`; status is a `RetrospectiveStatus` enum |
| `bl.tasks(status=, task_type=, assignee=, reviewer=, parent=, open_only=)` | filtered `list[Task]`, priority then key |
| `bl.counts()` | `{status: n}` across the project |
| `bl.statuses(task_type="story")` | the statuses this project's flow defines |
| `bl.flow(task_type="story")` | the `Workflow`: `.allows(a,b)`, `.next_from(s)`, `.display(s)`, `.initial`, `.terminal` |
| `bl.actions(key)` | the `list[Action]` configured for this task's current state |
| `bl.startable(actor=None, iteration=None)` | unscoped startable Features, Stories, Bugs, and subtasks with no unfinished blockers; Iteration containers are excluded, and an Iteration filter requires an Open Iteration |
| `bl.blocked()` | `[(Task, [blocking keys])]` for every blocked open task |
| `bl.cycles()` | dependency cycles as key lists; empty when sane |
| `bl.dependencies(key, kind=None)` | all incoming and outgoing edges, including satisfied dependencies, notes and statuses |
| `bl.artifacts(key)` | all durable artifacts recorded on a task |
| `bl.can(key, target="merge", **waivers)` | `Gate` — evaluates, never moves |
| `bl.inbox(actor=None, role=None, severity=None)` | `list[Thread]` waiting on someone, optionally filtered by `ReviewSeverity` |
| `bl.threads(key, state="open", severity=None)` | `list[Thread]` on one task, optionally filtered by `ReviewSeverity` |
| `bl.trigger(key, action: Action, actor=None, operation="api.trigger", parameters=None, **waivers)` | submit a typed `Action`; the workflow selects and enforces the destination |
| `bl.set_pr(key, url=, number=, repo=, state=, review_state=, actor=)` | record PR data and emit the matching `pr.*` action |
| `bl.review_open(key, author=, body=, severity=ReviewSeverity.BLOCKER, role=, title=, file=, line=)` | reviewer opens a typed thread; task status is unchanged |
| `bl.review_reply(comment, author=, action=, body=)` | advance the thread workflow; participant roles come from the thread |
| `bl.review_updates(root, after=)` | only comments added after the last comment key already in context |
| `bl.review_audit(root)` | decision authors, actions and timestamps for one thread |
| `bl.review_reopen(root, author=, body=, role=)` | reviewer reopens a closed thread, posts a reply, and emits managed blocker invalidation |
| `bl.review_set_severity(root, severity=ReviewSeverity.*, author=)` | auditably reclassify a review thread |
| `bl.assign(key, to=None, reviewer=None)` | reassign |
| `bl.create_feature(...)` / `bl.create_story(...)` / `bl.create_bug(...)` / `bl.create_iteration(...)` | create with optional plain/executable `acceptance_criteria`; Bugs and Iterations are standalone |
| `bl.create_retrospective_action(iteration=, repeated_issue=, proposed_solution=, title=None)` | create a project-owned `R-` action in Created |
| `bl.accept_retrospective_action(key)` | move a Created action to Ready |
| `bl.reject_retrospective_action(key, reason=)` | reject a Created or Ready action with a retained reason |
| `bl.close_retrospective_action(key, resolution_project=, feature=...\|bug=...)` | close a Ready action against exactly one Feature or Bug, including in another project |
| `task.iteration_members` / `task.iterations` | view Iteration membership from either side: an Iteration exposes its member Tasks, while a Story or Bug exposes its Iterations |
| `bl.add_iteration_member(iteration, member)` / `bl.remove_iteration_member(iteration, member)` | auditably manage Ready Story/Bug membership on an Open Iteration; actor comes from `api.open(actor=...)` |
| `bl.startable(actor, iteration="I-001")` | request unblocked deliverable work from one explicit Iteration; Iteration rows are excluded |
| `bl.task_type_counts()` | counts by type, including `iteration` |
| `bl.add_item(key, kind, content, execution_spec=None)` | author one plain/shell/hook item |
| `bl.set_items(key, kind, items)` | replace items from strings or `{content, execution}` mappings |
| `bl.run_item(item_id, project_root, policy=None, actor=None)` | execute one shell or hook item under trusted local policy |
| `bl.run_task(key, project_root, fail_fast=False, policy=None, actor=None)` | execute all executable items in declaration order |
| `bl.execution_history(item_id, limit=20, project_root=None)` | bounded newest-first result history with stale metadata |
| `bl.waive_validation(item_id, reason=, actor=None)` | record an audited waiver for the current execution spec |
| `bl.commit()` | flush early; `open()` commits for you on exit |

Waivers match the CLI flags: `allow_blocked`, `no_pr`, `allow_open_children`.

Shell runs return `ExecutionResult`; hook runs return
`ValidationExecutionResult`; `run_task` may contain both. Local policy is read
from the explicit project checkout. The default batch behavior runs
everything; `fail_fast=True` stops after the first fail, error, or item
timeout. Required-item aggregate success means current pass (waivers satisfy
the acceptance gate but are not reported as execution passes).

## Retrospective action lifecycle

Retrospective actions use project-local `R-` keys and always reference an
Iteration in their owning project. Their fixed lifecycle is
`Created -> Ready -> Done`, with `Rejected` as a terminal alternative from
Created or Ready. Rejection requires a non-empty reason. Done requires a
resolution project and exactly one Feature or Bug in that project.

```python
from backlog_cli import api

with api.open(actor="facilitator") as bl:
    action = bl.create_retrospective_action(
        iteration="I-007",
        repeated_issue="Release validation was skipped repeatedly.",
        proposed_solution="Add a release-validation skill and CI check.",
    )
    action_key = action.key

with api.open(actor="product-manager") as bl:
    bl.accept_retrospective_action(action_key)
    bl.close_retrospective_action(
        action_key,
        resolution_project="agent-tooling",
        feature="F-003",
    )

    ready = bl.retrospective_actions(status=api.RetrospectiveStatus.READY)
```

`RetrospectiveAction` exposes stored columns and joined reference fields,
including `key`, `project_slug`, `iteration_key`, `repeated_issue`,
`proposed_solution`, `status`, `rejection_reason`,
`resolution_project_slug`, `resolution_task_key`, and
`resolution_task_type`. It also provides `age_days`, `idle_days`, and
`is_open`. See [retrospectives.md](retrospectives.md).

## Bug and Iteration lifecycle

`create_bug(title, **kwargs)` creates a standalone `Task` with a `B-` key;
there is no Feature parent. It supports the same child, dependency, review,
PR, and delivery operations as a Story. `create_iteration(title, **kwargs)`
creates a standalone `Task` with an `I-` key. The returned tasks expose their
`task_type`, status, parent/child views, and (where applicable) PR and review
metadata like every other `Task`.

Use the public action enum for an Iteration's lifecycle. The workflow, rather
than the caller, selects the destination:

```python
from backlog_cli import api
from backlog_cli.api import Action

with api.open(actor="codex") as bl:
    bug = bl.create_bug(
        "Recovery link expires too early",
        priority="P1",
        acceptance_criteria=["A valid link remains usable until its expiry."],
    )
    bl.trigger(bug.key, Action.REFINEMENT_ACCEPTED)
    iteration = bl.create_iteration("July delivery slice", priority="P1")
    bl.trigger(iteration.key, Action.ITERATION_OPENED)
    bl.add_iteration_member(iteration.key, bug.key)

    iteration = bl.task(iteration.key)
    members = iteration.iteration_members
    selected = bl.startable(actor="codex", iteration=iteration.key)

    # After the member reaches a finished status and comments are accepted:
    bl.trigger(iteration.key, Action.ITERATION_CLOSED)
    # After closure, if no retained member conflicts with another Open Iteration:
    bl.trigger(iteration.key, Action.ITERATION_REOPENED)
```

`add_iteration_member` and `remove_iteration_member` are audit-recorded
operations. Adding requires an Open Iteration and a Ready Story or standalone
Ready Bug; Features, subtasks, Iterations, Bugs with a parent, and non-Ready
members are rejected. A Story or Bug can belong to only one Open Iteration.
Those checks happen when adding: an admitted member remains a member while it
starts, enters review, needs work, becomes Accepted or Done, or returns to
Incomplete. Removing from an Open Iteration does not delete or transition the
member. Reopening a Closed Iteration rejects retained members that conflict
with another Open Iteration.

The Iteration row is excluded from unscoped `startable()` results, while
otherwise-startable Features and subtasks remain available there. Passing
`iteration="I-001"` requires that Iteration to be Open and returns only its
eligible Story and Bug members; `task.iterations` and
`task.iteration_members` provide the corresponding views from either side.

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
acceptance. Opening or reopening a blocker emits managed `feedback.posted` or
`feedback.reopened`; the shipped workflow maps either event from Ready to
Incomplete for deliverable tasks. On an Iteration, `feedback.posted`,
`feedback.reopened`, and `feedback.resolved` are explicit lifecycle no-ops;
`iteration_comments_closed` still gates `Action.ITERATION_CLOSED` on every
open comment severity.

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

Iteration comments use the same `review_open`, `review_reply`, `threads`,
`review_updates`, and `review_reopen` APIs as comments on other tasks. The
opening reviewer owns acceptance, and all blocker, nice-to-have, and info
threads must be closed before an Iteration can close. Open and resolved
comments remain available through the Iteration's task and thread views after
it is Closed.

## Task

Any column is an attribute: `key`, `title`, `status`, `task_type`, `priority`,
`assignee`, `reviewer`, `parent_key`, `pr_url`, `pr_review_state`, `created_by`,
`created_at`, `updated_at`, `closed_at`. Plus:

Every new task requires an actor and stores it as `created_by`.
`refinement.accepted` requires a different named actor. The check runs before
hooks and audit logging. Migrated legacy tasks whose creation event had no
actor remain unattributed and operable.

| | |
| --- | --- |
| `t.age_days` / `t.idle_days` | days since created / since last change |
| `t.is_open` | not closed |
| `t.children` | `list[Task]` |
| `t.parent` | parent task key; alias for `parent_key` |
| `t.blockers` | unfinished blockers as `{other_key, other_status}` |
| `t.items(kind=None)` | criteria / checklist / notes as strings |
| `t.item_details(kind=None)` | plain/executable items with declarations, requirement and state |
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

## Incremental review reads

Read a narrowly filtered inbox once. Keep each thread's `reply_to` key in
session context as `LAST_SEEN`, then read only later additions:

```python
updates = bl.review_updates("C-003", after="C-007")
```

The result is `list[ReviewComment]` ordered oldest-to-newest; an empty list
means nothing changed.
Each comment exposes `key`, `root_key`, `parent_key`, `author`, `assignee`,
`reviewer`, `role`, `action`, `body`, `file`, `line`, and `created_at`.
`reviewer` is inherited from the thread opener. For a developer reply,
`assignee` is the responding author.

For multiple already-known threads, process updates in code and print only
threads that actually changed:

```bash
backlog-py <<'PY'
from backlog_cli import api

# Retain this small mapping in session context from the first inbox read.
last_seen = {"C-003": "C-007", "C-010": "C-012"}

with api.open() as bl:
    changed = []
    next_last_seen = []
    for root, after in last_seen.items():
        updates = bl.review_updates(root, after=after)
        if updates:
            for comment in updates:
                changed.append((root, comment.key, comment.author,
                                comment.action, comment.body))
            next_last_seen.append((root, updates[-1].key))
    for root, key, author, action, body in changed:
        print(f"{root} new={key} by={author} action={action}: {body}")
    for root, key in next_last_seen:
        print(f"{root} advance_LAST_SEEN={key} after processing")
PY
```

After processing every printed comment for a root, copy its
`advance_LAST_SEEN` value into retained session context. Do not call `inbox()`
again to detect comments on known roots, and do not call a full thread read to
rediscover comments already in context. If new roots may have opened and a
handoff verdict requires a complete inbox, one final inbox call is allowed only
with all applicable semantic filters and in-process reduction to previously
unseen root keys. A full thread read is an exception only when session context
was lost or a new comment is genuinely ambiguous without omitted history;
state which condition applies first.

## Worked examples

Which stories would be unblocked if S-002 landed:

```bash
backlog-py <<'PY'
from backlog_cli import api
with api.open() as bl:
    freed = [t.key for t in bl.tasks(open_only=True, task_type="story")
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

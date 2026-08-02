"""backlog — backlog tracker for coding agents (SQLite or shared PostgreSQL)."""

from __future__ import annotations


from .. import (
    core,
    deps,
    execution,
    hooks,
    review,
    workflow,
)
from ..db import (
    BacklogError,
)
from ..render import (
    render_task,
    row_to_dict,
    tasks_table,
)


from .context import Ctx, _task_rows

from .authoring import (
    _execution_spec,
)


def _list(ctx: Ctx, args, task_type: str | None) -> int:
    where, params = "", []
    if task_type:
        where += " AND t.task_type = ?"
        params.append(task_type)
    elif getattr(args, "type", None):
        where += " AND t.task_type = ?"
        params.append(core.normalize_type(args.type))
    if getattr(args, "status", None):
        where += " AND t.status = ?"
        params.append(core.normalize_status(args.status))
    if getattr(args, "open", False):
        where += " AND t.closed_at IS NULL"
    if getattr(args, "assignee", None):
        where += " AND t.assignee = ?"
        params.append(args.assignee)
    if getattr(args, "reviewer", None):
        where += " AND t.reviewer = ?"
        params.append(args.reviewer)
    if getattr(args, "parent", None):
        where += " AND p.key = ?"
        params.append(core.normalize_key(args.parent))
    rows = _task_rows(ctx.conn, ctx.pid, where, params)
    ctx.emit([row_to_dict(r) for r in rows], tasks_table(rows))
    return 0


def cmd_list(ctx: Ctx, args) -> int:
    return _list(ctx, args, None)


def cmd_feature_list(ctx: Ctx, args) -> int:
    return _list(ctx, args, "feature")


def cmd_story_list(ctx: Ctx, args) -> int:
    return _list(ctx, args, "story")


def cmd_bug_list(ctx: Ctx, args) -> int:
    return _list(ctx, args, "bug")


def cmd_subtask_list(ctx: Ctx, args) -> int:
    return _list(ctx, args, "subtask")


def cmd_set(ctx: Ctx, args) -> int:
    item_spec = _execution_spec(args)
    criteria = None
    if args.ac is not None:
        criteria = [line for line in args.ac.splitlines() if line.strip()]
        if item_spec and len(criteria) != 1:
            raise BacklogError(
                "executable --ac requires exactly one non-empty acceptance criterion"
            )
    row = core.update_task(
        ctx.conn,
        ctx.pid,
        args.key,
        actor=args.actor,
        title=args.title,
        description=args.description,
        priority=args.priority,
        branch=args.branch,
        owner=args.owner,
        parent=args.parent,
    )
    if criteria is not None:
        items = core.set_items(
            ctx.conn,
            ctx.pid,
            row["key"],
            "acceptance_criteria",
            criteria,
            actor=args.actor,
        )
        if item_spec:
            execution.set_executable(ctx.conn, items[0]["id"], item_spec)
    ctx.emit(row_to_dict(row), render_task(ctx.conn, row))
    return 0


def cmd_assign(ctx: Ctx, args) -> int:
    row = core.assign(
        ctx.conn,
        ctx.pid,
        args.key,
        to=args.to,
        reviewer=args.reviewer,
        actor=args.actor,
        to_kind=args.to_kind,
        reviewer_kind=args.reviewer_kind,
    )
    ctx.emit(
        row_to_dict(row),
        f"{row['key']}  assignee={row['assignee'] or '-'} ({row['assignee_kind']})  "
        f"reviewer={row['reviewer'] or '-'} ({row['reviewer_kind']})",
    )
    return 0


def cmd_show(ctx: Ctx, args) -> int:
    task = core.get_task(ctx.conn, ctx.pid, args.key)
    payload = row_to_dict(task)
    payload["items"] = [
        execution._item_details(ctx.conn, i)
        for i in core.task_items(ctx.conn, task["id"])
    ]
    payload["dependencies"] = deps.edges_for(ctx.conn, task["id"])
    payload["blocked_by"] = [
        b["other_key"] for b in deps.blockers(ctx.conn, task["id"])
    ]
    payload["open_threads"] = [
        review.thread_summary(ctx.conn, t["root_key"])
        for t in core.open_threads(ctx.conn, task["id"])
    ]
    payload["children"] = [
        row_to_dict(c) for c in core.children_of(ctx.conn, task["id"])
    ]
    payload["artifacts"] = [
        row_to_dict(a) for a in core.list_artifacts(ctx.conn, task["id"])
    ]
    ctx.emit(payload, render_task(ctx.conn, task))
    return 0


def cmd_action(ctx: Ctx, args) -> int:
    parameters = {}
    for item in args.parameter or []:
        if "=" not in item:
            raise BacklogError(f"parameter must be NAME=VALUE, got {item!r}")
        name, value = item.split("=", 1)
        if not name:
            raise BacklogError("parameter name cannot be empty")
        parameters[name] = value
    before = core.get_task(ctx.conn, ctx.pid, args.key)
    row, checks, transitioned = core.trigger_action(
        ctx.conn,
        ctx.pid,
        args.key,
        args.action,
        actor=args.actor,
        operation=args.operation,
        parameters=parameters,
        no_pr=args.no_pr,
        allow_open_children=args.allow_open_subtasks,
        allow_blocked=args.allow_blocked,
    )
    wf = workflow.get(ctx.conn, ctx.pid, row["task_type"])
    ctx.emit(
        {
            "action": hooks.normalize_action(args.action).value,
            "transitioned": transitioned,
            "from": before["status"],
            "task": row_to_dict(row),
            "checks": [check.as_dict() for check in checks],
        },
        (
            f"{row['key']}  action={hooks.normalize_action(args.action).value}  "
            + (
                f"{wf.display(before['status'])} -> {wf.display(row['status'])}"
                if transitioned
                else f"recorded; status remains {wf.display(row['status'])}"
            )
        ),
    )
    return 0

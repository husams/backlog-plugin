"""backlog — backlog tracker for coding agents (SQLite or shared PostgreSQL)."""

from __future__ import annotations


from .. import (
    core,
    execution,
)
from ..db import (
    BacklogError,
)
from ..render import (
    items_block,
    row_to_dict,
)


from .context import Ctx
from .authoring import _execution_spec


def cmd_item_add(ctx: Ctx, args) -> int:
    spec = _execution_spec(args)
    if spec and core.normalize_item_kind(args.kind) not in (
        "acceptance_criteria",
        "checklist",
    ):
        raise BacklogError(
            "only acceptance criteria and checklist items may declare execution"
        )
    lines = [line for line in args.content.splitlines() if line.strip()]
    if spec and len(lines) != 1:
        raise BacklogError(
            "an executable item requires exactly one non-empty content line"
        )
    rows = [
        core.add_item(ctx.conn, ctx.pid, args.key, args.kind, line, actor=args.actor)
        for line in lines
    ]
    if spec:
        execution.set_executable(ctx.conn, rows[0]["id"], spec)
    details = [execution._item_details(ctx.conn, row) for row in rows]
    ctx.emit(details, "\n".join(items_block(rows, conn=ctx.conn)) or "(nothing added)")
    return 0


def cmd_item_set(ctx: Ctx, args) -> int:
    spec = _execution_spec(args)
    if spec and core.normalize_item_kind(args.kind) not in (
        "acceptance_criteria",
        "checklist",
    ):
        raise BacklogError(
            "only acceptance criteria and checklist items may declare execution"
        )
    lines = [line for line in args.content.splitlines() if line.strip()]
    if spec and len(lines) != 1:
        raise BacklogError(
            "an executable item requires exactly one non-empty content line"
        )
    rows = core.set_items(
        ctx.conn, ctx.pid, args.key, args.kind, lines, actor=args.actor
    )
    if spec:
        execution.set_executable(ctx.conn, rows[0]["id"], spec)
    details = [execution._item_details(ctx.conn, row) for row in rows]
    ctx.emit(details, "\n".join(items_block(rows, conn=ctx.conn)) or "(cleared)")
    return 0


def cmd_item_list(ctx: Ctx, args) -> int:
    task = core.get_task(ctx.conn, ctx.pid, args.key)
    rows = core.task_items(ctx.conn, task["id"], args.kind)
    details = [execution._item_details(ctx.conn, row) for row in rows]
    ctx.emit(details, "\n".join(items_block(rows, conn=ctx.conn)) or "(none)")
    return 0


def cmd_item_check(ctx: Ctx, args) -> int:
    row = core.tick_item(
        ctx.conn,
        ctx.pid,
        args.id,
        done=not args.undo,
        actor=args.actor,
        waive_validation=args.waive_validation,
        waiver_reason=args.reason,
    )
    ctx.emit(
        row_to_dict(row),
        f"#{row['id']}  {'[x]' if row['done'] else '[ ]'} {row['content']}",
    )
    return 0


def cmd_item_rm(ctx: Ctx, args) -> int:
    row = core.remove_item(ctx.conn, ctx.pid, args.id, actor=args.actor)
    ctx.emit(row_to_dict(row), f"removed #{row['id']}  {row['content']}")
    return 0

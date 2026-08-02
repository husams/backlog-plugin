"""CLI commands for flat ordered task todos."""

from __future__ import annotations

from .. import core
from .context import Ctx


def _lines(rows: list[dict]) -> str:
    if not rows:
        return "(no todos)"
    return "\n".join(
        f"{row['position']:>3}  #{row['id']:<4} "
        f"{'[x]' if row['done'] else '[ ]'} {row['content']} "
        f"(updated by {row['updated_by']})"
        for row in rows
    )


def cmd_todo_add(ctx: Ctx, args) -> int:
    contents = [line.strip() for line in args.content.splitlines() if line.strip()]
    rows = core.add_todos(
        ctx.conn, ctx.pid, args.key, contents, actor=args.actor
    )
    ctx.emit(rows, _lines(rows))
    return 0


def cmd_todo_list(ctx: Ctx, args) -> int:
    rows = core.list_todos(ctx.conn, ctx.pid, args.key)
    ctx.emit(rows, _lines(rows))
    return 0


def cmd_todo_close(ctx: Ctx, args) -> int:
    row = core.set_todo_state(
        ctx.conn, ctx.pid, args.id, done=True, actor=args.actor
    )
    ctx.emit(row, _lines([row]))
    return 0


def cmd_todo_reopen(ctx: Ctx, args) -> int:
    row = core.set_todo_state(
        ctx.conn, ctx.pid, args.id, done=False, actor=args.actor
    )
    ctx.emit(row, _lines([row]))
    return 0


def cmd_todo_move(ctx: Ctx, args) -> int:
    row = core.move_todo(
        ctx.conn, ctx.pid, args.id, args.position, actor=args.actor
    )
    ctx.emit(row, _lines([row]))
    return 0

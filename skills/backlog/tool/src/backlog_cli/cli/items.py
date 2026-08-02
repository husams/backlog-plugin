"""backlog — backlog tracker for coding agents (SQLite or shared PostgreSQL)."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .. import (
    __version__, core, deps, execution, hooks, retrospective, review, templates, workflow,
)
from ..db import (
    BacklogError,
    Conn,
    connect,
    database_errors,
    get_or_create_project,
    init_store,
    list_projects,
    require_backlog_dir,
    require_project,
    resolve_spec,
    resync_sequences,
    slugify,
)
from ..render import (
    deps_block,
    items_block,
    projects_table,
    render_task,
    render_thread,
    row_to_dict,
    table,
    tasks_table,
)
from ..schema import (
    ARTIFACT_KINDS,
    GATE_CHECKS,
    GATE_DESCRIPTIONS,
    STATUS_CATEGORIES,
    TASK_KEY_PREFIX,
    DEPENDENCY_KINDS,
    ITEM_KINDS,
    PR_REVIEW_STATES,
    PR_STATES,
    SCHEMA_VERSION,
    STATUS_DISPLAY,
    STATUSES,
    TASK_PARENT_TYPES,
    TASK_TYPES,
    transitions_for,
)



from .context import Ctx, _task_rows
from .authoring import _execution_spec

def cmd_item_add(ctx: Ctx, args) -> int:
    spec = _execution_spec(args)
    if spec and core.normalize_item_kind(args.kind) not in (
        "acceptance_criteria", "checklist"
    ):
        raise BacklogError(
            "only acceptance criteria and checklist items may declare execution"
        )
    lines = [line for line in args.content.splitlines() if line.strip()]
    if spec and len(lines) != 1:
        raise BacklogError("an executable item requires exactly one non-empty content line")
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
        "acceptance_criteria", "checklist"
    ):
        raise BacklogError(
            "only acceptance criteria and checklist items may declare execution"
        )
    lines = [line for line in args.content.splitlines() if line.strip()]
    if spec and len(lines) != 1:
        raise BacklogError("an executable item requires exactly one non-empty content line")
    rows = core.set_items(ctx.conn, ctx.pid, args.key, args.kind,
                          lines, actor=args.actor)
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
        ctx.conn, ctx.pid, args.id, done=not args.undo, actor=args.actor,
        waive_validation=args.waive_validation, waiver_reason=args.reason,
    )
    ctx.emit(row_to_dict(row),
             f"#{row['id']}  {'[x]' if row['done'] else '[ ]'} {row['content']}")
    return 0


def cmd_item_rm(ctx: Ctx, args) -> int:
    row = core.remove_item(ctx.conn, ctx.pid, args.id, actor=args.actor)
    ctx.emit(row_to_dict(row), f"removed #{row['id']}  {row['content']}")
    return 0

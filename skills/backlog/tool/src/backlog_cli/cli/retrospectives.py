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

def _retrospective_text(row) -> str:
    lines = [
        f"{row['key']}  [{retrospective.STATUS_DISPLAY[row['status']]}]  {row['title']}",
        f"project: {row['project_slug']}",
        f"iteration: {row['iteration_key']}",
        f"repeated issue: {row['repeated_issue']}",
        f"proposed solution: {row['proposed_solution']}",
    ]
    if row["rejection_reason"]:
        lines.append(f"rejection reason: {row['rejection_reason']}")
    if row["resolution_task_key"]:
        lines.append(
            "resolution: "
            f"{row['resolution_project_slug']}:{row['resolution_task_key']} "
            f"({row['resolution_task_type']})"
        )
    return "\n".join(lines)


def cmd_retrospective_add(ctx: Ctx, args) -> int:
    row = retrospective.create_action(
        ctx.conn,
        ctx.pid,
        iteration=args.iteration,
        repeated_issue=args.issue,
        proposed_solution=args.solution,
        title=args.title,
        actor=args.actor,
    )
    ctx.emit(row_to_dict(row), _retrospective_text(row))
    return 0


def cmd_retrospective_list(ctx: Ctx, args) -> int:
    rows = retrospective.list_actions(
        ctx.conn, ctx.pid, status=args.status, iteration=args.iteration
    )
    text = table(
        ["KEY", "STATUS", "ITERATION", "TITLE", "RESOLUTION"],
        [[
            row["key"],
            retrospective.STATUS_DISPLAY[row["status"]],
            row["iteration_key"],
            row["title"],
            (
                f"{row['resolution_project_slug']}:{row['resolution_task_key']}"
                if row["resolution_task_key"] else ""
            ),
        ] for row in rows],
    )
    ctx.emit([row_to_dict(row) for row in rows], text)
    return 0


def cmd_retrospective_show(ctx: Ctx, args) -> int:
    row = retrospective.get_action(ctx.conn, ctx.pid, args.key)
    ctx.emit(row_to_dict(row), _retrospective_text(row))
    return 0


def cmd_retrospective_accept(ctx: Ctx, args) -> int:
    row = retrospective.accept_action(ctx.conn, ctx.pid, args.key, actor=args.actor)
    ctx.emit(row_to_dict(row), _retrospective_text(row))
    return 0


def cmd_retrospective_reject(ctx: Ctx, args) -> int:
    row = retrospective.reject_action(
        ctx.conn, ctx.pid, args.key, reason=args.reason, actor=args.actor
    )
    ctx.emit(row_to_dict(row), _retrospective_text(row))
    return 0


def cmd_retrospective_close(ctx: Ctx, args) -> int:
    resolution_task = args.feature or args.bug
    expected_type = "feature" if args.feature else "bug"
    row = retrospective.close_action(
        ctx.conn,
        ctx.pid,
        args.key,
        resolution_project=args.resolution_project,
        resolution_task=resolution_task,
        expected_task_type=expected_type,
        actor=args.actor,
    )
    ctx.emit(row_to_dict(row), _retrospective_text(row))
    return 0


def cmd_retrospective_history(ctx: Ctx, args) -> int:
    rows = retrospective.history(ctx.conn, ctx.pid, args.key)
    ctx.emit(
        [row_to_dict(row) for row in rows],
        table(
            ["TS", "KIND", "ACTOR", "FROM", "TO", "DETAIL"],
            [[
                row["ts"], row["kind"], row["actor"] or "-",
                row["from_value"] or "", row["to_value"] or "", row["detail"],
            ] for row in rows],
        ),
    )
    return 0

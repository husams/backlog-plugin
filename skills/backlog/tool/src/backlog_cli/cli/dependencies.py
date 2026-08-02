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

_DEP_LABEL = {
    ("out", "blocks"): "blocks",
    ("in", "blocks"): "blocked by",
    ("out", "relates"): "relates to",
    ("in", "relates"): "related from",
    ("out", "duplicates"): "duplicates",
    ("in", "duplicates"): "duplicated by",
}

def _dep_pair(args) -> tuple[str, str, str]:
    if args.blocks:
        return args.key, args.blocks, "blocks"
    if args.blocked_by:
        return args.blocked_by, args.key, "blocks"
    if args.relates:
        return args.key, args.relates, "relates"
    if args.duplicates:
        return args.key, args.duplicates, "duplicates"
    raise BacklogError(
        "say which way it points: --blocks / --blocked-by / --relates / --duplicates"
    )


def cmd_dep_add(ctx: Ctx, args) -> int:
    a, b, kind = _dep_pair(args)
    row = deps.add(ctx.conn, ctx.pid, a, b, kind, note=args.note or "", actor=args.actor)
    verb = "added" if row.get("created") else "already present"
    ctx.emit(row, f"{verb}: {row['from_key']} {row['kind']} {row['to_key']}")
    return 0


def cmd_dep_rm(ctx: Ctx, args) -> int:
    a, b, kind = _dep_pair(args)
    row = deps.remove(ctx.conn, ctx.pid, a, b, kind, actor=args.actor)
    ctx.emit(row, f"removed: {row['from_key']} {row['kind']} {row['to_key']}")
    return 0


def cmd_dep_list(ctx: Ctx, args) -> int:
    if args.key:
        task = core.get_task(ctx.conn, ctx.pid, args.key)
        edges = deps.edges_for(ctx.conn, task["id"], kind=args.kind)
        rows = [["OK" if e["satisfied"] else ("WAIT" if e["kind"] == "blocks" else ""),
                 _DEP_LABEL[(e["direction"], e["kind"])], e["other_key"],
                 e["other_status"], e["other_title"], e["note"]]
                for e in sorted(edges, key=lambda e: (e["kind"] != "blocks",
                                                      e["direction"], e["other_key"]))]
        ctx.emit(edges, table(["", "RELATION", "KEY", "STATUS", "TITLE", "NOTE"], rows))
        return 0
    rows = [row_to_dict(r) for r in deps.all_edges(ctx.conn, ctx.pid, kind=args.kind)]
    ctx.emit(rows, table(["FROM", "KIND", "TO", "NOTE"],
                         [[r["from_key"], r["kind"], r["to_key"], r["note"]] for r in rows]))
    return 0


def cmd_dep_check(ctx: Ctx, args) -> int:
    task = core.get_task(ctx.conn, ctx.pid, args.key)
    blockers = deps.blockers(ctx.conn, task["id"])
    ok = not blockers
    text = f"{task['key']}  " + (
        "READY — nothing is blocking it" if ok
        else "BLOCKED\n" + table(["KEY", "STATUS", "TITLE"],
                                 [[b["other_key"], b["other_status"], b["other_title"]]
                                  for b in blockers])
    )
    ctx.emit({"key": task["key"], "ok": ok, "blocked_by": blockers}, text)
    return 0 if ok else 2


def cmd_dep_graph(ctx: Ctx, args) -> int:
    conn = ctx.conn
    if args.format == "dot":
        print(deps.dot(conn, ctx.pid))
        return 0
    edges = [row_to_dict(r) for r in deps.all_edges(conn, ctx.pid)]
    loops = deps.cycles(conn)
    blocked = deps.blocked_by_map(conn, ctx.pid)
    if args.format == "json" or ctx.json:
        ctx.json = True
        ctx.emit({"edges": edges, "cycles": loops, "blocked_by": blocked}, "")
        return 0
    text = table(["FROM", "KIND", "TO"], [[e["from_key"], e["kind"], e["to_key"]] for e in edges])
    if blocked:
        text += "\n\ncurrently blocked:\n" + "\n".join(
            f"  {k} waits on {', '.join(v)}" for k, v in sorted(blocked.items()))
    if loops:
        text += "\n\nCYCLES:\n" + "\n".join("  " + " -> ".join(c) for c in loops)
    print(text)
    return 0

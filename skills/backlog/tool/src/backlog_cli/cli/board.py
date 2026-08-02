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

def cmd_board(ctx: Ctx, args) -> int:
    conn = ctx.conn
    flows = workflow.all_for(conn, ctx.pid)
    # Column order comes from the project's workflow, so a custom status lands
    # where its author put it rather than at the end.
    order, seen = [], set()
    for ttype in TASK_TYPES:
        for st in flows[ttype].ordered:
            if st["slug"] not in seen:
                seen.add(st["slug"])
                order.append((st["slug"], st["display"], st["category"]))

    rows = _task_rows(conn, ctx.pid, "")
    blocked = deps.blocked_by_map(conn, ctx.pid)
    if not args.all:
        rows = [r for r in rows if flows[r["task_type"]].is_open(r["status"])]
    iterations = [r for r in rows if r["task_type"] == "iteration"]
    rows = [r for r in rows if r["task_type"] != "iteration"]
    if args.iteration:
        selected = core.get_task(conn, ctx.pid, args.iteration)
        if selected["task_type"] != "iteration":
            raise BacklogError(f"{selected['key']} is not an Iteration")
        if selected["status"] != "open":
            raise BacklogError(
                f"{selected['key']} is {selected['status']}; the board may only "
                "be scoped to an Open Iteration"
            )
        member_ids = {m["id"] for m in core.iteration_members(conn, selected["id"])}
        iterations = [selected]
        rows = [r for r in rows if r["id"] in member_ids and r["task_type"] in {"story", "bug"}
                and r["status"] in core.ACTIONABLE_BY_DEV and r["key"] not in blocked]
    retrospective_actions = retrospective.list_open_actions(
        conn, ctx.pid, iteration=args.iteration
    )
    by_status: dict[str, list] = {}
    for r in rows:
        by_status.setdefault(r["status"], []).append(r)

    open_by_task = {
        r["key"]: r["n"] for r in conn.execute(
            "SELECT t.key, COUNT(*) AS n FROM review_thread r JOIN task t ON t.id = r.task_id "
            "WHERE r.state != 'closed' AND t.project_id = ? GROUP BY t.key", (ctx.pid,)
        ).fetchall()
    }
    eligible_by_iteration = {
        r["key"]: [m for m in core.iteration_members(conn, r["id"])
                   if m["task_type"] in {"story", "bug"}
                   and m["status"] in core.ACTIONABLE_BY_DEV
                   and m["key"] not in blocked]
        for r in iterations
    }
    lines = []
    if iterations:
        lines.append(f"== Iterations ({len(iterations)})")
        for r in sorted(iterations, key=lambda x: (x["priority"], x["key"])):
            eligible = eligible_by_iteration[r["key"]]
            lines.append(
                f"  {r['key']:<7} I  {r['priority']}  {r['assignee'] or '-':<12} "
                f"{r['title']}  [{flows['iteration'].display(r['status'])}; "
                f"{len(eligible)} eligible members]"
            )
            for member in eligible:
                lines.append(f"    {member['key']:<7} {TASK_KEY_PREFIX[member['task_type']]}  "
                             f"{member['priority']}  {member['title']}")
    if retrospective_actions:
        lines.append(f"== Retrospective actions ({len(retrospective_actions)})")
        for action in retrospective_actions:
            decision = retrospective.required_decision(action["status"])
            lines.append(
                f"  {action['key']:<7} {retrospective.STATUS_DISPLAY[action['status']]:<7} "
                f"{action['iteration_key']}  {action['title']}  [next: {decision}]"
            )
    for slug, display, _cat in order:
        group = by_status.pop(slug, [])
        if not group:
            continue
        lines.append(f"== {display} ({len(group)})")
        for r in group:
            flags = []
            if blocked.get(r["key"]):
                flags.append("blocked by " + ", ".join(blocked[r["key"]]))
            if open_by_task.get(r["key"]):
                flags.append(f"{open_by_task[r['key']]} open review")
            if r["pr_number"]:
                flags.append(f"PR #{r['pr_number']} {r['pr_state']}/{r['pr_review_state']}")
            suffix = ("  [" + "; ".join(flags) + "]") if flags else ""
            lines.append(f"  {r['key']:<7} {TASK_KEY_PREFIX[r['task_type']]}  {r['priority']}  "
                         f"{r['assignee'] or '-':<12} {r['title']}{suffix}")
    # anything in a status no workflow declares is still shown, never hidden
    for slug, group in sorted(by_status.items()):
        lines.append(f"== {slug} ({len(group)})  [not in any workflow]")
        for r in group:
            lines.append(f"  {r['key']:<7} {r['title']}")
    ctx.emit(
        {"project": row_to_dict(ctx.project),
         "tasks_by_status": {slug: [row_to_dict(r) for r in group]
                             for slug, group in by_status.items()},
         "iterations": [{**row_to_dict(r),
                         "eligible_members": [row_to_dict(m)
                                              for m in eligible_by_iteration[r["key"]]]}
                        for r in iterations],
         "retrospective_actions": [
             {**row_to_dict(action),
              "required_decision": retrospective.required_decision(action["status"])}
             for action in retrospective_actions
         ],
         "open_review_threads": open_by_task, "blocked_by": blocked},
        f"project: {ctx.project['slug']}  ({ctx.project['name']})\n\n"
        + ("\n".join(lines) if lines else "(no open tasks)"),
    )
    return 0


def cmd_next(ctx: Ctx, args) -> int:
    conn, actor, pid = ctx.conn, args.actor, ctx.pid
    threads = review.inbox(conn, pid, actor=actor)
    where = " AND t.status IN ('ready','in_progress','needs_work')"
    params: list = []
    if actor:
        where += " AND t.assignee = ?"
        params.append(actor)
    all_dev = _task_rows(conn, pid, where, params)
    all_dev = [r for r in all_dev if r["task_type"] != "iteration"]
    if args.iteration:
        selected = core.get_task(conn, pid, args.iteration)
        if selected["task_type"] != "iteration":
            raise BacklogError(f"{selected['key']} is not an Iteration")
        if selected["status"] != "open":
            raise BacklogError(
                f"{selected['key']} is {selected['status']}; work may only be "
                "selected from an Open Iteration"
            )
        member_ids = {m["id"] for m in core.iteration_members(conn, selected["id"])}
        all_dev = [r for r in all_dev if r["id"] in member_ids]
    blocked = deps.blocked_by_map(conn, pid)
    dev_items = [r for r in all_dev if r["key"] not in blocked]
    blocked_items = [r for r in all_dev if r["key"] in blocked]
    retrospective_actions = retrospective.list_open_actions(
        conn, pid, iteration=args.iteration
    )

    rev_where = " AND t.status = 'in_review'"
    rev_params: list = []
    if actor:
        rev_where += " AND t.reviewer = ?"
        rev_params.append(actor)
    rev_items = _task_rows(conn, pid, rev_where, rev_params)

    ready_to_accept, ready_to_done = [], []
    for r in _task_rows(conn, pid, " AND t.status = 'in_review'"):
        ok, _ = core.gate(conn, pid, r["key"], "accepted")
        if ok and (not actor or r["reviewer"] == actor):
            ready_to_accept.append(r)
    for r in _task_rows(conn, pid, " AND t.status = 'accepted'"):
        ok, _ = core.gate(conn, pid, r["key"], "done")
        if ok and (not actor or r["assignee"] == actor):
            ready_to_done.append(r)

    parts = []
    if threads:
        parts.append(f"REVIEW THREADS WAITING ON YOU ({len(threads)})\n"
                     + "\n\n".join(render_thread(t) for t in threads))
    if retrospective_actions:
        lines = []
        for action in retrospective_actions:
            decision = retrospective.required_decision(action["status"])
            lines.append(
                f"  {action['key']}  {retrospective.STATUS_DISPLAY[action['status']]}  "
                f"{action['iteration_key']}  {action['title']}  [next: {decision}]"
            )
        parts.append(
            f"RETROSPECTIVE DECISIONS ({len(retrospective_actions)})\n"
            + "\n".join(lines)
        )
    if dev_items:
        parts.append("WORK TO DO\n" + tasks_table(dev_items))
    if blocked_items:
        parts.append("BLOCKED — do not start\n" + tasks_table(blocked_items) + "\n"
                     + "\n".join(f"  {r['key']} waits on " + ", ".join(blocked[r["key"]])
                                 for r in blocked_items))
    if rev_items:
        parts.append("AWAITING YOUR REVIEW\n" + tasks_table(rev_items))
    if ready_to_accept:
        parts.append(
            "GATES PASS — submit the review or delivery approval action\n"
            + tasks_table(ready_to_accept)
        )
    if ready_to_done:
        parts.append(
            "DELIVERY READY — submit the delivery or PR completion action\n"
            + tasks_table(ready_to_done)
        )
    ctx.emit(
        {"actor": actor, "project": ctx.project["slug"], "review_threads": threads,
         "work_to_do": [row_to_dict(r) for r in dev_items],
         "blocked": [{**row_to_dict(r), "blocked_by": blocked[r["key"]]} for r in blocked_items],
         "awaiting_your_review": [row_to_dict(r) for r in rev_items],
         "ready_to_accept": [row_to_dict(r) for r in ready_to_accept],
         "ready_to_done": [row_to_dict(r) for r in ready_to_done],
         "retrospective_actions": [
             {**row_to_dict(action),
              "required_decision": retrospective.required_decision(action["status"])}
             for action in retrospective_actions
         ]},
        "\n\n".join(parts) if parts else "(nothing actionable)",
    )
    return 0


def cmd_history(ctx: Ctx, args) -> int:
    task = core.get_task(ctx.conn, ctx.pid, args.key)
    rows = ctx.conn.execute(
        "SELECT * FROM event WHERE task_id = ? ORDER BY id", (task["id"],)
    ).fetchall()
    ctx.emit([row_to_dict(r) for r in rows],
             table(["TS", "KIND", "ACTOR", "FROM", "TO", "DETAIL"],
                   [[r["ts"], r["kind"], r["actor"] or "-", r["from_value"] or "",
                     r["to_value"] or "", r["detail"]] for r in rows]))
    return 0

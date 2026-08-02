"""backlog — backlog tracker for coding agents (SQLite or shared PostgreSQL)."""

from __future__ import annotations

import json
from pathlib import Path

from .. import (
    core,
    execution,
    hooks,
    workflow,
)
from ..render import (
    row_to_dict,
    table,
)
from ..schema import (
    TASK_TYPES,
)


from .context import Ctx


def _execution_text(results) -> str:
    lines = []
    for result in results:
        payload = _execution_payload(result)
        line = (
            f"#{payload['item_id']}  {payload['status'].upper():7} "
            f"executor={payload['executor']} duration={payload['duration_ms']}ms"
        )
        if payload["diagnostic"]:
            line += f"  {payload['diagnostic']}"
        lines.append(line)
    return "\n".join(lines) if lines else "no executable items"


def _execution_payload(result) -> dict:
    if hasattr(result, "as_dict"):
        return result.as_dict()
    record = result.record
    return {
        "item_id": int(record["item_id"]),
        "status": result.status.value,
        "executor": "hook",
        "expected": result.expected,
        "actual": result.actual,
        "actual_exit_code": None,
        "stdout": "",
        "stderr": "",
        "duration_ms": int(record["duration_ms"]),
        "diagnostic": result.detail or result.reason,
        "reason": result.reason,
        "detail": result.detail,
        "hook_name": result.hook_name,
        "implementation_identity": result.implementation_identity,
    }


def cmd_validation_run(ctx: Ctx, args) -> int:
    from ..api import Backlog
    from .. import execution

    backlog = Backlog(ctx.conn, ctx.project, ctx.spec, actor=args.actor)
    result = execution.run_validation(
        backlog,
        args.item_id,
        Path(args.project_root),
        actor=args.actor,
    )
    payload = _execution_payload(result)
    ctx.emit(payload, _execution_text([result]))
    return 0 if payload["status"] == "pass" else 2


def cmd_validation_run_all(ctx: Ctx, args) -> int:
    from ..api import Backlog
    from .. import execution

    backlog = Backlog(ctx.conn, ctx.project, ctx.spec, actor=args.actor)
    results = execution.run_task_validations(
        backlog,
        args.key,
        Path(args.project_root),
        fail_fast=args.fail_fast,
        actor=args.actor,
    )
    payload = [_execution_payload(result) for result in results]
    ctx.emit(payload, _execution_text(results))
    task = backlog.task(args.key)
    ok, _ = execution.required_results_pass(ctx.conn, task.id, Path(args.project_root))
    return 0 if ok else 2


def cmd_validation_history(ctx: Ctx, args) -> int:
    rows = execution.execution_history(
        ctx.conn,
        args.item_id,
        limit=args.limit,
        project_root=Path(args.project_root),
    )
    text_rows = [
        [
            str(row["id"]),
            row["status"],
            row["actor"],
            row["finished_at"],
            "yes" if row["stale"] else "no",
            json.dumps(row["expected"], sort_keys=True),
            json.dumps(row["actual"], sort_keys=True),
            row["diagnostic"],
        ]
        for row in rows
    ]
    ctx.emit(
        rows,
        table(
            [
                "ID",
                "STATUS",
                "ACTOR",
                "TIMESTAMP",
                "STALE",
                "EXPECTED",
                "ACTUAL",
                "DIAGNOSTIC",
            ],
            text_rows,
        ),
    )
    return 0


def cmd_validation_waive(ctx: Ctx, args) -> int:
    waiver = execution.waive_validation(
        ctx.conn,
        ctx.pid,
        args.item_id,
        actor=args.actor or "",
        reason=args.reason,
    )
    ctx.emit(
        waiver,
        f"#{args.item_id}  WAIVED by {waiver['actor']} at "
        f"{waiver['created_at']}: {waiver['reason']}",
    )
    return 0


def cmd_actions(ctx: Ctx, args) -> int:
    task = core.get_task(ctx.conn, ctx.pid, args.key)
    config_dir = hooks.project_backlog_dir(ctx.dir)
    actions = hooks.available_actions(config_dir, task["task_type"], task["status"])
    ctx.emit(
        [action.value for action in actions],
        "\n".join(action.value for action in actions)
        if actions
        else "(no configured actions)",
    )
    return 0


def cmd_gate(ctx: Ctx, args) -> int:
    ok, checks = core.gate(
        ctx.conn,
        ctx.pid,
        args.key,
        args.__dict__["for"],
        allow_open_children=args.allow_open_subtasks,
        no_pr=args.no_pr,
        allow_blocked=args.allow_blocked,
    )
    key = core.normalize_key(args.key)
    text = f"{key}  gate={args.__dict__['for']}  " + ("PASS" if ok else "BLOCKED")
    text += "\n" + "\n".join(
        f"  {'OK  ' if c.ok else 'FAIL'} {c.name}: {c.detail}" for c in checks
    )
    ctx.emit(
        {
            "key": key,
            "gate": args.__dict__["for"],
            "ok": ok,
            "checks": [c.as_dict() for c in checks],
        },
        text,
    )
    return 0 if ok else 2


def cmd_statuses(ctx: Ctx, args) -> int:
    """The flow this project actually runs — read it before moving anything."""
    payload, blocks = {}, []
    for ttype in [core.normalize_type(args.type)] if args.type else TASK_TYPES:
        wf = workflow.get(ctx.conn, ctx.pid, ttype)
        payload[ttype] = {
            "name": wf.name,
            "statuses": [row_to_dict(x) for x in wf.ordered],
            "transitions": {f: dict(t) for f, t in wf.transitions.items()},
        }
        blocks.append(f"== {ttype}  ({wf.name})\n" + workflow.render(wf))
    ctx.emit(payload, "\n\n".join(blocks))
    return 0

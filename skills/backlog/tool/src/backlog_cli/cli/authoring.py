"""backlog — backlog tracker for coding agents (SQLite or shared PostgreSQL)."""

from __future__ import annotations

import json

from .. import (
    core,
    execution,
    workflow,
)
from ..db import (
    BacklogError,
)
from ..render import (
    row_to_dict,
)


from .context import Ctx


def _json_argument(value: str | None, label: str, default):
    if value is None:
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError as exc:
        raise BacklogError(f"{label} must be valid JSON: {exc.msg}") from exc


def _matcher_argument(args, stream: str) -> dict | None:
    values = [
        (name, getattr(args, f"{stream}_{name}", None))
        for name in ("equals", "contains", "regex")
    ]
    selected = [(name, value) for name, value in values if value is not None]
    if len(selected) > 1:
        raise BacklogError(
            f"--{stream}-equals, --{stream}-contains, and --{stream}-regex "
            "are mutually exclusive"
        )
    return dict(selected) if selected else None


def _execution_spec(args) -> dict | None:
    shell = getattr(args, "shell", None)
    hook = getattr(args, "hook", None)
    if shell is None and hook is None:
        extras = [
            "requirement",
            "timeout",
            "working_directory",
            "expected_exit_code",
            "stdout_equals",
            "stdout_contains",
            "stdout_regex",
            "stderr_equals",
            "stderr_contains",
            "stderr_regex",
            "environment",
            "arguments",
            "expected_result",
        ]
        if any(getattr(args, name, None) is not None for name in extras):
            raise BacklogError(
                "execution options require exactly one of --shell or --hook"
            )
        return None
    requirement = getattr(args, "requirement", None) or "required"
    timeout_value = getattr(args, "timeout", None)
    timeout = 60 if timeout_value is None else timeout_value
    if shell is not None:
        environment = []
        for name in getattr(args, "environment", None) or []:
            if not name or "=" in name:
                raise BacklogError(
                    f"--env must be a variable NAME without a value, got {name!r}"
                )
            if not name.strip():
                raise BacklogError("--env variable name cannot be empty")
            environment.append(name)
        shell_spec = {
            "command": shell,
            "timeout_seconds": timeout,
            "working_directory": getattr(args, "working_directory", None) or ".",
            "expected_exit_code": (
                getattr(args, "expected_exit_code", None)
                if getattr(args, "expected_exit_code", None) is not None
                else 0
            ),
            "environment": environment,
        }
        for stream in ("stdout", "stderr"):
            matcher = _matcher_argument(args, stream)
            if matcher:
                shell_spec[stream] = matcher
        spec = {"executor": "shell", "requirement": requirement, "shell": shell_spec}
    else:
        spec = {
            "executor": "hook",
            "requirement": requirement,
            "hook": {
                "name": hook,
                "arguments": _json_argument(
                    getattr(args, "arguments", None), "--arguments", {}
                ),
                "timeout_seconds": timeout,
                "expected_result": _json_argument(
                    getattr(args, "expected_result", None), "--expected-result", None
                ),
            },
        }
    return execution.parse_spec(spec).canonical()


def _add_task(ctx: Ctx, args, task_type: str, parent: str | None) -> int:
    item_spec = _execution_spec(args)
    criteria = [line for line in (args.ac or "").splitlines() if line.strip()]
    if item_spec and len(criteria) != 1:
        raise BacklogError(
            "executable --ac requires exactly one non-empty acceptance criterion"
        )
    row = core.add_task(
        ctx.conn,
        ctx.pid,
        task_type,
        args.title,
        parent=parent,
        description=args.description or "",
        priority=args.priority,
        owner=args.owner,
        assignee=args.assignee,
        reviewer=args.reviewer,
        branch=getattr(args, "branch", None),
        actor=args.actor,
    )
    if criteria:
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
    wf = workflow.get(ctx.conn, ctx.pid, row["task_type"])
    ctx.emit(
        row_to_dict(row),
        f"{row['key']}  {row['title']}  [{wf.display(row['status'])}]"
        + (f"  parent={parent}" if parent else ""),
    )
    return 0


def cmd_task_add(ctx: Ctx, args) -> int:
    return _add_task(ctx, args, core.normalize_type(args.type), args.parent)


def cmd_feature_add(ctx: Ctx, args) -> int:
    return _add_task(ctx, args, "feature", None)


def cmd_story_add(ctx: Ctx, args) -> int:
    return _add_task(ctx, args, "story", args.feature)


def cmd_bug_add(ctx: Ctx, args) -> int:
    return _add_task(ctx, args, "bug", None)


def cmd_iteration_add(ctx: Ctx, args) -> int:
    return _add_task(ctx, args, "iteration", None)


def cmd_iteration_member_add(ctx: Ctx, args) -> int:
    core.add_iteration_member(
        ctx.conn, ctx.pid, args.iteration, args.member, actor=args.actor
    )
    ctx.emit(
        {"iteration": args.iteration.upper(), "member": args.member.upper()},
        f"added {args.member.upper()} to {args.iteration.upper()}",
    )
    return 0


def cmd_iteration_member_remove(ctx: Ctx, args) -> int:
    core.remove_iteration_member(
        ctx.conn, ctx.pid, args.iteration, args.member, actor=args.actor
    )
    ctx.emit(
        {"iteration": args.iteration.upper(), "member": args.member.upper()},
        f"removed {args.member.upper()} from {args.iteration.upper()}",
    )
    return 0


def cmd_subtask_add(ctx: Ctx, args) -> int:
    return _add_task(ctx, args, "subtask", args.story or args.bug)

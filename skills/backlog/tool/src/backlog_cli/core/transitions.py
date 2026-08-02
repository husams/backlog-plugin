"""Semantic workflow transition execution."""

from __future__ import annotations

from typing import Any

from .. import workflow
from ..db import BacklogError, Conn, Row, log_event, require_backlog_dir, utcnow
from .checks import Check, run_checks
from .normalization import normalize_status, require_independent_actor
from .task_queries import get_task, get_task_by_id


def _transition(
    conn: Conn,
    project_id: int,
    key: str,
    to_status: str,
    actor: str | None = None,
    reason: str = "",
    no_pr: bool = False,
    allow_open_children: bool = False,
    allow_blocked: bool = False,
    action=None,
    trigger: dict[str, Any] | None = None,
) -> tuple[Row, list[Check]]:
    """Private transition executor reached only after resolving an action."""
    task = get_task(conn, project_id, key)
    wf = workflow.get(conn, project_id, task["task_type"])
    target = (
        wf.resolve(to_status)
        if _known(wf, to_status)
        else normalize_status(to_status, wf)
    )
    current = task["status"]

    from .. import hooks
    from ..api import Backlog

    if action is None:
        raise BacklogError("internal transition requires a semantic action")
    hook_action = hooks.normalize_action(action)
    hook_trigger = dict(trigger or {})
    hook_trigger.setdefault("operation", "action")
    hook_trigger.setdefault("actor", actor)
    hook_trigger.setdefault("task_key", task["key"])
    hook_trigger.setdefault("parameters", {})
    hook_trigger["parameters"].setdefault("resolved_state", target)
    hook_trigger["parameters"].setdefault("reason", reason)
    backlog_dir = hooks.project_backlog_dir(require_backlog_dir())
    project = conn.execute(
        "SELECT * FROM project WHERE id = ?", (project_id,)
    ).fetchone()
    assert project is not None and conn.spec is not None
    hook_backlog = Backlog(conn, project, conn.spec, actor=actor)
    proposed = hooks.pre_transition(
        backlog_dir, hook_action, hook_trigger, current, target, hook_backlog
    )
    target = wf.resolve(proposed)

    if target == current:
        log_event(
            conn,
            "transition_skipped",
            project_id,
            task["id"],
            task["key"],
            actor,
            from_value=current,
            to_value=current,
            detail=f"{hook_action.value}: pre_transition kept current state",
        )
        conn.commit()
        return get_task_by_id(conn, task["id"]), []
    if not wf.allows(current, target):
        legal = (
            ", ".join(sorted(wf.display(t) for t in wf.next_from(current)))
            or "(none — terminal state)"
        )
        raise BacklogError(
            f"illegal transition for {task['key']} (a {task['task_type']}): "
            f"{wf.display(current)} -> {wf.display(target)}.\n"
            f"Legal next states from {wf.display(current)}: {legal}\n"
            f"(`backlog workflow show --type {task['task_type']}` prints this project's flow)"
        )

    names = wf.gates_for(current, target)
    checks = run_checks(
        conn,
        project_id,
        task,
        names,
        allow_open_children=allow_open_children,
        no_pr=no_pr,
        allow_blocked=allow_blocked,
    )
    if not all(c.ok for c in checks):
        failed = "\n".join(f"  FAIL {c.name}: {c.detail}" for c in checks if not c.ok)
        raise BacklogError(
            f"gate for {wf.display(target)} not satisfied on {task['key']}:\n{failed}"
        )

    ts = utcnow()
    if no_pr and "pr_recorded" in names:
        conn.execute("UPDATE task SET pr_waived = 1 WHERE id = ?", (task["id"],))
    closed = ts if wf.category(target) in ("done", "dropped") else None
    conn.execute(
        "UPDATE task SET status = ?, updated_at = ?, closed_at = ? WHERE id = ?",
        (target, ts, closed, task["id"]),
    )
    log_event(
        conn,
        "status",
        project_id,
        task["id"],
        task["key"],
        actor,
        from_value=current,
        to_value=target,
        detail=reason,
    )
    conn.commit()
    updated = get_task_by_id(conn, task["id"])
    hooks.post_transition(
        backlog_dir, hook_action, hook_trigger, current, updated["status"], hook_backlog
    )
    return updated, checks


def trigger_action(
    conn: Conn,
    project_id: int,
    key: str,
    action,
    *,
    actor: str | None = None,
    operation: str = "api.trigger",
    parameters: dict[str, Any] | None = None,
    no_pr: bool = False,
    allow_open_children: bool = False,
    allow_blocked: bool = False,
) -> tuple[Row, list[Check], bool]:
    """Submit a semantic action and let the configured workflow select state."""
    from .. import hooks

    task = get_task(conn, project_id, key)
    action = hooks.normalize_action(action)
    if action is hooks.Action.REFINEMENT_ACCEPTED:
        actor = require_independent_actor(
            task["key"], task["created_by"], actor, "refinement.accepted"
        )
    trigger = {
        "operation": operation,
        "actor": actor,
        "task_key": task["key"],
        "parameters": dict(parameters or {}),
    }
    backlog_dir = hooks.project_backlog_dir(require_backlog_dir())
    destination = hooks.resolve_transition(
        backlog_dir, task["task_type"], task["status"], action
    )
    log_event(
        conn,
        "action",
        project_id,
        task["id"],
        task["key"],
        actor,
        from_value=task["status"],
        to_value=action.value,
        detail=operation,
    )
    conn.commit()
    if destination is None:
        return get_task_by_id(conn, task["id"]), [], False
    if destination == task["status"]:
        return get_task_by_id(conn, task["id"]), [], False
    row, checks = _transition(
        conn,
        project_id,
        task["key"],
        destination,
        actor=actor,
        reason=f"{action.value} via {operation}",
        no_pr=no_pr,
        allow_open_children=allow_open_children,
        allow_blocked=allow_blocked,
        action=action,
        trigger=trigger,
    )
    return row, checks, row["status"] != task["status"]


def _known(wf, value: str) -> bool:
    try:
        wf.resolve(value)
        return True
    except BacklogError:
        return False

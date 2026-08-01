"""Retrospective improvement actions and their fixed lifecycle."""

from __future__ import annotations

from enum import Enum

from . import core
from .db import BacklogError, Conn, Row, log_event, next_key, require_project, utcnow


class RetrospectiveStatus(str, Enum):
    CREATED = "created"
    READY = "ready"
    DONE = "done"
    REJECTED = "rejected"


STATUSES = tuple(status.value for status in RetrospectiveStatus)
STATUS_DISPLAY = {
    "created": "Created",
    "ready": "Ready",
    "done": "Done",
    "rejected": "Rejected",
}
OPEN_STATUSES = {"created", "ready"}
REQUIRED_DECISIONS = {
    "created": "accept_or_reject",
    "ready": "close_or_reject",
}


def required_decision(status: str) -> str | None:
    """The decision that advances an open retrospective action."""
    return REQUIRED_DECISIONS.get(normalize_status(status))


def normalize_status(value: str) -> str:
    status = value.strip().lower().replace("-", "_").replace(" ", "_")
    if status == "reject":
        status = "rejected"
    if status not in STATUSES:
        raise BacklogError(
            f"unknown retrospective status {value!r}. Valid: "
            + ", ".join(STATUS_DISPLAY[s] for s in STATUSES)
        )
    return status


def _required(value: str, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise BacklogError(f"{label} must be a non-empty string")
    return value.strip()


_SELECT = (
    "SELECT a.*, p.slug AS project_slug, i.key AS iteration_key, "
    "rp.slug AS resolution_project_slug, rt.key AS resolution_task_key, "
    "rt.task_type AS resolution_task_type "
    "FROM retrospective_action a "
    "JOIN project p ON p.id=a.project_id "
    "JOIN task i ON i.id=a.iteration_id "
    "LEFT JOIN project rp ON rp.id=a.resolution_project_id "
    "LEFT JOIN task rt ON rt.id=a.resolution_task_id "
)


def get_action(conn: Conn, project_id: int, key: str) -> Row:
    normalized = core.normalize_key(key)
    row = conn.execute(
        _SELECT + "WHERE a.project_id=? AND a.key=?",
        (project_id, normalized),
    ).fetchone()
    if row is None:
        raise BacklogError(
            f"no retrospective action with key {normalized} in this project"
        )
    return row


def list_actions(
    conn: Conn,
    project_id: int,
    *,
    status: str | None = None,
    iteration: str | None = None,
) -> list[Row]:
    clauses = ["a.project_id=?"]
    params: list[object] = [project_id]
    if status is not None:
        clauses.append("a.status=?")
        params.append(normalize_status(status))
    if iteration is not None:
        iteration_row = core.get_task(conn, project_id, iteration)
        if iteration_row["task_type"] != "iteration":
            raise BacklogError(f"{iteration_row['key']} is not an Iteration")
        clauses.append("a.iteration_id=?")
        params.append(iteration_row["id"])
    return conn.execute(
        _SELECT + "WHERE " + " AND ".join(clauses) + " ORDER BY a.key",
        params,
    ).fetchall()


def list_open_actions(
    conn: Conn, project_id: int, *, iteration: str | None = None
) -> list[Row]:
    """Created and Ready actions, ordered by their project-local key."""
    return [
        row for row in list_actions(conn, project_id, iteration=iteration)
        if row["status"] in OPEN_STATUSES
    ]


def create_action(
    conn: Conn,
    project_id: int,
    *,
    iteration: str,
    repeated_issue: str,
    proposed_solution: str,
    title: str | None = None,
    actor: str | None = None,
) -> Row:
    actor = core.require_actor(actor, "retrospective action creation")
    issue = _required(repeated_issue, "repeated_issue")
    solution = _required(proposed_solution, "proposed_solution")
    action_title = _required(title, "title") if title is not None else issue.splitlines()[0]
    iteration_row = core.get_task(conn, project_id, iteration)
    if iteration_row["task_type"] != "iteration":
        raise BacklogError(f"{iteration_row['key']} is not an Iteration")

    key = next_key(conn, project_id, "R")
    ts = utcnow()
    conn.execute(
        "INSERT INTO retrospective_action("
        "project_id,iteration_id,key,title,repeated_issue,proposed_solution,status,"
        "created_by,created_at,updated_at) VALUES(?,?,?,?,?,?,'created',?,?,?)",
        (project_id, iteration_row["id"], key, action_title, issue, solution, actor, ts, ts),
    )
    log_event(
        conn,
        "retrospective.created",
        project_id,
        entity_key=key,
        actor=actor,
        to_value="created",
        detail=f"iteration={iteration_row['key']}; {action_title}",
    )
    conn.commit()
    return get_action(conn, project_id, key)


def _require_status(action: Row, allowed: set[str], operation: str) -> None:
    if action["status"] not in allowed:
        allowed_text = " or ".join(STATUS_DISPLAY[s] for s in sorted(allowed))
        raise BacklogError(
            f"cannot {operation} {action['key']} from "
            f"{STATUS_DISPLAY[action['status']]}; expected {allowed_text}"
        )


def accept_action(
    conn: Conn, project_id: int, key: str, *, actor: str | None = None
) -> Row:
    action = get_action(conn, project_id, key)
    _require_status(action, {"created"}, "accept")
    actor = core.require_independent_actor(
        action["key"], action["created_by"], actor, "retrospective acceptance"
    )
    ts = utcnow()
    cursor = conn.execute(
        "UPDATE retrospective_action SET status='ready',accepted_by=?,accepted_at=?,"
        "updated_at=? WHERE id=? AND status='created'",
        (actor, ts, ts, action["id"]),
    )
    if cursor.rowcount != 1:
        conn.rollback()
        raise BacklogError(f"{action['key']} changed concurrently; retry the accept operation")
    log_event(
        conn,
        "retrospective.accepted",
        project_id,
        entity_key=action["key"],
        actor=actor,
        from_value="created",
        to_value="ready",
    )
    conn.commit()
    return get_action(conn, project_id, action["key"])


def reject_action(
    conn: Conn,
    project_id: int,
    key: str,
    *,
    reason: str,
    actor: str | None = None,
) -> Row:
    rejection_reason = _required(reason, "reason")
    action = get_action(conn, project_id, key)
    _require_status(action, {"created", "ready"}, "reject")
    before = action["status"]
    ts = utcnow()
    cursor = conn.execute(
        "UPDATE retrospective_action SET status='rejected',rejection_reason=?,"
        "rejected_by=?,rejected_at=?,closed_by=?,closed_at=?,updated_at=? "
        "WHERE id=? AND status=?",
        (rejection_reason, actor, ts, actor, ts, ts, action["id"], before),
    )
    if cursor.rowcount != 1:
        conn.rollback()
        raise BacklogError(f"{action['key']} changed concurrently; retry the reject operation")
    log_event(
        conn,
        "retrospective.rejected",
        project_id,
        entity_key=action["key"],
        actor=actor,
        from_value=before,
        to_value="rejected",
        detail=rejection_reason,
    )
    conn.commit()
    return get_action(conn, project_id, action["key"])


def close_action(
    conn: Conn,
    project_id: int,
    key: str,
    *,
    resolution_project: str,
    resolution_task: str,
    expected_task_type: str | None = None,
    actor: str | None = None,
) -> Row:
    action = get_action(conn, project_id, key)
    _require_status(action, {"ready"}, "close")
    target_project = require_project(conn, resolution_project)
    target = core.get_task(conn, int(target_project["id"]), resolution_task)
    if target["task_type"] not in {"feature", "bug"}:
        raise BacklogError(
            f"{target['key']} is a {target['task_type']}; a retrospective action "
            "must close against a Feature or Bug"
        )
    if expected_task_type is not None and target["task_type"] != expected_task_type:
        raise BacklogError(
            f"{target['key']} is a {target['task_type']}, not a {expected_task_type}"
        )

    ts = utcnow()
    cursor = conn.execute(
        "UPDATE retrospective_action SET status='done',resolution_project_id=?,"
        "resolution_task_id=?,closed_by=?,closed_at=?,updated_at=? "
        "WHERE id=? AND status='ready'",
        (target_project["id"], target["id"], actor, ts, ts, action["id"]),
    )
    if cursor.rowcount != 1:
        conn.rollback()
        raise BacklogError(f"{action['key']} changed concurrently; retry the close operation")
    reference = f"{target_project['slug']}:{target['key']}"
    log_event(
        conn,
        "retrospective.closed",
        project_id,
        entity_key=action["key"],
        actor=actor,
        from_value="ready",
        to_value="done",
        detail=f"resolution={reference}",
    )
    conn.commit()
    return get_action(conn, project_id, action["key"])


def history(conn: Conn, project_id: int, key: str) -> list[Row]:
    action = get_action(conn, project_id, key)
    return conn.execute(
        "SELECT * FROM event WHERE project_id=? AND entity_key=? "
        "AND kind LIKE 'retrospective.%' ORDER BY id",
        (project_id, action["key"]),
    ).fetchall()

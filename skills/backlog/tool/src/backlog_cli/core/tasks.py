"""Task creation, updates, and assignment."""

from __future__ import annotations


from .. import workflow
from ..db import BacklogError, Conn, Row, actor_kind, log_event, next_key, utcnow
from ..schema import TASK_KEY_PREFIX, TASK_PARENT_TYPES
from .normalization import (
    normalize_priority,
    normalize_type,
    require_actor,
)

from .task_queries import get_task, get_task_by_id


def add_task(
    conn: Conn,
    project_id: int,
    task_type: str,
    title: str,
    parent: str | None = None,
    description: str = "",
    priority: str = "P2",
    owner: str | None = None,
    assignee: str | None = None,
    reviewer: str | None = None,
    branch: str | None = None,
    actor: str | None = None,
) -> Row:
    task_type = normalize_type(task_type)
    parent_id = None
    if parent:
        prow = get_task(conn, project_id, parent)
        allowed = TASK_PARENT_TYPES[task_type]
        if prow["task_type"] not in allowed:
            raise BacklogError(
                f"a {task_type} cannot sit under a {prow['task_type']} ({prow['key']}). "
                + (
                    f"Its parent must be a {' or '.join(sorted(allowed))}."
                    if allowed
                    else "It is a root and takes no parent."
                )
            )
        parent_id = prow["id"]
    elif task_type == "subtask":
        raise BacklogError("a subtask requires a parent story or bug (--parent <KEY>)")

    actor = require_actor(actor, "task creation")
    key = next_key(conn, project_id, TASK_KEY_PREFIX[task_type])
    initial = workflow.get(conn, project_id, task_type).initial
    ts = utcnow()
    conn.execute(
        "INSERT INTO task(project_id, key, task_type, parent_id, title, description, "
        "status, priority, owner, assignee, assignee_kind, reviewer, reviewer_kind, "
        "branch, created_by, created_at, updated_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            project_id,
            key,
            task_type,
            parent_id,
            title,
            description,
            initial,
            normalize_priority(priority),
            owner,
            assignee,
            actor_kind(assignee),
            reviewer,
            actor_kind(reviewer),
            branch,
            actor,
            ts,
            ts,
        ),
    )
    row = get_task(conn, project_id, key)
    log_event(
        conn,
        "created",
        project_id,
        row["id"],
        key,
        actor,
        to_value=initial,
        detail=f"{task_type}: {title}",
    )
    conn.commit()
    return row


def update_task(
    conn: Conn, project_id: int, key: str, actor: str | None = None, **fields
) -> Row:
    task = get_task(conn, project_id, key)
    sets, values, notes = [], [], []
    for name, value in fields.items():
        if value is None:
            continue
        if name == "priority":
            value = normalize_priority(value)
        elif name == "parent":
            prow = get_task(conn, project_id, value)
            allowed = TASK_PARENT_TYPES[task["task_type"]]
            if prow["task_type"] not in allowed:
                raise BacklogError(
                    f"a {task['task_type']} cannot sit under a {prow['task_type']}"
                )
            name, value = "parent_id", prow["id"]
        sets.append(f"{name} = ?")
        values.append(value)
        notes.append(f"{name}={value}")
    if not sets:
        return task
    sets.append("updated_at = ?")
    values += [utcnow(), task["id"]]
    conn.execute(f"UPDATE task SET {', '.join(sets)} WHERE id = ?", values)
    log_event(
        conn,
        "update",
        project_id,
        task["id"],
        task["key"],
        actor,
        detail="; ".join(notes),
    )
    conn.commit()
    return get_task_by_id(conn, task["id"])


def assign(
    conn: Conn,
    project_id: int,
    key: str,
    to: str | None = None,
    reviewer: str | None = None,
    actor: str | None = None,
    to_kind: str | None = None,
    reviewer_kind: str | None = None,
) -> Row:
    task = get_task(conn, project_id, key)
    if to is None and reviewer is None:
        raise BacklogError("nothing to assign: pass --to and/or --reviewer")
    sets, values, notes = [], [], []
    if to is not None:
        sets += ["assignee = ?", "assignee_kind = ?"]
        values += [to, to_kind or actor_kind(to)]
        notes.append(f"assignee {task['assignee'] or '-'} -> {to}")
    if reviewer is not None:
        sets += ["reviewer = ?", "reviewer_kind = ?"]
        values += [reviewer, reviewer_kind or actor_kind(reviewer)]
        notes.append(f"reviewer {task['reviewer'] or '-'} -> {reviewer}")
    sets.append("updated_at = ?")
    values += [utcnow(), task["id"]]
    conn.execute(f"UPDATE task SET {', '.join(sets)} WHERE id = ?", values)
    log_event(
        conn,
        "assign",
        project_id,
        task["id"],
        task["key"],
        actor,
        detail="; ".join(notes),
    )
    conn.commit()
    return get_task_by_id(conn, task["id"])

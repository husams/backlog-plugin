"""Task, iteration membership, assignment, and task-item lookups."""

from __future__ import annotations

from typing import Any

from .. import workflow
from ..db import BacklogError, Conn, Row, actor_kind, log_event, next_key, utcnow
from ..schema import TASK_KEY_PREFIX, TASK_PARENT_TYPES
from .normalization import (
    normalize_item_kind,
    normalize_key,
    normalize_priority,
    normalize_type,
    require_actor,
)

_TASK_FIELDS = {"title", "description", "branch", "owner"}


def get_task(conn: Conn, project_id: int, key: str) -> Row:
    row = conn.execute(
        "SELECT * FROM task WHERE project_id = ? AND key = ?",
        (project_id, normalize_key(key)),
    ).fetchone()
    if row is None:
        raise BacklogError(f"no task with key {normalize_key(key)} in this project")
    return row


def get_task_by_id(conn: Conn, task_id: int) -> Row:
    row = conn.execute("SELECT * FROM task WHERE id = ?", (task_id,)).fetchone()
    if row is None:
        raise BacklogError(f"no task with id {task_id}")
    return row


def find_task(conn: Conn, project_id: int, key: str) -> Row | None:
    return conn.execute(
        "SELECT * FROM task WHERE project_id = ? AND key = ?",
        (project_id, normalize_key(key)),
    ).fetchone()


def children_of(conn: Conn, task_id: int) -> list[Row]:
    return conn.execute(
        "SELECT * FROM task WHERE parent_id = ? ORDER BY key", (task_id,)
    ).fetchall()


def iteration_members(conn: Conn, iteration_id: int) -> list[Row]:
    return conn.execute(
        "SELECT t.* FROM iteration_member m JOIN task t ON t.id=m.member_id "
        "WHERE m.iteration_id=? ORDER BY t.priority,t.key", (iteration_id,)
    ).fetchall()


def add_iteration_member(conn: Conn, project_id: int, iteration_key: str,
                         member_key: str, actor: str | None = None) -> None:
    iteration = get_task(conn, project_id, iteration_key)
    member = get_task(conn, project_id, member_key)
    if iteration["task_type"] != "iteration":
        raise BacklogError(f"{iteration['key']} is not an Iteration")
    if iteration["status"] != "open":
        raise BacklogError(
            f"{iteration['key']} is {iteration['status']}; members may only be "
            "added to an Open Iteration"
        )
    if member["task_type"] not in {"story", "bug"}:
        raise BacklogError(
            f"{member['key']} is a {member['task_type']}; only Ready Stories "
            "and standalone Bugs may join an Iteration"
        )
    if member["status"] != "ready":
        raise BacklogError(
            f"{member['key']} is {member['status']}; only Ready Stories and Bugs "
            "may join an Iteration"
        )
    conflict = conn.execute(
        "SELECT i.key FROM iteration_member m JOIN task i ON i.id=m.iteration_id "
        "WHERE m.member_id=? AND i.id!=? AND i.status='open' ORDER BY i.key",
        (member["id"], iteration["id"]),
    ).fetchone()
    if conflict:
        raise BacklogError(
            f"{member['key']} already belongs to Open Iteration {conflict['key']}; "
            "remove it there before adding it here"
        )
    if conn.execute("SELECT 1 FROM iteration_member WHERE iteration_id=? AND member_id=?",
                    (iteration["id"], member["id"])).fetchone():
        return
    conn.execute(
        "INSERT INTO iteration_member(iteration_id,member_id,created_at) VALUES(?,?,?) "
        "ON CONFLICT(iteration_id,member_id) DO NOTHING",
        (iteration["id"], member["id"], utcnow()),
    )
    detail = f"added {member['key']} to {iteration['key']}"
    log_event(conn, "iteration.member_added", project_id, iteration["id"],
              iteration["key"], actor, to_value=member["key"], detail=detail)
    log_event(conn, "iteration.joined", project_id, member["id"], member["key"],
              actor, to_value=iteration["key"], detail=detail)
    conn.commit()


def remove_iteration_member(conn: Conn, project_id: int, iteration_key: str,
                            member_key: str, actor: str | None = None) -> None:
    iteration = get_task(conn, project_id, iteration_key)
    member = get_task(conn, project_id, member_key)
    if iteration["task_type"] != "iteration":
        raise BacklogError(f"{iteration['key']} is not an Iteration")
    if iteration["status"] != "open":
        raise BacklogError(
            f"{iteration['key']} is {iteration['status']}; members may only be "
            "removed from an Open Iteration"
        )
    cursor = conn.execute("DELETE FROM iteration_member WHERE iteration_id=? AND member_id=?",
                          (iteration["id"], member["id"]))
    if cursor.rowcount == 0:
        raise BacklogError(f"{member['key']} is not a member of {iteration['key']}")
    detail = f"removed {member['key']} from {iteration['key']}"
    log_event(conn, "iteration.member_removed", project_id, iteration["id"],
              iteration["key"], actor, from_value=member["key"], detail=detail)
    log_event(conn, "iteration.left", project_id, member["id"], member["key"],
              actor, from_value=iteration["key"], detail=detail)
    conn.commit()


def open_threads(conn: Conn, task_id: int) -> list[Row]:
    return conn.execute(
        "SELECT * FROM review_thread WHERE task_id = ? AND state != 'closed' ORDER BY root_key",
        (task_id,),
    ).fetchall()


def blocking_threads(conn: Conn, task_id: int) -> list[Row]:
    return conn.execute(
        "SELECT * FROM review_thread WHERE task_id = ? "
        "AND (state != 'closed' OR COALESCE(resolution, '') != 'accepted_by_reviewer') "
        "ORDER BY root_key",
        (task_id,),
    ).fetchall()


def task_items(conn: Conn, task_id: int, kind: str | None = None) -> list[Row]:
    sql = "SELECT * FROM task_item WHERE task_id = ?"
    params: list = [task_id]
    if kind:
        sql += " AND kind = ?"
        params.append(normalize_item_kind(kind))
    return conn.execute(sql + " ORDER BY kind, position, id", params).fetchall()


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
                + (f"Its parent must be a {' or '.join(sorted(allowed))}."
                   if allowed else "It is a root and takes no parent.")
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
        (project_id, key, task_type, parent_id, title, description, initial,
         normalize_priority(priority), owner, assignee, actor_kind(assignee),
         reviewer, actor_kind(reviewer), branch, actor, ts, ts),
    )
    row = get_task(conn, project_id, key)
    log_event(conn, "created", project_id, row["id"], key, actor,
              to_value=initial, detail=f"{task_type}: {title}")
    conn.commit()
    return row


def update_task(conn: Conn, project_id: int, key: str, actor: str | None = None,
                **fields) -> Row:
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
            if _would_loop(conn, task["id"], prow["id"]):
                raise BacklogError(f"{prow['key']} is below {task['key']}; that would loop")
            name, value = "parent_id", prow["id"]
        elif name not in _TASK_FIELDS:
            raise BacklogError(f"cannot set field {name!r}")
        sets.append(f"{name} = ?")
        values.append(value)
        notes.append(f"{name}={value}")
    if not sets:
        return task
    sets.append("updated_at = ?")
    values += [utcnow(), task["id"]]
    conn.execute(f"UPDATE task SET {', '.join(sets)} WHERE id = ?", values)
    log_event(conn, "update", project_id, task["id"], task["key"], actor,
              detail="; ".join(notes))
    conn.commit()
    return get_task_by_id(conn, task["id"])


def _would_loop(conn: Conn, task_id: int, new_parent_id: int) -> bool:
    seen = set()
    cur: int | None = new_parent_id
    while cur is not None and cur not in seen:
        if cur == task_id:
            return True
        seen.add(cur)
        row = conn.execute("SELECT parent_id FROM task WHERE id = ?", (cur,)).fetchone()
        cur = row["parent_id"] if row else None
    return False


def assign(conn: Conn, project_id: int, key: str, to: str | None = None,
           reviewer: str | None = None, actor: str | None = None,
           to_kind: str | None = None, reviewer_kind: str | None = None) -> Row:
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
    log_event(conn, "assign", project_id, task["id"], task["key"], actor,
              detail="; ".join(notes))
    conn.commit()
    return get_task_by_id(conn, task["id"])

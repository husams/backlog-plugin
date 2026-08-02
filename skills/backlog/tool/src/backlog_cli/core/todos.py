"""Flat, ordered implementation todos stored as dedicated task items."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from ..db import BacklogError, Conn, Row, log_event, utcnow
from .normalization import require_actor
from .task_queries import get_task


def _view(row: Mapping[str, Any]) -> dict[str, Any]:
    done = bool(row["done"])
    return {
        "id": int(row["id"]),
        "task_key": row["task_key"],
        "position": int(row["position"]),
        "content": row["content"],
        "state": "closed" if done else "open",
        "done": done,
        "created_by": row["created_by"],
        "updated_by": row["updated_by"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _rows(conn: Conn, task_id: int) -> list[Row]:
    return conn.execute(
        "SELECT i.*, t.key AS task_key FROM task_item i "
        "JOIN task t ON t.id=i.task_id "
        "WHERE i.task_id=? AND i.kind='todo' ORDER BY i.position,i.id",
        (task_id,),
    ).fetchall()


def _item(conn: Conn, project_id: int, todo_id: int) -> Row:
    row = conn.execute(
        "SELECT i.*, t.key AS task_key, t.project_id AS task_project_id "
        "FROM task_item i JOIN task t ON t.id=i.task_id "
        "WHERE i.id=? AND t.project_id=?",
        (todo_id, project_id),
    ).fetchone()
    if row is None:
        raise BacklogError(f"no todo with id {todo_id} in this project")
    if row["kind"] != "todo":
        raise BacklogError(
            f"task item #{todo_id} is a {row['kind']} item, not a todo"
        )
    return row


def list_todos(conn: Conn, project_id: int, key: str) -> list[dict[str, Any]]:
    task = get_task(conn, project_id, key)
    return [_view(row) for row in _rows(conn, task["id"])]


def add_todos(
    conn: Conn,
    project_id: int,
    key: str,
    contents: Iterable[str],
    *,
    actor: str | None,
) -> list[dict[str, Any]]:
    identity = require_actor(actor, "adding a todo")
    task = get_task(conn, project_id, key)
    prepared: list[str] = []
    for content in contents:
        if not isinstance(content, str) or not content.strip():
            raise BacklogError("todo content must be a non-empty string")
        prepared.append(content.strip())
    if not prepared:
        raise BacklogError("at least one non-empty todo is required")

    row = conn.execute(
        "SELECT COALESCE(MAX(position), -1) AS p FROM task_item "
        "WHERE task_id=? AND kind='todo'",
        (task["id"],),
    ).fetchone()
    start = int(row["p"]) + 1
    ts = utcnow()
    inserted_ids: list[int] = []
    for offset, content in enumerate(prepared):
        item_id = conn.insert_returning_id(
            "INSERT INTO task_item(task_id,kind,position,content,done,created_at,"
            "updated_at,created_by,updated_by) VALUES(?, 'todo', ?, ?, 0, ?, ?, ?, ?)",
            (task["id"], start + offset, content, ts, ts, identity, identity),
        )
        inserted_ids.append(item_id)
        log_event(
            conn,
            "todo.added",
            project_id,
            task["id"],
            task["key"],
            identity,
            to_value="open",
            detail=content[:120],
        )
    conn.commit()
    return [_view(_item(conn, project_id, item_id)) for item_id in inserted_ids]


def set_state(
    conn: Conn,
    project_id: int,
    todo_id: int,
    *,
    done: bool,
    actor: str | None,
) -> dict[str, Any]:
    operation = "closing a todo" if done else "reopening a todo"
    identity = require_actor(actor, operation)
    row = _item(conn, project_id, todo_id)
    if bool(row["done"]) == done:
        state = "closed" if done else "open"
        raise BacklogError(f"todo #{todo_id} is already {state}")
    ts = utcnow()
    conn.execute(
        "UPDATE task_item SET done=?,updated_at=?,updated_by=? WHERE id=?",
        (1 if done else 0, ts, identity, todo_id),
    )
    log_event(
        conn,
        "todo.closed" if done else "todo.reopened",
        project_id,
        row["task_id"],
        row["task_key"],
        identity,
        from_value="open" if done else "closed",
        to_value="closed" if done else "open",
        detail=row["content"][:120],
    )
    conn.commit()
    return _view(_item(conn, project_id, todo_id))


def move_todo(
    conn: Conn,
    project_id: int,
    todo_id: int,
    position: int,
    *,
    actor: str | None,
) -> dict[str, Any]:
    identity = require_actor(actor, "moving a todo")
    if not isinstance(position, int) or isinstance(position, bool):
        raise BacklogError("todo position must be an integer")
    row = _item(conn, project_id, todo_id)
    ordered = _rows(conn, row["task_id"])
    if position < 0 or position >= len(ordered):
        raise BacklogError(
            f"invalid todo position {position}; expected 0..{len(ordered) - 1}"
        )

    moving = next(item for item in ordered if item["id"] == todo_id)
    old_position = int(moving["position"])
    reordered = [item for item in ordered if item["id"] != todo_id]
    reordered.insert(position, moving)
    ts = utcnow()
    for index, item in enumerate(reordered):
        conn.execute(
            "UPDATE task_item SET position=?,updated_at=?,updated_by=? WHERE id=?",
            (index, ts, identity, item["id"]),
        )
    log_event(
        conn,
        "todo.moved",
        project_id,
        row["task_id"],
        row["task_key"],
        identity,
        from_value=str(old_position),
        to_value=str(position),
        detail=row["content"][:120],
    )
    conn.commit()
    return _view(_item(conn, project_id, todo_id))

"""Task and related-record lookups."""

from __future__ import annotations


from ..db import BacklogError, Conn, Row
from .normalization import (
    normalize_item_kind,
    normalize_key,
)


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

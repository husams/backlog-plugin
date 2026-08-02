"""Task-item creation, replacement, completion, and removal."""

from __future__ import annotations


from ..db import BacklogError, Conn, Row, log_event, utcnow
from ..schema import TICKABLE_ITEM_KINDS
from .normalization import normalize_item_kind
from .task_queries import get_task, get_task_by_id, task_items


def add_item(
    conn: Conn,
    project_id: int,
    key: str,
    kind: str,
    content: str,
    actor: str | None = None,
) -> Row:
    task = get_task(conn, project_id, key)
    kind = normalize_item_kind(kind)
    row = conn.execute(
        "SELECT COALESCE(MAX(position), -1) AS p FROM task_item WHERE task_id = ? AND kind = ?",
        (task["id"], kind),
    ).fetchone()
    position = int(row["p"]) + 1
    ts = utcnow()
    item_id = conn.insert_returning_id(
        "INSERT INTO task_item(task_id, kind, position, content, done, created_at, "
        "updated_at, created_by) VALUES(?,?,?,?,0,?,?,?)",
        (task["id"], kind, position, content, ts, ts, actor),
    )
    log_event(
        conn,
        "item",
        project_id,
        task["id"],
        task["key"],
        actor,
        to_value=kind,
        detail=content[:120],
    )
    conn.commit()
    return conn.execute("SELECT * FROM task_item WHERE id = ?", (item_id,)).fetchone()


def set_items(
    conn: Conn,
    project_id: int,
    key: str,
    kind: str,
    lines: list[str],
    actor: str | None = None,
) -> list[Row]:
    """Replace every item of one kind on a task. One line, one item."""
    task = get_task(conn, project_id, key)
    kind = normalize_item_kind(kind)
    conn.execute(
        "DELETE FROM task_item WHERE task_id = ? AND kind = ?", (task["id"], kind)
    )
    ts = utcnow()
    conn.executemany(
        "INSERT INTO task_item(task_id, kind, position, content, done, created_at, "
        "updated_at, created_by) VALUES(?,?,?,?,0,?,?,?)",
        [
            (task["id"], kind, i, line.strip(), ts, ts, actor)
            for i, line in enumerate(l for l in lines if l.strip())
        ],
    )
    log_event(
        conn,
        "item",
        project_id,
        task["id"],
        task["key"],
        actor,
        to_value=kind,
        detail=f"replaced with {len(lines)} line(s)",
    )
    conn.commit()
    return task_items(conn, task["id"], kind)


def tick_item(
    conn: Conn,
    project_id: int,
    item_id: int,
    done: bool = True,
    actor: str | None = None,
    waive_validation: bool = False,
    waiver_reason: str | None = None,
) -> Row:
    row = conn.execute("SELECT * FROM task_item WHERE id = ?", (item_id,)).fetchone()
    if row is None:
        raise BacklogError(f"no task item with id {item_id}")
    if row["kind"] not in TICKABLE_ITEM_KINDS:
        raise BacklogError(
            f"a {row['kind']} entry is not tickable; only a checklist entry is. "
            "Acceptance criteria are proven by review, not by a tick."
        )
    task = get_task_by_id(conn, row["task_id"])
    executable = conn.execute(
        "SELECT item_id FROM executable_item WHERE item_id=?", (item_id,)
    ).fetchone()
    if done and executable is not None:
        from pathlib import Path
        from .. import execution

        state = execution.item_state(conn, item_id, Path.cwd())
        if state != "pass":
            if not waive_validation:
                raise BacklogError(
                    f"executable checklist item {item_id} has {state} validation; "
                    "run it successfully or use --waive-validation with --reason"
                )
            execution.waive_validation(
                conn,
                project_id,
                item_id,
                actor=actor or "",
                reason=waiver_reason or "",
            )
    conn.execute(
        "UPDATE task_item SET done = ?, updated_at = ? WHERE id = ?",
        (1 if done else 0, utcnow(), item_id),
    )
    log_event(
        conn,
        "item",
        project_id,
        task["id"],
        task["key"],
        actor,
        to_value="done" if done else "open",
        detail=row["content"][:120],
    )
    conn.commit()
    return conn.execute("SELECT * FROM task_item WHERE id = ?", (item_id,)).fetchone()


def remove_item(
    conn: Conn, project_id: int, item_id: int, actor: str | None = None
) -> Row:
    row = conn.execute("SELECT * FROM task_item WHERE id = ?", (item_id,)).fetchone()
    if row is None:
        raise BacklogError(f"no task item with id {item_id}")
    if row["kind"] == "todo":
        raise BacklogError("todos cannot be removed through item rm")
    task = get_task_by_id(conn, row["task_id"])
    conn.execute("DELETE FROM task_item WHERE id = ?", (item_id,))
    log_event(
        conn,
        "item_removed",
        project_id,
        task["id"],
        task["key"],
        actor,
        detail=row["content"][:120],
    )
    conn.commit()
    return row

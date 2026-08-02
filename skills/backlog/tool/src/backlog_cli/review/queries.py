"""Review thread projections and filtered queries."""

from __future__ import annotations

from ..core import get_task, get_task_by_id, normalize_key
from ..db import BacklogError, Conn, Row
from ..schema import REVIEW_ROLES, ReviewSeverity
from .model import normalize_severity


def comment_dict(row: Row, reviewer: str) -> dict:
    return {
        "key": row["key"],
        "root": row["root_key"],
        "seq": row["seq"],
        "parent": row["parent_key"],
        "author": row["author"],
        "assignee": row["author"] if row["role"] == "developer" else None,
        "reviewer": reviewer,
        "author_kind": row["author_kind"],
        "role": row["role"],
        "action": row["action"],
        "body": row["body"],
        "file": row["file_path"],
        "line": row["line"],
        "at": row["created_at"],
    }


def thread_summary(conn: Conn, root_key: str) -> dict:
    """Root + direct parent of the latest comment + latest comment. Nothing else."""
    rk = normalize_key(root_key)
    thread = conn.execute("SELECT * FROM review_thread WHERE root_key = ?", (rk,)).fetchone()
    if thread is None:
        raise BacklogError(f"no review thread rooted at {rk}")
    task = get_task_by_id(conn, thread["task_id"])
    root = conn.execute("SELECT * FROM review_comment WHERE key = ?", (rk,)).fetchone()
    last = conn.execute(
        "SELECT * FROM review_comment WHERE key = ?", (thread["last_comment_key"],)
    ).fetchone()
    parent = None
    if last["parent_key"] and last["parent_key"] != rk:
        parent = conn.execute(
            "SELECT * FROM review_comment WHERE key = ?", (last["parent_key"],)
        ).fetchone()

    awaiting = None
    if thread["state"] == "awaiting_developer":
        awaiting = task["assignee"]
    elif thread["state"] == "awaiting_reviewer":
        awaiting = thread["opened_by"]

    return {
        "root": rk,
        "target": task["key"],
        "target_title": task["title"],
        "target_type": task["task_type"],
        "state": thread["state"],
        "resolution": thread["resolution"],
        "severity": thread["severity"],
        "awaiting_role": None if thread["state"] == "closed"
        else thread["state"].removeprefix("awaiting_"),
        "awaiting_actor": awaiting,
        "file": thread["file_path"],
        "line": thread["line"],
        "comment_count": thread["comment_count"],
        "opened_by": thread["opened_by"],
        "reviewer": thread["opened_by"],
        "opened_at": thread["opened_at"],
        "updated_at": thread["updated_at"],
        "root_comment": comment_dict(root, thread["opened_by"]),
        "parent_comment": (
            comment_dict(parent, thread["opened_by"]) if parent is not None else None
        ),
        "last_comment": comment_dict(last, thread["opened_by"]),
        "hidden_comments": max(0, int(thread["comment_count"]) - (2 if parent is None else 3)),
        "reply_to": thread["last_comment_key"],
    }


def full_thread(conn: Conn, root_key: str) -> dict:
    out = thread_summary(conn, root_key)
    rows = conn.execute(
        "SELECT * FROM review_comment WHERE root_key = ? ORDER BY seq",
        (normalize_key(root_key),),
    ).fetchall()
    out["comments"] = [comment_dict(r, out["reviewer"]) for r in rows]
    return out


def comment_updates(conn: Conn, root_key: str, after: str | None = None) -> list[dict]:
    """Return only comments added after a caller's last observed comment."""
    rk = normalize_key(root_key)
    thread = conn.execute(
        "SELECT * FROM review_thread WHERE root_key = ?", (rk,)
    ).fetchone()
    if thread is None:
        raise BacklogError(f"no review thread rooted at {rk}")
    after_seq = 0
    if after:
        marker = conn.execute(
            "SELECT seq, root_key FROM review_comment WHERE key = ?",
            (normalize_key(after),),
        ).fetchone()
        if marker is None or marker["root_key"] != rk:
            raise BacklogError(f"comment {normalize_key(after)} is not in thread {rk}")
        after_seq = int(marker["seq"])
    rows = conn.execute(
        "SELECT * FROM review_comment WHERE root_key = ? AND seq > ? ORDER BY seq",
        (rk, after_seq),
    ).fetchall()
    return [comment_dict(row, thread["opened_by"]) for row in rows]


def inbox(conn: Conn, project_id: int, actor: str | None = None, role: str | None = None,
          key: str | None = None, include_closed: bool = False,
          severity: ReviewSeverity | str | None = None) -> list[dict]:
    sql = ("SELECT r.root_key FROM review_thread r JOIN task t ON t.id = r.task_id "
           "WHERE t.project_id = ?")
    params: list = [project_id]
    if not include_closed:
        sql += " AND r.state != 'closed'"
    if key:
        sql += " AND t.key = ?"
        params.append(normalize_key(key))
    if severity is not None:
        sql += " AND r.severity = ?"
        params.append(normalize_severity(severity).value)
    if role:
        role = role.strip().lower()
        if role not in REVIEW_ROLES:
            raise BacklogError(f"role must be one of {', '.join(REVIEW_ROLES)}")
        sql += " AND r.state = ?"
        params.append(f"awaiting_{role}")
    if actor:
        sql += (" AND ((r.state = 'awaiting_developer' AND t.assignee = ?)"
                "   OR (r.state = 'awaiting_reviewer' AND r.opened_by = ?))")
        params += [actor, actor]
    sql += " ORDER BY t.priority, t.key, r.root_key"
    return [thread_summary(conn, r["root_key"]) for r in conn.execute(sql, params).fetchall()]


def list_threads(conn: Conn, project_id: int, key: str, state: str = "open",
                 severity: ReviewSeverity | str | None = None) -> list[dict]:
    task = get_task(conn, project_id, key)
    sql = "SELECT root_key FROM review_thread WHERE task_id = ?"
    params: list = [task["id"]]
    if state == "open":
        sql += " AND state != 'closed'"
    elif state == "closed":
        sql += " AND state = 'closed'"
    elif state != "all":
        raise BacklogError("state must be open, closed or all")
    if severity is not None:
        sql += " AND severity = ?"
        params.append(normalize_severity(severity).value)
    sql += " ORDER BY root_key"
    return [thread_summary(conn, r["root_key"]) for r in conn.execute(sql, params).fetchall()]

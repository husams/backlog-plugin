"""Review thread opening, severity, and attribution operations."""

from __future__ import annotations

from ..core import get_task, get_task_by_id, normalize_key, trigger_action
from ..db import (
    BacklogError,
    Conn,
    Row,
    actor_kind,
    log_event,
    next_comment_key,
    utcnow,
)
from ..hooks import Action
from ..schema import ReviewSeverity
from .model import _ball_after, _require_body, normalize_severity, resolve_role


def open_thread(
    conn: Conn,
    project_id: int,
    key: str,
    author: str,
    body: str,
    role: str | None = None,
    title: str = "",
    file_path: str | None = None,
    line: int | None = None,
    severity: ReviewSeverity | str = ReviewSeverity.BLOCKER,
) -> dict:
    body = _require_body(body)
    task = get_task(conn, project_id, key)
    role = resolve_role(task, author, role)
    if role != "reviewer":
        raise BacklogError("only the assigned reviewer can open a review thread")
    severity = normalize_severity(severity)
    ckey = next_comment_key(conn)
    ts = utcnow()
    if not title:
        lines = [ln for ln in body.strip().splitlines() if ln.strip()]
        title = lines[0][:120] if lines else ""
    conn.execute(
        "INSERT INTO review_comment(task_id, key, root_key, parent_key, seq, author, "
        "author_kind, role, action, body, file_path, line, created_at) "
        "VALUES(?,?,?,NULL,1,?,?,?,'open',?,?,?,?)",
        (
            task["id"],
            ckey,
            ckey,
            author,
            actor_kind(author),
            role,
            body,
            file_path,
            line,
            ts,
        ),
    )
    conn.execute(
        "INSERT INTO review_thread(task_id, root_key, state, severity, title, file_path, line, "
        "last_comment_key, comment_count, opened_by, opened_at, updated_at) "
        "VALUES(?,?,?,?,?,?,?,?,1,?,?,?)",
        (
            task["id"],
            ckey,
            _ball_after(role),
            severity.value,
            title,
            file_path,
            line,
            ckey,
            author,
            ts,
            ts,
        ),
    )
    log_event(
        conn,
        "review",
        project_id,
        task["id"],
        task["key"],
        author,
        to_value=ckey,
        detail=f"opened {ckey}",
    )
    conn.commit()
    if severity == ReviewSeverity.BLOCKER or task["task_type"] == "iteration":
        trigger_action(
            conn,
            project_id,
            task["key"],
            Action.FEEDBACK_POSTED,
            actor=author,
            operation=(
                "review.iteration_comment_opened"
                if task["task_type"] == "iteration"
                else "review.blocker_opened"
            ),
            parameters={
                "root": ckey,
                "body": body,
                "severity": severity.value,
            },
        )
    return _thread_summary(conn, ckey)


def set_severity(
    conn: Conn,
    project_id: int,
    root_key: str,
    severity: ReviewSeverity | str,
    author: str,
) -> dict:
    """Change a thread's severity through an audited public operation."""
    rk = normalize_key(root_key)
    level = normalize_severity(severity)
    thread = conn.execute(
        "SELECT r.* FROM review_thread r JOIN task t ON t.id = r.task_id "
        "WHERE r.root_key = ? AND t.project_id = ?",
        (rk, project_id),
    ).fetchone()
    if thread is None:
        raise BacklogError(f"no review thread rooted at {rk}")
    task = get_task_by_id(conn, thread["task_id"])
    if not task["reviewer"] or author.casefold() != task["reviewer"].strip().casefold():
        raise BacklogError("only the assigned reviewer can change review severity")
    if thread["severity"] == level.value:
        return _thread_summary(conn, rk)
    conn.execute(
        "UPDATE review_thread SET severity = ?, updated_at = ? WHERE root_key = ?",
        (level.value, utcnow(), rk),
    )
    log_event(
        conn,
        "review",
        project_id,
        task["id"],
        task["key"],
        author,
        from_value=thread["severity"],
        to_value=level.value,
        detail=f"severity changed on {rk}",
    )
    conn.commit()
    if (
        level == ReviewSeverity.BLOCKER
        and thread["severity"] != ReviewSeverity.BLOCKER.value
        and thread["state"] != "closed"
    ):
        trigger_action(
            conn,
            project_id,
            task["key"],
            Action.FEEDBACK_POSTED,
            actor=author,
            operation="review.blocker_escalated",
            parameters={
                "root": rk,
                "from_severity": thread["severity"],
                "severity": level.value,
            },
        )
    return _thread_summary(conn, rk)


def audit(conn: Conn, project_id: int, root_key: str) -> dict:
    """Return the immutable attribution trail for a thread and its decisions."""
    rk = normalize_key(root_key)
    thread = conn.execute(
        "SELECT r.* FROM review_thread r JOIN task t ON t.id = r.task_id "
        "WHERE r.root_key = ? AND t.project_id = ?",
        (rk, project_id),
    ).fetchone()
    if thread is None:
        raise BacklogError(f"no review thread rooted at {rk}")
    task = get_task_by_id(conn, thread["task_id"])
    rows = conn.execute(
        "SELECT * FROM review_comment WHERE root_key = ? ORDER BY seq", (rk,)
    ).fetchall()
    return {
        "root": rk,
        "target": task["key"],
        "reviewer": thread["opened_by"],
        "state": thread["state"],
        "resolution": thread["resolution"],
        "opened_at": thread["opened_at"],
        "closed_by": thread["closed_by"],
        "closed_at": thread["closed_at"],
        "decisions": [
            _query_comment_dict(row, thread["opened_by"])
            for row in rows
            if row["action"] in ("accept", "reject")
        ],
    }


def _thread_summary(conn: Conn, root_key: str) -> dict:
    from .queries import thread_summary

    return thread_summary(conn, root_key)


def _query_comment_dict(row: Row, reviewer: str) -> dict:
    from .queries import comment_dict

    return comment_dict(row, reviewer)

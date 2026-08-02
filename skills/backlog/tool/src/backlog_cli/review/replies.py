"""Review replies, resolution, and reopening operations."""

from __future__ import annotations

from ..core import get_task_by_id, normalize_key, trigger_action
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
from ..schema import REVIEW_ACTIONS, ReviewSeverity
from .model import (
    _THREAD_TRANSITIONS,
    _ball_after,
    _require_body,
    resolve_reply_role,
)


def reply(
    conn: Conn,
    project_id: int,
    comment_key: str,
    author: str,
    action: str,
    body: str,
    role: str | None = None,
    reopen: bool = False,
    emit_action: bool = True,
) -> dict:
    body = _require_body(body)
    ck = normalize_key(comment_key)
    parent = conn.execute(
        "SELECT * FROM review_comment WHERE key = ?", (ck,)
    ).fetchone()
    if parent is None:
        raise BacklogError(f"no review comment with key {ck}")
    thread = conn.execute(
        "SELECT * FROM review_thread WHERE root_key = ?", (parent["root_key"],)
    ).fetchone()
    task = get_task_by_id(conn, parent["task_id"])

    action = action.strip().lower()
    if action not in REVIEW_ACTIONS or action == "open":
        raise BacklogError(
            "action must be one of: comment, fix, reject, accept "
            "(use `review open` to start a new thread)"
        )
    if thread["state"] == "closed" and not reopen:
        raise BacklogError(
            f"thread {thread['root_key']} is closed ({thread['resolution']}). "
            f"Use `backlog review reopen {thread['root_key']}` if it must be re-litigated."
        )

    role = resolve_reply_role(thread, author, role)
    transition = _THREAD_TRANSITIONS.get((thread["state"], role, action))
    if transition is None and not reopen:
        allowed = sorted(
            candidate_action
            for state, candidate_role, candidate_action in _THREAD_TRANSITIONS
            if state == thread["state"] and candidate_role == role
        )
        detail = ", ".join(allowed) if allowed else "none"
        raise BacklogError(
            f"thread {thread['root_key']} does not allow {role} action {action!r} "
            f"from {thread['state']}; allowed for {role}: {detail}"
        )
    ckey = next_comment_key(conn)
    ts = utcnow()
    seq = int(thread["comment_count"]) + 1
    conn.execute(
        "INSERT INTO review_comment(task_id, key, root_key, parent_key, seq, author, "
        "author_kind, role, action, body, file_path, line, created_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            task["id"],
            ckey,
            thread["root_key"],
            parent["key"],
            seq,
            author,
            actor_kind(author),
            role,
            action,
            body,
            parent["file_path"],
            parent["line"],
            ts,
        ),
    )

    if reopen:
        state, resolution = _ball_after(role), None
        closed_by, closed_at = None, None
    else:
        assert transition is not None
        state, resolution = transition
        if state == "closed":
            closed_by, closed_at = author, ts
        else:
            closed_by, closed_at = None, None

    conn.execute(
        "UPDATE review_thread SET state = ?, resolution = ?, last_comment_key = ?, "
        "comment_count = ?, updated_at = ?, closed_by = ?, closed_at = ? WHERE root_key = ?",
        (state, resolution, ckey, seq, ts, closed_by, closed_at, thread["root_key"]),
    )
    log_event(
        conn,
        "review",
        project_id,
        task["id"],
        task["key"],
        author,
        from_value=thread["state"],
        to_value=state,
        detail=f"{action} on {thread['root_key']} ({ckey})",
    )
    conn.commit()
    if emit_action:
        if action == "accept" and _feedback_is_resolved(conn, task, thread):
            trigger_action(
                conn,
                project_id,
                task["key"],
                Action.FEEDBACK_RESOLVED,
                actor=author,
                operation="review.all_blockers_accepted",
                parameters={
                    "root": thread["root_key"],
                    "comment": ckey,
                    "accepted_by": author,
                },
            )
    return _thread_summary(conn, thread["root_key"])


def _unresolved_blockers(conn: Conn, task_id: int) -> list[Row]:
    """Blockers count as resolved only after reviewer acceptance."""
    return conn.execute(
        "SELECT * FROM review_thread WHERE task_id = ? AND severity = 'blocker' "
        "AND (state != 'closed' OR COALESCE(resolution, '') != 'accepted_by_reviewer') "
        "ORDER BY root_key",
        (task_id,),
    ).fetchall()


def _unresolved_threads(conn: Conn, task_id: int) -> list[Row]:
    """Iteration comments of every severity require reviewer acceptance."""
    return conn.execute(
        "SELECT * FROM review_thread WHERE task_id = ? "
        "AND (state != 'closed' OR COALESCE(resolution, '') != 'accepted_by_reviewer') "
        "ORDER BY root_key",
        (task_id,),
    ).fetchall()


def _feedback_is_resolved(conn: Conn, task: Row, thread: Row) -> bool:
    if task["task_type"] == "iteration":
        return not _unresolved_threads(conn, task["id"])
    return thread[
        "severity"
    ] == ReviewSeverity.BLOCKER.value and not _unresolved_blockers(conn, task["id"])


def reopen(
    conn: Conn,
    project_id: int,
    root_key: str,
    author: str,
    body: str,
    role: str | None = None,
) -> dict:
    body = _require_body(body)
    rk = normalize_key(root_key)
    thread = conn.execute(
        "SELECT * FROM review_thread WHERE root_key = ?", (rk,)
    ).fetchone()
    if thread is None:
        raise BacklogError(f"no review thread rooted at {rk}")
    if thread["state"] != "closed":
        raise BacklogError(f"thread {rk} is already open ({thread['state']})")
    task = get_task_by_id(conn, thread["task_id"])
    resolved_role = resolve_reply_role(thread, author, role)
    if resolved_role != "reviewer":
        raise BacklogError("only a reviewer can reopen a review thread")
    conn.execute(
        "UPDATE review_thread SET state = 'awaiting_developer', resolution = NULL, "
        "closed_by = NULL, closed_at = NULL WHERE root_key = ?",
        (rk,),
    )
    conn.commit()
    result = reply(
        conn,
        project_id,
        thread["last_comment_key"],
        author,
        "comment",
        body,
        role=role,
        reopen=True,
        emit_action=False,
    )
    if (
        thread["severity"] == ReviewSeverity.BLOCKER.value
        or task["task_type"] == "iteration"
    ):
        trigger_action(
            conn,
            project_id,
            task["key"],
            Action.FEEDBACK_REOPENED,
            actor=author,
            operation=(
                "review.iteration_comment_reopened"
                if task["task_type"] == "iteration"
                else "review.blocker_reopened"
            ),
            parameters={
                "root": rk,
                "reply": result["reply_to"],
                "body": body,
                "severity": thread["severity"],
            },
        )
    return result


def _thread_summary(conn: Conn, root_key: str) -> dict:
    from .queries import thread_summary

    return thread_summary(conn, root_key)

"""Threaded review comments.

Thread model
------------
A *thread* is a top-level (root) comment plus its chain of replies. Exactly one
party holds the ball at any time:

    open    -> ball flips to the other party (thread starts)
    comment -> ball flips to the other party
    fix     -> "I addressed this"; ball flips to the other party to verify
    reject  -> "I disagree, here is why"; ball flips to the other party
    accept  -> reviewer closes the thread after a developer reply

An agent never needs the whole thread: `inbox` returns the root comment, the
direct parent of the latest comment, and the latest comment.
"""

from __future__ import annotations

from .core import get_task, get_task_by_id, normalize_key, trigger_action
from .db import BacklogError, Conn, Row, actor_kind, log_event, next_comment_key, utcnow
from .hooks import Action
from .schema import REVIEW_ACTIONS, REVIEW_ROLES, REVIEW_SEVERITIES, ReviewSeverity


_THREAD_TRANSITIONS: dict[tuple[str, str, str], tuple[str, str | None]] = {
    ("awaiting_developer", "developer", "comment"): ("awaiting_reviewer", None),
    ("awaiting_developer", "developer", "fix"): ("awaiting_reviewer", None),
    ("awaiting_developer", "developer", "reject"): ("awaiting_reviewer", None),
    ("awaiting_reviewer", "reviewer", "comment"): ("awaiting_developer", None),
    ("awaiting_reviewer", "reviewer", "reject"): ("awaiting_developer", None),
    ("awaiting_reviewer", "reviewer", "accept"): (
        "closed", "accepted_by_reviewer"
    ),
}


def normalize_severity(value: ReviewSeverity | str) -> ReviewSeverity:
    if isinstance(value, ReviewSeverity):
        return value
    try:
        return ReviewSeverity(str(value).strip().lower())
    except ValueError:
        raise BacklogError(
            f"severity must be one of {', '.join(REVIEW_SEVERITIES)}"
        ) from None


def resolve_role(task: Row, author: str, role: str | None) -> str:
    if role and role != "auto":
        role = role.strip().lower()
        if role not in REVIEW_ROLES:
            raise BacklogError(f"role must be one of {', '.join(REVIEW_ROLES)}")
        return role
    if task["reviewer"] and author == task["reviewer"]:
        return "reviewer"
    if task["assignee"] and author == task["assignee"]:
        return "developer"
    raise BacklogError(
        f"cannot infer role for author {author!r} on {task['key']} "
        f"(assignee={task['assignee'] or '-'}, reviewer={task['reviewer'] or '-'}). "
        "Pass --role reviewer|developer."
    )


def _ball_after(role: str) -> str:
    return "awaiting_developer" if role == "reviewer" else "awaiting_reviewer"


def open_thread(conn: Conn, project_id: int, key: str, author: str, body: str,
                role: str | None = None, title: str = "", file_path: str | None = None,
                line: int | None = None,
                severity: ReviewSeverity | str = ReviewSeverity.BLOCKER) -> dict:
    task = get_task(conn, project_id, key)
    role = resolve_role(task, author, role)
    if role != "reviewer":
        raise BacklogError("only a reviewer can open a review thread")
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
        (task["id"], ckey, ckey, author, actor_kind(author), role, body,
         file_path, line, ts),
    )
    conn.execute(
        "INSERT INTO review_thread(task_id, root_key, state, severity, title, file_path, line, "
        "last_comment_key, comment_count, opened_by, opened_at, updated_at) "
        "VALUES(?,?,?,?,?,?,?,?,1,?,?,?)",
        (task["id"], ckey, _ball_after(role), severity.value, title, file_path, line,
         ckey, author, ts, ts),
    )
    log_event(conn, "review", project_id, task["id"], task["key"], author,
              to_value=ckey, detail=f"opened {ckey}")
    conn.commit()
    if severity == ReviewSeverity.BLOCKER:
        trigger_action(
            conn,
            project_id,
            task["key"],
            Action.FEEDBACK_POSTED,
            actor=author,
            operation="review.blocker_opened",
            parameters={
                "root": ckey,
                "body": body,
                "severity": severity.value,
            },
        )
    return thread_summary(conn, ckey)


def set_severity(conn: Conn, project_id: int, root_key: str,
                 severity: ReviewSeverity | str, author: str) -> dict:
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
    if thread["severity"] == level.value:
        return thread_summary(conn, rk)
    task = get_task_by_id(conn, thread["task_id"])
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
    return thread_summary(conn, rk)


def reply(conn: Conn, project_id: int, comment_key: str, author: str, action: str,
          body: str, role: str | None = None, reopen: bool = False,
          emit_action: bool = True) -> dict:
    ck = normalize_key(comment_key)
    parent = conn.execute("SELECT * FROM review_comment WHERE key = ?", (ck,)).fetchone()
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

    role = resolve_role(task, author, role)
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
        (task["id"], ckey, thread["root_key"], parent["key"], seq, author,
         actor_kind(author), role, action, body, parent["file_path"], parent["line"], ts),
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
    log_event(conn, "review", project_id, task["id"], task["key"], author,
              from_value=thread["state"], to_value=state,
              detail=f"{action} on {thread['root_key']} ({ckey})")
    conn.commit()
    if emit_action:
        if (
            action == "accept"
            and thread["severity"] == ReviewSeverity.BLOCKER.value
            and not _unresolved_blockers(conn, task["id"])
        ):
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
    return thread_summary(conn, thread["root_key"])


def _unresolved_blockers(conn: Conn, task_id: int) -> list[Row]:
    """Blockers count as resolved only after reviewer acceptance."""
    return conn.execute(
        "SELECT * FROM review_thread WHERE task_id = ? AND severity = 'blocker' "
        "AND (state != 'closed' OR COALESCE(resolution, '') != 'accepted_by_reviewer') "
        "ORDER BY root_key",
        (task_id,),
    ).fetchall()


def reopen(conn: Conn, project_id: int, root_key: str, author: str, body: str,
           role: str | None = None) -> dict:
    rk = normalize_key(root_key)
    thread = conn.execute("SELECT * FROM review_thread WHERE root_key = ?", (rk,)).fetchone()
    if thread is None:
        raise BacklogError(f"no review thread rooted at {rk}")
    if thread["state"] != "closed":
        raise BacklogError(f"thread {rk} is already open ({thread['state']})")
    task = get_task_by_id(conn, thread["task_id"])
    resolved_role = resolve_role(task, author, role)
    if resolved_role != "reviewer":
        raise BacklogError("only a reviewer can reopen a review thread")
    conn.execute(
        "UPDATE review_thread SET state = 'awaiting_developer', resolution = NULL, "
        "closed_by = NULL, closed_at = NULL WHERE root_key = ?",
        (rk,),
    )
    conn.commit()
    result = reply(
        conn, project_id, thread["last_comment_key"], author, "comment", body,
        role=role, reopen=True, emit_action=False
    )
    if thread["severity"] == ReviewSeverity.BLOCKER.value:
        trigger_action(
            conn,
            project_id,
            task["key"],
            Action.FEEDBACK_REOPENED,
            actor=author,
            operation="review.blocker_reopened",
            parameters={
                "root": rk,
                "reply": result["reply_to"],
                "body": body,
                "severity": thread["severity"],
            },
        )
    return result


def _comment_dict(row: Row) -> dict:
    return {
        "key": row["key"],
        "seq": row["seq"],
        "parent": row["parent_key"],
        "author": row["author"],
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
        awaiting = task["reviewer"]

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
        "opened_at": thread["opened_at"],
        "updated_at": thread["updated_at"],
        "root_comment": _comment_dict(root),
        "parent_comment": _comment_dict(parent) if parent is not None else None,
        "last_comment": _comment_dict(last),
        "hidden_comments": max(0, int(thread["comment_count"]) - (2 if parent is None else 3)),
        "reply_to": thread["last_comment_key"],
    }


def full_thread(conn: Conn, root_key: str) -> dict:
    out = thread_summary(conn, root_key)
    rows = conn.execute(
        "SELECT * FROM review_comment WHERE root_key = ? ORDER BY seq",
        (normalize_key(root_key),),
    ).fetchall()
    out["comments"] = [_comment_dict(r) for r in rows]
    return out


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
                "   OR (r.state = 'awaiting_reviewer' AND t.reviewer = ?))")
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

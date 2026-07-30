"""Threaded review comments.

Thread model
------------
A *thread* is a top-level (root) comment plus its chain of replies. Exactly one
party holds the ball at any time:

    open    -> ball flips to the other party (thread starts)
    comment -> ball flips to the other party
    fix     -> "I addressed this"; ball flips to the other party to verify
    reject  -> "I disagree, here is why"; ball flips to the other party
    accept  -> thread CLOSES, regardless of which party accepted

An agent never needs the whole thread: `inbox` returns the root comment, the
direct parent of the latest comment, and the latest comment.
"""

from __future__ import annotations

from .core import get_task, get_task_by_id, normalize_key, trigger_action
from .db import BacklogError, Conn, Row, actor_kind, log_event, next_comment_key, utcnow
from .hooks import Action
from .schema import REVIEW_ACTIONS, REVIEW_ROLES


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
                line: int | None = None) -> dict:
    task = get_task(conn, project_id, key)
    role = resolve_role(task, author, role)
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
        "INSERT INTO review_thread(task_id, root_key, state, title, file_path, line, "
        "last_comment_key, comment_count, opened_by, opened_at, updated_at) "
        "VALUES(?,?,?,?,?,?,?,1,?,?,?)",
        (task["id"], ckey, _ball_after(role), title, file_path, line, ckey, author, ts, ts),
    )
    log_event(conn, "review", project_id, task["id"], task["key"], author,
              to_value=ckey, detail=f"opened {ckey}")
    conn.commit()
    trigger_action(
        conn,
        project_id,
        task["key"],
        Action.FEEDBACK_POSTED,
        actor=author,
        operation="review.open",
        parameters={"comment": ckey, "body": body, "role": role},
    )
    return thread_summary(conn, ckey)


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

    if action == "accept":
        state, resolution = "closed", f"accepted_by_{role}"
        closed_by, closed_at = author, ts
    else:
        state, resolution = _ball_after(role), None
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
        action_event = {
            "comment": Action.FEEDBACK_REPLIED,
            "fix": Action.FEEDBACK_REPLIED,
            "reject": Action.FEEDBACK_REJECTED,
            "accept": Action.FEEDBACK_ACCEPTED,
        }[action]
        trigger_action(
            conn,
            project_id,
            task["key"],
            action_event,
            actor=author,
            operation="review.reply",
            parameters={
                "root": thread["root_key"],
                "comment": ckey,
                "reply_action": action,
                "body": body,
                "role": role,
            },
        )
    return thread_summary(conn, thread["root_key"])


def reopen(conn: Conn, project_id: int, root_key: str, author: str, body: str,
           role: str | None = None) -> dict:
    rk = normalize_key(root_key)
    thread = conn.execute("SELECT * FROM review_thread WHERE root_key = ?", (rk,)).fetchone()
    if thread is None:
        raise BacklogError(f"no review thread rooted at {rk}")
    if thread["state"] != "closed":
        raise BacklogError(f"thread {rk} is already open ({thread['state']})")
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
    task = get_task_by_id(conn, thread["task_id"])
    trigger_action(
        conn,
        project_id,
        task["key"],
        Action.FEEDBACK_REOPENED,
        actor=author,
        operation="review.reopen",
        parameters={"root": rk, "body": body, "role": role},
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
          key: str | None = None, include_closed: bool = False) -> list[dict]:
    sql = ("SELECT r.root_key FROM review_thread r JOIN task t ON t.id = r.task_id "
           "WHERE t.project_id = ?")
    params: list = [project_id]
    if not include_closed:
        sql += " AND r.state != 'closed'"
    if key:
        sql += " AND t.key = ?"
        params.append(normalize_key(key))
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


def list_threads(conn: Conn, project_id: int, key: str, state: str = "open") -> list[dict]:
    task = get_task(conn, project_id, key)
    sql = "SELECT root_key FROM review_thread WHERE task_id = ?"
    params: list = [task["id"]]
    if state == "open":
        sql += " AND state != 'closed'"
    elif state == "closed":
        sql += " AND state = 'closed'"
    elif state != "all":
        raise BacklogError("state must be open, closed or all")
    sql += " ORDER BY root_key"
    return [thread_summary(conn, r["root_key"]) for r in conn.execute(sql, params).fetchall()]

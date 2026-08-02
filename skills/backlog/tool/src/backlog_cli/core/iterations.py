"""Iteration membership mutations and lookups."""

from __future__ import annotations


from ..db import BacklogError, Conn, Row, log_event, utcnow

from .task_queries import get_task


def iteration_members(conn: Conn, iteration_id: int) -> list[Row]:
    return conn.execute(
        "SELECT t.* FROM iteration_member m JOIN task t ON t.id=m.member_id "
        "WHERE m.iteration_id=? ORDER BY t.priority,t.key",
        (iteration_id,),
    ).fetchall()


def add_iteration_member(
    conn: Conn,
    project_id: int,
    iteration_key: str,
    member_key: str,
    actor: str | None = None,
) -> None:
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
    if conn.execute(
        "SELECT 1 FROM iteration_member WHERE iteration_id=? AND member_id=?",
        (iteration["id"], member["id"]),
    ).fetchone():
        return
    conn.execute(
        "INSERT INTO iteration_member(iteration_id,member_id,created_at) VALUES(?,?,?) "
        "ON CONFLICT(iteration_id,member_id) DO NOTHING",
        (iteration["id"], member["id"], utcnow()),
    )
    detail = f"added {member['key']} to {iteration['key']}"
    log_event(
        conn,
        "iteration.member_added",
        project_id,
        iteration["id"],
        iteration["key"],
        actor,
        to_value=member["key"],
        detail=detail,
    )
    log_event(
        conn,
        "iteration.joined",
        project_id,
        member["id"],
        member["key"],
        actor,
        to_value=iteration["key"],
        detail=detail,
    )
    conn.commit()


def remove_iteration_member(
    conn: Conn,
    project_id: int,
    iteration_key: str,
    member_key: str,
    actor: str | None = None,
) -> None:
    iteration = get_task(conn, project_id, iteration_key)
    member = get_task(conn, project_id, member_key)
    if iteration["task_type"] != "iteration":
        raise BacklogError(f"{iteration['key']} is not an Iteration")
    if iteration["status"] != "open":
        raise BacklogError(
            f"{iteration['key']} is {iteration['status']}; members may only be "
            "removed from an Open Iteration"
        )
    cursor = conn.execute(
        "DELETE FROM iteration_member WHERE iteration_id=? AND member_id=?",
        (iteration["id"], member["id"]),
    )
    if cursor.rowcount == 0:
        raise BacklogError(f"{member['key']} is not a member of {iteration['key']}")
    detail = f"removed {member['key']} from {iteration['key']}"
    log_event(
        conn,
        "iteration.member_removed",
        project_id,
        iteration["id"],
        iteration["key"],
        actor,
        from_value=member["key"],
        detail=detail,
    )
    log_event(
        conn,
        "iteration.left",
        project_id,
        member["id"],
        member["key"],
        actor,
        from_value=iteration["key"],
        detail=detail,
    )
    conn.commit()

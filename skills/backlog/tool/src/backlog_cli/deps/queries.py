"""Dependency edge queries and blocker projections."""

from __future__ import annotations

from ..db import Conn, Row
from .model import OTHER, is_satisfied, normalize_kind

# --------------------------------------------------------------------------- #

def outgoing(conn: Conn, task_id: int, kind: str | None = None) -> list[Row]:
    sql = (f"SELECT d.*, {OTHER} FROM dependency d JOIN task t ON t.id = d.to_task_id "
           "WHERE d.from_task_id = ?")
    params: list = [task_id]
    if kind:
        sql += " AND d.kind = ?"
        params.append(normalize_kind(kind))
    return conn.execute(sql + " ORDER BY d.kind, t.key", params).fetchall()


def incoming(conn: Conn, task_id: int, kind: str | None = None) -> list[Row]:
    sql = (f"SELECT d.*, {OTHER} FROM dependency d JOIN task t ON t.id = d.from_task_id "
           "WHERE d.to_task_id = ?")
    params: list = [task_id]
    if kind:
        sql += " AND d.kind = ?"
        params.append(normalize_kind(kind))
    return conn.execute(sql + " ORDER BY d.kind, t.key", params).fetchall()


def edges_for(conn: Conn, task_id: int, kind: str | None = None) -> list[dict]:
    out = [{**dict(r), "direction": "out"} for r in outgoing(conn, task_id, kind)]
    inc = [{**dict(r), "direction": "in"} for r in incoming(conn, task_id, kind)]
    edges = out + inc
    for e in edges:
        e["satisfied"] = is_satisfied(e["other_status"])
    return edges


def blockers(conn: Conn, task_id: int, open_only: bool = True) -> list[dict]:
    """Everything that must finish before this task may start."""
    out = []
    project_id = conn.execute(
        "SELECT project_id FROM task WHERE id = ?", (task_id,)
    ).fetchone()["project_id"]
    for r in incoming(conn, task_id, "blocks"):
        e = dict(r)
        e["satisfied"] = is_satisfied(e["other_status"], conn, project_id, e["other_type"])
        if open_only and e["satisfied"]:
            continue
        out.append(e)
    return out


def blocked_by_map(conn: Conn, project_id: int) -> dict[str, list[str]]:
    """key -> unsatisfied blocker keys, for the whole project. One query."""
    rows = conn.execute(
        "SELECT tgt.key AS target, src.key AS blocker, src.status AS blocker_status, "
        "       src.task_type AS blocker_type "
        "FROM dependency d "
        "JOIN task src ON src.id = d.from_task_id "
        "JOIN task tgt ON tgt.id = d.to_task_id "
        "WHERE d.kind = 'blocks' AND tgt.project_id = ? "
        "ORDER BY tgt.key, src.key",
        (project_id,),
    ).fetchall()
    out: dict[str, list[str]] = {}
    for r in rows:
        if is_satisfied(r["blocker_status"], conn, project_id, r["blocker_type"]):
            continue
        out.setdefault(r["target"], []).append(r["blocker"])
    return out


def all_edges(conn: Conn, project_id: int, kind: str | None = None) -> list[Row]:
    sql = (
        "SELECT d.*, src.key AS from_key, dst.key AS to_key, "
        "       src.status AS from_status, dst.status AS to_status "
        "FROM dependency d "
        "JOIN task src ON src.id = d.from_task_id "
        "JOIN task dst ON dst.id = d.to_task_id "
        "WHERE src.project_id = ?"
    )
    params: list = [project_id]
    if kind:
        sql += " AND d.kind = ?"
        params.append(normalize_kind(kind))
    return conn.execute(sql + " ORDER BY d.kind, src.key, dst.key", params).fetchall()


# --------------------------------------------------------------------------- #

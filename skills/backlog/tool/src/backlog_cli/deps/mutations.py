"""Dependency edge mutation operations."""

from __future__ import annotations

from ..db import BacklogError, Conn, log_event, utcnow
from ..schema import HARD_DEPENDENCY_KINDS
from .graph import cycle_path, keys_for
from .model import canonical, normalize_kind

# --------------------------------------------------------------------------- #


def add(
    conn: Conn,
    project_id: int,
    from_key: str,
    to_key: str,
    kind: str = "blocks",
    note: str = "",
    actor: str | None = None,
    external_id: str | None = None,
) -> dict:
    from ..core import get_task

    kind = normalize_kind(kind)
    src = get_task(conn, project_id, from_key)
    dst = get_task(conn, project_id, to_key)
    if src["id"] == dst["id"]:
        raise BacklogError(f"{src['key']} cannot depend on itself")
    a, b = canonical(src["id"], dst["id"], kind)

    # Check for the edge first: it is an indexed lookup, whereas the cycle check
    # walks every `blocks` edge. An edge that is already recorded cannot
    # introduce a cycle, and re-syncing an unchanged graph is the common case.
    existing = conn.execute(
        "SELECT * FROM dependency WHERE from_task_id=? AND to_task_id=? AND kind=?",
        (a, b, kind),
    ).fetchone()
    if existing is not None:
        if (note and note != existing["note"]) or (
            external_id and external_id != existing["external_id"]
        ):
            conn.execute(
                "UPDATE dependency SET note = COALESCE(NULLIF(?,''), note), "
                "external_id = COALESCE(?, external_id) WHERE id = ?",
                (note, external_id, existing["id"]),
            )
            conn.commit()
        return {
            **dict(existing),
            "created": False,
            "from_key": src["key"],
            "to_key": dst["key"],
        }

    if kind in HARD_DEPENDENCY_KINDS:
        path = cycle_path(conn, a, b)
        if path:
            keys = keys_for(conn, path)
            raise BacklogError(
                "that edge would create a dependency cycle: " + " -> ".join(keys)
            )

    conn.execute(
        "INSERT INTO dependency(from_task_id, to_task_id, kind, note, external_id, "
        "created_at, created_by) VALUES(?,?,?,?,?,?,?)",
        (a, b, kind, note, external_id, utcnow(), actor),
    )
    detail = f"{src['key']} {kind} {dst['key']}"
    log_event(
        conn,
        "dependency",
        project_id,
        src["id"],
        src["key"],
        actor,
        to_value=dst["key"],
        detail=detail,
    )
    log_event(
        conn,
        "dependency",
        project_id,
        dst["id"],
        dst["key"],
        actor,
        from_value=src["key"],
        detail=detail,
    )
    conn.commit()
    row = conn.execute(
        "SELECT * FROM dependency WHERE from_task_id=? AND to_task_id=? AND kind=?",
        (a, b, kind),
    ).fetchone()
    return {**dict(row), "created": True, "from_key": src["key"], "to_key": dst["key"]}


def remove(
    conn: Conn,
    project_id: int,
    from_key: str,
    to_key: str,
    kind: str = "blocks",
    actor: str | None = None,
) -> dict:
    from ..core import get_task

    kind = normalize_kind(kind)
    src = get_task(conn, project_id, from_key)
    dst = get_task(conn, project_id, to_key)
    a, b = canonical(src["id"], dst["id"], kind)
    row = conn.execute(
        "SELECT * FROM dependency WHERE from_task_id=? AND to_task_id=? AND kind=?",
        (a, b, kind),
    ).fetchone()
    if row is None:
        raise BacklogError(f"no {kind} edge {src['key']} -> {dst['key']}")
    conn.execute("DELETE FROM dependency WHERE id = ?", (row["id"],))
    detail = f"{src['key']} {kind} {dst['key']}"
    log_event(
        conn,
        "dependency_removed",
        project_id,
        src["id"],
        src["key"],
        actor,
        to_value=dst["key"],
        detail=detail,
    )
    log_event(
        conn,
        "dependency_removed",
        project_id,
        dst["id"],
        dst["key"],
        actor,
        from_value=src["key"],
        detail=detail,
    )
    conn.commit()
    return {**dict(row), "from_key": src["key"], "to_key": dst["key"]}


# --------------------------------------------------------------------------- #

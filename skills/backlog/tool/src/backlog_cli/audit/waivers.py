"""Validation waiver persistence."""

from __future__ import annotations

from typing import Any

from ..db import BacklogError, Conn, log_event, utcnow
from ..execution.store import executable_item
from .common import _task_for_item

def waive_validation(
    conn: Conn, project_id: int, item_id: int, *, actor: str, reason: str,
) -> dict[str, Any]:
    actor = (actor or "").strip()
    reason = (reason or "").strip()
    if not actor:
        raise BacklogError("validation waiver requires a non-empty actor")
    if not reason:
        raise BacklogError("validation waiver requires a non-empty reason")
    task = _task_for_item(conn, project_id, item_id)
    executable = executable_item(conn, item_id)
    now = utcnow()
    waiver_id = conn.insert_returning_id(
        "INSERT INTO validation_waiver(item_id,spec_fingerprint,actor,reason,created_at) "
        "VALUES(?,?,?,?,?)",
        (item_id, executable["spec_fingerprint"], actor, reason, now),
    )
    log_event(
        conn, "validation.waived", project_id, task["id"], task["key"], actor,
        to_value=str(item_id), detail=reason,
    )
    conn.commit()
    row = conn.execute(
        "SELECT * FROM validation_waiver WHERE id=?", (waiver_id,)
    ).fetchone()
    return {key: row[key] for key in row.keys()}


def current_waiver(conn: Conn, item_id: int) -> dict[str, Any] | None:
    executable = executable_item(conn, item_id)
    row = conn.execute(
        "SELECT * FROM validation_waiver WHERE item_id=? AND spec_fingerprint=? "
        "AND superseded_at IS NULL ORDER BY id DESC LIMIT 1",
        (item_id, executable["spec_fingerprint"]),
    ).fetchone()
    return {key: row[key] for key in row.keys()} if row else None

"""Shared validation audit projections."""

from __future__ import annotations

import json
from typing import Any, Mapping

from ..db import BacklogError, Conn
from ..execution.contracts import SourceIdentity


def _task_for_item(conn: Conn, project_id: int, item_id: int):
    row = conn.execute(
        "SELECT t.* FROM task t JOIN task_item i ON i.task_id=t.id "
        "WHERE t.project_id=? AND i.id=?",
        (project_id, item_id),
    ).fetchone()
    if row is None:
        raise BacklogError(f"no task item with id {item_id} in this project")
    return row


def _source_matches(result: Mapping[str, Any], current: SourceIdentity) -> bool:
    if current.unavailable or result["source_revision_unavailable"]:
        return True
    return (
        result["source_revision"] == current.revision
        and result["source_dirty_fingerprint"] == current.dirty_fingerprint
    )


def _decode_json(value: str | None) -> Any:
    return json.loads(value) if value is not None else None

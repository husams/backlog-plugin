"""Bounded validation execution history."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..db import BacklogError, Conn
from ..execution.policy import source_identity
from ..execution.store import executable_item
from .common import _decode_json, _source_matches


def execution_history(
    conn: Conn,
    item_id: int,
    *,
    limit: int = 20,
    project_root: Path | None = None,
) -> list[dict[str, Any]]:
    """Return newest-first bounded result history."""
    executable = executable_item(conn, item_id)
    if isinstance(limit, bool) or limit < 1 or limit > 100:
        raise BacklogError("history limit must be between 1 and 100")
    current_source = source_identity(project_root) if project_root is not None else None
    rows = conn.execute(
        "SELECT * FROM execution_result WHERE item_id=? ORDER BY id DESC LIMIT ?",
        (item_id, limit),
    ).fetchall()
    history: list[dict[str, Any]] = []
    for row in rows:
        value = {key: row[key] for key in row.keys()}
        expected = _decode_json(value.pop("expected_result"))
        actual = _decode_json(value.pop("actual_result"))
        value["expected"] = expected
        value["actual"] = actual
        value["diagnostic"] = value.pop("detail")
        value["stale"] = value["spec_fingerprint"] != executable[
            "spec_fingerprint"
        ] or (current_source is not None and not _source_matches(row, current_source))
        history.append(value)
    return history

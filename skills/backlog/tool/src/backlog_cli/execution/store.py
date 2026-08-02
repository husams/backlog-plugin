"""Executable-item declaration persistence."""

from __future__ import annotations

import json
from typing import Any, Mapping

from ..db import BacklogError, Conn, utcnow
from .specs import parse_spec


def set_executable(
    conn: Conn, item_id: int, value: Mapping[str, Any]
) -> dict[str, Any]:
    spec = parse_spec(value)
    item = conn.execute(
        "SELECT id, kind FROM task_item WHERE id = ?", (item_id,)
    ).fetchone()
    if item is None:
        raise BacklogError(f"no task item with id {item_id}")
    if item["kind"] not in ("acceptance_criteria", "checklist"):
        raise BacklogError(
            "only acceptance criteria and checklist items may declare execution; "
            f"item {item_id} is {item['kind']}"
        )
    now = utcnow()
    encoded = json.dumps(spec.canonical(), sort_keys=True, separators=(",", ":"))
    conn.execute(
        "INSERT INTO executable_item(item_id, executor, requirement, execution_spec, "
        "spec_fingerprint, created_at, updated_at) VALUES(?,?,?,?,?,?,?) "
        "ON CONFLICT(item_id) DO UPDATE SET executor=excluded.executor, "
        "requirement=excluded.requirement, execution_spec=excluded.execution_spec, "
        "spec_fingerprint=excluded.spec_fingerprint, updated_at=excluded.updated_at",
        (
            item_id,
            spec.executor.value,
            spec.requirement.value,
            encoded,
            spec.fingerprint,
            now,
            now,
        ),
    )
    conn.commit()
    return executable_item(conn, item_id)


def executable_item(conn: Conn, item_id: int) -> dict[str, Any]:
    row = conn.execute(
        "SELECT * FROM executable_item WHERE item_id = ?", (item_id,)
    ).fetchone()
    if row is None:
        raise BacklogError(f"task item {item_id} has no execution specification")
    result = {key: row[key] for key in row.keys()}
    result["execution_spec"] = json.loads(result["execution_spec"])
    return result


def _item_details(conn: Conn, item: Mapping[str, Any]) -> dict[str, Any]:
    from ..audit import item_state

    """Return a task item with its execution declaration and current state."""
    result = {key: item[key] for key in item.keys()}
    row = conn.execute(
        "SELECT executor, requirement, execution_spec, spec_fingerprint "
        "FROM executable_item WHERE item_id = ?",
        (item["id"],),
    ).fetchone()
    if row is None:
        result.update({"executor": "plain", "requirement": None, "state": None})
        return result
    spec = json.loads(row["execution_spec"])
    result.update(
        {
            "executor": row["executor"],
            "requirement": row["requirement"],
            "state": item_state(conn, int(item["id"])),
            "execution_spec": spec,
            "spec_fingerprint": row["spec_fingerprint"],
        }
    )
    return result

"""Validation result recording and aggregate status checks."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..db import BacklogError, Conn, utcnow
from ..execution.contracts import SourceIdentity, TerminalStatus, _json_value
from ..execution.policy import source_identity
from ..execution.store import executable_item
from .common import _source_matches
from .waivers import current_waiver


def record_result(
    conn: Conn,
    item_id: int,
    spec_fingerprint: str,
    status: TerminalStatus | str,
    *,
    reason: str = "",
    detail: str = "",
    source: SourceIdentity | None = None,
    actual_exit_code: int | None = None,
    stdout: str = "",
    stderr: str = "",
    duration_ms: int = 0,
    started_at: str | None = None,
    finished_at: str | None = None,
    expected: Any = None,
    actual: Any = None,
    hook_name: str | None = None,
    implementation_identity: str | None = None,
    actor: str | None = None,
) -> dict[str, Any]:
    try:
        terminal = TerminalStatus(status)
    except ValueError as exc:
        raise BacklogError(
            "execution result must be pass, fail, error, or skipped"
        ) from exc
    if terminal == TerminalStatus.SKIPPED and reason not in {
        "policy_denied",
        "batch_budget_exhausted",
    }:
        raise BacklogError(
            "skipped execution results require reason=policy_denied "
            "or batch_budget_exhausted"
        )
    source = source or SourceIdentity(unavailable=True)
    if hook_name is not None and not isinstance(hook_name, str):
        raise BacklogError("hook_name must be a string")
    if implementation_identity is not None and not isinstance(
        implementation_identity, str
    ):
        raise BacklogError("implementation_identity must be a string")
    _json_value(expected, "expected result")
    _json_value(actual, "actual result")
    now = utcnow()
    rid = conn.insert_returning_id(
        "INSERT INTO execution_result(item_id,spec_fingerprint,status,reason,detail,"
        "expected_result,actual_result,hook_name,implementation_identity,"
        "actual_exit_code,stdout,stderr,duration_ms,"
        "source_revision,source_dirty_fingerprint,source_revision_unavailable,"
        "actor,started_at,finished_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            item_id,
            spec_fingerprint,
            terminal.value,
            reason,
            detail,
            json.dumps(
                expected, sort_keys=True, separators=(",", ":"), ensure_ascii=False
            ),
            json.dumps(
                actual, sort_keys=True, separators=(",", ":"), ensure_ascii=False
            ),
            hook_name,
            implementation_identity,
            actual_exit_code,
            stdout,
            stderr,
            duration_ms,
            source.revision,
            source.dirty_fingerprint,
            1 if source.unavailable else 0,
            (actor or "unknown").strip() or "unknown",
            started_at or now,
            finished_at or now,
        ),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM execution_result WHERE id = ?", (rid,)).fetchone()
    return {key: row[key] for key in row.keys()}


def item_state(conn: Conn, item_id: int, project_root: Path | None = None) -> str:
    """Return pending or the latest fresh terminal status."""
    executable = executable_item(conn, item_id)
    row = conn.execute(
        "SELECT * FROM execution_result WHERE item_id = ? AND spec_fingerprint = ? "
        "ORDER BY id DESC LIMIT 1",
        (item_id, executable["spec_fingerprint"]),
    ).fetchone()
    if row is None:
        return "pending"
    if project_root is not None and not _source_matches(
        row, source_identity(project_root)
    ):
        return "pending"
    return row["status"]


def required_validations_pass(
    conn: Conn,
    task_id: int,
    project_root: Path | None = None,
) -> tuple[bool, list[int]]:
    rows = conn.execute(
        "SELECT e.item_id, e.spec_fingerprint FROM executable_item e "
        "JOIN task_item i ON i.id=e.item_id "
        "WHERE i.task_id=? AND e.requirement='required' ORDER BY e.item_id",
        (task_id,),
    ).fetchall()
    failed: list[int] = []
    for row in rows:
        latest = conn.execute(
            "SELECT * FROM execution_result WHERE item_id=? AND spec_fingerprint=? "
            "ORDER BY id DESC LIMIT 1",
            (row["item_id"], row["spec_fingerprint"]),
        ).fetchone()
        passed = (
            latest is not None
            and latest["status"] == TerminalStatus.PASS.value
            and (
                project_root is None
                or _source_matches(latest, source_identity(project_root))
            )
        )
        if not passed and current_waiver(conn, int(row["item_id"])) is None:
            failed.append(int(row["item_id"]))
    return not failed, failed


def required_results_pass(
    conn: Conn,
    task_id: int,
    project_root: Path | None = None,
) -> tuple[bool, list[int]]:
    """Aggregate execution verdict; unlike workflow gates, waivers are not passes."""
    rows = conn.execute(
        "SELECT e.item_id FROM executable_item e JOIN task_item i ON i.id=e.item_id "
        "WHERE i.task_id=? AND e.requirement='required' ORDER BY e.item_id",
        (task_id,),
    ).fetchall()
    failed = [
        int(row["item_id"])
        for row in rows
        if item_state(conn, int(row["item_id"]), project_root) != "pass"
    ]
    return not failed, failed

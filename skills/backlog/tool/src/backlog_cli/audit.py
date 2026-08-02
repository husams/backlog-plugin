"""Validation results, history, waivers, diagnostics, and workflow gates."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from .db import BacklogError, Conn, log_event, utcnow
from .execution.contracts import Requirement, SourceIdentity, TerminalStatus, _json_value
from .execution.policy import source_identity
from .execution.store import executable_item


def record_result(
    conn: Conn, item_id: int, spec_fingerprint: str, status: TerminalStatus | str,
    *, reason: str = "", detail: str = "", source: SourceIdentity | None = None,
    actual_exit_code: int | None = None, stdout: str = "", stderr: str = "",
    duration_ms: int = 0,
    started_at: str | None = None, finished_at: str | None = None,
    expected: Any = None, actual: Any = None, hook_name: str | None = None,
    implementation_identity: str | None = None, actor: str | None = None,
) -> dict[str, Any]:
    try:
        terminal = TerminalStatus(status)
    except ValueError as exc:
        raise BacklogError("execution result must be pass, fail, error, or skipped") from exc
    if terminal == TerminalStatus.SKIPPED and reason not in {
        "policy_denied", "batch_budget_exhausted",
    }:
        raise BacklogError(
            "skipped execution results require reason=policy_denied "
            "or batch_budget_exhausted"
        )
    source = source or SourceIdentity(unavailable=True)
    if hook_name is not None and not isinstance(hook_name, str):
        raise BacklogError("hook_name must be a string")
    if implementation_identity is not None and not isinstance(implementation_identity, str):
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
        (item_id, spec_fingerprint, terminal.value, reason, detail,
         json.dumps(expected, sort_keys=True, separators=(",", ":"), ensure_ascii=False),
         json.dumps(actual, sort_keys=True, separators=(",", ":"), ensure_ascii=False),
         hook_name, implementation_identity, actual_exit_code, stdout, stderr,
         duration_ms, source.revision,
         source.dirty_fingerprint, 1 if source.unavailable else 0,
         (actor or "unknown").strip() or "unknown", started_at or now, finished_at or now),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM execution_result WHERE id = ?", (rid,)).fetchone()
    return {key: row[key] for key in row.keys()}


def _task_for_item(conn: Conn, project_id: int, item_id: int):
    row = conn.execute(
        "SELECT t.* FROM task t JOIN task_item i ON i.task_id=t.id "
        "WHERE t.project_id=? AND i.id=?", (project_id, item_id),
    ).fetchone()
    if row is None:
        raise BacklogError(f"no task item with id {item_id} in this project")
    return row


def item_state(conn: Conn, item_id: int, project_root: Path | None = None) -> str:
    """Return pending or the latest fresh terminal status."""
    executable = executable_item(conn, item_id)
    row = conn.execute(
        "SELECT * FROM execution_result WHERE item_id = ? AND spec_fingerprint = ? "
        "ORDER BY id DESC LIMIT 1", (item_id, executable["spec_fingerprint"]),
    ).fetchone()
    if row is None:
        return "pending"
    if project_root is not None and not _source_matches(row, source_identity(project_root)):
        return "pending"
    return row["status"]


def required_validations_pass(
    conn: Conn, task_id: int, project_root: Path | None = None,
) -> tuple[bool, list[int]]:
    rows = conn.execute(
        "SELECT e.item_id, e.spec_fingerprint FROM executable_item e "
        "JOIN task_item i ON i.id=e.item_id "
        "WHERE i.task_id=? AND e.requirement='required' ORDER BY e.item_id", (task_id,),
    ).fetchall()
    failed: list[int] = []
    for row in rows:
        latest = conn.execute(
            "SELECT * FROM execution_result WHERE item_id=? AND spec_fingerprint=? "
            "ORDER BY id DESC LIMIT 1", (row["item_id"], row["spec_fingerprint"]),
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
    conn: Conn, task_id: int, project_root: Path | None = None,
) -> tuple[bool, list[int]]:
    """Aggregate execution verdict; unlike workflow gates, waivers are not passes."""
    rows = conn.execute(
        "SELECT e.item_id FROM executable_item e JOIN task_item i ON i.id=e.item_id "
        "WHERE i.task_id=? AND e.requirement='required' ORDER BY e.item_id",
        (task_id,),
    ).fetchall()
    failed = [
        int(row["item_id"]) for row in rows
        if item_state(conn, int(row["item_id"]), project_root) != "pass"
    ]
    return not failed, failed


def execution_history(
    conn: Conn, item_id: int, *, limit: int = 20,
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
        value["stale"] = (
            value["spec_fingerprint"] != executable["spec_fingerprint"]
            or (
                current_source is not None
                and not _source_matches(row, current_source)
            )
        )
        history.append(value)
    return history


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


def validation_diagnostics(conn: Conn) -> list[dict[str, Any]]:
    """Skipped and active-waiver details for doctor."""
    rows = conn.execute(
        "SELECT e.item_id,e.executor,e.spec_fingerprint,i.content,t.key AS task_key "
        "FROM executable_item e JOIN task_item i ON i.id=e.item_id "
        "JOIN task t ON t.id=i.task_id ORDER BY t.key,e.item_id"
    ).fetchall()
    diagnostics: list[dict[str, Any]] = []
    for row in rows:
        latest = conn.execute(
            "SELECT * FROM execution_result WHERE item_id=? AND spec_fingerprint=? "
            "ORDER BY id DESC LIMIT 1",
            (row["item_id"], row["spec_fingerprint"]),
        ).fetchone()
        skipped = conn.execute(
            "SELECT s.* FROM execution_result s WHERE s.item_id=? "
            "AND s.spec_fingerprint=? AND s.status='skipped' "
            "AND NOT EXISTS (SELECT 1 FROM execution_result p "
            "  WHERE p.item_id=s.item_id AND p.spec_fingerprint=s.spec_fingerprint "
            "  AND p.status='pass' AND p.id>s.id) "
            "ORDER BY s.id DESC LIMIT 1",
            (row["item_id"], row["spec_fingerprint"]),
        ).fetchone()
        waiver = current_waiver(conn, int(row["item_id"]))
        if skipped is not None:
            prior = conn.execute(
                "SELECT status FROM execution_result WHERE item_id=? "
                "AND spec_fingerprint=? AND id<? ORDER BY id DESC LIMIT 1",
                (row["item_id"], row["spec_fingerprint"], skipped["id"]),
            ).fetchone()
            diagnostics.append({
                "kind": "skipped", "task": row["task_key"],
                "item_id": int(row["item_id"]), "item": row["content"],
                "executor": row["executor"], "actor": skipped["actor"],
                "reason": skipped["reason"],
                "prior_result": prior["status"] if prior else "pending",
                "timestamp": skipped["finished_at"],
            })
        if waiver is not None:
            diagnostics.append({
                "kind": "waived", "task": row["task_key"],
                "item_id": int(row["item_id"]), "item": row["content"],
                "executor": row["executor"], "actor": waiver["actor"],
                "reason": waiver["reason"],
                "prior_result": latest["status"] if latest else "pending",
                "timestamp": waiver["created_at"],
            })
    return diagnostics


def _after_pass(backlog, item_id: int, actor: str) -> None:
    now = utcnow()
    backlog._conn.execute(
        "UPDATE validation_waiver SET superseded_at=? "
        "WHERE item_id=? AND superseded_at IS NULL",
        (now, item_id),
    )
    row = backlog._conn.execute(
        "SELECT i.kind,i.done,e.requirement FROM task_item i "
        "JOIN executable_item e ON e.item_id=i.id WHERE i.id=?",
        (item_id,),
    ).fetchone()
    if (
        row is not None and row["kind"] == "checklist"
        and row["requirement"] == Requirement.REQUIRED.value and not row["done"]
    ):
        task = _task_for_item(backlog._conn, backlog.pid, item_id)
        backlog._conn.execute(
            "UPDATE task_item SET done=1,updated_at=? WHERE id=?", (now, item_id)
        )
        log_event(
            backlog._conn, "item", backlog.pid, task["id"], task["key"], actor,
            to_value="done", detail=f"validation pass automatically checked item #{item_id}",
        )
    backlog._conn.commit()


def _source_matches(result: Mapping[str, Any], current: SourceIdentity) -> bool:
    if current.unavailable or result["source_revision_unavailable"]:
        return True
    return (
        result["source_revision"] == current.revision
        and result["source_dirty_fingerprint"] == current.dirty_fingerprint
    )


def _decode_json(value: str | None) -> Any:
    return json.loads(value) if value is not None else None


def source_revision_unavailable_items(conn: Conn) -> list[int]:
    """Items whose latest fresh attempt still lacks source identity."""
    rows = conn.execute(
        "SELECT e.item_id FROM executable_item e "
        "JOIN execution_result r ON r.id = ("
        "  SELECT r2.id FROM execution_result r2 "
        "  WHERE r2.item_id=e.item_id AND r2.spec_fingerprint=e.spec_fingerprint "
        "  ORDER BY r2.id DESC LIMIT 1"
        ") WHERE r.source_revision_unavailable=1 ORDER BY e.item_id"
    ).fetchall()
    return [int(row["item_id"]) for row in rows]

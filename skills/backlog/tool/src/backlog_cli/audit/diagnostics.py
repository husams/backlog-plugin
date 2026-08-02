"""Validation diagnostics and post-pass workflow updates."""

from __future__ import annotations

from typing import Any

from ..db import Conn, log_event, utcnow
from ..execution.contracts import Requirement
from .common import _task_for_item
from .waivers import current_waiver


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
            diagnostics.append(
                {
                    "kind": "skipped",
                    "task": row["task_key"],
                    "item_id": int(row["item_id"]),
                    "item": row["content"],
                    "executor": row["executor"],
                    "actor": skipped["actor"],
                    "reason": skipped["reason"],
                    "prior_result": prior["status"] if prior else "pending",
                    "timestamp": skipped["finished_at"],
                }
            )
        if waiver is not None:
            diagnostics.append(
                {
                    "kind": "waived",
                    "task": row["task_key"],
                    "item_id": int(row["item_id"]),
                    "item": row["content"],
                    "executor": row["executor"],
                    "actor": waiver["actor"],
                    "reason": waiver["reason"],
                    "prior_result": latest["status"] if latest else "pending",
                    "timestamp": waiver["created_at"],
                }
            )
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
        row is not None
        and row["kind"] == "checklist"
        and row["requirement"] == Requirement.REQUIRED.value
        and not row["done"]
    ):
        task = _task_for_item(backlog._conn, backlog.pid, item_id)
        backlog._conn.execute(
            "UPDATE task_item SET done=1,updated_at=? WHERE id=?", (now, item_id)
        )
        log_event(
            backlog._conn,
            "item",
            backlog.pid,
            task["id"],
            task["key"],
            actor,
            to_value="done",
            detail=f"validation pass automatically checked item #{item_id}",
        )
    backlog._conn.commit()


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

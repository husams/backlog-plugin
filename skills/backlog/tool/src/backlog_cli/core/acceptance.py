"""Reviewer verdicts on acceptance criteria.

A criterion is proven by review, not by an implementer's tick, so it is never
tickable through `tick_item`. What a reviewer records instead is an attributed
verdict carrying the evidence they judged it on. The verdict is bound to the
criterion text it was given for: editing the criterion makes the verdict stale,
which counts as unverified until somebody reviews the new wording.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from typing import Any

from ..db import BacklogError, Conn, Row, log_event, utcnow
from .normalization import require_actor
from .task_queries import get_task

# Enough evidence to be an observation rather than a rubber stamp. "ok" and
# "lgtm" are exactly what this gate exists to reject.
MIN_EVIDENCE_LENGTH = 10


def content_hash(content: str) -> str:
    """The criterion text a verdict was recorded against."""
    return "sha256:" + hashlib.sha256(content.encode("utf-8")).hexdigest()


def _view(row: Mapping[str, Any]) -> dict[str, Any]:
    """One criterion with its verdict reduced to a single reportable state."""
    verdict = row["verdict_state"]
    stale = bool(verdict) and row["content_hash"] != content_hash(row["content"])
    return {
        "id": int(row["id"]),
        "task_key": row["task_key"],
        "position": int(row["position"]),
        "content": row["content"],
        "state": "unverified" if (not verdict or stale) else verdict,
        "verdict_by": row["verdict_by"],
        "verdict_at": row["verdict_at"],
        "evidence": row["evidence"],
        "stale": stale,
    }


def _rows(conn: Conn, task_id: int) -> list[Row]:
    return conn.execute(
        "SELECT i.id, i.position, i.content, t.key AS task_key, "
        "v.state AS verdict_state, v.actor AS verdict_by, "
        "v.created_at AS verdict_at, v.evidence, v.content_hash "
        "FROM task_item i JOIN task t ON t.id=i.task_id "
        "LEFT JOIN acceptance_verdict v ON v.item_id=i.id "
        "WHERE i.task_id=? AND i.kind='acceptance_criteria' ORDER BY i.position,i.id",
        (task_id,),
    ).fetchall()


def _criterion(conn: Conn, project_id: int, item_id: int) -> Row:
    row = conn.execute(
        "SELECT i.*, t.key AS task_key, t.created_by AS task_created_by, "
        "t.assignee AS task_assignee FROM task_item i JOIN task t ON t.id=i.task_id "
        "WHERE i.id=? AND t.project_id=?",
        (item_id, project_id),
    ).fetchone()
    if row is None:
        raise BacklogError(f"no task item with id {item_id} in this project")
    if row["kind"] != "acceptance_criteria":
        raise BacklogError(
            f"task item #{item_id} is a {row['kind']} item, not an acceptance criterion"
        )
    return row


def list_criteria(conn: Conn, project_id: int, key: str) -> list[dict[str, Any]]:
    task = get_task(conn, project_id, key)
    return [_view(row) for row in _rows(conn, task["id"])]


def criteria_state(conn: Conn, task_id: int) -> list[dict[str, Any]]:
    """The same view as `list_criteria`, for a task already resolved by id."""
    return [_view(row) for row in _rows(conn, task_id)]


def record_verdict(
    conn: Conn,
    project_id: int,
    item_id: int,
    *,
    met: bool,
    evidence: str,
    actor: str | None,
) -> dict[str, Any]:
    """Record one reviewer's verdict on one acceptance criterion."""
    state = "met" if met else "unmet"
    identity = require_actor(actor, f"recording a {state} acceptance verdict")
    if not isinstance(evidence, str) or len(evidence.strip()) < MIN_EVIDENCE_LENGTH:
        raise BacklogError(
            f"a {state} verdict requires evidence of at least "
            f"{MIN_EVIDENCE_LENGTH} characters describing how the criterion was "
            "checked; 'ok' is not evidence"
        )
    row = _criterion(conn, project_id, item_id)
    _require_independent_verifier(row, identity)

    previous = conn.execute(
        "SELECT state FROM acceptance_verdict WHERE item_id=?", (item_id,)
    ).fetchone()
    ts = utcnow()
    conn.execute(
        "INSERT INTO acceptance_verdict(item_id,state,actor,evidence,content_hash,"
        "created_at) VALUES(?,?,?,?,?,?) ON CONFLICT(item_id) DO UPDATE SET "
        "state=excluded.state, actor=excluded.actor, evidence=excluded.evidence, "
        "content_hash=excluded.content_hash, created_at=excluded.created_at",
        (
            item_id,
            state,
            identity,
            evidence.strip(),
            content_hash(row["content"]),
            ts,
        ),
    )
    log_event(
        conn,
        f"criterion.{state}",
        project_id,
        row["task_id"],
        row["task_key"],
        identity,
        from_value=previous["state"] if previous is not None else None,
        to_value=state,
        detail=f"#{item_id} {evidence.strip()[:120]}",
    )
    conn.commit()
    return next(
        view for view in criteria_state(conn, row["task_id"]) if view["id"] == item_id
    )


def _require_independent_verifier(row: Row, identity: str) -> None:
    """A criterion cannot be signed off by the people who produced the work.

    Mirrors `require_independent_actor`: a NULL attribution is tolerated only
    because historical rows cannot be backfilled, and the comparison is
    case-insensitive so `Claude` and `claude` are the same worker. Independence
    means "not the producer", so it never depends on a reviewer having been
    assigned -- that is not an invariant anywhere else in the flow.
    """
    for label, name in (
        ("implemented", row["task_assignee"]),
        ("created", row["task_created_by"]),
    ):
        if name and identity.casefold() == name.strip().casefold():
            raise BacklogError(
                f"{identity} {label} {row['task_key']} and cannot verify its "
                "acceptance criteria; use an independent reviewer"
            )


def clear_verdicts(
    conn: Conn,
    project_id: int,
    key: str,
    *,
    reason: str,
    actor: str | None,
) -> int:
    """Drop every verdict on a task, so the next review starts from scratch."""
    identity = require_actor(actor, "clearing acceptance verdicts")
    if not isinstance(reason, str) or not reason.strip():
        raise BacklogError("clearing acceptance verdicts requires a non-empty reason")
    task = get_task(conn, project_id, key)
    cleared = discard_verdicts(
        conn, project_id, task, actor=identity, reason=reason.strip()
    )
    conn.commit()
    return cleared


def discard_verdicts(
    conn: Conn,
    project_id: int,
    task: Row,
    *,
    actor: str | None,
    reason: str,
) -> int:
    """Uncommitted verdict invalidation, for callers already inside a change.

    Unlike `clear_verdicts` this does not demand an actor: the workflow itself
    invalidates verdicts when work is sent back, and that happens on behalf of
    whoever made the move, attributed or not.
    """
    rows = conn.execute(
        "SELECT v.item_id FROM acceptance_verdict v JOIN task_item i ON i.id=v.item_id "
        "WHERE i.task_id=?",
        (task["id"],),
    ).fetchall()
    if not rows:
        return 0
    conn.execute(
        "DELETE FROM acceptance_verdict WHERE item_id IN "
        "(SELECT id FROM task_item WHERE task_id=?)",
        (task["id"],),
    )
    log_event(
        conn,
        "criterion.cleared",
        project_id,
        task["id"],
        task["key"],
        actor,
        to_value="unverified",
        detail=f"{len(rows)} verdict(s) cleared: {reason}"[:120],
    )
    return len(rows)

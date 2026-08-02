"""Dependency edges between tasks.

An edge is stored once, in the canonical direction `from <kind> to`:

    blocks      the source must finish before the target may start (gate-enforced)
    relates     soft association, no ordering
    duplicates  the source is a duplicate of the target

Both ends are `task.id`, so a feature blocking a story is the same row shape as
a subtask blocking a subtask — no union across tables, and the database keeps
the endpoints honest.
"""

from __future__ import annotations

from ..db import BacklogError, Conn, Row, log_event, utcnow
from ..schema import (
    DEPENDENCY_KIND_ALIASES,
    DEPENDENCY_KINDS,
    HARD_DEPENDENCY_KINDS,
    SATISFIED_STATUSES,
    SYMMETRIC_DEPENDENCY_KINDS,
)

# Resolves the task on the far side of an edge in the same query.
OTHER = (
    "t.key AS other_key, t.title AS other_title, t.status AS other_status, "
    "t.task_type AS other_type, t.priority AS other_priority, t.id AS other_id"
)


def normalize_kind(value: str) -> str:
    slug = value.strip().lower().replace("-", "_").replace(" ", "_")
    slug = DEPENDENCY_KIND_ALIASES.get(slug, slug)
    if slug not in DEPENDENCY_KINDS:
        raise BacklogError(
            f"unknown dependency kind {value!r}. Valid: {', '.join(DEPENDENCY_KINDS)}"
        )
    return slug


def is_satisfied(status: str | None, conn: "Conn | None" = None,
                 project_id: int | None = None, task_type: str | None = None) -> bool:
    """Has the task progressed far enough to stop blocking its dependents?

    The answer belongs to the project's workflow (`satisfies_dependency`), so a
    project that adds its own terminal status is understood without a code
    change; the built-in set is only the fallback.
    """
    if conn is not None and project_id is not None and task_type:
        from .. import workflow

        return workflow.get(conn, project_id, task_type).satisfies(status or "")
    return status in SATISFIED_STATUSES


def canonical(a: int, b: int, kind: str) -> tuple[int, int]:
    """`relates` has no direction, so store one row per pair."""
    if kind in SYMMETRIC_DEPENDENCY_KINDS and a > b:
        return b, a
    return a, b


# --------------------------------------------------------------------------- #

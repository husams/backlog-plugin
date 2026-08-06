"""Gate evaluation for concrete moves and public targets."""

from __future__ import annotations

from ..db import Conn
from .checks import Check, GATE_TARGET_CHECKS, normalize_gate, run_checks
from .task_queries import get_task


def gate(
    conn: Conn,
    project_id: int,
    key: str,
    target: str,
    allow_open_children: bool = False,
    no_pr: bool = False,
    allow_blocked: bool = False,
) -> tuple[bool, list[Check]]:
    """Evaluate the gate for `start`, `in_review`, `accepted`, `done` or `merge`.

    A target names a set of checks; what each check *means* lives in
    `run_checks`, which is also what a workflow transition evaluates. Both
    routes therefore ask the same code the same question — including the parts
    that depend on the task type, such as a feature carrying no pull request of
    its own.
    """
    task = get_task(conn, project_id, key)
    checks = run_checks(
        conn,
        project_id,
        task,
        GATE_TARGET_CHECKS[normalize_gate(target)],
        allow_open_children=allow_open_children,
        no_pr=no_pr,
        allow_blocked=allow_blocked,
    )
    return all(c.ok for c in checks), checks

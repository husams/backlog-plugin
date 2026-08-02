"""Thin public retrospective API passthroughs."""

from __future__ import annotations

from ..db import BacklogError
from .model import RetrospectiveStatus
from .store import (
    accept_action,
    close_action,
    create_action,
    get_action,
    list_actions,
    reject_action,
)

from ..types import RetrospectiveAction


def create_retrospective_action(
    self,
    *,
    iteration: str,
    repeated_issue: str,
    proposed_solution: str,
    title: str | None = None,
) -> RetrospectiveAction:
    """Record a proposed workflow improvement from an Iteration."""
    return RetrospectiveAction(
        create_action(
            self._conn,
            self.pid,
            iteration=iteration,
            repeated_issue=repeated_issue,
            proposed_solution=proposed_solution,
            title=title,
            actor=self.actor,
        )
    )


def retrospective_action(self, key: str) -> RetrospectiveAction:
    """One retrospective action by project-local key."""
    return RetrospectiveAction(get_action(self._conn, self.pid, key))


def retrospective_actions(
    self,
    *,
    status: RetrospectiveStatus | None = None,
    iteration: str | None = None,
) -> list[RetrospectiveAction]:
    """Retrospective actions filtered by lifecycle state or Iteration."""
    if status is not None and not isinstance(status, RetrospectiveStatus):
        raise TypeError("status must be a RetrospectiveStatus")
    rows = list_actions(
        self._conn,
        self.pid,
        status=status.value if status is not None else None,
        iteration=iteration,
    )
    return [RetrospectiveAction(row) for row in rows]


def accept_retrospective_action(self, key: str) -> RetrospectiveAction:
    """Accept a Created action and make it Ready for implementation."""
    return RetrospectiveAction(
        accept_action(self._conn, self.pid, key, actor=self.actor)
    )


def reject_retrospective_action(self, key: str, *, reason: str) -> RetrospectiveAction:
    """Reject a Created or Ready action, retaining the required reason."""
    return RetrospectiveAction(
        reject_action(self._conn, self.pid, key, reason=reason, actor=self.actor)
    )


def close_retrospective_action(
    self,
    key: str,
    *,
    resolution_project: str,
    feature: str | None = None,
    bug: str | None = None,
) -> RetrospectiveAction:
    """Close a Ready action against one Feature or Bug in any project."""
    references = [reference for reference in (feature, bug) if reference is not None]
    if len(references) != 1:
        raise BacklogError("pass exactly one of feature or bug")
    action = close_action(
        self._conn,
        self.pid,
        key,
        resolution_project=resolution_project,
        resolution_task=references[0],
        expected_task_type="feature" if feature is not None else "bug",
        actor=self.actor,
    )
    return RetrospectiveAction(action)

"""Retrospective-action value objects and lifecycle APIs."""

from __future__ import annotations

from .. import retrospective
from ..db import BacklogError
from ..retrospective import RetrospectiveStatus
from .common import _age_days


class RetrospectiveAction:
    """One workflow improvement discovered during an Iteration."""

    __slots__ = ("_row",)

    def __init__(self, row):
        self._row = row

    def __getattr__(self, name):
        try:
            return self._row[name]
        except (IndexError, KeyError):
            raise AttributeError(name) from None

    def __getitem__(self, name):
        return self._row[name]

    def __repr__(self) -> str:
        return f"<RetrospectiveAction {self._row['key']} {self._row['status']}>"

    def __str__(self) -> str:
        return (
            f"{self._row['key']}  {self._row['status']:<8} "
            f"{self._row['title']}  [{self._row['iteration_key']}]"
        )

    @property
    def age_days(self) -> float:
        return _age_days(self._row["created_at"])

    @property
    def idle_days(self) -> float:
        return _age_days(self._row["updated_at"])

    @property
    def required_decision(self) -> str | None:
        return retrospective.required_decision(self._row["status"])

    @property
    def is_open(self) -> bool:
        return self._row["closed_at"] is None


class RetrospectiveApi:
    __slots__ = ()

    def create_retrospective_action(
        self,
        *,
        iteration: str,
        repeated_issue: str,
        proposed_solution: str,
        title: str | None = None,
    ) -> RetrospectiveAction:
        """Record a proposed workflow improvement from an Iteration."""
        return RetrospectiveAction(retrospective.create_action(
            self._conn,
            self.pid,
            iteration=iteration,
            repeated_issue=repeated_issue,
            proposed_solution=proposed_solution,
            title=title,
            actor=self.actor,
        ))

    def retrospective_action(self, key: str) -> RetrospectiveAction:
        """One retrospective action by project-local key."""
        return RetrospectiveAction(
            retrospective.get_action(self._conn, self.pid, key)
        )

    def retrospective_actions(
        self,
        *,
        status: RetrospectiveStatus | None = None,
        iteration: str | None = None,
    ) -> list[RetrospectiveAction]:
        """Retrospective actions filtered by lifecycle state or Iteration."""
        if status is not None and not isinstance(status, RetrospectiveStatus):
            raise TypeError("status must be a RetrospectiveStatus")
        rows = retrospective.list_actions(
            self._conn,
            self.pid,
            status=status.value if status is not None else None,
            iteration=iteration,
        )
        return [RetrospectiveAction(row) for row in rows]

    def accept_retrospective_action(self, key: str) -> RetrospectiveAction:
        """Accept a Created action and make it Ready for implementation."""
        return RetrospectiveAction(retrospective.accept_action(
            self._conn, self.pid, key, actor=self.actor
        ))

    def reject_retrospective_action(
        self, key: str, *, reason: str
    ) -> RetrospectiveAction:
        """Reject a Created or Ready action, retaining the required reason."""
        return RetrospectiveAction(retrospective.reject_action(
            self._conn, self.pid, key, reason=reason, actor=self.actor
        ))

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
        action = retrospective.close_action(
            self._conn,
            self.pid,
            key,
            resolution_project=resolution_project,
            resolution_task=references[0],
            expected_task_type="feature" if feature is not None else "bug",
            actor=self.actor,
        )
        return RetrospectiveAction(action)

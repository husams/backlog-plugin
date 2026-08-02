"""Workflow actions, gates, transitions, and pull-request APIs."""

from __future__ import annotations

from dataclasses import dataclass

from .. import core, hooks
from ..db import BacklogError, require_backlog_dir
from ..hooks import Action
from .tasks import Task


@dataclass(frozen=True)
class Gate:
    """The verdict of a gate, already reduced to something printable."""

    key: str
    target: str
    ok: bool
    checks: list[tuple[str, bool, str]]

    @property
    def failures(self) -> list[str]:
        return [f"{name}: {detail}" for name, passed, detail in self.checks if not passed]

    def __str__(self) -> str:
        if self.ok:
            return f"{self.key}  READY    all {self.target} gates pass"
        return f"{self.key}  BLOCKED  " + "; ".join(self.failures)


class WorkflowApi:
    __slots__ = ()

    def actions(self, key: str) -> list[Action]:
        """Semantic actions configured for the task's current state."""
        task = self.task(key)
        config_dir = hooks.project_backlog_dir(require_backlog_dir())
        return hooks.available_actions(
            config_dir, task.task_type, task.status
        )

    def can(self, key: str, target: str = "merge", **waivers) -> Gate:
        """Evaluate a gate without moving anything.

        `target` is one of start / in_review / accepted / done / merge.
        Waivers mirror the CLI flags: allow_blocked, no_pr, allow_open_children.
        """
        ok, checks = core.gate(self._conn, self.pid, key, target, **waivers)
        return Gate(core.normalize_key(key), core.normalize_gate(target), ok,
                    [(c.name, c.ok, c.detail) for c in checks])

    def trigger(self, key: str, action: Action, *,
                actor: str | None = None, operation: str = "api.trigger",
                parameters: dict | None = None, **waivers) -> Task:
        """Submit an action; workflow configuration selects the destination."""
        if not isinstance(action, Action):
            raise TypeError("action must be an Action")
        if action in hooks.THREAD_MANAGED_ACTIONS:
            raise BacklogError(
                f"{action.value} is managed by review threads; use review_open, "
                "review_reply, or review_reopen"
            )
        row, _, _ = core.trigger_action(
            self._conn,
            self.pid,
            key,
            action,
            actor=actor or self.actor,
            operation=operation,
            parameters=parameters,
            **waivers,
        )
        return self._task(row)

    def set_pr(self, key: str, *, url: str | None = None,
               number: int | None = None, repo: str | None = None,
               state: str | None = None, review_state: str | None = None,
               actor: str | None = None) -> Task:
        """Record PR state and emit the corresponding standard PR action."""
        row = core.set_pr(
            self._conn,
            self.pid,
            key,
            url=url,
            number=number,
            repo=repo,
            state=state,
            review_state=review_state,
            actor=actor or self.actor,
        )
        return self._task(row)

"""Gate and mutation API methods."""

from .. import hooks
from ..db import BacklogError
from ..hooks import Action
from ..types import Gate, Task
from .checks import normalize_gate
from .gates import gate
from .normalization import normalize_key
from .pull_requests import set_pr as update_pr
from .transitions import trigger_action
from .tasks import assign as assign_task


def can(self, key: str, target: str = "merge", **waivers) -> Gate:
    """Evaluate a gate without moving anything.

    `target` is one of start / in_review / accepted / done / merge.
    Waivers mirror the CLI flags: allow_blocked, no_pr, allow_open_children.
    """
    ok, checks = gate(self._conn, self.pid, key, target, **waivers)
    return Gate(
        normalize_key(key),
        normalize_gate(target),
        ok,
        [(c.name, c.ok, c.detail) for c in checks],
    )


def trigger(
    self,
    key: str,
    action: Action,
    *,
    actor: str | None = None,
    operation: str = "api.trigger",
    parameters: dict | None = None,
    **waivers,
) -> Task:
    """Submit an action; workflow configuration selects the destination."""
    if not isinstance(action, Action):
        raise TypeError("action must be an Action")
    if action in hooks.THREAD_MANAGED_ACTIONS:
        raise BacklogError(
            f"{action.value} is managed by review threads; use review_open, "
            "review_reply, or review_reopen"
        )
    row, _, _ = trigger_action(
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


def set_pr(
    self,
    key: str,
    *,
    url: str | None = None,
    number: int | None = None,
    repo: str | None = None,
    state: str | None = None,
    review_state: str | None = None,
    actor: str | None = None,
) -> Task:
    """Record PR state and emit the corresponding standard PR action."""
    row = update_pr(
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


def assign(
    self,
    key: str,
    to: str | None = None,
    reviewer: str | None = None,
    actor: str | None = None,
) -> Task:
    row = assign_task(
        self._conn,
        self.pid,
        key,
        to=to,
        reviewer=reviewer,
        actor=actor or self.actor,
    )
    self._conn.commit()
    return self._task(row)

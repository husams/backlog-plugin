"""Action-driven transitions and optional project transition hooks."""

from __future__ import annotations

import hashlib
import importlib.util
import sys
from enum import Enum
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING, Any

import yaml

from ..db import BacklogError, Conn, utcnow
from ..schema import GATE_CHECKS, STATUS_CATEGORIES, TASK_TYPES

if TYPE_CHECKING:
    from .api import Backlog

Trigger = dict[str, Any]


class Action(str, Enum):
    ITEM_CREATED = "item.created"
    ITEM_UPDATED = "item.updated"
    ITEM_CANCELLED = "item.cancelled"
    ITEM_REOPENED = "item.reopened"
    ITEM_ARCHIVED = "item.archived"

    REFINEMENT_SUBMITTED = "refinement.submitted"
    REFINEMENT_MARKED_INCOMPLETE = "refinement.marked_incomplete"
    REFINEMENT_ACCEPTED = "refinement.accepted"

    WORK_STARTED = "work.started"
    WORK_PAUSED = "work.paused"
    WORK_RESUMED = "work.resumed"
    WORK_BLOCKED = "work.blocked"
    WORK_UNBLOCKED = "work.unblocked"
    WORK_COMPLETED = "work.completed"

    REVIEW_SUBMITTED = "review.submitted"
    REVIEW_APPROVED = "review.approved"
    REVIEW_CHANGES_REQUESTED = "review.changes_requested"
    REVIEW_DISMISSED = "review.dismissed"

    FEEDBACK_POSTED = "feedback.posted"
    FEEDBACK_ACCEPTED = "feedback.accepted"
    FEEDBACK_REJECTED = "feedback.rejected"
    FEEDBACK_REPLIED = "feedback.replied"
    FEEDBACK_RESOLVED = "feedback.resolved"
    FEEDBACK_REOPENED = "feedback.reopened"

    PR_CREATED = "pr.created"
    PR_UPDATED = "pr.updated"
    PR_MARKED_READY = "pr.marked_ready"
    PR_APPROVED = "pr.approved"
    PR_CHANGES_REQUESTED = "pr.changes_requested"
    PR_MERGED = "pr.merged"
    PR_CLOSED = "pr.closed"
    PR_REOPENED = "pr.reopened"

    CHECK_STARTED = "check.started"
    CHECK_PASSED = "check.passed"
    CHECK_FAILED = "check.failed"
    CHECK_CANCELLED = "check.cancelled"
    CHECK_TIMED_OUT = "check.timed_out"

    DELIVERY_ACCEPTED = "delivery.accepted"
    DELIVERY_REJECTED = "delivery.rejected"
    DELIVERY_RELEASED = "delivery.released"
    ITERATION_OPENED = "iteration.opened"
    ITERATION_CLOSED = "iteration.closed"
    ITERATION_REOPENED = "iteration.reopened"


THREAD_MANAGED_ACTIONS = frozenset({
    Action.FEEDBACK_POSTED,
    Action.FEEDBACK_ACCEPTED,
    Action.FEEDBACK_REJECTED,
    Action.FEEDBACK_REPLIED,
    Action.FEEDBACK_RESOLVED,
    Action.FEEDBACK_REOPENED,
})


def public_actions() -> list[Action]:
    """Actions callers may submit directly; review actions come from thread APIs."""
    return [action for action in Action if action not in THREAD_MANAGED_ACTIONS]


def normalize_action(value: Action | str) -> Action:
    if isinstance(value, Action):
        return value
    text = str(value).strip()
    try:
        return Action(text.lower())
    except ValueError:
        try:
            return Action[text.upper().replace(".", "_").replace("-", "_")]
        except KeyError:
            raise BacklogError(
                f"unknown action {value!r}. Valid: "
                + ", ".join(action.value for action in Action)
            ) from None

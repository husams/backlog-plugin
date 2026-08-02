"""Retrospective improvement actions and their fixed lifecycle."""

from __future__ import annotations

from enum import Enum

from ..db import BacklogError


class RetrospectiveStatus(str, Enum):
    CREATED = "created"
    READY = "ready"
    DONE = "done"
    REJECTED = "rejected"


STATUSES = tuple(status.value for status in RetrospectiveStatus)
STATUS_DISPLAY = {
    "created": "Created",
    "ready": "Ready",
    "done": "Done",
    "rejected": "Rejected",
}
OPEN_STATUSES = {"created", "ready"}
REQUIRED_DECISIONS = {
    "created": "accept_or_reject",
    "ready": "close_or_reject",
}


def required_decision(status: str) -> str | None:
    """The decision that advances an open retrospective action."""
    return REQUIRED_DECISIONS.get(normalize_status(status))


def normalize_status(value: str) -> str:
    status = value.strip().lower().replace("-", "_").replace(" ", "_")
    if status == "reject":
        status = "rejected"
    if status not in STATUSES:
        raise BacklogError(
            f"unknown retrospective status {value!r}. Valid: "
            + ", ".join(STATUS_DISPLAY[s] for s in STATUSES)
        )
    return status


def _required(value: str, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise BacklogError(f"{label} must be a non-empty string")
    return value.strip()

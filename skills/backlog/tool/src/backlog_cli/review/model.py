"""Review severity, participant roles, and thread state rules."""

from __future__ import annotations

from ..db import BacklogError, Row
from ..schema import REVIEW_ROLES, REVIEW_SEVERITIES, ReviewSeverity

_THREAD_TRANSITIONS: dict[tuple[str, str, str], tuple[str, str | None]] = {
    ("awaiting_developer", "developer", "comment"): ("awaiting_reviewer", None),
    ("awaiting_developer", "developer", "fix"): ("awaiting_reviewer", None),
    ("awaiting_developer", "developer", "reject"): ("awaiting_reviewer", None),
    ("awaiting_reviewer", "reviewer", "reject"): ("awaiting_developer", None),
    ("awaiting_reviewer", "reviewer", "accept"): ("closed", "accepted_by_reviewer"),
}


def normalize_severity(value: ReviewSeverity | str) -> ReviewSeverity:
    if isinstance(value, ReviewSeverity):
        return value
    return ReviewSeverity(str(value).strip().lower())


def resolve_role(task: Row, author: str, role: str | None) -> str:
    requested = role.strip().lower() if role and role != "auto" else None
    if requested and requested not in REVIEW_ROLES:
        raise BacklogError(f"role must be one of {', '.join(REVIEW_ROLES)}")
    if task["reviewer"] and author.casefold() == task["reviewer"].strip().casefold():
        if requested == "developer":
            raise BacklogError("the assigned reviewer cannot act as developer")
        return "reviewer"
    if task["assignee"] and author.casefold() == task["assignee"].strip().casefold():
        if requested == "reviewer":
            raise BacklogError("the assigned implementer cannot act as reviewer")
        return "developer"
    if requested == "reviewer":
        raise BacklogError("only the assigned reviewer may open a review thread")
    if requested == "developer":
        raise BacklogError("only the assigned implementer may act as developer")
    if not task["reviewer"]:
        raise BacklogError(
            f"{task['key']} has no assigned reviewer; assign one before opening a review thread"
        )
    raise BacklogError(
        f"only the assigned reviewer or implementer may act on {task['key']} "
        f"(assignee={task['assignee'] or '-'}, reviewer={task['reviewer'] or '-'}). "
        "Assign both roles before opening or replying to review."
    )


def resolve_reply_role(
    thread: Row, task: Row, author: str, role: str | None, action: str | None = None
) -> str:
    """Keep the opening reviewer fixed and infer every other responder as a
    developer. Reply callers never need to repeat task assignment metadata."""
    fixed_reviewer = thread["opened_by"]
    requested = role.strip().lower() if role and role != "auto" else None
    if requested and requested not in REVIEW_ROLES:
        raise BacklogError(f"role must be one of {', '.join(REVIEW_ROLES)}")
    if author.casefold() == fixed_reviewer.strip().casefold():
        if requested == "developer":
            raise BacklogError(
                f"{author!r} opened thread {thread['root_key']} as reviewer "
                "and cannot reply as developer"
            )
        return "reviewer"
    if requested == "reviewer":
        raise BacklogError(
            f"thread {thread['root_key']} reviewer is {fixed_reviewer!r}; "
            f"{author!r} cannot replace them"
        )
    if not task["assignee"] or author.casefold() != task["assignee"].strip().casefold():
        raise BacklogError(
            f"thread {thread['root_key']} does not allow developer action "
            f"{action!r}; {author!r} is not the assigned implementer for {task['key']}; "
            "a third actor cannot reply as developer"
        )
    return "developer"


def _require_body(body: str) -> str:
    if not body or not body.strip():
        raise BacklogError("review comments require a non-empty body")
    return body


def _ball_after(role: str) -> str:
    return "awaiting_developer" if role == "reviewer" else "awaiting_reviewer"

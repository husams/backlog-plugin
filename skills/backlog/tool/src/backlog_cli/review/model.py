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
    if role and role != "auto":
        role = role.strip().lower()
        if role not in REVIEW_ROLES:
            raise BacklogError(f"role must be one of {', '.join(REVIEW_ROLES)}")
        return role
    if task["reviewer"] and author == task["reviewer"]:
        return "reviewer"
    if task["assignee"] and author == task["assignee"]:
        return "developer"
    if not task["reviewer"] and author != task["assignee"]:
        return "reviewer"
    raise BacklogError(
        f"cannot infer role for author {author!r} on {task['key']} "
        f"(assignee={task['assignee'] or '-'}, reviewer={task['reviewer'] or '-'}). "
        'Pass role="reviewer" or role="developer" in Python '
        "(--role reviewer|developer on the CLI)."
    )


def resolve_reply_role(thread: Row, author: str, role: str | None) -> str:
    """Keep the opening reviewer fixed and infer every other responder as a
    developer. Reply callers never need to repeat task assignment metadata."""
    fixed_reviewer = thread["opened_by"]
    requested = role.strip().lower() if role and role != "auto" else None
    if requested and requested not in REVIEW_ROLES:
        raise BacklogError(f"role must be one of {', '.join(REVIEW_ROLES)}")
    if author == fixed_reviewer:
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
    return "developer"


def _require_body(body: str) -> str:
    if not body or not body.strip():
        raise BacklogError("review comments require a non-empty body")
    return body


def _ball_after(role: str) -> str:
    return "awaiting_developer" if role == "reviewer" else "awaiting_reviewer"

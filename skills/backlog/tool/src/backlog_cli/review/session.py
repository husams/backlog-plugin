"""Thin public review API passthroughs."""

from __future__ import annotations

from ..db import BacklogError
from ..schema import ReviewSeverity
from .operations import audit, open_thread, set_severity
from .queries import comment_updates, inbox as query_inbox, list_threads
from .replies import reopen, reply

from ..types import ReviewComment, Thread, _review_comment, _thread


def inbox(
    self,
    actor: str | None = None,
    role: str | None = None,
    severity: ReviewSeverity | None = None,
) -> list[Thread]:
    """Review threads waiting on `actor`, oldest first."""
    if severity is not None and not isinstance(severity, ReviewSeverity):
        raise TypeError("severity must be a ReviewSeverity")
    out = [
        _thread(thread)
        for thread in query_inbox(
            self._conn, self.pid, actor=actor, role=role, severity=severity
        )
    ]
    return sorted(out, key=lambda t: -t.age_days)


def threads(
    self, key: str, state: str = "open", severity: ReviewSeverity | None = None
) -> list[Thread]:
    """Review threads on one task."""
    if severity is not None and not isinstance(severity, ReviewSeverity):
        raise TypeError("severity must be a ReviewSeverity")
    return [
        _thread(t)
        for t in list_threads(self._conn, self.pid, key, state, severity=severity)
    ]


def review_updates(self, root: str, *, after: str | None = None) -> list[ReviewComment]:
    """Only comments not yet seen by this session/caller.

    Save the latest returned comment key and pass it as ``after`` on the
    next read. An empty list means there is no new feedback.
    """
    return [
        _review_comment(comment)
        for comment in comment_updates(self._conn, root, after=after)
    ]


def review_audit(self, root: str) -> dict:
    """Decision attribution and timestamps for one review thread."""
    return audit(self._conn, self.pid, root)


def review_open(
    self,
    key: str,
    *,
    author: str,
    body: str,
    role: str = "auto",
    title: str = "",
    file: str | None = None,
    line: int | None = None,
    severity: ReviewSeverity = ReviewSeverity.BLOCKER,
) -> Thread:
    """Open a thread without directly changing the task's workflow state."""
    _require_session_actor(self, author)
    if not isinstance(severity, ReviewSeverity):
        raise TypeError("severity must be a ReviewSeverity")
    return _thread(
        open_thread(
            self._conn,
            self.pid,
            key,
            author,
            body,
            role=role,
            title=title,
            file_path=file,
            line=line,
            severity=severity,
        )
    )


def review_set_severity(
    self, root: str, *, severity: ReviewSeverity, author: str
) -> Thread:
    """Change a review thread's severity and record the actor."""
    _require_session_actor(self, author)
    if not isinstance(severity, ReviewSeverity):
        raise TypeError("severity must be a ReviewSeverity")
    return _thread(set_severity(self._conn, self.pid, root, severity, author))


def review_reply(
    self, comment: str, *, author: str, action: str, body: str, role: str = "auto"
) -> Thread:
    """Reply to review feedback and emit the matching feedback action."""
    _require_session_actor(self, author)
    return _thread(
        reply(
            self._conn,
            self.pid,
            comment,
            author,
            action,
            body,
            role=role,
        )
    )


def review_reopen(
    self, root: str, *, author: str, body: str, role: str = "auto"
) -> Thread:
    """Reviewer reopens a closed thread and posts the required reply."""
    _require_session_actor(self, author)
    return _thread(
        reopen(
            self._conn,
            self.pid,
            root,
            author,
            body,
            role=role,
        )
    )


def _require_session_actor(self, author: str) -> None:
    if self.actor is not None and author != self.actor:
        raise BacklogError(
            f"review author {author!r} does not match session actor {self.actor!r}"
        )

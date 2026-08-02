"""Review inbox, thread, comment, and reply APIs."""

from __future__ import annotations

from dataclasses import dataclass

from .. import review
from ..db import BacklogError
from ..schema import ReviewSeverity


@dataclass(frozen=True)
class Thread:
    """A review thread, carrying only what `review inbox` shows: the root
    comment, the latest reply, and who the ball is with. Never the whole
    thread -- ask for `full` on the CLI if that is genuinely needed."""

    root_key: str
    task_key: str
    task_title: str
    opened_by: str
    reviewer: str
    severity: ReviewSeverity
    state: str
    awaiting_role: str | None
    awaiting_actor: str | None
    body: str
    latest: str
    latest_author: str
    file: str | None
    line: int | None
    hidden_comments: int
    reply_to: str
    age_days: float

    @property
    def where(self) -> str:
        return f"{self.file}:{self.line}" if self.file else ""

    def __str__(self) -> str:
        head = (f"{self.task_key}  {self.root_key}  "
                f"{self.opened_by}, {self.age_days:.0f}d")
        if self.file:
            head += f"  {self.where}"
        return f"{head}\n  {self.body}"


def _first_line(text: str | None) -> str:
    lines = (text or "").strip().splitlines()
    return lines[0] if lines else ""


def _thread(t: dict) -> Thread:
    """Map one `review.thread_summary` dict onto the Thread dataclass."""
    root = t.get("root_comment") or {}
    last = t.get("last_comment") or {}
    return Thread(
        root_key=t["root"],
        task_key=t["target"],
        task_title=t.get("target_title", ""),
        opened_by=t.get("opened_by") or root.get("author", ""),
        reviewer=t.get("reviewer") or t.get("opened_by") or root.get("author", ""),
        severity=ReviewSeverity(t.get("severity", ReviewSeverity.BLOCKER.value)),
        state=t.get("state", ""),
        awaiting_role=t.get("awaiting_role"),
        awaiting_actor=t.get("awaiting_actor"),
        body=_first_line(root.get("body")),
        latest=_first_line(last.get("body")),
        latest_author=last.get("author", ""),
        file=t.get("file"),
        line=t.get("line"),
        hidden_comments=int(t.get("hidden_comments") or 0),
        reply_to=t.get("reply_to", ""),
        age_days=_age_days(t.get("opened_at")),
    )


@dataclass(frozen=True)
class ReviewComment:
    """One newly observed review comment."""

    key: str
    root_key: str
    parent_key: str | None
    author: str
    assignee: str | None
    reviewer: str
    role: str
    action: str
    body: str
    file: str | None
    line: int | None
    created_at: str


def _review_comment(row: dict) -> ReviewComment:
    return ReviewComment(
        key=row["key"],
        root_key=row.get("root") or row.get("root_key") or "",
        parent_key=row.get("parent"),
        author=row["author"],
        assignee=row.get("assignee"),
        reviewer=row["reviewer"],
        role=row["role"],
        action=row["action"],
        body=row["body"],
        file=row.get("file"),
        line=row.get("line"),
        created_at=row.get("at", ""),
    )


class ReviewApi:
    __slots__ = ()

    def inbox(self, actor: str | None = None, role: str | None = None,
              severity: ReviewSeverity | None = None) -> list[Thread]:
        """Review threads waiting on `actor`, oldest first."""
        if severity is not None and not isinstance(severity, ReviewSeverity):
            raise TypeError("severity must be a ReviewSeverity")
        out = [_thread(t) for t in review.inbox(
            self._conn, self.pid, actor=actor, role=role, severity=severity
        )]
        return sorted(out, key=lambda t: -t.age_days)

    def threads(self, key: str, state: str = "open",
                severity: ReviewSeverity | None = None) -> list[Thread]:
        """Review threads on one task."""
        if severity is not None and not isinstance(severity, ReviewSeverity):
            raise TypeError("severity must be a ReviewSeverity")
        return [_thread(t) for t in review.list_threads(
            self._conn, self.pid, key, state, severity=severity
        )]

    def review_updates(self, root: str, *,
                       after: str | None = None) -> list[ReviewComment]:
        """Only comments not yet seen by this session/caller.

        Save the latest returned comment key and pass it as ``after`` on the
        next read. An empty list means there is no new feedback.
        """
        return [
            _review_comment(comment)
            for comment in review.comment_updates(self._conn, root, after=after)
        ]

    def review_audit(self, root: str) -> dict:
        """Decision attribution and timestamps for one review thread."""
        return review.audit(self._conn, self.pid, root)

    def review_open(self, key: str, *, author: str, body: str,
                    role: str = "auto", title: str = "",
                    file: str | None = None, line: int | None = None,
                    severity: ReviewSeverity = ReviewSeverity.BLOCKER) -> Thread:
        """Open a thread without directly changing the task's workflow state."""
        self._require_session_actor(author)
        if not isinstance(severity, ReviewSeverity):
            raise TypeError("severity must be a ReviewSeverity")
        return _thread(review.open_thread(
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
        ))

    def review_set_severity(self, root: str, *,
                            severity: ReviewSeverity, author: str) -> Thread:
        """Change a review thread's severity and record the actor."""
        self._require_session_actor(author)
        if not isinstance(severity, ReviewSeverity):
            raise TypeError("severity must be a ReviewSeverity")
        return _thread(review.set_severity(
            self._conn, self.pid, root, severity, author
        ))

    def review_reply(self, comment: str, *, author: str, action: str,
                     body: str, role: str = "auto") -> Thread:
        """Reply to review feedback and emit the matching feedback action."""
        self._require_session_actor(author)
        return _thread(review.reply(
            self._conn,
            self.pid,
            comment,
            author,
            action,
            body,
            role=role,
        ))

    def review_reopen(self, root: str, *, author: str, body: str,
                      role: str = "auto") -> Thread:
        """Reviewer reopens a closed thread and posts the required reply."""
        self._require_session_actor(author)
        return _thread(review.reopen(
            self._conn,
            self.pid,
            root,
            author,
            body,
            role=role,
        ))

    def _require_session_actor(self, author: str) -> None:
        if self.actor is not None and author != self.actor:
            raise BacklogError(
                f"review author {author!r} does not match session actor {self.actor!r}"
            )

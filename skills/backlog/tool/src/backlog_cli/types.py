"""Public value objects returned by the backlog API."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from . import core, deps, execution
from .schema import ReviewSeverity

if TYPE_CHECKING:
    from .api import Backlog


def _age_days(stamp: str) -> float:
    when = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    return (datetime.now(timezone.utc) - when).total_seconds() / 86400.0


@dataclass(frozen=True)
class Store:
    """Which store and project this session talks to."""

    backend: str
    scope: str
    project: str
    location: str

    def __str__(self) -> str:
        return f"{self.location} ({self.backend}/{self.scope}) project={self.project}"


class Task:
    """One row of `task`, with attribute access and a few derived fields.

    Columns are reachable as attributes (`t.key`, `t.status`, `t.assignee`,
    `t.pr_url`, ...) or by subscript for anything unusual.
    """

    __slots__ = ("_row", "_bl")

    def __init__(self, row, bl: "Backlog"):
        self._row = row
        self._bl = bl

    def __getattr__(self, name):
        try:
            return self._row[name]
        except (IndexError, KeyError):
            raise AttributeError(name) from None

    def __getitem__(self, name):
        return self._row[name]

    def __repr__(self) -> str:
        return f"<Task {self._row['key']} {self._row['status']}>"

    def __str__(self) -> str:
        return f"{self._row['key']}  {self._row['status']:<12} {self._row['title']}"

    @property
    def age_days(self) -> float:
        """Days since the task was created."""
        return _age_days(self._row["created_at"])

    @property
    def idle_days(self) -> float:
        """Days since anything changed on the task."""
        return _age_days(self._row["updated_at"])

    @property
    def is_open(self) -> bool:
        return self._row["closed_at"] is None

    # -- navigation ------------------------------------------------------- #

    @property
    def children(self) -> list["Task"]:
        return [
            self._bl._task(r) for r in core.children_of(self._bl._conn, self._row["id"])
        ]

    @property
    def parent(self) -> str | None:
        """Parent task key; alias for ``parent_key``."""
        return self._row["parent_key"]

    @property
    def blockers(self) -> list[dict]:
        """Unfinished tasks standing in the way, as `{other_key, other_status}`."""
        return deps.blockers(self._bl._conn, self._row["id"])

    def items(self, kind: str | None = None) -> list[str]:
        """Acceptance criteria (`kind="criteria"`), checklist entries or notes."""
        rows = core.task_items(self._bl._conn, self._row["id"], kind)
        return [r["content"] for r in rows]

    def item_details(self, kind: str | None = None) -> list[dict]:
        """Plain and executable items with their declarations and state."""
        rows = core.task_items(self._bl._conn, self._row["id"], kind)
        return [execution._item_details(self._bl._conn, row) for row in rows]

    @property
    def todos(self) -> list[dict]:
        """Flat implementation todos in their current stable order."""
        return self._bl.todos(self.key)

    @property
    def acceptance_criteria(self) -> list[dict]:
        """Acceptance criteria with each one's review verdict."""
        return self._bl.acceptance_criteria(self.key)

    @property
    def open_threads(self) -> list[str]:
        return [
            r["root_key"] for r in core.open_threads(self._bl._conn, self._row["id"])
        ]

    @property
    def iteration_members(self) -> list["Task"]:
        """Tasks grouped by this Iteration, ordered by priority."""
        if self.task_type != "iteration":
            return []
        return [
            self._bl._task(r)
            for r in core.iteration_members(self._bl._conn, self._row["id"])
        ]

    @property
    def iterations(self) -> list["Task"]:
        """Iterations containing this task."""
        rows = self._bl._conn.execute(
            "SELECT i.* FROM iteration_member m JOIN task i ON i.id=m.iteration_id "
            "WHERE m.member_id=? ORDER BY i.priority,i.key",
            (self._row["id"],),
        ).fetchall()
        return [self._bl._task(r) for r in rows]


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
        from . import retrospective

        return retrospective.required_decision(self._row["status"])

    @property
    def is_open(self) -> bool:
        return self._row["closed_at"] is None


@dataclass(frozen=True)
class Gate:
    """The verdict of a gate, already reduced to something printable."""

    key: str
    target: str
    ok: bool
    checks: list[tuple[str, bool, str]]

    @property
    def failures(self) -> list[str]:
        return [
            f"{name}: {detail}" for name, passed, detail in self.checks if not passed
        ]

    def __str__(self) -> str:
        if self.ok:
            return f"{self.key}  READY    all {self.target} gates pass"
        return f"{self.key}  BLOCKED  " + "; ".join(self.failures)


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
        head = (
            f"{self.task_key}  {self.root_key}  {self.opened_by}, {self.age_days:.0f}d"
        )
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

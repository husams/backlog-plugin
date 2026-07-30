"""Stable, agent-facing API over the backlog store.

The CLI answers one question per process. This module answers questions the CLI
has no flag for -- "which in_review stories have been idle more than three days
and already pass the merge gate" -- in a single process, against a single
connection, and returns Python objects you reduce to a short answer yourself.

    from backlog_cli import api

    with api.open() as bl:
        stale = [t for t in bl.tasks(status="in_review") if t.idle_days > 3]
        print(f"{len(stale)} stale: {', '.join(t.key for t in stale)}")

Everything here obeys the same workflow rows, gates and audit trail as the CLI:
`move` still refuses an illegal transition, `can` still evaluates real gates.
There is deliberately no way to execute SQL -- that would bypass all of it.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
from datetime import datetime, timezone

from . import core, deps, hooks, review, workflow
from .hooks import Action
from .db import (
    BacklogError,
    Conn,
    connect,
    list_projects,
    require_backlog_dir,
    require_project,
    resolve_spec,
)

__all__ = [
    "open", "Backlog", "Task", "Gate", "Thread", "Store", "Action", "BacklogError"
]


def _age_days(stamp: str | None) -> float:
    if not stamp:
        return 0.0
    try:
        when = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
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
        return [Task(r, self._bl) for r in core.children_of(self._bl._conn, self._row["id"])]

    @property
    def blockers(self) -> list[dict]:
        """Unfinished tasks standing in the way, as `{other_key, other_status}`."""
        return deps.blockers(self._bl._conn, self._row["id"])

    def items(self, kind: str | None = None) -> list[str]:
        """Acceptance criteria (`kind="criteria"`), checklist entries or notes."""
        rows = core.task_items(self._bl._conn, self._row["id"], kind)
        return [r["content"] for r in rows]

    @property
    def open_threads(self) -> list[str]:
        return [r["root_key"] for r in core.open_threads(self._bl._conn, self._row["id"])]


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


@dataclass(frozen=True)
class Thread:
    """A review thread, carrying only what `review inbox` shows: the root
    comment, the latest reply, and who the ball is with. Never the whole
    thread -- ask for `full` on the CLI if that is genuinely needed."""

    root_key: str
    task_key: str
    task_title: str
    opened_by: str
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


class Backlog:
    """An open session. Prefer the `open()` context manager over building one."""

    __slots__ = ("_conn", "actor", "_project", "_spec")

    def __init__(self, conn: Conn, project_row, spec, actor: str | None = None):
        self._conn = conn
        self.actor = actor
        self._project = project_row
        self._spec = spec
        conn.project_id = int(project_row["id"])

    # -- context ---------------------------------------------------------- #

    @property
    def pid(self) -> int:
        return int(self._project["id"])

    @property
    def store(self) -> Store:
        return Store(self._spec.dialect, self._spec.scope,
                     self._project["slug"], self._spec.location)

    @property
    def artifacts_dir(self):
        return require_backlog_dir()

    def projects(self) -> list[str]:
        return [r["slug"] for r in list_projects(self._conn)]

    # -- reading ---------------------------------------------------------- #

    def task(self, key: str) -> Task:
        """One task by key. Raises BacklogError if it does not exist."""
        return Task(core.get_task(self._conn, self.pid, key), self)

    def find(self, key: str) -> Task | None:
        row = core.find_task(self._conn, self.pid, key)
        return Task(row, self) if row is not None else None

    def tasks(self, *, status: str | None = None, task_type: str | None = None,
              assignee: str | None = None, reviewer: str | None = None,
              parent: str | None = None, open_only: bool = False) -> list[Task]:
        """Every task matching the filters, ordered by priority then key."""
        where, params = [], []
        if status:
            where.append("t.status = ?")
            params.append(core.normalize_status(status))
        if task_type:
            where.append("t.task_type = ?")
            params.append(core.normalize_type(task_type))
        if assignee:
            where.append("t.assignee = ?")
            params.append(assignee)
        if reviewer:
            where.append("t.reviewer = ?")
            params.append(reviewer)
        if parent:
            where.append("t.parent_id = (SELECT id FROM task WHERE project_id = ? AND key = ?)")
            params += [self.pid, core.normalize_key(parent)]
        if open_only:
            where.append("t.closed_at IS NULL")
        sql = ("SELECT t.*, p.key AS parent_key FROM task t "
               "LEFT JOIN task p ON p.id = t.parent_id WHERE t.project_id = ?")
        if where:
            sql += " AND " + " AND ".join(where)
        rows = self._conn.execute(sql + " ORDER BY t.priority, t.key",
                                  [self.pid, *params]).fetchall()
        return [Task(r, self) for r in rows]

    def counts(self) -> dict[str, int]:
        """How many tasks sit in each status. Useful for a one-line board."""
        rows = self._conn.execute(
            "SELECT status, COUNT(*) AS n FROM task WHERE project_id = ? GROUP BY status",
            (self.pid,)).fetchall()
        return {r["status"]: int(r["n"]) for r in rows}

    def statuses(self, task_type: str = "story") -> list[str]:
        """The statuses this project's flow actually defines for a task type."""
        return list(workflow.get(self._conn, self.pid, core.normalize_type(task_type)).statuses)

    def flow(self, task_type: str = "story"):
        """The Workflow object: `.allows(a, b)`, `.next_from(s)`, `.display(s)`."""
        return workflow.get(self._conn, self.pid, core.normalize_type(task_type))

    def startable(self, actor: str | None = None) -> list[Task]:
        """Open, unblocked tasks -- optionally only those assigned to `actor`."""
        out = []
        for t in self.tasks(assignee=actor, open_only=True):
            if not t.blockers:
                out.append(t)
        return out

    def blocked(self) -> list[tuple[Task, list[str]]]:
        """Every open task that something unfinished is standing in front of."""
        out = []
        for t in self.tasks(open_only=True):
            names = [b["other_key"] for b in t.blockers]
            if names:
                out.append((t, names))
        return out

    def cycles(self) -> list[list[str]]:
        """Dependency cycles, as lists of keys. Empty when the graph is sane."""
        return deps.cycles(self._conn)

    # -- gates ------------------------------------------------------------ #

    def can(self, key: str, target: str = "merge", **waivers) -> Gate:
        """Evaluate a gate without moving anything.

        `target` is one of start / in_review / accepted / done / merge.
        Waivers mirror the CLI flags: allow_blocked, no_pr, allow_open_children.
        """
        ok, checks = core.gate(self._conn, self.pid, key, target, **waivers)
        return Gate(core.normalize_key(key), core.normalize_gate(target), ok,
                    [(c.name, c.ok, c.detail) for c in checks])

    # -- review ----------------------------------------------------------- #

    def inbox(self, actor: str | None = None, role: str | None = None) -> list[Thread]:
        """Review threads waiting on `actor`, oldest first."""
        out = [_thread(t) for t in review.inbox(self._conn, self.pid, actor=actor, role=role)]
        return sorted(out, key=lambda t: -t.age_days)

    def threads(self, key: str, state: str = "open") -> list[Thread]:
        """Review threads on one task."""
        return [_thread(t) for t in review.list_threads(self._conn, self.pid, key, state)]

    # -- writing ---------------------------------------------------------- #

    def move(self, key: str, to_status: str, actor: str | None = None,
             reason: str = "", **waivers) -> Task:
        """Transition a task. Refuses exactly as the CLI does, and commits."""
        row, _ = core.move(self._conn, self.pid, key, to_status,
                           actor=actor or self.actor, reason=reason, **waivers)
        return Task(row, self)

    def trigger(self, key: str, action: Action | str, *,
                actor: str | None = None, operation: str = "api.trigger",
                parameters: dict | None = None, **waivers) -> Task:
        """Submit an action; workflow configuration selects the destination."""
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
        return Task(row, self)

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
        return Task(row, self)

    def review_open(self, key: str, *, author: str, body: str,
                    role: str = "auto", title: str = "",
                    file: str | None = None, line: int | None = None) -> Thread:
        """Open review feedback and emit `feedback.posted`."""
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
        ))

    def review_reply(self, comment: str, *, author: str, action: str,
                     body: str, role: str = "auto") -> Thread:
        """Reply to review feedback and emit the matching feedback action."""
        return _thread(review.reply(
            self._conn,
            self.pid,
            comment,
            author,
            action,
            body,
            role=role,
        ))

    def assign(self, key: str, to: str | None = None, reviewer: str | None = None,
               actor: str | None = None) -> Task:
        row = core.assign(self._conn, self.pid, key, to=to, reviewer=reviewer,
                          actor=actor or self.actor)
        self._conn.commit()
        return Task(row, self)

    def commit(self) -> None:
        self._conn.commit()


@contextlib.contextmanager
def open(project: str | None = None, actor: str | None = None):
    """Open a session against the store this directory resolves to.

        with api.open(actor="claude") as bl:
            print(len(bl.startable("claude")), "ready to start")

    The connection is closed on exit; writes made through `move`/`assign` are
    already committed, and anything left pending is committed for you.
    """
    spec = resolve_spec()
    conn = connect(spec=spec)
    try:
        project_row = require_project(conn, project or spec.project)
        hooks.apply_workflow(conn, int(project_row["id"]), require_backlog_dir())
        bl = Backlog(conn, project_row, spec, actor=actor)
        yield bl
        conn.commit()
    finally:
        conn.close()

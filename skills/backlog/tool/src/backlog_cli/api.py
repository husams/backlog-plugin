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
`trigger` submits a semantic action and `can` evaluates real gates.
There is deliberately no way to execute SQL -- that would bypass all of it.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
from datetime import datetime, timezone

from . import core, deps, execution, hooks, review, workflow
from .hooks import Action
from .schema import ReviewSeverity
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
    "open", "Backlog", "Task", "Gate", "Thread", "ReviewComment", "Store", "Action",
    "ReviewSeverity", "BacklogError", "ExecutionSpec", "ExecutionPolicy",
    "Executor", "Requirement", "TerminalStatus", "SourceIdentity",
    "ValidationContext", "ValidationHookResult", "ValidationExecutionResult",
    "ExecutionResult", "validation_hook",
]

ExecutionSpec = execution.ExecutionSpec
ExecutionPolicy = execution.ExecutionPolicy
Executor = execution.Executor
Requirement = execution.Requirement
TerminalStatus = execution.TerminalStatus
SourceIdentity = execution.SourceIdentity
ValidationContext = execution.ValidationContext
ValidationHookResult = execution.ValidationHookResult
ValidationExecutionResult = execution.ValidationExecutionResult
validation_hook = execution.validation_hook


def _validated_spec(value: dict | None) -> dict | None:
    if value is None:
        return None
    return execution.parse_spec(value).canonical()


def _prepare_items(items: list[str | dict]) -> list[tuple[str, dict | None]]:
    prepared: list[tuple[str, dict | None]] = []
    for item in items:
        if isinstance(item, str):
            content, spec = item, None
        elif isinstance(item, dict):
            unknown = set(item) - {"content", "execution"}
            if unknown:
                raise BacklogError(
                    "unknown item fields: " + ", ".join(sorted(unknown))
                )
            content = item.get("content")
            spec = _validated_spec(item.get("execution"))
        else:
            raise TypeError("items must be strings or {content, execution} mappings")
        if not isinstance(content, str) or not content.strip():
            raise BacklogError("item content must be a non-empty string")
        prepared.append((content.strip(), spec))
    return prepared
ExecutionResult = execution.ExecutionResult


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

    def item_details(self, kind: str | None = None) -> list[dict]:
        """Plain and executable items with safe display metadata and state."""
        rows = core.task_items(self._bl._conn, self._row["id"], kind)
        return [execution.public_item(self._bl._conn, row) for row in rows]

    def executable_items(self) -> list[dict]:
        """Executable item declarations, with environment values redacted."""
        rows = self._bl._conn.execute(
            "SELECT e.item_id FROM executable_item e "
            "JOIN task_item i ON i.id=e.item_id "
            "WHERE i.task_id=? ORDER BY i.kind,i.position,i.id", (self._row["id"],),
        ).fetchall()
        return [
            execution.public_executable(self._bl._conn, int(row["item_id"]))
            for row in rows
        ]

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

    # -- creation and item authoring ------------------------------------- #

    def create_task(
        self, task_type: str, title: str, *, parent: str | None = None,
        description: str = "", priority: str = "P2", owner: str | None = None,
        assignee: str | None = None, reviewer: str | None = None,
        branch: str | None = None, acceptance_criteria: list[str | dict] | None = None,
    ) -> Task:
        """Create a task; criteria may be strings or ``{content, execution}`` mappings."""
        prepared = _prepare_items(acceptance_criteria or [])
        row = core.add_task(
            self._conn, self.pid, task_type, title, parent=parent,
            description=description, priority=priority, owner=owner,
            assignee=assignee, reviewer=reviewer, branch=branch, actor=self.actor,
        )
        for content, spec in prepared:
            item = core.add_item(
                self._conn, self.pid, row["key"], "acceptance_criteria",
                content, actor=self.actor,
            )
            if spec is not None:
                execution.set_executable(self._conn, item["id"], spec)
        return Task(core.get_task(self._conn, self.pid, row["key"]), self)

    def create_feature(self, title: str, **kwargs) -> Task:
        """Create a feature with optional plain or executable criteria."""
        kwargs.pop("branch", None)
        return self.create_task("feature", title, **kwargs)

    def create_story(self, title: str, *, feature: str | None = None, **kwargs) -> Task:
        """Create a story with optional plain or executable criteria."""
        return self.create_task("story", title, parent=feature, **kwargs)

    def add_item(
        self, key: str, kind: str, content: str, *, execution_spec: dict | None = None
    ) -> dict:
        """Add one plain, shell, or hook item and return its safe public view."""
        spec = _validated_spec(execution_spec)
        normalized_kind = core.normalize_item_kind(kind)
        if spec is not None and normalized_kind not in ("acceptance_criteria", "checklist"):
            raise BacklogError(
                "only acceptance criteria and checklist items may declare execution"
            )
        row = core.add_item(
            self._conn, self.pid, key, normalized_kind, content, actor=self.actor
        )
        if spec is not None:
            execution.set_executable(self._conn, row["id"], spec)
        return execution.public_item(self._conn, row)

    def set_items(self, key: str, kind: str, items: list[str | dict]) -> list[dict]:
        """Replace one item kind using strings or ``{content, execution}`` mappings."""
        prepared = _prepare_items(items)
        normalized_kind = core.normalize_item_kind(kind)
        if (
            any(spec is not None for _, spec in prepared)
            and normalized_kind not in ("acceptance_criteria", "checklist")
        ):
            raise BacklogError(
                "only acceptance criteria and checklist items may declare execution"
            )
        rows = core.set_items(
            self._conn, self.pid, key, normalized_kind,
            [content for content, _ in prepared], actor=self.actor,
        )
        for row, (_, spec) in zip(rows, prepared):
            if spec is not None:
                execution.set_executable(self._conn, row["id"], spec)
        return [execution.public_item(self._conn, row) for row in rows]

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

    def actions(self, key: str) -> list[Action]:
        """Semantic actions configured for the task's current state."""
        task = self.task(key)
        config_dir = hooks.project_backlog_dir(require_backlog_dir())
        return hooks.available_actions(
            config_dir, task.task_type, task.status
        )

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

    # -- writing ---------------------------------------------------------- #

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
                    file: str | None = None, line: int | None = None,
                    severity: ReviewSeverity = ReviewSeverity.BLOCKER) -> Thread:
        """Open a thread without directly changing the task's workflow state."""
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
        if not isinstance(severity, ReviewSeverity):
            raise TypeError("severity must be a ReviewSeverity")
        return _thread(review.set_severity(
            self._conn, self.pid, root, severity, author
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

    def review_reopen(self, root: str, *, author: str, body: str,
                      role: str = "auto") -> Thread:
        """Reviewer reopens a closed thread and posts the required reply."""
        return _thread(review.reopen(
            self._conn,
            self.pid,
            root,
            author,
            body,
            role=role,
        ))

    def assign(self, key: str, to: str | None = None, reviewer: str | None = None,
               actor: str | None = None) -> Task:
        row = core.assign(self._conn, self.pid, key, to=to, reviewer=reviewer,
                          actor=actor or self.actor)
        self._conn.commit()
        return Task(row, self)

    def set_item_execution(self, item_id: int, spec: dict) -> dict:
        """Attach or replace the typed execution declaration for one item."""
        execution.set_executable(self._conn, item_id, spec)
        return execution.public_executable(self._conn, item_id)

    def record_execution_result(
        self, item_id: int, spec_fingerprint: str,
        status: TerminalStatus | str, **kwargs,
    ) -> dict:
        """Record one terminal attempt; pending is represented by no row."""
        return execution.record_result(
            self._conn, item_id, spec_fingerprint, status, **kwargs
        )

    def execution_history(self, item_id: int, *, limit: int = 20,
                          project_root=None) -> list[dict]:
        """Newest-first bounded validation history with freshness metadata."""
        from pathlib import Path
        root = Path(project_root) if project_root is not None else None
        return execution.execution_history(
            self._conn, item_id, limit=limit, project_root=root
        )

    def waive_validation(self, item_id: int, *, reason: str,
                         actor: str | None = None) -> dict:
        """Audit an explicit waiver for the item's current execution spec."""
        return execution.waive_validation(
            self._conn, self.pid, item_id,
            actor=actor or self.actor or "", reason=reason,
        )

    def execution_policy(self, project_root) -> ExecutionPolicy:
        """Load trusted local policy from the executing project checkout."""
        from pathlib import Path
        return execution.load_policy(Path(project_root))

    def source_identity(self, project_root) -> SourceIdentity:
        """Return optional clean/dirty VCS identity for a validation run."""
        from pathlib import Path
        return execution.source_identity(Path(project_root))

    def run_hook_validation(
        self, item_id: int, *, actor: str | None = None, project_root=".",
    ) -> ValidationExecutionResult:
        """Resolve and run one trusted, allowlisted local validation hook."""
        from pathlib import Path
        return execution.run_hook_validation(
            self, item_id, actor=actor or self.actor or "unknown",
            project_root=Path(project_root),
        )

    def run_item(self, item_id: int, project_root, *,
                 policy: ExecutionPolicy | None = None,
                 actor: str | None = None) -> ExecutionResult:
        """Run one shell or hook executable item under trusted local policy."""
        from pathlib import Path
        return execution.run_validation(
            self, item_id, Path(project_root), policy=policy,
            actor=actor or self.actor,
        )

    def run_task(self, key: str, project_root, *, fail_fast: bool = False,
                 policy: ExecutionPolicy | None = None,
                 actor: str | None = None) -> list[ExecutionResult]:
        """Run all executable items in declaration order."""
        from pathlib import Path
        return execution.run_task_validations(
            self, key, Path(project_root), fail_fast=fail_fast, policy=policy,
            actor=actor or self.actor,
        )

    def commit(self) -> None:
        self._conn.commit()


@contextlib.contextmanager
def open(project: str | None = None, actor: str | None = None):
    """Open a session against the store this directory resolves to.

        with api.open(actor="claude") as bl:
            print(len(bl.startable("claude")), "ready to start")

    The connection is closed on exit; writes made through `trigger`/`assign` are
    already committed, and anything left pending is committed for you.
    """
    spec = resolve_spec()
    conn = connect(spec=spec)
    try:
        project_row = require_project(conn, project or spec.project)
        config_dir = hooks.project_backlog_dir(require_backlog_dir())
        hooks.apply_workflow(conn, int(project_row["id"]), config_dir)
        bl = Backlog(conn, project_row, spec, actor=actor)
        yield bl
        conn.commit()
    finally:
        conn.close()

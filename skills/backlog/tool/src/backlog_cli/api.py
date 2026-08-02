"""Stable, minimal external API for agent-authored scripts."""

from __future__ import annotations

import contextlib

from . import hooks
from .core import session_actions, session_queries, session_tasks
from .db import (
    BacklogError,
    Conn,
    connect,
    list_projects,
    require_backlog_dir,
    require_project,
    resolve_spec,
)
from .execution import (
    ExecutionPolicy,
    ExecutionResult,
    ExecutionSpec,
    Executor,
    Requirement,
    SourceIdentity,
    TerminalStatus,
    ValidationContext,
    ValidationExecutionResult,
    ValidationHookResult,
    validation_hook,
)
from .hooks import Action
from .execution import session as validation
from .retrospective import RetrospectiveStatus, session as retrospectives
from .review import session as reviews
from .schema import ReviewSeverity
from .types import Gate, RetrospectiveAction, ReviewComment, Store, Task, Thread

__all__ = [
    "open",
    "Backlog",
    "Task",
    "RetrospectiveAction",
    "Gate",
    "Thread",
    "ReviewComment",
    "Store",
    "Action",
    "RetrospectiveStatus",
    "ReviewSeverity",
    "BacklogError",
    "ExecutionSpec",
    "ExecutionPolicy",
    "ExecutionResult",
    "Executor",
    "Requirement",
    "TerminalStatus",
    "SourceIdentity",
    "ValidationContext",
    "ValidationHookResult",
    "ValidationExecutionResult",
    "validation_hook",
]


class Backlog:
    """Connection and project state for the public API."""

    __slots__ = ("_conn", "actor", "_project", "_spec")

    create_task = session_tasks.create_task
    create_feature = session_tasks.create_feature
    create_story = session_tasks.create_story
    create_bug = session_tasks.create_bug
    create_iteration = session_tasks.create_iteration
    add_iteration_member = session_tasks.add_iteration_member
    remove_iteration_member = session_tasks.remove_iteration_member
    add_item = session_tasks.add_item
    set_items = session_tasks.set_items
    add_todo = session_tasks.add_todo
    add_todos = session_tasks.add_todos
    todos = session_tasks.todos
    close_todo = session_tasks.close_todo
    reopen_todo = session_tasks.reopen_todo
    move_todo = session_tasks.move_todo
    task = session_tasks.task
    find = session_tasks.find
    _task = session_tasks._task
    tasks = session_tasks.tasks

    counts = session_queries.counts
    task_type_counts = session_queries.task_type_counts
    statuses = session_queries.statuses
    flow = session_queries.flow
    actions = session_queries.actions
    startable = session_queries.startable
    blocked = session_queries.blocked
    cycles = session_queries.cycles
    dependencies = session_queries.dependencies
    artifacts = session_queries.artifacts

    can = session_actions.can
    trigger = session_actions.trigger
    set_pr = session_actions.set_pr
    assign = session_actions.assign

    inbox = reviews.inbox
    threads = reviews.threads
    review_updates = reviews.review_updates
    review_audit = reviews.review_audit
    review_open = reviews.review_open
    review_set_severity = reviews.review_set_severity
    review_reply = reviews.review_reply
    review_reopen = reviews.review_reopen

    create_retrospective_action = retrospectives.create_retrospective_action
    retrospective_action = retrospectives.retrospective_action
    retrospective_actions = retrospectives.retrospective_actions
    accept_retrospective_action = retrospectives.accept_retrospective_action
    reject_retrospective_action = retrospectives.reject_retrospective_action
    close_retrospective_action = retrospectives.close_retrospective_action

    set_item_execution = validation.set_item_execution
    record_execution_result = validation.record_execution_result
    execution_history = validation.execution_history
    waive_validation = validation.waive_validation
    execution_policy = validation.execution_policy
    source_identity = validation.source_identity
    run_hook_validation = validation.run_hook_validation
    run_item = validation.run_item
    run_task = validation.run_task

    def __init__(self, conn: Conn, project_row, spec, actor: str | None = None):
        self._conn = conn
        self.actor = actor
        self._project = project_row
        self._spec = spec
        conn.project_id = int(project_row["id"])

    @property
    def pid(self) -> int:
        return int(self._project["id"])

    @property
    def store(self) -> Store:
        return Store(
            self._spec.dialect,
            self._spec.scope,
            self._project["slug"],
            self._spec.location,
        )

    @property
    def artifacts_dir(self):
        return require_backlog_dir()

    def projects(self) -> list[str]:
        return [r["slug"] for r in list_projects(self._conn)]

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

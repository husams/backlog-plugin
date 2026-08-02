"""Stable, minimal external API for agent-authored scripts."""

from __future__ import annotations

import contextlib

from . import core, hooks
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
    ValidationApi,
    ValidationContext,
    ValidationExecutionResult,
    ValidationHookResult,
    validation_hook,
)
from .hooks import Action
from .retrospective import RetrospectiveApi, RetrospectiveStatus
from .review import ReviewApi
from .schema import ReviewSeverity
from .types import Gate, RetrospectiveAction, ReviewComment, Store, Task, Thread

__all__ = [
    "open", "Backlog", "Task", "RetrospectiveAction", "Gate", "Thread",
    "ReviewComment", "Store", "Action", "RetrospectiveStatus", "ReviewSeverity",
    "BacklogError", "ExecutionSpec", "ExecutionPolicy", "ExecutionResult",
    "Executor", "Requirement", "TerminalStatus", "SourceIdentity",
    "ValidationContext", "ValidationHookResult", "ValidationExecutionResult",
    "validation_hook",
]


class Backlog(core.CoreApi, ReviewApi, RetrospectiveApi, ValidationApi):
    """Small external facade composed from domain passthroughs."""

    __slots__ = ("_conn", "actor", "_project", "_spec")

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
        return Store(self._spec.dialect, self._spec.scope,
                     self._project["slug"], self._spec.location)

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

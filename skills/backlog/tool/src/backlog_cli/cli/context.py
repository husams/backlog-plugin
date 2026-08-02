"""backlog — backlog tracker for coding agents (SQLite or shared PostgreSQL)."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .. import (
    __version__, core, deps, execution, hooks, retrospective, review, templates, workflow,
)
from ..db import (
    BacklogError,
    Conn,
    connect,
    database_errors,
    get_or_create_project,
    init_store,
    list_projects,
    require_backlog_dir,
    require_project,
    resolve_spec,
    resync_sequences,
    slugify,
)
from ..render import (
    deps_block,
    items_block,
    projects_table,
    render_task,
    render_thread,
    row_to_dict,
    table,
    tasks_table,
)
from ..schema import (
    ARTIFACT_KINDS,
    GATE_CHECKS,
    GATE_DESCRIPTIONS,
    STATUS_CATEGORIES,
    TASK_KEY_PREFIX,
    DEPENDENCY_KINDS,
    ITEM_KINDS,
    PR_REVIEW_STATES,
    PR_STATES,
    SCHEMA_VERSION,
    STATUS_DISPLAY,
    STATUSES,
    TASK_PARENT_TYPES,
    TASK_TYPES,
    transitions_for,
)



class Ctx:
    def __init__(self, args: argparse.Namespace):
        self.json = bool(getattr(args, "json", False))
        self.project_override = getattr(args, "project", None)
        self._dir: Path | None = None
        self._conn: Conn | None = None
        self._spec = None
        self._project = None

    @property
    def spec(self):
        if self._spec is None:
            self._spec = resolve_spec()
        return self._spec

    @property
    def dir(self) -> Path:
        if self._dir is None:
            self._dir = require_backlog_dir()
        return self._dir

    @property
    def conn(self) -> Conn:
        if self._conn is None:
            self._conn = connect(spec=self.spec)
        return self._conn

    @property
    def project(self):
        """The project row every task command is scoped to."""
        if self._project is None:
            slug = self.project_override or self.spec.project
            self._project = require_project(self.conn, slug)
            config_dir = hooks.project_backlog_dir(self.dir)
            hooks.apply_workflow(self.conn, int(self._project["id"]), config_dir)
        return self._project

    @property
    def pid(self) -> int:
        return int(self.project["id"])

    def emit(self, payload, text: str) -> None:
        if self.json:
            print(json.dumps(payload, indent=2, sort_keys=True, default=str))
        else:
            print(text)


def _task_rows(conn: Conn, project_id: int, where: str = "", params=()) -> list:
    """Tasks with their parent key resolved, ready for `tasks_table`."""
    sql = ("SELECT t.*, p.key AS parent_key FROM task t "
           "LEFT JOIN task p ON p.id = t.parent_id WHERE t.project_id = ?")
    return conn.execute(sql + where + " ORDER BY t.priority, t.key",
                        [project_id, *params]).fetchall()

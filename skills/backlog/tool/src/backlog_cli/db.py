"""Store location, backend selection, connection, project resolution, migration.

Three deployment shapes, chosen entirely by environment so a project never has
to change how it calls the CLI:

    repo     (default)  ./.backlog/backlog.db next to the code, committed to git
    central             one file holding every project on this machine,
                        e.g. BACKLOG_DB=sqlite BACK_LOG_URL=~/.backlog
    shared              a PostgreSQL server holding every project,
                        e.g. BACKLOG_DB=postgres
                             BACK_LOG_URL=postgresql://host/backlog

In every shape the store holds a `project` table and each task carries a
`project_id`; which project a command acts on is decided by `BACKLOG_PROJECT`,
defaulting to the git repository's directory name.

Environment:

    BACKLOG_DB          sqlite | postgres. Unset => repo SQLite.
                        Legacy URL/path values remain accepted.
    BACK_LOG_URL        SQLite path/URL or PostgreSQL DSN for the selected backend.
    BACKLOG_PROJECT     project slug (default: the git repository's name)
    BACKLOG_SCHEMA      PostgreSQL schema to use (default: backlog)
    BACKLOG_DIR         explicit .backlog directory (repo mode only)
    BACKLOG_ARTIFACTS   where artifact files live; defaults next to the store
    BACKLOG_PG_RETRIES / BACKLOG_PG_RETRY_DELAY   transient-connect retry

Everything above the connection is dialect-agnostic: `Conn.execute` takes
`?` placeholders and rows behave like mappings on both backends.
"""

from __future__ import annotations

import os
import re
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .schema import KNOWN_AGENTS, SCHEMA_SQL, SCHEMA_VERSION

BACKLOG_DIR_NAME = ".backlog"
DB_NAME = "backlog.db"
ARTIFACTS_DIR_NAME = "artifacts"
DEFAULT_PG_SCHEMA = "backlog"

SQLITE, POSTGRES = "sqlite", "postgres"

# A result row. sqlite3.Row and psycopg's dict_row both behave as mappings with
# .keys(), r["col"] and dict(r); nothing else is assumed.
Row = Any


class BacklogError(Exception):
    """User-facing error; printed without a traceback and exits non-zero."""


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def actor_kind(name: str | None) -> str:
    """Classify a free-text actor name as an agent or a human.

    Names stay free text — this only records what kind of worker the name
    refers to, so a board can separate agent work from human work without
    forcing a naming convention.
    """
    if not name:
        return "unknown"
    n = name.strip().lower()
    if n in KNOWN_AGENTS or n.endswith("-agent") or n.startswith("agent:"):
        return "agent"
    return "human"


# --------------------------------------------------------------------------- #
# dialect-neutral connection
# --------------------------------------------------------------------------- #

def _translate(sql: str, has_params: bool) -> str:
    """`?` placeholders -> `%s`, leaving anything inside quotes alone."""
    out: list[str] = []
    in_str = False
    for ch in sql:
        if ch == "'":
            in_str = not in_str
            out.append(ch)
        elif ch == "%" and not in_str and has_params:
            out.append("%%")
        elif ch == "?" and not in_str:
            out.append("%s")
        else:
            out.append(ch)
    return "".join(out)


def split_statements(script: str) -> list[str]:
    """Split a DDL script on top-level semicolons.

    `--` comments are passed through verbatim but never interpreted, so an
    apostrophe or a semicolon inside one cannot desynchronise the split.
    """
    parts: list[str] = []
    buf: list[str] = []
    in_str = in_comment = False
    i = 0
    while i < len(script):
        ch = script[i]
        if in_comment:
            buf.append(ch)
            if ch == "\n":
                in_comment = False
            i += 1
            continue
        if not in_str and ch == "-" and script[i:i + 2] == "--":
            in_comment = True
            buf.append(ch)
            i += 1
            continue
        if ch == "'":
            in_str = not in_str
        if ch == ";" and not in_str:
            stmt = "".join(buf).strip()
            if stmt:
                parts.append(stmt)
            buf = []
        else:
            buf.append(ch)
        i += 1
    tail = "".join(buf).strip()
    if tail:
        parts.append(tail)
    return parts


# SQLite DDL -> PostgreSQL. The schema is deliberately plain SQL so this stays
# a two-line translation rather than a second schema to keep in step.
_PG_DDL = [
    (re.compile(r"\bINTEGER PRIMARY KEY AUTOINCREMENT\b", re.I), "SERIAL PRIMARY KEY"),
    (re.compile(r"^\s*PRAGMA[^;]*$", re.I | re.M), ""),
]


def _pg_ddl(stmt: str) -> str:
    for pattern, repl in _PG_DDL:
        stmt = pattern.sub(repl, stmt)
    return stmt.strip()


class Conn:
    """Thin wrapper so every caller writes SQLite-flavoured SQL exactly once."""

    def __init__(self, raw, dialect: str, spec: "StoreSpec | None" = None):
        self._raw = raw
        self.dialect = dialect
        self.spec = spec
        self.project_id: int | None = None

    def execute(self, sql: str, params=()):
        if self.dialect == SQLITE:
            return self._raw.execute(sql, tuple(params))
        args = tuple(params)
        cur = self._raw.cursor()
        cur.execute(_translate(sql, bool(args)), args or None)
        return cur

    def executemany(self, sql: str, seq):
        rows = list(seq)
        if not rows:
            return None
        if self.dialect == SQLITE:
            return self._raw.executemany(sql, rows)
        cur = self._raw.cursor()
        cur.executemany(_translate(sql, True), rows)
        return cur

    def executescript(self, script: str) -> None:
        if self.dialect == SQLITE:
            self._raw.executescript(script)
            return
        cur = self._raw.cursor()
        for stmt in split_statements(script):
            ddl = _pg_ddl(stmt)
            if ddl:
                cur.execute(ddl)
        self._raw.commit()

    def insert_returning_id(self, sql: str, params=()) -> int:
        """INSERT one row and return its generated id, on either backend."""
        if self.dialect == SQLITE:
            cur = self._raw.execute(sql, tuple(params))
            return int(cur.lastrowid)
        cur = self.execute(sql + " RETURNING id", params)
        return int(cur.fetchone()["id"])

    def commit(self) -> None:
        self._raw.commit()

    def rollback(self) -> None:
        """Clear a failed statement. On PostgreSQL the whole transaction is
        poisoned after any error, so a probe that is *allowed* to fail must
        roll back before anything else runs."""
        self._raw.rollback()

    def close(self) -> None:
        self._raw.close()

    @property
    def is_postgres(self) -> bool:
        return self.dialect == POSTGRES

    def table_exists(self, name: str) -> bool:
        if self.dialect == SQLITE:
            row = self.execute(
                "SELECT 1 AS ok FROM sqlite_master WHERE type='table' AND name = ?", (name,)
            ).fetchone()
        else:
            row = self.execute(
                "SELECT 1 AS ok FROM information_schema.tables "
                "WHERE table_schema = current_schema() AND table_name = ?",
                (name,),
            ).fetchone()
        return row is not None

    def integrity_ok(self) -> bool:
        if self.dialect == SQLITE:
            return self._raw.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        return self.execute("SELECT 1").fetchone() is not None


def database_errors() -> tuple[type[Exception], ...]:
    """Errors either backend may raise. Reported as `error: database: ...`."""
    errs: list[type[Exception]] = [sqlite3.DatabaseError]
    try:  # pragma: no cover - only when psycopg is installed
        import psycopg

        errs.append(psycopg.Error)
    except ImportError:
        pass
    return tuple(errs)


# --------------------------------------------------------------------------- #
# store resolution
# --------------------------------------------------------------------------- #

@dataclass
class StoreSpec:
    dialect: str
    scope: str  # repo | central | shared
    project: str
    artifacts_dir: Path
    db_path: Path | None = None
    dsn: str | None = None
    schema: str | None = None
    backlog_dir: Path | None = None

    @property
    def location(self) -> str:
        if self.dialect == POSTGRES:
            u = urlparse(self.dsn or "")
            return f"postgresql://{u.hostname or '?'}{u.path or ''} schema={self.schema}"
        return str(self.db_path)


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    return slug or "default"


def _repo_root(start: Path | None = None) -> Path | None:
    cur = (start or Path.cwd()).resolve()
    for candidate in [cur, *cur.parents]:
        if (candidate / ".git").exists():
            return candidate
    return None


def default_project(start: Path | None = None) -> str:
    env = os.environ.get("BACKLOG_PROJECT")
    if env:
        return slugify(env)
    root = _repo_root(start) or (start or Path.cwd()).resolve()
    return slugify(root.name)


def find_backlog_dir(start: Path | None = None) -> Path | None:
    env = os.environ.get("BACKLOG_DIR")
    if env:
        p = Path(env).expanduser()
        return p.resolve() if p.is_dir() else None
    cur = (start or Path.cwd()).resolve()
    for candidate in [cur, *cur.parents]:
        d = candidate / BACKLOG_DIR_NAME
        if d.is_dir():
            return d
    return None


def resolve_spec(start: Path | None = None, for_init: bool = False) -> StoreSpec:
    """Work out which store this invocation talks to. Never connects."""
    selector = (os.environ.get("BACKLOG_DB") or "").strip()
    configured_url = (os.environ.get("BACK_LOG_URL") or "").strip()
    project = default_project(start)
    art = os.environ.get("BACKLOG_ARTIFACTS")

    if selector.lower() in (SQLITE, POSTGRES):
        backend = selector.lower()
        url = configured_url
        if backend == POSTGRES and not url:
            raise BacklogError(
                "BACKLOG_DB=postgres requires BACK_LOG_URL with a PostgreSQL DSN."
            )
        if backend == POSTGRES and urlparse(url).scheme.lower() not in (
            "postgres", "postgresql"
        ):
            raise BacklogError(
                "BACK_LOG_URL must start with postgres:// or postgresql:// "
                "when BACKLOG_DB=postgres."
            )
        if backend == SQLITE and urlparse(url).scheme.lower() in (
            "postgres", "postgresql"
        ):
            raise BacklogError(
                "BACK_LOG_URL cannot be a PostgreSQL DSN when BACKLOG_DB=sqlite."
            )
    else:
        # Compatibility with releases where BACKLOG_DB itself held a path/URL.
        url = selector

    if url:
        scheme = urlparse(url).scheme.lower()
        if scheme in ("postgres", "postgresql"):
            home = Path(art).expanduser() if art else Path.home() / BACKLOG_DIR_NAME
            return StoreSpec(
                dialect=POSTGRES, scope="shared", project=project, dsn=url,
                schema=os.environ.get("BACKLOG_SCHEMA", DEFAULT_PG_SCHEMA),
                artifacts_dir=home / ARTIFACTS_DIR_NAME,
            )
        raw = url[len("sqlite://"):] if scheme == "sqlite" else url
        path = Path(raw if raw.startswith("/") else raw.lstrip("/")).expanduser()
        # A path ending in .db names the file; anything else is a directory
        # holding one store for every project on this machine.
        home = path.resolve() if path.suffix == ".db" else (path / DB_NAME).resolve()
        return StoreSpec(
            dialect=SQLITE, scope="central", project=project, db_path=home,
            backlog_dir=home.parent,
            artifacts_dir=(Path(art).expanduser() if art else home.parent / ARTIFACTS_DIR_NAME),
        )

    d = find_backlog_dir(start)
    if d is None:
        if not for_init:
            raise BacklogError(
                "no backlog store found.\n"
                "  repo mode     : run `backlog init` in the project root\n"
                "  central/shared: set BACKLOG_DB (see `backlog where --help`)"
            )
        d = (start or Path.cwd()).resolve() / BACKLOG_DIR_NAME
    return StoreSpec(
        dialect=SQLITE, scope="repo", project=project, db_path=d / DB_NAME,
        backlog_dir=d,
        artifacts_dir=(Path(art).expanduser() if art else d / ARTIFACTS_DIR_NAME),
    )


def require_backlog_dir(start: Path | None = None) -> Path:
    """Where artifact files belong for this invocation."""
    spec = resolve_spec(start)
    spec.artifacts_dir.parent.mkdir(parents=True, exist_ok=True)
    return spec.backlog_dir or spec.artifacts_dir.parent


# --------------------------------------------------------------------------- #
# connect
# --------------------------------------------------------------------------- #

def _connect_sqlite(spec: StoreSpec, create: bool = False) -> Conn:
    assert spec.db_path is not None
    if not spec.db_path.exists():
        if not create:
            raise BacklogError(f"{spec.db_path} does not exist. Run `backlog init` first.")
        spec.db_path.parent.mkdir(parents=True, exist_ok=True)
    raw = sqlite3.connect(spec.db_path)
    raw.row_factory = sqlite3.Row
    raw.execute("PRAGMA foreign_keys = ON")
    return Conn(raw, SQLITE, spec)


_TRANSIENT = (
    "remaining connection slots",
    "too many clients",
    "too many connections",
    "the database system is starting up",
    "connection timeout expired",
)


def _is_transient(message: str) -> bool:
    low = message.lower()
    return any(m in low for m in _TRANSIENT)


def _connect_hint(spec: StoreSpec, exc: Exception | None) -> str:
    text = f"cannot reach the backlog store at {spec.location}: {exc}"
    if exc is not None and _is_transient(str(exc)):
        text += (
            "\n  The server is out of connection slots. Retry, or free some up "
            "(`SELECT datname, count(*) FROM pg_stat_activity GROUP BY 1`).\n"
            "  BACKLOG_PG_RETRIES / BACKLOG_PG_RETRY_DELAY tune the retry.\n"
            "  Set BACKLOG_DB=sqlite and unset BACK_LOG_URL to use repo SQLite."
        )
    return text


def _connect_postgres(spec: StoreSpec, create: bool = False) -> Conn:
    try:
        import psycopg
        from psycopg.rows import dict_row
    except ImportError:  # pragma: no cover - depends on install extras
        raise BacklogError(
            "The selected backend is PostgreSQL but the psycopg driver is not installed.\n"
            "  install it into the skill:  uv sync --project "
            "~/.claude/skills/backlog/tool --extra postgres\n"
            "  or export BACKLOG_EXTRAS=postgres and re-run (the launcher syncs it)."
        )
    # A shared server hands out a finite number of connection slots, and a busy
    # neighbour can hold them all for a moment. That is transient, so retry.
    attempts = max(1, int(os.environ.get("BACKLOG_PG_RETRIES", "5")))
    delay = float(os.environ.get("BACKLOG_PG_RETRY_DELAY", "1.5"))
    raw = None
    for attempt in range(attempts):
        try:
            raw = psycopg.connect(spec.dsn, row_factory=dict_row, autocommit=False)
            break
        except psycopg.OperationalError as exc:
            if not _is_transient(str(exc)) or attempt == attempts - 1:
                raise BacklogError(_connect_hint(spec, exc)) from None
            time.sleep(delay * (attempt + 1))
    assert raw is not None
    cur = raw.cursor()
    cur.execute(f'CREATE SCHEMA IF NOT EXISTS "{spec.schema}"')
    cur.execute(f'SET search_path TO "{spec.schema}"')
    raw.commit()
    return Conn(raw, POSTGRES, spec)


def connect(backlog_dir: Path | None = None, spec: StoreSpec | None = None,
            create: bool = False) -> Conn:
    spec = spec or resolve_spec()
    conn = (_connect_postgres(spec, create) if spec.dialect == POSTGRES
            else _connect_sqlite(spec, create))
    _check_version(conn, spec)
    return conn


# --------------------------------------------------------------------------- #
# version / bootstrap / migration
# --------------------------------------------------------------------------- #

def _check_version(conn: Conn, spec: StoreSpec) -> None:
    try:
        row = conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
    except Exception:
        conn.rollback()  # an empty store has no `meta` yet
        row = None
    if row is None:
        _bootstrap(conn)
        return
    found = int(row["value"])
    if found > SCHEMA_VERSION:
        raise BacklogError(
            f"backlog store schema v{found} is newer than this tool (v{SCHEMA_VERSION}). "
            "Update the backlog skill."
        )
    if found < SCHEMA_VERSION:
        migrate(conn, found, spec)


def _bootstrap(conn: Conn) -> None:
    from . import templates

    conn.executescript(SCHEMA_SQL)
    conn.execute(
        "INSERT INTO meta(key, value) VALUES('schema_version', ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (str(SCHEMA_VERSION),),
    )
    conn.execute(
        "INSERT INTO meta(key, value) VALUES('created_at', ?) ON CONFLICT(key) DO NOTHING",
        (utcnow(),),
    )
    conn.commit()
    templates.install_builtins(conn)


_V2_TABLES = ["feature", "item", "dependency", "review_comment", "review_thread",
              "artifact", "event", "key_counter"]

# The retired feature vocabulary, mapped onto the one status machine.
_FEATURE_STATUS_IN = {
    "planned": "created",
    "active": "in_progress",
    "shipped": "done",
    "dropped": "incomplete",
}


def migrate(conn: Conn, from_version: int, spec: StoreSpec) -> list[str]:
    """Forward migration.

    v1/v2 (feature + item)  -> v3 (project + task)
    v3                      -> v4 (workflows as data)
    v14                     -> v15 (retrospective improvement actions)
    v15                     -> v16 (task creator attribution and separation)
    """
    notes: list[str] = []
    if from_version >= SCHEMA_VERSION:
        return notes

    if from_version >= 3 or not conn.table_exists("feature"):
        _add_column(conn, "task", "created_by", "TEXT")
        if from_version < 13:
            notes += _upgrade_bug_task_constraint(conn)
        notes += _backfill_task_creators(conn)
        _add_column(conn, "project", "template_id", "INTEGER")
        _add_column(
            conn,
            "review_thread",
            "severity",
            "TEXT NOT NULL DEFAULT 'blocker' "
            "CHECK (severity IN ('blocker','nice_to_have','info'))",
        )
        _add_column(conn, "execution_result", "expected_result", "TEXT")
        _add_column(conn, "execution_result", "actual_result", "TEXT")
        _add_column(conn, "execution_result", "hook_name", "TEXT")
        _add_column(conn, "execution_result", "implementation_identity", "TEXT")
        _add_column(conn, "execution_result", "actual_exit_code", "INTEGER")
        _add_column(conn, "execution_result", "stdout", "TEXT NOT NULL DEFAULT ''")
        _add_column(conn, "execution_result", "stderr", "TEXT NOT NULL DEFAULT ''")
        _add_column(conn, "execution_result", "duration_ms", "INTEGER NOT NULL DEFAULT 0")
        _add_column(conn, "execution_result", "actor", "TEXT NOT NULL DEFAULT 'unknown'")
        # Already the task shape (or empty): additive tables plus a seeded
        # workflow for every project that does not have one yet.
        conn.executescript(SCHEMA_SQL)
        conn.execute(
            "INSERT INTO meta(key, value) VALUES('schema_version', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (str(SCHEMA_VERSION),),
        )
        conn.commit()
        from . import templates

        added = templates.install_builtins(conn)
        if added:
            notes.append("installed templates: " + ", ".join(added))
        notes += _adopt_default_template(conn)
        notes += _upgrade_bug_template_workflows(conn)
        notes += _upgrade_iteration_template_workflows(conn)
        notes += _seed_missing_workflows(conn)
        notes += _upgrade_iteration_feedback_flow(conn)
        notes += _upgrade_feature_review_flow(conn)
        notes += _upgrade_required_validation_gates(conn)
        resync_sequences(conn)
        return notes or ["schema brought up to date"]

    old = _read_v2(conn)
    for name in _V2_TABLES:
        if conn.table_exists(name):
            conn.execute(f"DROP TABLE {name}")
    conn.commit()
    conn.executescript(SCHEMA_SQL)
    conn.commit()

    project_id = _insert_project(conn, spec.project, spec)
    notes.append(f"project '{spec.project}' created")
    notes += _seed_missing_workflows(conn)
    notes += _load_v2_into_v3(conn, project_id, old)

    conn.execute(
        "INSERT INTO meta(key, value) VALUES('schema_version', ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (str(SCHEMA_VERSION),),
    )
    conn.commit()
    resync_sequences(conn)
    return notes


def _upgrade_bug_task_constraint(conn: Conn) -> list[str]:
    """Allow Bug and Iteration rows in stores created before schema v13."""
    if conn.is_postgres:
        conn.execute("ALTER TABLE task DROP CONSTRAINT IF EXISTS task_task_type_check")
        conn.execute(
            "ALTER TABLE task ADD CONSTRAINT task_task_type_check "
            "CHECK (task_type IN ('feature','story','bug','subtask','iteration'))"
        )
        conn.commit()
        return ["enabled the bug task type"]

    conn.rollback()
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.executescript("""
CREATE TABLE task_v13 (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES project(id) ON DELETE CASCADE,
    key TEXT NOT NULL,
    task_type TEXT NOT NULL CHECK (task_type IN ('feature','story','bug','subtask','iteration')),
    parent_id INTEGER REFERENCES task(id) ON DELETE SET NULL,
    title TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'created',
    priority TEXT NOT NULL DEFAULT 'P2' CHECK (priority IN ('P0','P1','P2','P3')),
    owner TEXT,
    assignee TEXT,
    assignee_kind TEXT NOT NULL DEFAULT 'unknown'
        CHECK (assignee_kind IN ('human','agent','unknown')),
    reviewer TEXT,
    reviewer_kind TEXT NOT NULL DEFAULT 'unknown'
        CHECK (reviewer_kind IN ('human','agent','unknown')),
    branch TEXT,
    pr_url TEXT,
    pr_number INTEGER,
    pr_repo TEXT,
    pr_state TEXT NOT NULL DEFAULT 'none'
        CHECK (pr_state IN ('none','draft','open','merged','closed')),
    pr_review_state TEXT NOT NULL DEFAULT 'none'
        CHECK (pr_review_state IN ('none','pending','changes_requested','approved')),
    pr_waived INTEGER NOT NULL DEFAULT 0,
    created_by TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    closed_at TEXT,
    UNIQUE (project_id, key)
);
INSERT INTO task_v13(
    id, project_id, key, task_type, parent_id, title, description, status,
    priority, owner, assignee, assignee_kind, reviewer, reviewer_kind, branch,
    pr_url, pr_number, pr_repo, pr_state, pr_review_state, pr_waived,
    created_by, created_at, updated_at, closed_at
)
SELECT
    id, project_id, key, task_type, parent_id, title, description, status,
    priority, owner, assignee, assignee_kind, reviewer, reviewer_kind, branch,
    pr_url, pr_number, pr_repo, pr_state, pr_review_state, pr_waived,
    created_by, created_at, updated_at, closed_at
FROM task;
DROP TABLE task;
ALTER TABLE task_v13 RENAME TO task;
CREATE INDEX idx_task_project ON task(project_id, status);
CREATE INDEX idx_task_parent ON task(parent_id);
CREATE INDEX idx_task_type ON task(project_id, task_type);
""")
    conn.commit()
    conn.execute("PRAGMA foreign_keys = ON")
    return ["enabled the bug and iteration task types"]


def _upgrade_bug_template_workflows(conn: Conn) -> list[str]:
    """Give every existing template a Bug workflow copied from its Story flow."""
    added = 0
    for template in conn.execute("SELECT id FROM template ORDER BY id").fetchall():
        exists = conn.execute(
            "SELECT id FROM template_workflow WHERE template_id = ? AND task_type = 'bug'",
            (template["id"],),
        ).fetchone()
        story = conn.execute(
            "SELECT * FROM template_workflow WHERE template_id = ? AND task_type = 'story'",
            (template["id"],),
        ).fetchone()
        if exists is not None or story is None:
            continue
        bug_id = conn.insert_returning_id(
            "INSERT INTO template_workflow(template_id, task_type, name, description) "
            "VALUES(?, 'bug', ?, ?)",
            (template["id"], "bug flow", story["description"]),
        )
        conn.execute(
            "INSERT INTO template_status(template_workflow_id, slug, display, category, "
            "position, satisfies_dependency, is_initial, is_terminal, description) "
            "SELECT ?, slug, display, category, position, satisfies_dependency, "
            "is_initial, is_terminal, description FROM template_status "
            "WHERE template_workflow_id = ?",
            (bug_id, story["id"]),
        )
        conn.execute(
            "INSERT INTO template_transition(template_workflow_id, from_status, to_status, "
            "gates, note) SELECT ?, from_status, to_status, gates, note "
            "FROM template_transition WHERE template_workflow_id = ?",
            (bug_id, story["id"]),
        )
        added += 1
    conn.commit()
    return [f"added Bug workflows to {added} template(s)"] if added else []


def _upgrade_iteration_template_workflows(conn: Conn) -> list[str]:
    """Give existing templates the shipped three-state Iteration flow."""
    added = 0
    for template in conn.execute("SELECT id FROM template ORDER BY id").fetchall():
        exists = conn.execute(
            "SELECT id FROM template_workflow WHERE template_id=? AND task_type='iteration'",
            (template["id"],),
        ).fetchone()
        if exists is not None:
            continue
        wf_id = conn.insert_returning_id(
            "INSERT INTO template_workflow(template_id,task_type,name,description) "
            "VALUES(?,'iteration','iteration flow','Parallel unit of work')",
            (template["id"],),
        )
        conn.executemany(
            "INSERT INTO template_status(template_workflow_id,slug,display,category,position,"
            "satisfies_dependency,is_initial,is_terminal,description) VALUES(?,?,?,?,?,?,?,?,?)",
            [
                (wf_id, "planned", "Planned", "backlog", 0, 0, 1, 0, ""),
                (wf_id, "open", "Open", "active", 1, 0, 0, 0, ""),
                (wf_id, "closed", "Closed", "done", 2, 1, 0, 1, ""),
            ],
        )
        conn.executemany(
            "INSERT INTO template_transition(template_workflow_id,from_status,to_status,gates,note) "
            "VALUES(?,?,?,?,?)",
            [
                (wf_id, "planned", "open", "", "iteration.opened"),
                (wf_id, "open", "closed", "iteration_members_finished,iteration_comments_closed", "iteration.closed"),
                (wf_id, "closed", "open", "iteration_members_finished", "iteration.reopened"),
            ],
        )
        added += 1
    conn.commit()
    return [f"added Iteration workflows to {added} template(s)"] if added else []


def _upgrade_iteration_feedback_flow(conn: Conn) -> list[str]:
    """Add all-severity comment closure policy to existing Iteration flows."""
    changed = 0
    for workflow_table, transition_table, fk in (
        ("template_workflow", "template_transition", "template_workflow_id"),
        ("workflow", "workflow_transition", "workflow_id"),
    ):
        workflows = conn.execute(
            f"SELECT id FROM {workflow_table} WHERE task_type = 'iteration'"
        ).fetchall()
        for workflow_row in workflows:
            workflow_id = workflow_row["id"]
            close = conn.execute(
                f"SELECT id, gates FROM {transition_table} "
                f"WHERE {fk} = ? AND from_status = 'open' AND to_status = 'closed'",
                (workflow_id,),
            ).fetchone()
            if close is not None:
                gates = [g.strip() for g in (close["gates"] or "").split(",") if g.strip()]
                if "iteration_comments_closed" not in gates:
                    gates.append("iteration_comments_closed")
                    conn.execute(
                        f"UPDATE {transition_table} SET gates = ? WHERE id = ?",
                        (",".join(gates), close["id"]),
                    )
                    changed += 1
    conn.commit()
    return [f"gated Iteration closure in {changed} transition(s)"] if changed else []


def _add_column(conn: Conn, table: str, column: str, decl: str) -> None:
    """`CREATE TABLE IF NOT EXISTS` cannot add a column to a table that already
    exists, so a new one needs an explicit ALTER."""
    try:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
        conn.commit()
    except Exception:
        conn.rollback()  # already there


def _backfill_task_creators(conn: Conn) -> list[str]:
    """Recover task creator attribution from the original creation event."""
    updated = 0
    tasks = conn.execute(
        "SELECT id FROM task WHERE created_by IS NULL OR created_by = ''"
    ).fetchall()
    for task in tasks:
        event = conn.execute(
            "SELECT actor FROM event "
            "WHERE task_id = ? AND kind = 'created' "
            "AND actor IS NOT NULL AND actor <> '' "
            "ORDER BY id LIMIT 1",
            (task["id"],),
        ).fetchone()
        if event is None:
            continue
        conn.execute(
            "UPDATE task SET created_by = ? WHERE id = ?",
            (event["actor"], task["id"]),
        )
        updated += 1
    conn.commit()
    return [f"attributed creators for {updated} task(s)"] if updated else []


def _adopt_default_template(conn: Conn) -> list[str]:
    """Point projects that predate templates at the default one."""
    from . import templates

    tpl = templates.default(conn)
    cur = conn.execute(
        "UPDATE project SET template_id = ? WHERE template_id IS NULL", (tpl["id"],)
    )
    conn.commit()
    n = cur.rowcount or 0
    return [f"{n} project(s) adopted the '{tpl['slug']}' template"] if n else []


def _seed_missing_workflows(conn: Conn) -> list[str]:
    """Give every project the built-in flow it does not yet have."""
    from . import workflow

    notes = []
    for proj in conn.execute("SELECT id, slug FROM project ORDER BY id").fetchall():
        before = conn.execute(
            "SELECT COUNT(*) AS n FROM workflow WHERE project_id = ?", (proj["id"],)
        ).fetchone()["n"]
        workflow.seed_all(conn, proj["id"])
        after = conn.execute(
            "SELECT COUNT(*) AS n FROM workflow WHERE project_id = ?", (proj["id"],)
        ).fetchone()["n"]
        if after > before:
            notes.append(f"seeded {after - before} workflow(s) for project '{proj['slug']}'")
    return notes


def _upgrade_feature_review_flow(conn: Conn) -> list[str]:
    """Add the missing scope-review path to shipped software-delivery flows."""
    transitions = [
        ("created", "in_review", ""),
        ("incomplete", "in_review", ""),
        ("in_review", "ready", "review_threads_closed"),
        ("in_review", "incomplete", ""),
    ]
    template_rows = conn.execute(
        "SELECT w.id FROM template_workflow w "
        "JOIN template t ON t.id = w.template_id "
        "WHERE t.slug = 'software-delivery' AND w.task_type = 'feature'"
    ).fetchall()
    project_rows = conn.execute(
        "SELECT w.id FROM workflow w "
        "JOIN project p ON p.id = w.project_id "
        "JOIN template t ON t.id = p.template_id "
        "WHERE t.slug = 'software-delivery' AND w.task_type = 'feature'"
    ).fetchall()
    for row in template_rows:
        conn.executemany(
            "INSERT INTO template_transition(template_workflow_id, from_status, "
            "to_status, gates) VALUES(?,?,?,?) "
            "ON CONFLICT(template_workflow_id, from_status, to_status) DO NOTHING",
            [(row["id"], source, target, gates) for source, target, gates in transitions],
        )
    for row in project_rows:
        conn.executemany(
            "INSERT INTO workflow_transition(workflow_id, from_status, to_status, gates) "
            "VALUES(?,?,?,?) "
            "ON CONFLICT(workflow_id, from_status, to_status) DO NOTHING",
            [(row["id"], source, target, gates) for source, target, gates in transitions],
        )
    conn.commit()
    count = len(template_rows) + len(project_rows)
    return [f"enabled feature scope review in {count} workflow(s)"] if count else []


def _read_v2(conn: Conn) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for name in _V2_TABLES:
        if not conn.table_exists(name):
            out[name] = []
            continue
        out[name] = [dict(r) for r in conn.execute(f"SELECT * FROM {name}").fetchall()]
    return out


def _insert_project(conn: Conn, slug: str, spec: StoreSpec | None = None,
                    name: str | None = None, description: str = "",
                    template: str | None = None) -> int:
    from . import templates

    templates.install_builtins(conn)
    tpl = templates.require(conn, template) if template else templates.default(conn)
    ts = utcnow()
    repo = str(spec.backlog_dir.parent) if spec and spec.backlog_dir else None
    return conn.insert_returning_id(
        "INSERT INTO project(template_id, slug, name, description, status, repo_path, "
        "created_at, updated_at) VALUES(?,?,?,?,'active',?,?,?)",
        (tpl["id"], slug, name or slug, description, repo, ts, ts),
    )


def _load_v2_into_v3(conn: Conn, project_id: int, old: dict[str, list[dict]]) -> list[str]:
    """Copy a v2 dataset into the v3 shape. Key -> new task id throughout."""
    notes: list[str] = []
    ids: dict[str, int] = {}

    def add_task(row: dict, task_type: str, parent_key: str | None) -> None:
        ts = row.get("created_at") or utcnow()
        status = (row.get("status") or "created")
        if task_type == "feature":
            status = _FEATURE_STATUS_IN.get(status, status)
        ids[row["key"]] = conn.insert_returning_id(
            "INSERT INTO task(project_id, key, task_type, parent_id, title, description, "
            "status, priority, owner, assignee, assignee_kind, reviewer, reviewer_kind, "
            "branch, pr_url, pr_number, pr_repo, pr_state, pr_review_state, pr_waived, "
            "created_at, updated_at, closed_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                project_id, row["key"], task_type,
                ids.get(parent_key) if parent_key else None,
                row.get("title") or row["key"], row.get("description") or "",
                status, row.get("priority") or "P2", row.get("owner"),
                row.get("assignee"), actor_kind(row.get("assignee")),
                row.get("reviewer"), actor_kind(row.get("reviewer")),
                row.get("branch"), row.get("pr_url"), row.get("pr_number"),
                row.get("pr_repo"), row.get("pr_state") or "none",
                row.get("pr_review_state") or "none", row.get("pr_waived") or 0,
                ts, row.get("updated_at") or ts, row.get("closed_at"),
            ),
        )

    for f in sorted(old["feature"], key=lambda r: r["key"]):
        add_task(f, "feature", None)
    items = {r["key"]: r for r in old["item"]}
    for r in sorted(old["item"], key=lambda r: r["key"]):
        if r.get("kind") == "story":
            add_task(r, "story", r.get("parent_key"))
    for r in sorted(old["item"], key=lambda r: r["key"]):
        if r.get("kind") == "subtask":
            add_task(r, "subtask", r.get("parent_key"))
    notes.append(f"{len(ids)} tasks migrated")

    # acceptance criteria text -> one task_item per line
    ac_rows = []
    ts = utcnow()
    for r in old["item"]:
        text = (r.get("acceptance_criteria") or "").strip()
        if not text or r["key"] not in ids:
            continue
        for pos, line in enumerate(l for l in text.splitlines() if l.strip()):
            ac_rows.append((ids[r["key"]], "acceptance_criteria", pos, line.strip(),
                            0, ts, ts, "migration"))
    conn.executemany(
        "INSERT INTO task_item(task_id, kind, position, content, done, created_at, "
        "updated_at, created_by) VALUES(?,?,?,?,?,?,?,?)",
        ac_rows,
    )
    if ac_rows:
        notes.append(f"{len(ac_rows)} acceptance-criteria lines split into task_item rows")

    dep_rows = [
        (ids[d["from_key"]], ids[d["to_key"]], d["kind"], d.get("note") or "",
         d.get("external_id"), d.get("created_at") or ts, d.get("created_by"))
        for d in old["dependency"]
        if d.get("from_key") in ids and d.get("to_key") in ids
    ]
    conn.executemany(
        "INSERT INTO dependency(from_task_id, to_task_id, kind, note, external_id, "
        "created_at, created_by) VALUES(?,?,?,?,?,?,?)",
        dep_rows,
    )
    dropped = len(old["dependency"]) - len(dep_rows)
    notes.append(f"{len(dep_rows)} dependencies migrated"
                 + (f" ({dropped} dropped: endpoint missing)" if dropped else ""))

    conn.executemany(
        "INSERT INTO artifact(task_id, rel_path, title, kind, created_at, created_by) "
        "VALUES(?,?,?,?,?,?)",
        [(ids[a["entity_key"]], a["rel_path"], a.get("title") or "", a.get("kind") or "doc",
          a.get("created_at") or ts, a.get("created_by"))
         for a in old["artifact"] if a.get("entity_key") in ids],
    )
    conn.executemany(
        "INSERT INTO review_thread(task_id, root_key, state, resolution, title, file_path, "
        "line, last_comment_key, comment_count, opened_by, opened_at, updated_at, "
        "closed_by, closed_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [(ids[t["target_key"]], t["root_key"], t["state"], t.get("resolution"),
          t.get("title") or "", t.get("file_path"), t.get("line"), t["last_comment_key"],
          t.get("comment_count") or 1, t["opened_by"], t["opened_at"], t["updated_at"],
          t.get("closed_by"), t.get("closed_at"))
         for t in old["review_thread"] if t.get("target_key") in ids],
    )
    conn.executemany(
        "INSERT INTO review_comment(task_id, key, root_key, parent_key, seq, author, "
        "author_kind, role, action, body, file_path, line, created_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [(ids[c["target_key"]], c["key"], c["root_key"], c.get("parent_key"), c["seq"],
          c["author"], actor_kind(c["author"]), c["role"], c["action"], c["body"],
          c.get("file_path"), c.get("line"), c["created_at"])
         for c in old["review_comment"] if c.get("target_key") in ids],
    )
    conn.executemany(
        "INSERT INTO event(ts, project_id, task_id, entity_key, actor, actor_kind, kind, "
        "from_value, to_value, detail) VALUES(?,?,?,?,?,?,?,?,?,?)",
        [(e["ts"], project_id, ids.get(e.get("entity_key")), e.get("entity_key") or "",
          e.get("actor"), actor_kind(e.get("actor")), e["kind"], e.get("from_value"),
          e.get("to_value"), e.get("detail") or "")
         for e in old["event"]],
    )
    conn.executemany(
        "INSERT INTO key_counter(project_id, prefix, next_value) VALUES(?,?,?)",
        [(project_id, k["prefix"], k["next_value"]) for k in old["key_counter"]],
    )
    conn.commit()
    notes.append(f"{len(old['event'])} history events carried over")
    return notes


def _upgrade_required_validation_gates(conn: Conn) -> list[str]:
    """Make executable requirements part of every acceptance transition."""
    changed = 0
    for table in ("template_transition", "workflow_transition"):
        rows = conn.execute(
            f"SELECT id, gates FROM {table} WHERE to_status = 'accepted'"
        ).fetchall()
        for row in rows:
            gates = [g.strip() for g in (row["gates"] or "").split(",") if g.strip()]
            if "required_validations_pass" in gates:
                continue
            gates.append("required_validations_pass")
            conn.execute(
                f"UPDATE {table} SET gates = ? WHERE id = ?",
                (",".join(gates), row["id"]),
            )
            changed += 1
    conn.commit()
    return [f"added required validation gate to {changed} acceptance transition(s)"] \
        if changed else []


def load_v2_export(conn: Conn, project_id: int, tables: dict[str, list[dict]]) -> list[str]:
    """Public entry point for importing a v2 JSON dump into a v3 store."""
    old = {name: tables.get(name, []) for name in _V2_TABLES}
    notes = _load_v2_into_v3(conn, project_id, old)
    resync_sequences(conn)
    return notes


# --------------------------------------------------------------------------- #
# init / projects / sequences
# --------------------------------------------------------------------------- #

REPO_README = (
    "# .backlog\n\n"
    "Active development backlog for this repository: projects, tasks (features,\n"
    "stories and subtasks), their dependencies, acceptance criteria and\n"
    "checklists, assignments, status, review threads and PR links.\n\n"
    "- `backlog.db` — SQLite store. **Committed to git.** Never hand-edit it.\n"
    "- `artifacts/<KEY>/` — design notes, specs, logs attached to a task.\n\n"
    "Drive it with the `backlog` skill CLI, not by opening the database:\n\n"
    "```\n"
    "~/.claude/skills/backlog/bin/backlog board\n"
    "~/.claude/skills/backlog/bin/backlog next --actor developer\n"
    "```\n\n"
    "Set `BACKLOG_DB` to move this backlog into a central file or a shared\n"
    "PostgreSQL server; `backlog where` reports which store is in use.\n"
)


def init_store(root: Path, force: bool = False, spec: StoreSpec | None = None) -> StoreSpec:
    spec = spec or resolve_spec(root, for_init=True)

    if spec.dialect == POSTGRES:
        conn = _connect_postgres(spec, create=True)
        _check_version(conn, spec)
        get_or_create_project(conn, spec.project, spec)
        conn.close()
        spec.artifacts_dir.mkdir(parents=True, exist_ok=True)
        return spec

    assert spec.db_path is not None
    spec.db_path.parent.mkdir(parents=True, exist_ok=True)
    spec.artifacts_dir.mkdir(parents=True, exist_ok=True)
    keep = spec.artifacts_dir / ".gitkeep"
    if not keep.exists():
        keep.write_text("")

    conn = _connect_sqlite(spec, create=True)
    _check_version(conn, spec)
    get_or_create_project(conn, spec.project, spec)
    conn.close()

    if spec.scope == "repo":
        home = spec.backlog_dir or spec.db_path.parent
        gitattributes = home / ".gitattributes"
        if not gitattributes.exists():
            gitattributes.write_text(
                "# The backlog database is a binary file: never let git try to merge it.\n"
                "# On conflict, resolve with `backlog export` / `backlog import --replace`.\n"
                "backlog.db binary -diff merge=binary\n"
                "backlog.db-wal binary -diff\n"
                "backlog.db-shm binary -diff\n"
            )
        gitignore = home / ".gitignore"
        if not gitignore.exists():
            gitignore.write_text("backlog.db-wal\nbacklog.db-shm\nbacklog.db-journal\n")
        readme = home / "README.md"
        if not readme.exists():
            readme.write_text(REPO_README)
    return spec


def get_project(conn: Conn, slug: str) -> Row | None:
    return conn.execute("SELECT * FROM project WHERE slug = ?", (slugify(slug),)).fetchone()


def get_or_create_project(conn: Conn, slug: str, spec: StoreSpec | None = None,
                          name: str | None = None, description: str = "",
                          template: str | None = None) -> Row:
    slug = slugify(slug)
    row = get_project(conn, slug)
    if row is not None:
        return row
    project_id = _insert_project(conn, slug, spec, name, description, template)
    conn.commit()
    from . import workflow

    workflow.seed_all(conn, project_id)
    row = get_project(conn, slug)
    assert row is not None
    return row


def require_project(conn: Conn, slug: str) -> Row:
    row = get_project(conn, slug)
    if row is None:
        raise BacklogError(
            f"no project '{slugify(slug)}' in this store. "
            f"Create it with `backlog project add --name '{slug}'`, or run `backlog projects`."
        )
    return row


def list_projects(conn: Conn) -> list[Row]:
    return conn.execute(
        "SELECT p.*, "
        "  (SELECT COUNT(*) FROM task t WHERE t.project_id = p.id) AS tasks, "
        "  (SELECT COUNT(*) FROM task t WHERE t.project_id = p.id "
        "     AND t.status NOT IN ('accepted','done')) AS open_tasks "
        "FROM project p ORDER BY p.slug"
    ).fetchall()


_SERIAL_TABLES = ["template", "template_workflow", "template_status",
                  "template_transition", "project", "workflow", "workflow_status",
                  "workflow_transition", "task", "retrospective_action", "task_item",
                  "execution_result",
                  "validation_waiver", "dependency", "artifact",
                  "review_thread", "review_comment", "event"]


def resync_sequences(conn: Conn) -> list[str]:
    """Advance each SERIAL past ids that were inserted explicitly.

    PostgreSQL does not bump a sequence when a row supplies its own id, so a
    restore leaves every sequence at 1 and the next insert collides. SQLite's
    AUTOINCREMENT maintains `sqlite_sequence` on insert, so this is a no-op.
    """
    if conn.dialect != POSTGRES:
        return []
    with_id = {
        r["table_name"]
        for r in conn.execute(
            "SELECT table_name FROM information_schema.columns "
            "WHERE table_schema = current_schema() AND column_name = 'id'"
        ).fetchall()
    }
    moved = []
    for table in _SERIAL_TABLES:
        if table not in with_id:
            continue
        seq = conn.execute("SELECT pg_get_serial_sequence(?, 'id') AS seq", (table,)).fetchone()
        if seq is None or not seq["seq"]:
            continue
        top = conn.execute(f"SELECT MAX(id) AS m FROM {table}").fetchone()["m"]
        if top is None:
            continue
        conn.execute("SELECT setval(?, ?, true)", (seq["seq"], int(top)))
        moved.append(f"{table}.id -> {top}")
    conn.commit()
    return moved


# --------------------------------------------------------------------------- #
# shared helpers
# --------------------------------------------------------------------------- #

def next_key(conn: Conn, project_id: int, prefix: str) -> str:
    conn.execute(
        "INSERT INTO key_counter(project_id, prefix, next_value) VALUES(?,?,1) "
        "ON CONFLICT(project_id, prefix) DO NOTHING",
        (project_id, prefix),
    )
    row = conn.execute(
        "SELECT next_value FROM key_counter WHERE project_id = ? AND prefix = ?",
        (project_id, prefix),
    ).fetchone()
    n = int(row["next_value"])
    conn.execute(
        "UPDATE key_counter SET next_value = ? WHERE project_id = ? AND prefix = ?",
        (n + 1, project_id, prefix),
    )
    return f"{prefix}-{n:03d}"


def next_comment_key(conn: Conn) -> str:
    """Allocate a review-comment key that is unique across the whole store.

    Task keys are scoped to a project, but review comments are addressed
    without a project qualifier and their schema-level key is global. The
    counter therefore lives in ``meta``. The initial insert also serializes
    concurrent allocators: PostgreSQL locks the conflicting row, while SQLite
    takes its write lock.
    """
    counter = "review_comment_next_value"
    conn.execute(
        "INSERT INTO meta(key, value) VALUES(?, '1') ON CONFLICT(key) DO NOTHING",
        (counter,),
    )
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (counter,)).fetchone()
    highest = conn.execute(
        "SELECT MAX(CAST(SUBSTR(key, 3) AS INTEGER)) AS n "
        "FROM review_comment WHERE key LIKE 'C-%'"
    ).fetchone()
    n = max(int(row["value"]), int(highest["n"] or 0) + 1)
    conn.execute("UPDATE meta SET value = ? WHERE key = ?", (str(n + 1), counter))
    return f"C-{n:03d}"


def log_event(
    conn: Conn,
    kind: str,
    project_id: int | None = None,
    task_id: int | None = None,
    entity_key: str = "",
    actor: str | None = None,
    from_value: str | None = None,
    to_value: str | None = None,
    detail: str = "",
) -> None:
    conn.execute(
        "INSERT INTO event(ts, project_id, task_id, entity_key, actor, actor_kind, kind, "
        "from_value, to_value, detail) VALUES(?,?,?,?,?,?,?,?,?,?)",
        (utcnow(), project_id, task_id, entity_key, actor, actor_kind(actor), kind,
         from_value, to_value, detail),
    )

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

import re
import sqlite3
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from ..schema import KNOWN_AGENTS

if TYPE_CHECKING:
    from .resolution import StoreSpec

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
        if not in_str and ch == "-" and script[i : i + 2] == "--":
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
                "SELECT 1 AS ok FROM sqlite_master WHERE type='table' AND name = ?",
                (name,),
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
    import psycopg

    return sqlite3.DatabaseError, psycopg.Error


# --------------------------------------------------------------------------- #

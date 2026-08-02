"""SQLite and PostgreSQL connection creation."""

from __future__ import annotations

import os
import sqlite3
import time
from pathlib import Path

from .common import POSTGRES, SQLITE, BacklogError, Conn
from .resolution import StoreSpec, resolve_spec

# --------------------------------------------------------------------------- #

def connect_sqlite(spec: StoreSpec, create: bool = False) -> Conn:
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


def connect_postgres(spec: StoreSpec, create: bool = False) -> Conn:
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
    conn = (connect_postgres(spec, create) if spec.dialect == POSTGRES
            else connect_sqlite(spec, create))
    from .migrations import check_version

    check_version(conn, spec)
    return conn


# --------------------------------------------------------------------------- #

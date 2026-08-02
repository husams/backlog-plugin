"""SQLite and PostgreSQL connection creation."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from .common import POSTGRES, SQLITE, BacklogError, Conn
from .resolution import StoreSpec, resolve_spec

# --------------------------------------------------------------------------- #


def connect_sqlite(spec: StoreSpec, create: bool = False) -> Conn:
    assert spec.db_path is not None
    if not spec.db_path.exists():
        if not create:
            raise BacklogError(
                f"{spec.db_path} does not exist. Run `backlog init` first."
            )
        spec.db_path.parent.mkdir(parents=True, exist_ok=True)
    raw = sqlite3.connect(spec.db_path)
    raw.row_factory = sqlite3.Row
    raw.execute("PRAGMA foreign_keys = ON")
    return Conn(raw, SQLITE, spec)


def connect_postgres(spec: StoreSpec, create: bool = False) -> Conn:
    try:
        import psycopg
        from psycopg.rows import dict_row
    except ImportError:  # pragma: no cover - depends on install extras
        raise BacklogError(
            "The selected backend is PostgreSQL but the psycopg driver is not installed.\n"
            "  install it with the `postgres` extra."
        ) from None
    try:
        raw = psycopg.connect(spec.dsn, row_factory=dict_row, autocommit=False)
    except psycopg.OperationalError as exc:
        raise BacklogError(
            f"cannot reach the backlog store at {spec.location}: {exc}"
        ) from None
    cur = raw.cursor()
    cur.execute(f'CREATE SCHEMA IF NOT EXISTS "{spec.schema}"')
    cur.execute(f'SET search_path TO "{spec.schema}"')
    raw.commit()
    return Conn(raw, POSTGRES, spec)


def connect(
    backlog_dir: Path | None = None, spec: StoreSpec | None = None, create: bool = False
) -> Conn:
    spec = spec or resolve_spec()
    conn = (
        connect_postgres(spec, create)
        if spec.dialect == POSTGRES
        else connect_sqlite(spec, create)
    )
    from .migrations import check_version

    check_version(conn, spec)
    return conn


# --------------------------------------------------------------------------- #

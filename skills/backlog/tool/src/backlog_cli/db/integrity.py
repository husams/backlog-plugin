"""Structural agreement between the recorded schema version and the store.

`schema_version` says which shape the store is supposed to have. Nothing used
to check that it actually has it, so a migration whose DDL silently failed left
a store that reported a version it did not implement. These helpers compare the
live catalogue against the shipped DDL and repair the difference.
"""

from __future__ import annotations

import re

from ..schema import SCHEMA_SQL
from .common import Conn, split_statements

_CREATE_TABLE = re.compile(
    r"^CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*\((.*)\)\s*$",
    re.I | re.S,
)

# A line in a CREATE TABLE body that declares a constraint, not a column.
_CONSTRAINT = re.compile(
    r"^(?:UNIQUE|CHECK|PRIMARY\s+KEY|FOREIGN\s+KEY|CONSTRAINT)\b", re.I
)


def _split_top_level(body: str) -> list[str]:
    """Split a CREATE TABLE body on commas that are not inside parentheses."""
    parts: list[str] = []
    buf: list[str] = []
    depth = 0
    in_str = False
    for ch in body:
        if ch == "'":
            in_str = not in_str
        if not in_str:
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
            elif ch == "," and depth == 0:
                parts.append("".join(buf))
                buf = []
                continue
        buf.append(ch)
    parts.append("".join(buf))
    return parts


def _strip_comments(text: str) -> str:
    return "\n".join(line.split("--", 1)[0] for line in text.splitlines())


def expected_schema() -> dict[str, dict[str, str]]:
    """`{table: {column: declaration}}` as the shipped DDL defines it."""
    tables: dict[str, dict[str, str]] = {}
    for statement in split_statements(_strip_comments(SCHEMA_SQL)):
        match = _CREATE_TABLE.match(statement.strip())
        if match is None:
            continue
        table, body = match.group(1), match.group(2)
        columns: dict[str, str] = {}
        for part in _split_top_level(body):
            decl = " ".join(part.split())
            if not decl or _CONSTRAINT.match(decl):
                continue
            name, _, rest = decl.partition(" ")
            columns[name] = rest.strip()
        tables[table] = columns
    return tables


def schema_drift(conn: Conn) -> list[str]:
    """Tables and columns the recorded schema version promises but the store
    does not have. Empty means the store implements the version it reports."""
    problems: list[str] = []
    for table, columns in sorted(expected_schema().items()):
        if not conn.table_exists(table):
            problems.append(f"table {table} is missing")
            continue
        present = conn.columns(table)
        for column in columns:
            if column not in present:
                problems.append(f"column {table}.{column} is missing")
    return problems


def repair_schema(conn: Conn, spec) -> list[str]:
    """Bring a store that under-implements its recorded version back into
    shape, without hand-written SQL.

    Creates whatever tables are missing, adds whatever columns are missing, and
    re-runs the additive data upgrades. Every step is idempotent, so repairing
    an already-healthy store changes nothing.
    """
    from .common import BacklogError
    from .migrations import migrate
    from .upgrades import add_column

    if not schema_drift(conn):
        return ["store already matches its recorded schema version"]

    notes: list[str] = []
    conn.executescript(SCHEMA_SQL)  # whatever tables are missing
    conn.commit()
    for table, columns in sorted(expected_schema().items()):
        for column, decl in columns.items():
            if not conn.column_exists(table, column):
                add_column(conn, table, column, decl)
                notes.append(f"added {table}.{column}")

    # 13 skips the pre-v13 table rebuilds (which this store is past) while
    # still re-running every additive upgrade from v13 onwards.
    notes += migrate(conn, 13, spec)

    remaining = schema_drift(conn)
    if remaining:
        raise BacklogError(
            "repair could not bring the store up to its recorded schema version:\n"
            + "\n".join(f"  - {problem}" for problem in remaining)
        )
    return notes

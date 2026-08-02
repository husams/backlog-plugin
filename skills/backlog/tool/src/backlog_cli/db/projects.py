"""Store initialization, projects, sequences, and audit helpers."""

from __future__ import annotations

from pathlib import Path

from .common import (
    POSTGRES,
    BacklogError,
    Conn,
    Row,
    actor_kind,
    utcnow,
)
from .connection import connect_sqlite, connect_postgres
from .legacy import insert_project
from .migrations import check_version
from .resolution import StoreSpec, resolve_spec, slugify

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


def init_store(
    root: Path, force: bool = False, spec: StoreSpec | None = None
) -> StoreSpec:
    spec = spec or resolve_spec(root, for_init=True)

    if spec.dialect == POSTGRES:
        conn = connect_postgres(spec, create=True)
        check_version(conn, spec)
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

    conn = connect_sqlite(spec, create=True)
    check_version(conn, spec)
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
    return conn.execute(
        "SELECT * FROM project WHERE slug = ?", (slugify(slug),)
    ).fetchone()


def get_or_create_project(
    conn: Conn,
    slug: str,
    spec: StoreSpec | None = None,
    name: str | None = None,
    description: str = "",
    template: str | None = None,
) -> Row:
    slug = slugify(slug)
    row = get_project(conn, slug)
    if row is not None:
        return row
    project_id = insert_project(conn, slug, spec, name, description, template)
    conn.commit()
    from .. import workflow

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


_SERIAL_TABLES = [
    "template",
    "template_workflow",
    "template_status",
    "template_transition",
    "project",
    "workflow",
    "workflow_status",
    "workflow_transition",
    "task",
    "retrospective_action",
    "task_item",
    "execution_result",
    "validation_waiver",
    "dependency",
    "artifact",
    "review_thread",
    "review_comment",
    "event",
]


def resync_sequences(conn: Conn) -> list[str]:
    """Advance each SERIAL past ids that were inserted explicitly.

    PostgreSQL does not bump a sequence when a row supplies its own id, so a
    restore leaves every sequence at 1 and the next insert collides. SQLite's
    AUTOINCREMENT maintains `sqlite_sequence` on insert, so this is a no-op.
    """
    if conn.dialect != POSTGRES:
        return []
    moved = []
    for table in _SERIAL_TABLES:
        seq = conn.execute(
            "SELECT pg_get_serial_sequence(?, 'id') AS seq", (table,)
        ).fetchone()
        assert seq is not None and seq["seq"]
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
        (
            utcnow(),
            project_id,
            task_id,
            entity_key,
            actor,
            actor_kind(actor),
            kind,
            from_value,
            to_value,
            detail,
        ),
    )

"""Additive database upgrades grouped by responsibility."""

from __future__ import annotations

from ..common import Conn


def upgrade_bug_task_constraint(conn: Conn) -> list[str]:
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


def add_column(conn: Conn, table: str, column: str, decl: str) -> None:
    """`CREATE TABLE IF NOT EXISTS` cannot add a column to a table that already
    exists, so a new one needs an explicit ALTER."""
    try:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
        conn.commit()
    except Exception:
        conn.rollback()  # already there


def backfill_task_creators(conn: Conn) -> list[str]:
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


def upgrade_todo_task_items(conn: Conn) -> list[str]:
    """Allow dedicated todo items and retain their latest mutation actor."""
    add_column(conn, "task_item", "updated_by", "TEXT")
    conn.execute(
        "UPDATE task_item SET updated_by=created_by WHERE updated_by IS NULL"
    )
    conn.commit()
    if conn.is_postgres:
        conn.execute(
            "ALTER TABLE task_item DROP CONSTRAINT IF EXISTS task_item_kind_check"
        )
        conn.execute(
            "ALTER TABLE task_item ADD CONSTRAINT task_item_kind_check "
            "CHECK (kind IN ('acceptance_criteria','checklist','note','todo'))"
        )
        conn.commit()
        return ["enabled ordered todo task items"]

    conn.execute("PRAGMA foreign_keys = OFF")
    conn.executescript("""
CREATE TABLE task_item_v18 (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER NOT NULL REFERENCES task(id) ON DELETE CASCADE,
    kind TEXT NOT NULL CHECK (kind IN ('acceptance_criteria','checklist','note','todo')),
    position INTEGER NOT NULL DEFAULT 0,
    content TEXT NOT NULL,
    done INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    created_by TEXT,
    updated_by TEXT
);
INSERT INTO task_item_v18(
    id,task_id,kind,position,content,done,created_at,updated_at,created_by,updated_by
)
SELECT
    id,task_id,kind,position,content,done,created_at,updated_at,created_by,updated_by
FROM task_item;
DROP TABLE task_item;
ALTER TABLE task_item_v18 RENAME TO task_item;
CREATE INDEX idx_item_task ON task_item(task_id,kind,position);
""")
    conn.commit()
    conn.execute("PRAGMA foreign_keys = ON")
    return ["enabled ordered todo task items"]

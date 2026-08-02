"""Schema bootstrap and migration orchestration."""

from __future__ import annotations

from ..schema import SCHEMA_SQL, SCHEMA_VERSION
from .common import BacklogError, Conn, utcnow
from .resolution import StoreSpec
from .legacy import (
    V2_TABLES,
    insert_project,
    load_v2_into_v3,
    read_v2,
    upgrade_required_validation_gates,
)
from .upgrades import (
    add_column,
    adopt_default_template,
    backfill_task_creators,
    seed_missing_workflows,
    upgrade_bug_task_constraint,
    upgrade_bug_template_workflows,
    upgrade_feature_review_flow,
    upgrade_iteration_feedback_flow,
    upgrade_iteration_retrospective_action_gate,
    upgrade_iteration_template_workflows,
)

# --------------------------------------------------------------------------- #


def check_version(conn: Conn, spec: StoreSpec) -> None:
    try:
        row = conn.execute(
            "SELECT value FROM meta WHERE key = 'schema_version'"
        ).fetchone()
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
    from .. import templates

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


def migrate(conn: Conn, from_version: int, spec: StoreSpec) -> list[str]:
    """Forward migration.

    v1/v2 (feature + item)  -> v3 (project + task)
    v3                      -> v4 (workflows as data)
    v14                     -> v15 (retrospective improvement actions)
    v15                     -> v16 (task creator attribution and separation)
    v16                     -> v17 (Iteration retrospective-action closure gate)
    """
    notes: list[str] = []
    if from_version >= 3 or not conn.table_exists("feature"):
        add_column(conn, "task", "created_by", "TEXT")
        if from_version < 13:
            notes += upgrade_bug_task_constraint(conn)
        notes += backfill_task_creators(conn)
        add_column(conn, "project", "template_id", "INTEGER")
        add_column(
            conn,
            "review_thread",
            "severity",
            "TEXT NOT NULL DEFAULT 'blocker' "
            "CHECK (severity IN ('blocker','nice_to_have','info'))",
        )
        add_column(conn, "execution_result", "expected_result", "TEXT")
        add_column(conn, "execution_result", "actual_result", "TEXT")
        add_column(conn, "execution_result", "hook_name", "TEXT")
        add_column(conn, "execution_result", "implementation_identity", "TEXT")
        add_column(conn, "execution_result", "actual_exit_code", "INTEGER")
        add_column(conn, "execution_result", "stdout", "TEXT NOT NULL DEFAULT ''")
        add_column(conn, "execution_result", "stderr", "TEXT NOT NULL DEFAULT ''")
        add_column(
            conn, "execution_result", "duration_ms", "INTEGER NOT NULL DEFAULT 0"
        )
        add_column(conn, "execution_result", "actor", "TEXT NOT NULL DEFAULT 'unknown'")
        # Already the task shape (or empty): additive tables plus a seeded
        # workflow for every project that does not have one yet.
        conn.executescript(SCHEMA_SQL)
        conn.execute(
            "INSERT INTO meta(key, value) VALUES('schema_version', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (str(SCHEMA_VERSION),),
        )
        conn.commit()
        from .. import templates

        added = templates.install_builtins(conn)
        if added:
            notes.append("installed templates: " + ", ".join(added))
        notes += adopt_default_template(conn)
        notes += upgrade_bug_template_workflows(conn)
        notes += upgrade_iteration_template_workflows(conn)
        notes += seed_missing_workflows(conn)
        notes += upgrade_iteration_feedback_flow(conn)
        notes += upgrade_iteration_retrospective_action_gate(conn)
        notes += upgrade_feature_review_flow(conn)
        notes += upgrade_required_validation_gates(conn)
        _resync_sequences(conn)
        return notes or ["schema brought up to date"]

    old = read_v2(conn)
    for name in V2_TABLES:
        if conn.table_exists(name):
            conn.execute(f"DROP TABLE {name}")
    conn.commit()
    conn.executescript(SCHEMA_SQL)
    conn.commit()

    project_id = insert_project(conn, spec.project, spec)
    notes.append(f"project '{spec.project}' created")
    notes += seed_missing_workflows(conn)
    notes += load_v2_into_v3(conn, project_id, old)

    conn.execute(
        "INSERT INTO meta(key, value) VALUES('schema_version', ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (str(SCHEMA_VERSION),),
    )
    conn.commit()
    _resync_sequences(conn)
    return notes


def _resync_sequences(conn: Conn) -> list[str]:
    from .projects import resync_sequences

    return resync_sequences(conn)

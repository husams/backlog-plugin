from __future__ import annotations

import json
import sqlite3

from pytest_bdd import given, parsers, scenarios, then, when

from backlog_cli.schema import SCHEMA_VERSION

from .conftest import World


scenarios("features/database_migrations.feature")


@given(parsers.parse("a real SQLite store shaped like schema version {version:d}"))
def historical_store(world: World, version: int) -> None:
    world.run("story", "add", "--title", "Migration survivor", actor="creator")
    path = world.root / ".backlog" / "backlog.db"
    conn = sqlite3.connect(path)
    try:
        conn.execute("PRAGMA foreign_keys=OFF")
        if version == 7:
            conn.execute("DROP TABLE IF EXISTS validation_waiver")
            conn.execute("DROP TABLE execution_result")
            conn.execute("DROP TABLE executable_item")
        elif version == 9:
            for column in ("actual_exit_code", "stdout", "stderr", "duration_ms"):
                conn.execute(f"ALTER TABLE execution_result DROP COLUMN {column}")
        elif version == 10:
            conn.execute("DROP TABLE validation_waiver")
            conn.execute("ALTER TABLE execution_result DROP COLUMN actor")
        elif version == 12:
            conn.execute(
                "DELETE FROM workflow WHERE task_type IN ('bug','iteration')"
            )
            conn.execute(
                "DELETE FROM template_workflow WHERE task_type IN ('bug','iteration')"
            )
        elif version == 14:
            conn.execute("DROP TABLE retrospective_action")
        elif version == 15:
            conn.execute("UPDATE task SET created_by=NULL")
        elif version == 16:
            for table in ("template_transition", "workflow_transition"):
                rows = conn.execute(
                    f"SELECT id,gates FROM {table} WHERE to_status='accepted'"
                ).fetchall()
                for row_id, gates in rows:
                    reduced = ",".join(
                        gate
                        for gate in (gates or "").split(",")
                        if gate != "required_validations_pass"
                    )
                    conn.execute(
                        f"UPDATE {table} SET gates=? WHERE id=?", (reduced, row_id)
                    )
        conn.execute(
            "UPDATE meta SET value=? WHERE key='schema_version'", (str(version),)
        )
        conn.commit()
    finally:
        conn.close()


@when("the backlog opens the historical store")
def open_historical_store(world: World) -> None:
    world.run("doctor")


@then("the database schema is current")
def schema_is_current(world: World) -> None:
    path = world.root / ".backlog" / "backlog.db"
    conn = sqlite3.connect(path)
    try:
        version = conn.execute(
            "SELECT value FROM meta WHERE key='schema_version'"
        ).fetchone()[0]
        assert int(version) == SCHEMA_VERSION
        assert conn.execute(
            "SELECT created_by FROM task WHERE key='S-001'"
        ).fetchone()[0] == "creator"
    finally:
        conn.close()


@then("current task types and gates are available")
def current_types_and_gates(world: World) -> None:
    bug = world.run("bug", "add", "--title", "Migrated bug", actor="creator")
    iteration = world.run(
        "iteration", "add", "--title", "Migrated iteration", actor="creator"
    )
    assert bug["task_type"] == "bug"
    assert iteration["task_type"] == "iteration"
    statuses = world.run("statuses")
    assert {"bug", "iteration"}.issubset(statuses)
    gates = world.run("workflow", "gates")
    assert "required_validations_pass" in gates["gates"]
    assert "iteration_retrospective_actions_clear" in gates["gates"]


@given("a version two export containing linked work")
def legacy_export(world: World) -> None:
    payload = {
        "format": "backlog-export",
        "schema_version": 2,
        "tables": {
            "feature": [
                {
                    "key": "F-900",
                    "title": "Legacy feature",
                    "status": "active",
                    "priority": "P1",
                    "created_at": "2024-01-01T00:00:00Z",
                    "updated_at": "2024-01-02T00:00:00Z",
                }
            ],
            "item": [
                {
                    "key": "S-900",
                    "kind": "story",
                    "parent_key": "F-900",
                    "title": "Legacy story",
                    "status": "created",
                    "acceptance_criteria": "first criterion\nsecond criterion",
                },
                {
                    "key": "T-900",
                    "kind": "subtask",
                    "parent_key": "S-900",
                    "title": "Legacy subtask",
                    "status": "created",
                },
            ],
            "dependency": [
                {
                    "from_key": "S-900",
                    "to_key": "T-900",
                    "kind": "blocks",
                    "note": "legacy link",
                },
                {
                    "from_key": "missing",
                    "to_key": "T-900",
                    "kind": "blocks",
                },
            ],
            "artifact": [
                {
                    "entity_key": "S-900",
                    "rel_path": "artifacts/S-900/evidence.txt",
                    "title": "Evidence",
                    "kind": "doc",
                }
            ],
            "review_thread": [],
            "review_comment": [],
            "event": [
                {
                    "ts": "2024-01-01T00:00:00Z",
                    "entity_key": "S-900",
                    "actor": "legacy-agent",
                    "kind": "created",
                    "to_value": "created",
                }
            ],
            "key_counter": [{"prefix": "S", "next_value": 901}],
        },
    }
    path = world.root / "legacy-v2.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    world.env["LEGACY_EXPORT"] = str(path)


@when("the legacy export is imported")
def import_legacy_export(world: World) -> None:
    world.run(
        "import",
        world.env["LEGACY_EXPORT"],
        "--as-project",
        "legacy-project",
    )


@then("the migrated legacy work is queryable")
def legacy_work_is_queryable(world: World) -> None:
    feature = world.run("--project", "legacy-project", "show", "F-900")
    story = world.run("--project", "legacy-project", "show", "S-900")
    subtask = world.run("--project", "legacy-project", "show", "T-900")
    assert feature["status"] == "in_progress"
    assert story["task_type"] == "story"
    assert subtask["task_type"] == "subtask"
    assert len(
        [item for item in story["items"] if item["kind"] == "acceptance_criteria"]
    ) == 2
    dependencies = world.run("--project", "legacy-project", "dep", "list")
    assert len(dependencies) == 1

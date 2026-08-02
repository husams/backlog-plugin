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
            conn.execute("UPDATE project SET template_id=NULL")
            for table in (
                "template_transition",
                "template_status",
                "template_workflow",
                "template",
            ):
                conn.execute(f"DELETE FROM {table}")
        elif version == 9:
            for column in ("actual_exit_code", "stdout", "stderr", "duration_ms"):
                conn.execute(f"ALTER TABLE execution_result DROP COLUMN {column}")
        elif version == 10:
            conn.execute("DROP TABLE validation_waiver")
            conn.execute("ALTER TABLE execution_result DROP COLUMN actor")
        elif version == 12:
            conn.execute("DELETE FROM workflow WHERE task_type IN ('bug','iteration')")
            conn.execute(
                "DELETE FROM template_workflow WHERE task_type IN ('bug','iteration')"
            )
        elif version == 14:
            conn.execute("DROP TABLE retrospective_action")
            template_iteration = conn.execute(
                "SELECT id FROM template_workflow WHERE task_type='iteration' LIMIT 1"
            ).fetchone()[0]
            conn.execute(
                "DELETE FROM template_transition WHERE template_workflow_id=? "
                "AND from_status='open' AND to_status='closed'",
                (template_iteration,),
            )
            for table, workflow_table, fk in (
                ("template_transition", "template_workflow", "template_workflow_id"),
                ("workflow_transition", "workflow", "workflow_id"),
            ):
                rows = conn.execute(
                    f"SELECT t.id,t.gates FROM {table} t JOIN {workflow_table} w "
                    f"ON w.id=t.{fk} WHERE w.task_type='iteration' "
                    "AND t.from_status='open' AND t.to_status='closed'"
                ).fetchall()
                for row_id, gates in rows:
                    reduced = ",".join(
                        gate
                        for gate in (gates or "").split(",")
                        if gate
                        not in {
                            "iteration_comments_closed",
                            "iteration_retrospective_actions_clear",
                        }
                    )
                    conn.execute(
                        f"UPDATE {table} SET gates=? WHERE id=?", (reduced, row_id)
                    )
        elif version == 15:
            conn.execute("UPDATE task SET created_by=NULL")
            conn.execute(
                "INSERT INTO task(project_id,key,task_type,parent_id,title,description,status,"
                "priority,owner,assignee,assignee_kind,reviewer,reviewer_kind,branch,pr_url,"
                "pr_number,pr_repo,pr_state,pr_review_state,pr_waived,created_by,created_at,"
                "updated_at,closed_at) SELECT project_id,'S-999',task_type,parent_id,"
                "'Task without a creation event',description,status,priority,owner,assignee,"
                "assignee_kind,reviewer,reviewer_kind,branch,pr_url,pr_number,pr_repo,pr_state,"
                "pr_review_state,pr_waived,NULL,created_at,updated_at,closed_at FROM task "
                "WHERE key='S-001'"
            )
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
        if "VERSION_TWO_DATABASE" not in world.env:
            assert (
                conn.execute(
                    "SELECT created_by FROM task WHERE key='S-001'"
                ).fetchone()[0]
                == "creator"
            )
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


@given("a real version two SQLite database")
def version_two_database(world: World) -> None:
    path = world.root / ".backlog" / "backlog.db"
    path.unlink()
    conn = sqlite3.connect(path)
    try:
        conn.executescript(
            """
            CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
            INSERT INTO meta(key,value) VALUES('schema_version','2');
            CREATE TABLE feature(
                key TEXT PRIMARY KEY, title TEXT, status TEXT, priority TEXT,
                created_at TEXT, updated_at TEXT
            );
            CREATE TABLE item(
                key TEXT PRIMARY KEY, kind TEXT, parent_key TEXT, title TEXT,
                status TEXT, acceptance_criteria TEXT,
                created_at TEXT, updated_at TEXT
            );
            CREATE TABLE dependency(
                from_key TEXT, to_key TEXT, kind TEXT, note TEXT,
                external_id TEXT, created_at TEXT, created_by TEXT
            );
            CREATE TABLE artifact(
                entity_key TEXT, rel_path TEXT, title TEXT, kind TEXT,
                created_at TEXT, created_by TEXT
            );
            CREATE TABLE event(
                ts TEXT, entity_key TEXT, actor TEXT, kind TEXT,
                from_value TEXT, to_value TEXT, detail TEXT
            );
            CREATE TABLE key_counter(prefix TEXT, next_value INTEGER);
            INSERT INTO feature(key,title,status,priority,created_at,updated_at)
            VALUES('F-001','Version two feature','active','P1',
                   '2024-01-01T00:00:00Z','2024-01-02T00:00:00Z');
            INSERT INTO item(key,kind,parent_key,title,status,acceptance_criteria,
                             created_at,updated_at)
            VALUES('S-001','story','F-001','Version two story','created',
                   NULL,
                   '2024-01-01T00:00:00Z','2024-01-02T00:00:00Z');
            INSERT INTO dependency(from_key,to_key,kind,note)
            VALUES('F-001','S-001','relates','migrated relationship');
            INSERT INTO artifact(entity_key,rel_path,title,kind)
            VALUES('S-001','artifacts/S-001/design.md','Design','doc');
            INSERT INTO event(ts,entity_key,actor,kind,to_value)
            VALUES('2024-01-01T00:00:00Z','S-001','legacy-agent','created','created');
            INSERT INTO key_counter(prefix,next_value) VALUES('S',2);
            """
        )
        conn.commit()
    finally:
        conn.close()
    artifact = world.root / ".backlog" / "artifacts" / "S-001" / "design.md"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text("migrated design\n", encoding="utf-8")
    world.env["VERSION_TWO_DATABASE"] = "1"


@given("a real SQLite store from a newer schema version")
def newer_sqlite_database(world: World) -> None:
    path = world.root / ".backlog" / "backlog.db"
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            "UPDATE meta SET value=? WHERE key='schema_version'",
            (str(SCHEMA_VERSION + 1),),
        )
        conn.commit()
    finally:
        conn.close()


@given("a real SQLite store with a damaged schema")
def damaged_sqlite_database(world: World) -> None:
    path = world.root / ".backlog" / "backlog.db"
    conn = sqlite3.connect(path)
    try:
        conn.execute("DROP TABLE task")
        conn.commit()
    finally:
        conn.close()


@when("the newer store is opened")
def open_newer_store(world: World) -> None:
    world.run("doctor", expected=None)
    assert world.last_result is not None
    assert world.last_result.returncode == 1


@when("the damaged store is opened")
def open_damaged_store(world: World) -> None:
    world.run("doctor", expected=None)
    assert world.last_result is not None
    assert world.last_result.returncode == 1


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
    assert (
        len([item for item in story["items"] if item["kind"] == "acceptance_criteria"])
        == 2
    )
    dependencies = world.run("--project", "legacy-project", "dep", "list")
    assert len(dependencies) == 1


@then("the version two database work is queryable")
def version_two_database_work_is_queryable(world: World) -> None:
    feature = world.run("show", "F-001")
    story = world.run("show", "S-001")
    assert feature["status"] == "in_progress"
    assert story["parent_id"] == feature["id"]
    assert story["items"] == []
    assert len(story["dependencies"]) == 1
    accepted = world.run("action", "S-001", "refinement.accepted")
    assert accepted["task"]["status"] == "ready"

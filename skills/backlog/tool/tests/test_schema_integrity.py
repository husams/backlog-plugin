"""B-001: a migration must never report success for DDL that did not apply."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from backlog_cli import db
from backlog_cli.db import BacklogError
from backlog_cli.db.integrity import expected_schema, schema_drift
from backlog_cli.db.upgrades import add_column
from backlog_cli.schema import SCHEMA_VERSION
from _support import attributed_cli_args


SOURCE_ROOT = Path(__file__).resolve().parents[1] / "src"
PG_DSN = os.environ.get("BACKLOG_TEST_PG_DSN", "")


class _StoreTest(unittest.TestCase):
    """A repo-mode SQLite store driven through the real CLI."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.environment = {
            **os.environ,
            "BACKLOG_DB": "sqlite",
            "BACK_LOG_URL": "",
            "BACKLOG_DIR": "",
            "PYTHONPATH": str(SOURCE_ROOT),
        }
        self.env = patch.dict(os.environ, self.environment)
        self.env.start()
        self.old_cwd = Path.cwd()
        os.chdir(self.root)
        self.cli("init", ".")
        self.db_path = self.root / ".backlog" / "backlog.db"

    def tearDown(self):
        os.chdir(self.old_cwd)
        self.env.stop()
        self.tmp.cleanup()

    def raw(self, *args):
        return subprocess.run(
            [sys.executable, "-m", "backlog_cli.cli", *args],
            cwd=self.root,
            env=self.environment,
            text=True,
            capture_output=True,
        )

    def cli(self, *args, json_output=False):
        command = attributed_cli_args(args)
        if json_output:
            command.insert(0, "--json")
        result = self.raw(*command)
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        return json.loads(result.stdout) if json_output else result.stdout

    def break_task_item_updated_by(self):
        """Reproduce the reported store: schema_version says 18, but the column
        the v18 migration was supposed to add is absent."""
        raw = sqlite3.connect(self.db_path)
        raw.execute("ALTER TABLE task_item DROP COLUMN updated_by")
        raw.commit()
        raw.close()


class AddColumnTest(unittest.TestCase):
    def _sqlite_conn(self):
        raw = sqlite3.connect(":memory:")
        raw.row_factory = sqlite3.Row
        conn = db.Conn(raw, db.SQLITE, None)
        conn.execute("CREATE TABLE task_item (id INTEGER PRIMARY KEY)")
        conn.commit()
        return conn

    def test_missing_table_is_a_no_op(self):
        conn = self._sqlite_conn()
        add_column(conn, "not_a_table", "updated_by", "TEXT")
        self.assertFalse(conn.table_exists("not_a_table"))

    def test_existing_column_is_a_no_op(self):
        conn = self._sqlite_conn()
        add_column(conn, "task_item", "updated_by", "TEXT")
        add_column(conn, "task_item", "updated_by", "TEXT")
        self.assertTrue(conn.column_exists("task_item", "updated_by"))

    def test_a_failing_alter_is_not_swallowed(self):
        """The defect: any ALTER failure used to be read as 'already there'."""
        conn = self._sqlite_conn()
        real_execute = conn.execute

        def refuse(sql, params=()):
            if sql.lstrip().upper().startswith("ALTER"):
                raise sqlite3.OperationalError("permission denied for relation")
            return real_execute(sql, params)

        with patch.object(conn, "execute", side_effect=refuse):
            with self.assertRaises(sqlite3.OperationalError):
                add_column(conn, "task_item", "updated_by", "TEXT")
        self.assertFalse(conn.column_exists("task_item", "updated_by"))

    def test_an_alter_that_did_not_apply_is_reported(self):
        """Success is confirmed from the catalogue, not from the absence of an
        exception."""
        conn = self._sqlite_conn()
        real_execute = conn.execute

        def swallow(sql, params=()):
            if sql.lstrip().upper().startswith("ALTER"):
                return None  # the server accepted it, but nothing changed
            return real_execute(sql, params)

        with patch.object(conn, "execute", side_effect=swallow):
            with self.assertRaisesRegex(BacklogError, r"could not add"):
                add_column(conn, "task_item", "updated_by", "TEXT")


class ExpectedSchemaTest(unittest.TestCase):
    def test_shipped_ddl_declares_the_v18_column(self):
        expected = expected_schema()
        self.assertIn("task_item", expected)
        self.assertIn("updated_by", expected["task_item"])
        # Constraint clauses are not columns.
        self.assertNotIn("UNIQUE", expected["template_workflow"])
        self.assertNotIn("CHECK", expected["task_item"])

    def test_shipped_ddl_declares_the_v19_acceptance_verdict_table(self):
        expected = expected_schema()
        self.assertEqual(
            set(expected["acceptance_verdict"]),
            {"item_id", "state", "actor", "evidence", "content_hash", "created_at"},
        )


class SchemaVersionHonestyTest(_StoreTest):
    def test_a_failing_migration_step_leaves_the_version_behind(self):
        """AC: a step that fails must not let the store advertise the version
        it failed to reach."""
        conn = db.connect()
        conn.execute(
            "INSERT INTO meta(key,value) VALUES('schema_version','17') "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value"
        )
        conn.commit()
        spec = db.resolve_spec()
        with patch(
            "backlog_cli.db.migrations.upgrade_required_validation_gates",
            side_effect=BacklogError("ALTER refused"),
        ):
            with self.assertRaises(BacklogError):
                db.migrate(conn, 17, spec)
        recorded = conn.execute(
            "SELECT value FROM meta WHERE key='schema_version'"
        ).fetchone()["value"]
        self.assertEqual(int(recorded), 17, "a failed migration bumped the version")
        conn.close()

    def test_a_successful_migration_records_the_version(self):
        conn = db.connect()
        conn.execute("UPDATE meta SET value='17' WHERE key='schema_version'")
        conn.commit()
        db.migrate(conn, 17, db.resolve_spec())
        recorded = conn.execute(
            "SELECT value FROM meta WHERE key='schema_version'"
        ).fetchone()["value"]
        self.assertEqual(int(recorded), SCHEMA_VERSION)
        conn.close()


class DoctorSchemaDriftTest(_StoreTest):
    def test_doctor_fails_a_store_that_does_not_implement_its_version(self):
        """AC: v18 without task_item.updated_by must read as broken, not OK."""
        healthy = self.raw("doctor")
        self.assertEqual(healthy.returncode, 0, healthy.stdout)
        self.assertIn("OK", healthy.stdout)

        self.break_task_item_updated_by()
        broken = self.raw("doctor")
        self.assertEqual(broken.returncode, 1)
        self.assertIn("FAIL", broken.stdout)
        self.assertIn("task_item.updated_by is missing", broken.stdout)
        self.assertIn(f"schema v{SCHEMA_VERSION}", broken.stdout)

    def test_repair_restores_the_column_and_todos_work_again(self):
        """AC: a supported CLI repair path, no hand-written SQL."""
        story = self.cli(
            "story", "add", "--title", "Repair", "--actor", "creator", json_output=True
        )
        self.break_task_item_updated_by()
        self.assertNotEqual(self.raw("todo", "add", story["key"], "--content", "x").returncode, 0)

        repaired = self.raw("doctor", "--repair")
        self.assertEqual(repaired.returncode, 0, repaired.stdout + repaired.stderr)
        self.assertIn("added task_item.updated_by", repaired.stdout)
        self.assertIn("OK", repaired.stdout)

        first = self.cli(
            "--actor", "dev", "todo", "add", story["key"], "--content", "x",
            json_output=True,
        )[0]
        self.cli("--actor", "dev", "todo", "close", str(first["id"]))
        second = self.cli(
            "--actor", "dev", "todo", "add", story["key"], "--content", "y",
            json_output=True,
        )[0]
        self.cli("--actor", "dev", "todo", "move", str(second["id"]), "--position", "0")
        rows = self.cli("todo", "list", story["key"], json_output=True)
        self.assertEqual([r["content"] for r in rows], ["y", "x"])
        self.assertEqual([r["updated_by"] for r in rows], ["dev", "dev"])

    def test_doctor_detects_and_repairs_a_missing_acceptance_verdict_table(self):
        """AC: the v19 table participates in the same DDL verification."""
        raw = sqlite3.connect(self.db_path)
        raw.execute("DROP TABLE acceptance_verdict")
        raw.commit()
        raw.close()

        broken = self.raw("doctor")
        self.assertEqual(broken.returncode, 1)
        self.assertIn("table acceptance_verdict is missing", broken.stdout)

        repaired = self.raw("doctor", "--repair")
        self.assertEqual(repaired.returncode, 0, repaired.stdout + repaired.stderr)
        self.assertIn("OK", repaired.stdout)

    def test_repair_on_a_healthy_store_changes_nothing(self):
        first = self.raw("doctor", "--repair")
        self.assertEqual(first.returncode, 0, first.stdout)
        self.assertIn("already matches", first.stdout)


@unittest.skipUnless(PG_DSN, "set BACKLOG_TEST_PG_DSN to run PostgreSQL tests")
class NamespacedPostgresMigrationTest(unittest.TestCase):
    """AC: migration DDL must resolve against the store's configured schema."""

    def setUp(self):
        self.schema = f"backlog_test_{uuid.uuid4().hex[:10]}"
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.environment = {
            **os.environ,
            "BACKLOG_DB": "postgres",
            "BACK_LOG_URL": PG_DSN,
            "BACKLOG_SCHEMA": self.schema,
            "BACKLOG_PROJECT": "pg-migration-test",
            "BACKLOG_DIR": "",
            "PYTHONPATH": str(SOURCE_ROOT),
        }
        self.env = patch.dict(os.environ, self.environment)
        self.env.start()
        self.old_cwd = Path.cwd()
        os.chdir(self.root)
        self.cli("init", ".")

    def tearDown(self):
        import psycopg

        os.chdir(self.old_cwd)
        with psycopg.connect(PG_DSN, autocommit=True) as raw:
            raw.execute(f'DROP SCHEMA IF EXISTS "{self.schema}" CASCADE')
        self.env.stop()
        self.tmp.cleanup()

    def raw(self, *args):
        return subprocess.run(
            [sys.executable, "-m", "backlog_cli.cli", *args],
            cwd=self.root,
            env=self.environment,
            text=True,
            capture_output=True,
        )

    def cli(self, *args, json_output=False):
        command = attributed_cli_args(args)
        if json_output:
            command.insert(0, "--json")
        result = self.raw(*command)
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        return json.loads(result.stdout) if json_output else result.stdout

    def _regress_to_v17(self):
        """Put the namespaced store back in the pre-v18 shape."""
        conn = db.connect()
        conn.execute(
            f'ALTER TABLE "{self.schema}".task_item DROP COLUMN IF EXISTS updated_by'
        )
        conn.execute("UPDATE meta SET value='17' WHERE key='schema_version'")
        conn.commit()
        conn.close()

    def test_v17_to_v18_adds_the_column_in_the_configured_schema(self):
        story = self.cli(
            "story", "add", "--title", "PG", "--actor", "creator", json_output=True
        )
        self._regress_to_v17()

        conn = db.connect()  # connect() migrates
        self.assertEqual(
            int(
                conn.execute(
                    "SELECT value FROM meta WHERE key='schema_version'"
                ).fetchone()["value"]
            ),
            SCHEMA_VERSION,
        )
        columns = conn.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = ? AND table_name = 'task_item'",
            (self.schema,),
        ).fetchall()
        self.assertIn(
            "updated_by",
            {row["column_name"] for row in columns},
            "v18 DDL did not land in the store's configured schema",
        )
        self.assertEqual(schema_drift(conn), [])
        conn.close()

        # AC: todo add / close / move end to end on a namespaced PostgreSQL store.
        first = self.cli(
            "--actor", "dev", "todo", "add", story["key"], "--content", "first",
            json_output=True,
        )[0]
        second = self.cli(
            "--actor", "dev", "todo", "add", story["key"], "--content", "second",
            json_output=True,
        )[0]
        self.cli("--actor", "dev", "todo", "close", str(first["id"]))
        self.cli(
            "--actor", "dev", "todo", "move", str(second["id"]), "--position", "0"
        )
        rows = self.cli("todo", "list", story["key"], json_output=True)
        self.assertEqual([row["content"] for row in rows], ["second", "first"])
        self.assertEqual([row["updated_by"] for row in rows], ["dev", "dev"])

    def test_doctor_reports_and_repairs_a_namespaced_store(self):
        conn = db.connect()
        conn.execute(
            f'ALTER TABLE "{self.schema}".task_item DROP COLUMN IF EXISTS updated_by'
        )
        conn.commit()
        conn.close()

        broken = self.raw("doctor")
        self.assertEqual(broken.returncode, 1, broken.stdout)
        self.assertIn("task_item.updated_by is missing", broken.stdout)

        repaired = self.raw("doctor", "--repair")
        self.assertEqual(repaired.returncode, 0, repaired.stdout + repaired.stderr)
        self.assertIn("OK", repaired.stdout)


if __name__ == "__main__":
    unittest.main()

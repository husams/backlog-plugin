from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SOURCE_ROOT = Path(__file__).resolve().parents[1] / "src"


class TaskCreatorSeparationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.env = {
            **os.environ,
            "BACKLOG_DB": "sqlite",
            "BACK_LOG_URL": "",
            "PYTHONPATH": str(SOURCE_ROOT),
        }
        self.cli("init", ".")

    def tearDown(self):
        self.tmp.cleanup()

    def raw(self, *args):
        return subprocess.run(
            [sys.executable, "-m", "backlog_cli.cli", *args],
            cwd=self.root,
            env=self.env,
            text=True,
            capture_output=True,
        )

    def cli(self, *args, json_output=False):
        command = list(args)
        if json_output:
            command.insert(0, "--json")
        result = self.raw(*command)
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        return json.loads(result.stdout) if json_output else result.stdout

    def test_creator_is_stored_and_cannot_accept_own_task(self):
        created = self.cli(
            "story",
            "add",
            "--title",
            "Independent refinement",
            "--actor",
            "codex",
            json_output=True,
        )
        self.assertEqual(created["created_by"], "codex")
        self.assertIn("creator    : codex", self.cli("show", created["key"]))

        self_acceptance = self.raw(
            "action", created["key"], "refinement.accepted", "--actor", "CODEX"
        )
        self.assertNotEqual(self_acceptance.returncode, 0)
        self.assertIn("cannot perform refinement.accepted", self_acceptance.stderr)

        actorless = self.raw("action", created["key"], "refinement.accepted")
        self.assertNotEqual(actorless.returncode, 0)
        self.assertIn("requires an independent actor", actorless.stderr)

        shown = self.cli("show", created["key"], json_output=True)
        self.assertEqual(shown["status"], "created")
        history = self.cli("history", created["key"], json_output=True)
        self.assertEqual([event["kind"] for event in history], ["created"])

        accepted = self.cli(
            "action",
            created["key"],
            "refinement.accepted",
            "--actor",
            "product-manager",
            json_output=True,
        )
        self.assertEqual(accepted["task"]["status"], "ready")
        history = self.cli("history", created["key"], json_output=True)
        self.assertEqual(history[-2]["kind"], "action")
        self.assertEqual(history[-2]["actor"], "product-manager")

    def test_new_task_requires_actor_but_unattributed_legacy_task_remains_operable(
        self,
    ):
        missing = self.raw("bug", "add", "--title", "Missing attribution")
        self.assertNotEqual(missing.returncode, 0)
        self.assertIn("task creation requires an actor", missing.stderr)

        created = self.cli(
            "bug",
            "add",
            "--title",
            "Legacy attribution",
            "--actor",
            "legacy-seeder",
            json_output=True,
        )
        database = self.root / ".backlog" / "backlog.db"
        with sqlite3.connect(database) as conn:
            conn.execute(
                "UPDATE task SET created_by = NULL WHERE key = ?", (created["key"],)
            )
            conn.execute(
                "UPDATE event SET actor = NULL WHERE entity_key = ? AND kind = 'created'",
                (created["key"],),
            )

        accepted = self.cli(
            "action", created["key"], "refinement.accepted", json_output=True
        )
        self.assertEqual(accepted["task"]["status"], "ready")

    def test_v15_migration_backfills_creator_from_creation_event(self):
        created = self.cli(
            "feature",
            "add",
            "--title",
            "Migrated creator",
            "--actor",
            "codex",
            json_output=True,
        )
        database = self.root / ".backlog" / "backlog.db"
        with sqlite3.connect(database) as conn:
            conn.execute("UPDATE meta SET value = '15' WHERE key = 'schema_version'")
            conn.execute("ALTER TABLE task DROP COLUMN created_by")

        shown = self.cli("show", created["key"], json_output=True)
        self.assertEqual(shown["created_by"], "codex")

        self_acceptance = self.raw(
            "action", created["key"], "refinement.accepted", "--actor", "codex"
        )
        self.assertNotEqual(self_acceptance.returncode, 0)
        self.assertIn("cannot perform refinement.accepted", self_acceptance.stderr)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backlog_cli import api, db
from backlog_cli.db import BacklogError
from _support import attributed_cli_args


SOURCE_ROOT = Path(__file__).resolve().parents[1] / "src"


class TodoTest(unittest.TestCase):
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

    def story(self, title="Todo story"):
        return self.cli(
            "story",
            "add",
            "--title",
            title,
            "--assignee",
            "developer",
            "--reviewer",
            "reviewer",
            "--actor",
            "creator",
            json_output=True,
        )

    def start(self, key):
        self.cli("action", key, "refinement.accepted", "--actor", "reviewer")
        self.cli("action", key, "work.started", "--actor", "developer")

    def test_cli_and_api_lifecycle_preserves_order_state_and_attribution(self):
        story = self.story()
        with api.open(actor="developer") as backlog:
            first, second = backlog.add_todos(story["key"], ["first", "second"])
            third = backlog.add_todo(story["key"], "third")
            backlog.close_todo(second["id"])
            backlog.move_todo(second["id"], 0)
            self.assertEqual(backlog.task(story["key"]).todos, backlog.todos(story["key"]))

        reopened = self.cli(
            "--actor",
            "reviewer",
            "todo",
            "reopen",
            str(second["id"]),
            json_output=True,
        )
        self.assertEqual(reopened["updated_by"], "reviewer")
        cli_rows = self.cli("todo", "list", story["key"], json_output=True)
        with api.open() as backlog:
            api_rows = backlog.todos(story["key"])
        self.assertEqual(cli_rows, api_rows)
        self.assertEqual([row["position"] for row in api_rows], [0, 1, 2])
        self.assertEqual(
            [row["content"] for row in api_rows], ["second", "first", "third"]
        )
        self.assertEqual([row["state"] for row in api_rows], ["open", "open", "open"])
        history = self.cli("history", story["key"])
        for evidence in (
            "todo.added",
            "todo.closed",
            "todo.moved",
            "todo.reopened",
            "developer",
            "reviewer",
        ):
            self.assertIn(evidence, history)
        self.assertEqual({first["id"], second["id"], third["id"]}, {row["id"] for row in api_rows})

    def test_review_submission_requires_every_todo_and_rechecks_after_changes(self):
        empty = self.story("No todos")
        self.start(empty["key"])
        self.cli(
            "action", empty["key"], "review.submitted", "--actor", "developer"
        )

        story = self.story("Gated")
        self.start(story["key"])
        added = self.cli(
            "--actor",
            "developer",
            "todo",
            "add",
            story["key"],
            "--content",
            "write code\nrun tests",
            json_output=True,
        )
        rejected = self.raw(
            "--actor", "developer", "action", story["key"], "review.submitted"
        )
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn("todos_closed", rejected.stderr)
        self.assertIn("write code", rejected.stderr)
        self.assertIn("run tests", rejected.stderr)
        for todo in added:
            self.cli("--actor", "developer", "todo", "close", str(todo["id"]))
        self.cli(
            "action", story["key"], "review.submitted", "--actor", "developer"
        )
        self.cli(
            "action", story["key"], "review.changes_requested", "--actor", "reviewer"
        )
        self.cli("action", story["key"], "work.resumed", "--actor", "developer")
        self.cli("--actor", "developer", "todo", "reopen", str(added[0]["id"]))
        self.cli(
            "--actor",
            "developer",
            "todo",
            "add",
            story["key"],
            "--content",
            "address feedback",
        )
        rejected_again = self.raw(
            "--actor", "developer", "action", story["key"], "review.submitted"
        )
        self.assertNotEqual(rejected_again.returncode, 0)
        self.assertIn("write code", rejected_again.stderr)
        self.assertIn("address feedback", rejected_again.stderr)

    def test_invalid_operations_are_atomic_and_other_item_semantics_remain(self):
        story = self.story()
        checklist = self.cli(
            "item",
            "add",
            story["key"],
            "--kind",
            "checklist",
            "--content",
            "ordinary checklist",
            json_output=True,
        )[0]
        self.cli(
            "item", "add", story["key"], "--kind", "note", "--content", "note"
        )
        todos = self.cli(
            "--actor",
            "developer",
            "todo",
            "add",
            story["key"],
            "--content",
            "one\ntwo",
            json_output=True,
        )
        before = self.cli("todo", "list", story["key"], json_output=True)
        failures = (
            self.raw("--actor", "developer", "todo", "add", "S-999", "--content", "x"),
            self.raw("--actor", "developer", "todo", "close", "999999"),
            self.raw(
                "--actor", "developer", "todo", "close", str(checklist["id"])
            ),
            self.raw(
                "--actor",
                "developer",
                "todo",
                "move",
                str(todos[0]["id"]),
                "--position",
                "9",
            ),
        )
        self.assertTrue(all(result.returncode != 0 for result in failures))
        self.assertIn("not a todo", failures[2].stderr)
        self.assertIn("expected 0..1", failures[3].stderr)
        self.assertEqual(before, self.cli("todo", "list", story["key"], json_output=True))
        details = self.cli("item", "list", story["key"], json_output=True)
        self.assertEqual(
            [row["kind"] for row in details if row["kind"] != "todo"],
            ["checklist", "note"],
        )

        subtask = self.cli(
            "subtask",
            "add",
            "--story",
            story["key"],
            "--title",
            "unfinished",
            "--actor",
            "creator",
            json_output=True,
        )
        gate = self.raw("gate", story["key"], "--for", "accepted")
        self.assertEqual(gate.returncode, 2)
        self.assertIn("children_complete", gate.stdout)
        self.assertIn(subtask["key"], gate.stdout)

    def test_public_api_validates_arguments_and_project_scope(self):
        story = self.story()
        with api.open(actor="developer") as backlog:
            todo = backlog.add_todo(story["key"], "scoped")
            with self.assertRaisesRegex(TypeError, "list of strings"):
                backlog.add_todos(story["key"], "not-a-list")
            with self.assertRaisesRegex(BacklogError, "non-empty"):
                backlog.add_todos(story["key"], ["valid", " "])
            self.assertEqual(
                [row["content"] for row in backlog.todos(story["key"])],
                ["scoped"],
            )

        self.cli(
            "project", "add", "--name", "Other", "--slug", "other", "--template", "software-delivery"
        )
        with api.open(project="other", actor="developer") as other:
            with self.assertRaisesRegex(BacklogError, "no todo"):
                other.close_todo(todo["id"])

    def test_schema_v17_upgrade_preserves_items_and_enables_todos(self):
        story = self.story()
        self.cli(
            "item",
            "add",
            story["key"],
            "--kind",
            "checklist",
            "--content",
            "preserved",
            "--shell",
            "true",
        )
        spec = db.resolve_spec(self.root)
        conn = db.connect(spec=spec)
        try:
            conn.execute("UPDATE meta SET value='17' WHERE key='schema_version'")
            conn.commit()
        finally:
            conn.close()

        with api.open(actor="developer") as backlog:
            self.assertEqual(backlog.task(story["key"]).items("checklist"), ["preserved"])
            self.assertEqual(
                backlog.task(story["key"]).item_details("checklist")[0]["executor"],
                "shell",
            )
            self.assertIn(
                "todos_closed",
                backlog.flow("story").gates_for("in_progress", "in_review"),
            )
            todo = backlog.add_todo(story["key"], "after migration")
            self.assertEqual(todo["position"], 0)
        conn = db.connect(spec=spec)
        try:
            version = conn.execute(
                "SELECT value FROM meta WHERE key='schema_version'"
            ).fetchone()["value"]
            self.assertEqual(version, "19")
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()

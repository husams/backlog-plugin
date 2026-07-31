from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backlog_cli import api
from backlog_cli.db import BacklogError


class BugTaskIntegrationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.env = {
            **os.environ,
            "BACKLOG_DB": "sqlite",
            "BACK_LOG_URL": "",
            "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
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

    def open_api(self, actor="codex"):
        env = patch.dict(os.environ, self.env, clear=False)
        cwd = patch.object(Path, "cwd", return_value=self.root)
        return env, cwd

    def test_cli_and_api_create_standalone_bugs_with_distinct_identity(self):
        created = self.cli("bug", "add", "--title", "CLI defect", json_output=True)
        self.assertEqual(created["key"], "B-001")
        self.assertEqual(created["task_type"], "bug")
        self.assertIsNone(created["parent_id"])

        env, cwd = self.open_api()
        with env, cwd, api.open(actor="codex") as bl:
            bug = bl.create_bug("API defect", acceptance_criteria=["It is fixed"])
            self.assertEqual((bug.key, bug.task_type, bug.parent), ("B-002", "bug", None))
            self.assertEqual(bug.items("acceptance_criteria"), ["It is fixed"])

        shown = self.cli("show", "B-001")
        listed = self.cli("bug", "list")
        board = self.cli("board")
        self.assertIn("B-001  [Bug]", shown)
        self.assertIn("B-001", listed)
        self.assertIn(" B ", board)

    def test_bug_workflow_matches_story_and_is_inspectable(self):
        env, cwd = self.open_api()
        with env, cwd, api.open() as bl:
            story = bl.flow(task_type="story")
            bug = bl.flow(task_type="bug")
            self.assertEqual(list(bug.statuses), list(story.statuses))
            self.assertEqual(bug.transitions, story.transitions)
        statuses = self.cli("statuses", "--type", "bug")
        self.assertIn("== bug", statuses)
        self.assertIn("In Review", statuses)

    def test_bug_parent_is_rejected_by_cli_api_update_and_import(self):
        self.cli("feature", "add", "--title", "Container")
        generic = self.raw(
            "task", "add", "--type", "bug", "--parent", "F-001", "--title", "Bad"
        )
        self.assertNotEqual(generic.returncode, 0)
        self.assertIn("a bug cannot sit under a feature", generic.stderr)

        self.cli("bug", "add", "--title", "Standalone")
        update = self.raw("set", "B-001", "--parent", "F-001")
        self.assertNotEqual(update.returncode, 0)
        self.assertIn("a bug cannot sit under a feature", update.stderr)

        env, cwd = self.open_api()
        with env, cwd, api.open() as bl:
            with self.assertRaisesRegex(BacklogError, "a bug cannot sit under a feature"):
                bl.create_task("bug", "API bad", parent="F-001")

        exported = self.root / "export.json"
        self.cli("export", "--out", str(exported))
        payload = json.loads(exported.read_text())
        feature = next(row for row in payload["tables"]["task"] if row["key"] == "F-001")
        bug = next(row for row in payload["tables"]["task"] if row["key"] == "B-001")
        bug["parent_id"] = feature["id"]
        exported.write_text(json.dumps(payload))
        imported = self.raw("import", str(exported), "--replace")
        self.assertNotEqual(imported.returncode, 0)
        self.assertIn("import rejected: a bug cannot sit under a feature", imported.stderr)
        self.assertIn("B-001", self.cli("show", "B-001"))

    def test_bug_supports_subtasks_delivery_metadata_and_history(self):
        self.cli("bug", "add", "--title", "Defect", "--ac", "Regression is covered")
        self.cli("subtask", "add", "--bug", "B-001", "--title", "Regression test")
        detail = self.cli("show", "B-001")
        self.assertIn("T-001", detail)
        self.assertIn("subtasks:", detail)

        self.cli("assign", "B-001", "--to", "codex", "--reviewer", "claude")
        self.cli("story", "add", "--title", "Related work")
        self.cli("dep", "add", "B-001", "--relates", "S-001")
        self.cli("action", "B-001", "refinement.accepted")
        self.cli("action", "B-001", "work.started")
        self.cli(
            "pr", "set", "B-001", "--url",
            "https://gitlab.example/group/project/-/merge_requests/1", "--state", "open",
        )
        self.cli(
            "review", "open", "B-001", "--author", "claude", "--severity", "info",
            "--body", "Delivery trace",
        )
        detail = self.cli("show", "B-001")
        history = self.cli("history", "B-001")
        self.assertIn("codex*", detail)
        self.assertIn("Regression is covered", detail)
        self.assertIn("merge_requests/1", detail)
        self.assertIn("C-001", detail)
        self.assertIn("created", history)
        self.assertIn("pr", history)

        gate = self.raw("gate", "B-001", "--for", "accepted")
        self.assertEqual(gate.returncode, 2)
        self.assertIn("children_complete", gate.stdout)
        self.assertIn("T-001=created", gate.stdout)


if __name__ == "__main__":
    unittest.main()

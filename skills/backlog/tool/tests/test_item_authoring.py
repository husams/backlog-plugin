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


class ItemAuthoringCliTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.env = {
            **os.environ,
            "BACKLOG_DB": "sqlite",
            "BACK_LOG_URL": "",
            "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
        }
        self.run_cli("init", ".")

    def tearDown(self):
        self.tmp.cleanup()

    def raw(self, *args, json_output=False):
        command = [sys.executable, "-m", "backlog_cli.cli"]
        if json_output:
            command.append("--json")
        command.extend(args)
        return subprocess.run(
            command, cwd=self.root, env=self.env, text=True, capture_output=True
        )

    def run_cli(self, *args, json_output=False):
        result = self.raw(*args, json_output=json_output)
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        return json.loads(result.stdout) if json_output else result.stdout

    def test_plain_syntax_remains_unchanged(self):
        created = self.run_cli(
            "story", "add", "--title", "Plain", "--ac", "first\nsecond",
            json_output=True,
        )
        items = self.run_cli("item", "list", created["key"], json_output=True)
        self.assertEqual([item["content"] for item in items], ["first", "second"])
        self.assertEqual([item["executor"] for item in items], ["plain", "plain"])
        self.assertNotIn("pending", self.run_cli("show", created["key"]))

    def test_shell_create_list_show_and_secret_redaction(self):
        created = self.run_cli(
            "feature", "add", "--title", "Shell",
            "--ac", "unit tests pass",
            "--shell", "python -m unittest",
            "--requirement", "advisory",
            "--expected-exit-code", "0",
            "--stdout-contains", "OK",
            "--env", "API_TOKEN=top-secret",
            json_output=True,
        )
        listed_text = self.run_cli("item", "list", created["key"])
        shown_text = self.run_cli("show", created["key"])
        listed_json = self.run_cli("item", "list", created["key"], json_output=True)
        shown_json = self.run_cli("show", created["key"], json_output=True)
        for output in (listed_text, shown_text, json.dumps(listed_json), json.dumps(shown_json)):
            self.assertNotIn("top-secret", output)
        self.assertIn("[shell, advisory, pending]", listed_text)
        self.assertIn("API_TOKEN (values hidden)", shown_text)
        self.assertEqual(listed_json[0]["state"], "pending")
        self.assertEqual(
            listed_json[0]["execution_spec"]["shell"]["environment"], ["API_TOKEN"]
        )

    def test_hook_story_create_and_item_set_update(self):
        story = self.run_cli(
            "story", "add", "--title", "Hook", "--ac", "policy passes",
            "--hook", "checks.policy", "--arguments", '{"strict":true}',
            "--expected-result", '{"passed":true}',
            json_output=True,
        )
        initial = self.run_cli("item", "list", story["key"], json_output=True)
        self.assertEqual(initial[0]["executor"], "hook")
        self.assertEqual(initial[0]["state"], "pending")
        self.assertEqual(
            initial[0]["execution_spec"]["hook"]["expected_result"], {"passed": True}
        )

        replaced = self.run_cli(
            "item", "set", story["key"], "--kind", "acceptance_criteria",
            "--content", "shell now passes", "--shell", "true",
            "--requirement", "required", json_output=True,
        )
        self.assertEqual(replaced[0]["executor"], "shell")
        self.assertEqual(replaced[0]["requirement"], "required")
        self.assertEqual(replaced[0]["state"], "pending")

    def test_executable_multiline_and_note_are_rejected_without_partial_item(self):
        story = self.run_cli("story", "add", "--title", "Errors", json_output=True)
        result = self.raw(
            "item", "add", story["key"], "--content", "one\ntwo", "--shell", "true"
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("exactly one", result.stderr)
        self.assertEqual(
            self.run_cli("item", "list", story["key"], json_output=True), []
        )
        result = self.raw(
            "item", "add", story["key"], "--kind", "note",
            "--content", "not executable", "--hook", "checks.note",
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("only acceptance criteria and checklist", result.stderr)
        self.assertEqual(
            self.run_cli("item", "list", story["key"], json_output=True), []
        )


class ItemAuthoringApiTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.env = patch.dict(
            os.environ,
            {"BACKLOG_DB": "sqlite", "BACK_LOG_URL": "", "BACKLOG_DIR": ""},
        )
        self.env.start()
        self.old_cwd = Path.cwd()
        os.chdir(self.root)
        subprocess.run(
            [sys.executable, "-m", "backlog_cli.cli", "init", "."],
            check=True, text=True, capture_output=True,
        )

    def tearDown(self):
        os.chdir(self.old_cwd)
        self.env.stop()
        self.tmp.cleanup()

    @staticmethod
    def shell(secret="hidden"):
        return {
            "executor": "shell",
            "requirement": "required",
            "shell": {
                "command": "true",
                "environment": {"TOKEN": secret},
            },
        }

    def test_create_add_set_list_and_safe_update(self):
        with api.open(actor="S-011") as backlog:
            feature = backlog.create_feature(
                "API feature",
                acceptance_criteria=[
                    "plain",
                    {"content": "shell", "execution": self.shell()},
                ],
            )
            details = feature.item_details()
            self.assertEqual(
                [item["executor"] for item in details], ["plain", "shell"]
            )
            self.assertEqual(details[1]["state"], "pending")
            self.assertEqual(
                details[1]["execution_spec"]["shell"]["environment"], ["TOKEN"]
            )
            checklist = backlog.add_item(
                feature.key, "checklist", "hook",
                execution_spec={
                    "executor": "hook",
                    "requirement": "advisory",
                    "hook": {
                        "name": "checks.api",
                        "arguments": {"fast": True},
                        "expected_result": True,
                    },
                },
            )
            self.assertEqual(checklist["executor"], "hook")
            updated = backlog.set_item_execution(
                checklist["id"], {
                    "executor": "shell",
                    "shell": {"command": "true", "environment": {"PASSWORD": "secret"}},
                },
            )
            self.assertEqual(updated["execution_spec"]["shell"]["environment"], ["PASSWORD"])
            self.assertNotIn("secret", json.dumps(updated))
            self.assertIn("item_id", updated)

            replaced = backlog.set_items(
                feature.key, "checklist",
                ["plain checklist", {"content": "required", "execution": self.shell()}],
            )
            self.assertEqual([item["executor"] for item in replaced], ["plain", "shell"])

    def test_invalid_api_spec_does_not_create_item(self):
        with api.open() as backlog:
            story = backlog.create_story("Safe")
            with self.assertRaises(BacklogError):
                backlog.add_item(
                    story.key, "acceptance_criteria", "bad",
                    execution_spec={"executor": "shell", "shell": {"command": ""}},
                )
            self.assertEqual(story.item_details(), [])


if __name__ == "__main__":
    unittest.main()

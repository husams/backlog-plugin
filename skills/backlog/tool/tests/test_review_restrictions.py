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
from backlog_cli.schema import ReviewSeverity
from _support import attributed_cli_args


SOURCE_ROOT = Path(__file__).resolve().parents[1] / "src"


class ReviewRestrictionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.env = {
            **os.environ,
            "BACKLOG_DB": "sqlite",
            "BACK_LOG_URL": "",
            "PYTHONPATH": str(SOURCE_ROOT),
        }
        self.run_cli("init", ".")

    def tearDown(self):
        self.tmp.cleanup()

    def run_cli(self, *args, json_output=False):
        args = attributed_cli_args(args)
        command = [sys.executable, "-m", "backlog_cli.cli"]
        if json_output:
            command.append("--json")
        result = subprocess.run(
            [*command, *args],
            cwd=self.root,
            env=self.env,
            text=True,
            capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        return json.loads(result.stdout) if json_output else result.stdout

    def test_escalation_invalidates_ready_and_audit_records_decision(self):
        story = self.run_cli("story", "add", "--title", "Review me", json_output=True)
        key = story["key"]
        self.run_cli("assign", key, "--to", "dev", "--reviewer", "reviewer")
        self.run_cli("action", key, "refinement.accepted", "--actor", "reviewer")
        opened = self.run_cli(
            "review",
            "open",
            key,
            "--author",
            "reviewer",
            "--severity",
            "nice_to_have",
            "--body",
            "Tighten this",
            json_output=True,
        )
        root = opened["root"]
        self.run_cli(
            "review",
            "severity",
            root,
            "--severity",
            "blocker",
            "--author",
            "reviewer",
        )
        self.assertEqual(
            self.run_cli("show", key, json_output=True)["status"], "incomplete"
        )
        reply = self.run_cli(
            "review",
            "reply",
            root,
            "--author",
            "dev",
            "--action",
            "fix",
            "--body",
            "Changed the validation",
            json_output=True,
        )
        self.run_cli(
            "review",
            "reply",
            reply["reply_to"],
            "--author",
            "reviewer",
            "--action",
            "accept",
            "--body",
            "Verified",
        )
        audit = self.run_cli("review", "audit", root, json_output=True)
        self.assertEqual(audit["decisions"][-1]["author"], "reviewer")
        self.assertEqual(audit["decisions"][-1]["action"], "accept")

    def test_python_session_actor_rejects_review_impersonation(self):
        with (
            patch.dict(os.environ, self.env),
            patch("pathlib.Path.cwd", return_value=self.root),
        ):
            with api.open(actor="reviewer") as bl:
                story = bl.create_story("Actor bound")
                with self.assertRaisesRegex(
                    BacklogError, "does not match session actor"
                ):
                    bl.review_open(
                        story.key,
                        author="someone-else",
                        body="No",
                        role="reviewer",
                        severity=ReviewSeverity.BLOCKER,
                    )

    def test_task_parent_projection_is_uniform(self):
        feature = self.run_cli("feature", "add", "--title", "Parent", json_output=True)
        story = self.run_cli(
            "story",
            "add",
            "--title",
            "Child",
            "--feature",
            feature["key"],
            json_output=True,
        )
        with (
            patch.dict(os.environ, self.env),
            patch("pathlib.Path.cwd", return_value=self.root),
        ):
            with api.open() as bl:
                self.assertEqual(bl.task(story["key"]).parent_key, feature["key"])
                self.assertEqual(bl.find(story["key"]).parent, feature["key"])


if __name__ == "__main__":
    unittest.main()

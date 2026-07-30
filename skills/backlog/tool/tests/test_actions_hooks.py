from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backlog_cli import hooks


class ActionHookIntegrationTest(unittest.TestCase):
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
        hooks = self.root / ".backlog" / "hooks.py"
        hooks.write_text(
            """
import os
from pathlib import Path


def _record(stage, action, current_state, new_state):
    path = Path(os.environ["BACKLOG_HOOK_LOG"])
    with path.open("a", encoding="utf-8") as stream:
        stream.write(
            f"{stage}:{action.value}:{current_state}:{new_state}\\n"
        )


def pre_transition(action, trigger, current_state, new_state):
    _record("pre", action, current_state, new_state)
    if trigger["parameters"].get("block") == "yes":
        return current_state
    return new_state


def post_transition(action, trigger, previous_state, current_state):
    _record("post", action, previous_state, current_state)
""".lstrip(),
            encoding="utf-8",
        )
        self.log = self.root / "hooks.log"
        self.env["BACKLOG_HOOK_LOG"] = str(self.log)

    def tearDown(self):
        self.tmp.cleanup()

    def run_cli(self, *args, json_output=False):
        command = [sys.executable, "-m", "backlog_cli.cli"]
        if json_output:
            command.append("--json")
        command.extend(args)
        result = subprocess.run(
            command,
            cwd=self.root,
            env=self.env,
            text=True,
            capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        return json.loads(result.stdout) if json_output else result.stdout

    def status(self, key):
        return self.run_cli("show", key, json_output=True)["status"]

    def test_action_pr_lifecycle_and_hooks(self):
        self.run_cli("story", "add", "--title", "Action lifecycle")

        blocked = self.run_cli(
            "action",
            "S-001",
            "refinement.accepted",
            "--parameter",
            "block=yes",
            json_output=True,
        )
        self.assertFalse(blocked["transitioned"])
        self.assertEqual(self.status("S-001"), "created")

        self.run_cli("action", "S-001", "refinement.accepted")
        self.assertEqual(self.status("S-001"), "ready")

        self.run_cli("action", "S-001", "work.started")
        self.assertEqual(self.status("S-001"), "in_progress")

        self.run_cli(
            "pr", "set", "S-001",
            "--url", "https://github.com/example/project/pull/1",
            "--state", "open",
        )
        self.assertEqual(self.status("S-001"), "in_review")

        self.run_cli("pr", "set", "S-001", "--review-state", "approved")
        self.assertEqual(self.status("S-001"), "accepted")

        self.run_cli("pr", "set", "S-001", "--state", "merged")
        self.assertEqual(self.status("S-001"), "done")

        log = self.log.read_text(encoding="utf-8")
        self.assertIn("pre:refinement.accepted:created:ready", log)
        self.assertIn("post:refinement.accepted:created:ready", log)
        self.assertIn("post:pr.created:in_progress:in_review", log)
        self.assertIn("post:pr.approved:in_review:accepted", log)
        self.assertIn("post:pr.merged:accepted:done", log)

    def test_legacy_move_also_runs_hooks(self):
        self.run_cli("feature", "add", "--title", "Legacy move")
        self.run_cli("move", "F-001", "ready")
        self.assertEqual(self.status("F-001"), "ready")
        self.assertIn(
            "post:refinement.accepted:created:ready",
            self.log.read_text(encoding="utf-8"),
        )

    def test_project_workflow_replaces_default_action_mapping(self):
        self.run_cli("story", "add", "--title", "Custom workflow")
        (self.root / ".backlog" / "workflow.yaml").write_text(
            """
version: 1
name: custom
states:
  - slug: created
    initial: true
  - slug: incomplete
transitions:
  - task_types: [story]
    from: created
    action: refinement.accepted
    to: incomplete
""".lstrip(),
            encoding="utf-8",
        )
        self.run_cli("action", "S-001", "refinement.accepted")
        self.assertEqual(self.status("S-001"), "incomplete")

    def test_repository_hooks_override_shared_store_location(self):
        central_store = self.root / "central-store"
        with patch.object(hooks.Path, "cwd", return_value=self.root):
            self.assertEqual(
                hooks.project_backlog_dir(central_store),
                (self.root / ".backlog").resolve(),
            )


if __name__ == "__main__":
    unittest.main()

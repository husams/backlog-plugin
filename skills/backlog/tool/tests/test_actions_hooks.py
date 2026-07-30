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
from backlog_cli.schema import ReviewSeverity


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
        hooks = self.root / ".backlog" / "hooks"
        hooks.mkdir()
        (hooks / "__init__.py").write_text(
            """
from .notifications import post_transition
from .transitions import pre_transition
""".lstrip(),
            encoding="utf-8",
        )
        (hooks / "transitions.py").write_text(
            """
import os
from pathlib import Path
from typing import Any

from backlog_cli.api import Backlog
from backlog_cli.hooks import Action


def _record(action, current_state, new_state):
    path = Path(os.environ["BACKLOG_HOOK_LOG"])
    with path.open("a", encoding="utf-8") as stream:
        stream.write(
            f"pre:{action.value}:{current_state}:{new_state}\\n"
        )


def pre_transition(
    action: Action,
    trigger: dict[str, Any],
    current_state: str,
    new_state: str,
    backlog: Backlog,
) -> str:
    assert backlog.task(trigger["task_key"]).status == current_state
    _record(action, current_state, new_state)
    if trigger["parameters"].get("block") == "yes":
        return current_state
    return new_state
""".lstrip(),
            encoding="utf-8",
        )
        (hooks / "notifications.py").write_text(
            """
import os
from pathlib import Path
from typing import Any

from backlog_cli.api import Backlog
from backlog_cli.hooks import Action


def post_transition(
    action: Action,
    trigger: dict[str, Any],
    previous_state: str,
    current_state: str,
    backlog: Backlog,
) -> None:
    assert backlog.task(trigger["task_key"]).status == current_state
    path = Path(os.environ["BACKLOG_HOOK_LOG"])
    with path.open("a", encoding="utf-8") as stream:
        stream.write(
            f"post:{action.value}:{previous_state}:{current_state}\\n"
        )
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

    def run_cli_raw(self, *args):
        return subprocess.run(
            [sys.executable, "-m", "backlog_cli.cli", *args],
            cwd=self.root,
            env=self.env,
            text=True,
            capture_output=True,
        )

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

    def test_review_severity_is_fixed_enum_and_only_blockers_gate(self):
        self.assertEqual(
            [level.value for level in ReviewSeverity],
            ["blocker", "nice_to_have", "info"],
        )
        self.run_cli("feature", "add", "--title", "Severity")
        self.run_cli("move", "F-001", "ready")
        self.run_cli("move", "F-001", "in_progress")
        self.run_cli("move", "F-001", "in_review")

        blocker = self.run_cli(
            "review", "open", "F-001",
            "--author", "reviewer",
            "--role", "reviewer",
            "--severity", "blocker",
            "--body", "Requirements are incomplete.",
            json_output=True,
        )
        advisory = self.run_cli(
            "review", "open", "F-001",
            "--author", "reviewer",
            "--role", "reviewer",
            "--severity", "nice_to_have",
            "--body", "Consider adding another example.",
            json_output=True,
        )
        self.assertEqual(blocker["severity"], "blocker")
        self.assertEqual(advisory["severity"], "nice_to_have")

        blocked = self.run_cli_raw("gate", "F-001", "--for", "accepted")
        self.assertEqual(blocked.returncode, 2)
        self.assertIn("1 blocking: C-001", blocked.stdout)

        changed = self.run_cli(
            "review", "severity", "C-001",
            "--severity", "info",
            "--author", "reviewer",
            json_output=True,
        )
        self.assertEqual(changed["severity"], "info")
        allowed = self.run_cli_raw("gate", "F-001", "--for", "accepted")
        self.assertEqual(allowed.returncode, 0, allowed.stderr or allowed.stdout)

        filtered = self.run_cli(
            "review", "list", "F-001",
            "--severity", "nice_to_have",
            json_output=True,
        )
        self.assertEqual([thread["root"] for thread in filtered], ["C-002"])


if __name__ == "__main__":
    unittest.main()

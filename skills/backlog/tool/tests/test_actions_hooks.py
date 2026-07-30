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

    def test_direct_status_transition_is_not_agent_facing(self):
        self.run_cli("feature", "add", "--title", "Action only")
        available = self.run_cli("actions", "F-001", json_output=True)
        self.assertEqual(
            available,
            ["refinement.accepted", "refinement.marked_incomplete"],
        )
        result = self.run_cli_raw("move", "F-001", "ready")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("invalid choice: 'move'", result.stderr)

        from backlog_cli.api import Backlog

        self.assertFalse(hasattr(Backlog, "move"))

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

    def test_review_severity_is_fixed_enum_and_all_feedback_must_be_resolved(self):
        self.assertEqual(
            [level.value for level in ReviewSeverity],
            ["blocker", "nice_to_have", "info"],
        )
        self.run_cli("feature", "add", "--title", "Severity")
        self.run_cli("action", "F-001", "refinement.accepted")
        self.run_cli("action", "F-001", "work.started")
        self.run_cli("action", "F-001", "review.submitted")

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
        self.assertIn("2 blocking: C-001, C-002", blocked.stdout)

        changed = self.run_cli(
            "review", "severity", "C-001",
            "--severity", "info",
            "--author", "reviewer",
            json_output=True,
        )
        self.assertEqual(changed["severity"], "info")
        still_blocked = self.run_cli_raw("gate", "F-001", "--for", "accepted")
        self.assertEqual(still_blocked.returncode, 2)
        self.assertIn("2 blocking: C-001, C-002", still_blocked.stdout)

        filtered = self.run_cli(
            "review", "list", "F-001",
            "--severity", "nice_to_have",
            json_output=True,
        )
        self.assertEqual([thread["root"] for thread in filtered], ["C-002"])

    def test_thread_inherits_reviewer_and_updates_are_incremental(self):
        self.run_cli("story", "add", "--title", "Thread participants")
        self.run_cli(
            "assign", "S-001", "--to", "original-developer",
            "--reviewer", "opening-reviewer",
        )
        opened = self.run_cli(
            "review", "open", "S-001",
            "--author", "opening-reviewer",
            "--body", "Please simplify this.",
            json_output=True,
        )
        self.assertEqual(opened["reviewer"], "opening-reviewer")

        reply = self.run_cli(
            "review", "reply", opened["reply_to"],
            "--author", "replacement-developer",
            "--action", "fix",
            "--body", "Simplified through the public API.",
            json_output=True,
        )
        self.assertEqual(reply["last_comment"]["assignee"], "replacement-developer")
        self.assertEqual(reply["last_comment"]["reviewer"], "opening-reviewer")

        impostor = self.run_cli_raw(
            "review", "reply", reply["reply_to"],
            "--author", "different-reviewer",
            "--role", "reviewer",
            "--action", "accept",
            "--body", "Trying to take over.",
        )
        self.assertNotEqual(impostor.returncode, 0)
        self.assertIn("cannot replace", impostor.stderr)

        neutral = self.run_cli_raw(
            "review", "reply", reply["reply_to"],
            "--author", "opening-reviewer",
            "--action", "comment",
            "--body", "No decision.",
        )
        self.assertNotEqual(neutral.returncode, 0)
        self.assertIn("allowed for reviewer: accept, reject", neutral.stderr)

        empty = self.run_cli_raw(
            "review", "reply", reply["reply_to"],
            "--author", "opening-reviewer",
            "--action", "accept",
            "--body", "   ",
        )
        self.assertNotEqual(empty.returncode, 0)
        self.assertIn("non-empty body", empty.stderr)

        from backlog_cli import api

        with patch.object(Path, "cwd", return_value=self.root), patch.dict(
            os.environ, self.env, clear=False
        ):
            with api.open() as bl:
                initial = bl.review_updates(opened["root"])
                self.assertEqual([comment.key for comment in initial], ["C-001", "C-002"])
                self.assertEqual(initial[-1].assignee, "replacement-developer")
                self.assertEqual(initial[-1].reviewer, "opening-reviewer")
                self.assertEqual(
                    bl.review_updates(opened["root"], after=initial[-1].key), []
                )

        accepted = self.run_cli(
            "review", "reply", reply["reply_to"],
            "--author", "opening-reviewer",
            "--action", "accept",
            "--body", "Accepted after verification.",
            json_output=True,
        )
        with patch.object(Path, "cwd", return_value=self.root), patch.dict(
            os.environ, self.env, clear=False
        ):
            with api.open() as bl:
                updates = bl.review_updates(opened["root"], after=reply["reply_to"])
                self.assertEqual([comment.key for comment in updates], [accepted["reply_to"]])

    def test_task_waits_for_reviewer_acceptance_of_every_blocker(self):
        self.run_cli("feature", "add", "--title", "Refinement review")
        self.run_cli(
            "assign", "F-001", "--to", "developer", "--reviewer", "reviewer"
        )
        self.run_cli("action", "F-001", "refinement.marked_incomplete")
        forged_resolution = self.run_cli_raw(
            "action", "F-001", "feedback.resolved"
        )
        self.assertNotEqual(forged_resolution.returncode, 0)
        self.assertIn("invalid choice: 'feedback.resolved'", forged_resolution.stderr)
        self.run_cli(
            "review", "open", "F-001",
            "--author", "reviewer",
            "--severity", "blocker",
            "--body", "First blocking issue.",
        )
        self.run_cli(
            "review", "open", "F-001",
            "--author", "reviewer",
            "--severity", "blocker",
            "--body", "Second blocking issue.",
        )

        developer_accept = self.run_cli_raw(
            "review", "reply", "C-001",
            "--author", "developer",
            "--action", "accept",
            "--body", "Looks done.",
        )
        self.assertNotEqual(developer_accept.returncode, 0)
        self.assertIn("does not allow developer action 'accept'", developer_accept.stderr)

        self.run_cli(
            "review", "reply", "C-001",
            "--author", "developer",
            "--action", "fix",
            "--body", "Fixed the first issue.",
        )
        self.run_cli(
            "review", "reply", "C-003",
            "--author", "reviewer",
            "--action", "accept",
            "--body", "First fix accepted.",
        )
        self.assertEqual(self.status("F-001"), "incomplete")

        self.run_cli(
            "review", "reply", "C-002",
            "--author", "developer",
            "--action", "fix",
            "--body", "Fixed the second issue.",
        )
        self.run_cli(
            "review", "reply", "C-005",
            "--author", "reviewer",
            "--action", "accept",
            "--body", "Second fix accepted.",
        )
        self.assertEqual(self.status("F-001"), "ready")

        first = self.run_cli(
            "review", "thread", "C-001", json_output=True
        )
        second = self.run_cli(
            "review", "thread", "C-002", json_output=True
        )
        self.assertEqual(first["resolution"], "accepted_by_reviewer")
        self.assertEqual(second["resolution"], "accepted_by_reviewer")

        self.run_cli(
            "review", "reopen", "C-001",
            "--author", "reviewer",
            "--body", "The first issue regressed; please fix it again.",
        )
        self.assertEqual(self.status("F-001"), "incomplete")
        reopened = self.run_cli(
            "review", "thread", "C-001", json_output=True
        )
        self.assertEqual(reopened["state"], "awaiting_developer")
        self.assertEqual(
            reopened["last_comment"]["body"],
            "The first issue regressed; please fix it again.",
        )

        self.run_cli(
            "review", "reply", reopened["reply_to"],
            "--author", "developer",
            "--action", "fix",
            "--body", "Fixed the regression.",
        )
        awaiting_review = self.run_cli(
            "review", "thread", "C-001", json_output=True
        )
        self.run_cli(
            "review", "reply", awaiting_review["reply_to"],
            "--author", "reviewer",
            "--action", "accept",
            "--body", "Regression fix accepted.",
        )
        self.assertEqual(self.status("F-001"), "ready")

        self.run_cli(
            "review", "open", "F-001",
            "--author", "reviewer",
            "--severity", "blocker",
            "--body", "A new blocker was found after refinement.",
        )
        self.assertEqual(self.status("F-001"), "incomplete")

        from backlog_cli.api import Backlog

        self.assertTrue(hasattr(Backlog, "review_reopen"))


if __name__ == "__main__":
    unittest.main()

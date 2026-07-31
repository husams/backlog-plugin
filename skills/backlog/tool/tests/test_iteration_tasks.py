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


class IterationTaskIntegrationTest(unittest.TestCase):
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
            [sys.executable, "-m", "backlog_cli.cli", *args], cwd=self.root,
            env=self.env, text=True, capture_output=True,
        )

    def cli(self, *args, json_output=False):
        command = list(args)
        if json_output:
            command.insert(0, "--json")
        result = self.raw(*command)
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        return json.loads(result.stdout) if json_output else result.stdout

    def open_api(self):
        return (
            patch.dict(os.environ, self.env, clear=False),
            patch.object(Path, "cwd", return_value=self.root),
        )

    def finish_feature(self, key):
        for action in (
            "refinement.accepted", "work.started", "work.completed",
            "review.approved", "delivery.released",
        ):
            self.cli("action", key, action)

    def test_iteration_identity_fields_flow_and_semantic_actions(self):
        created = self.cli(
            "iteration", "add", "--title", "Parallel slice",
            "--description", "Independent delivery", "--priority", "P1",
            "--owner", "product", "--assignee", "codex", "--reviewer", "claude",
            json_output=True,
        )
        self.assertEqual((created["key"], created["task_type"], created["status"]),
                         ("I-001", "iteration", "planned"))
        shown = self.cli("show", "I-001")
        self.assertIn("Independent delivery", shown)
        self.assertIn("product", shown)
        self.assertIn("codex*", shown)
        self.assertIn("claude*", shown)
        self.assertIn("iteration.opened", self.cli("actions", "I-001"))
        self.cli("action", "I-001", "iteration.opened")
        self.assertIn("iteration.closed", self.cli("actions", "I-001"))
        self.cli("action", "I-001", "iteration.closed")
        self.assertIn("iteration.reopened", self.cli("actions", "I-001"))
        self.cli("action", "I-001", "iteration.reopened")
        self.assertIn("Open", self.cli("show", "I-001"))
        statuses = self.cli("statuses", "--type", "iteration")
        self.assertIn("Planned", statuses)
        self.assertIn("Open", statuses)
        self.assertIn("Closed", statuses)

    def test_parallel_open_iterations_priority_board_and_explicit_selection(self):
        self.cli("iteration", "add", "--title", "Later", "--priority", "P2")
        self.cli("iteration", "add", "--title", "First", "--priority", "P0")
        self.cli("feature", "add", "--title", "Alpha", "--assignee", "codex")
        self.cli("feature", "add", "--title", "Beta", "--assignee", "codex")
        self.cli("action", "F-001", "refinement.accepted")
        self.cli("action", "F-002", "refinement.accepted")
        env, cwd = self.open_api()
        with env, cwd, api.open(actor="codex") as bl:
            bl.add_iteration_member("I-001", "F-001")
            bl.add_iteration_member("I-002", "F-002")
        self.cli("action", "I-001", "iteration.opened")
        self.cli("action", "I-002", "iteration.opened")
        board = self.cli("board")
        self.assertIn("== Iterations (2)", board)
        self.assertLess(board.index("I-002"), board.index("I-001"))
        selected = self.cli("next", "--iteration", "I-001")
        self.assertIn("F-001", selected)
        self.assertNotIn("F-002", selected)
        self.assertNotIn("I-001", self.cli("next"))
        env, cwd = self.open_api()
        with env, cwd, api.open(actor="codex") as bl:
            self.assertEqual([t.key for t in bl.startable("codex", iteration="I-002")],
                             ["F-002"])
            self.assertEqual(bl.task_type_counts()["iteration"], 2)

    def test_close_rejects_unfinished_members_without_changing_them(self):
        self.cli("iteration", "add", "--title", "Close gate")
        self.cli("feature", "add", "--title", "Member")
        env, cwd = self.open_api()
        with env, cwd, api.open(actor="codex") as bl:
            bl.add_iteration_member("I-001", "F-001")
        self.cli("action", "I-001", "iteration.opened")
        rejected = self.raw("action", "I-001", "iteration.closed")
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn("F-001=created", rejected.stderr)
        self.assertIn("Created", self.cli("show", "F-001"))
        self.finish_feature("F-001")
        self.cli("action", "I-001", "iteration.closed")
        self.assertIn("Done", self.cli("show", "F-001"))

    def test_details_api_views_and_reopen_membership_conflict(self):
        self.cli("iteration", "add", "--title", "Original")
        self.cli("iteration", "add", "--title", "Current")
        self.cli("feature", "add", "--title", "Shared member")
        env, cwd = self.open_api()
        with env, cwd, api.open(actor="codex") as bl:
            bl.add_iteration_member("I-001", "F-001")
            bl.add_iteration_member("I-002", "F-001")
            self.assertEqual([t.key for t in bl.task("I-001").iteration_members], ["F-001"])
            self.assertEqual([t.key for t in bl.task("F-001").iterations], ["I-001", "I-002"])
        self.cli("action", "I-001", "iteration.opened")
        self.finish_feature("F-001")
        self.cli("action", "I-001", "iteration.closed")
        self.cli("action", "I-002", "iteration.opened")
        detail = self.cli("show", "I-001")
        self.assertIn("members: 1", detail)
        self.assertIn("F-001", detail)
        exported = self.root / "iterations.json"
        self.cli("export", "--out", str(exported))
        payload = json.loads(exported.read_text())
        self.assertEqual(len(payload["tables"]["iteration_member"]), 2)
        rejected = self.raw("action", "I-001", "iteration.reopened")
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn("I-002", rejected.stderr)

    def test_iteration_comments_use_review_threads_and_block_closure_at_all_severities(self):
        self.cli(
            "iteration", "add", "--title", "Feedback cycle",
            "--assignee", "developer", "--reviewer", "assigned-reviewer",
        )

        roots = []
        for severity in ("blocker", "nice_to_have", "info"):
            opened = self.cli(
                "review", "open", "I-001", "--author", "opening-reviewer",
                "--role", "reviewer", "--severity", severity,
                "--body", f"{severity} retrospective observation",
                json_output=True,
            )
            roots.append(opened["root"])
            self.assertEqual(opened["target_type"], "iteration")
            self.assertEqual(opened["severity"], severity)
            self.assertEqual(opened["awaiting_role"], "developer")
            self.assertEqual(opened["awaiting_actor"], "developer")
            self.assertEqual(self.cli("show", "I-001", json_output=True)["status"], "planned")

        developer_inbox = self.cli(
            "review", "inbox", "--actor", "developer", "--item", "I-001",
            json_output=True,
        )
        self.assertEqual([row["severity"] for row in developer_inbox],
                         ["blocker", "nice_to_have", "info"])
        for root, action in zip(roots, ("fix", "comment", "reject")):
            reply = self.cli(
                "review", "reply", root, "--author", "developer", "--action", action,
                "--body", f"Developer disposition via {action}", json_output=True,
            )
            self.assertEqual(reply["awaiting_role"], "reviewer")
            self.assertEqual(reply["awaiting_actor"], "opening-reviewer")

        reviewer_inbox = self.cli(
            "review", "inbox", "--actor", "opening-reviewer", "--item", "I-001",
            json_output=True,
        )
        self.assertEqual([row["root"] for row in reviewer_inbox], roots)
        forbidden = self.raw(
            "review", "reply", reviewer_inbox[0]["reply_to"], "--author", "assigned-reviewer",
            "--action", "accept", "--body", "Attempt to replace opening reviewer",
        )
        self.assertNotEqual(forbidden.returncode, 0)
        self.assertIn("does not allow developer action 'accept'", forbidden.stderr)

        for row in reviewer_inbox:
            self.cli(
                "review", "reply", row["reply_to"], "--author", "opening-reviewer",
                "--action", "accept", "--body", "Accepted for retrospective",
            )
            audit = self.cli("review", "audit", row["root"], json_output=True)
            self.assertEqual(audit["reviewer"], "opening-reviewer")
            self.assertEqual(audit["decisions"][-1]["author"], "opening-reviewer")
            self.assertEqual(audit["decisions"][-1]["action"], "accept")

        self.cli("action", "I-001", "iteration.opened")
        observation = self.cli(
            "review", "open", "I-001", "--author", "opening-reviewer",
            "--role", "reviewer", "--severity", "info",
            "--body", "Unexpected behavior to retain", json_output=True,
        )
        rejected = self.raw("action", "I-001", "iteration.closed")
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn("iteration_comments_closed", rejected.stderr)
        self.assertIn(observation["root"], rejected.stderr)
        self.assertEqual(self.cli("show", "I-001", json_output=True)["status"], "open")
        response = self.cli(
            "review", "reply", observation["root"], "--author", "developer",
            "--action", "comment", "--body", "Captured for the retrospective",
            json_output=True,
        )
        self.cli(
            "review", "reply", response["reply_to"], "--author", "opening-reviewer",
            "--action", "accept", "--body", "Observation recorded",
        )
        self.cli("action", "I-001", "iteration.closed")

        retained = self.cli(
            "review", "list", "I-001", "--state", "all", json_output=True,
        )
        self.assertEqual({row["root"] for row in retained}, {*roots, observation["root"]})
        self.assertTrue(all(row["state"] == "closed" for row in retained))

        after_close = self.cli(
            "review", "open", "I-001", "--author", "opening-reviewer",
            "--role", "reviewer", "--severity", "nice_to_have",
            "--body", "Post-close retrospective note", json_output=True,
        )
        self.assertEqual(self.cli("show", "I-001", json_output=True)["status"], "closed")
        post_response = self.cli(
            "review", "reply", after_close["root"], "--author", "developer",
            "--action", "fix", "--body", "Added the missing note", json_output=True,
        )
        self.cli(
            "review", "reply", post_response["reply_to"], "--author", "opening-reviewer",
            "--action", "accept", "--body", "Verified after closure",
        )
        forbidden_reopen = self.raw(
            "review", "reopen", after_close["root"], "--author", "developer",
            "--body", "Developer cannot reopen",
        )
        self.assertNotEqual(forbidden_reopen.returncode, 0)
        self.cli(
            "review", "reopen", after_close["root"], "--author", "opening-reviewer",
            "--body", "Reconsider during retrospective",
        )
        self.assertEqual(self.cli("show", "I-001", json_output=True)["status"], "closed")

        exported = self.root / "events.json"
        self.cli("export", "--out", str(exported))
        actions = [
            event["to_value"] for event in json.loads(exported.read_text())["tables"]["event"]
            if event["kind"] == "action" and event["entity_key"] == "I-001"
        ]
        self.assertIn("feedback.posted", actions)
        self.assertIn("feedback.resolved", actions)
        self.assertIn("feedback.reopened", actions)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import json
import os
import re
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

    def ready_story(self, title="Story", assignee="codex", feature=None):
        args = ["story", "add", "--title", title, "--assignee", assignee]
        if feature:
            args += ["--feature", feature]
        created = self.cli(*args, json_output=True)
        self.cli("action", created["key"], "refinement.accepted")
        return created["key"]

    def ready_bug(self, title="Bug", assignee="codex"):
        created = self.cli("bug", "add", "--title", title, "--assignee", assignee,
                           json_output=True)
        self.cli("action", created["key"], "refinement.accepted")
        return created["key"]

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

    def test_agents_add_story_from_any_feature_and_standalone_bug(self):
        self.cli("iteration", "add", "--title", "Later", "--priority", "P2")
        self.cli("iteration", "add", "--title", "First", "--priority", "P0")
        self.cli("feature", "add", "--title", "Alpha")
        story = self.ready_story("Across feature", feature="F-001")
        bug = self.ready_bug("Standalone")
        self.cli("action", "I-001", "iteration.opened")
        self.cli("action", "I-002", "iteration.opened")
        self.cli("--actor", "agent-a", "iteration", "member-add", "I-001", story)
        env, cwd = self.open_api()
        with env, cwd, api.open(actor="agent-b") as bl:
            bl.add_iteration_member("I-002", bug)
            self.assertEqual([t.key for t in bl.task("I-001").iteration_members], [story])
            self.assertEqual([t.key for t in bl.task(bug).iterations], ["I-002"])
        board = self.cli("board")
        self.assertIn("== Iterations (2)", board)
        self.assertLess(board.index("I-002"), board.index("I-001"))
        selected = self.cli("next", "--iteration", "I-001")
        self.assertIn(story, selected)
        self.assertNotIn(bug, selected)
        self.assertNotIn("I-001", self.cli("next"))
        env, cwd = self.open_api()
        with env, cwd, api.open(actor="codex") as bl:
            self.assertEqual([t.key for t in bl.startable("codex", iteration="I-002")],
                             [bug])
            self.assertEqual(bl.task_type_counts()["iteration"], 2)

    def test_add_rejects_ineligible_types_statuses_and_non_open_target(self):
        self.cli("iteration", "add", "--title", "Target")
        self.cli("feature", "add", "--title", "Feature")
        self.cli("story", "add", "--feature", "F-001", "--title", "Not ready")
        self.cli("subtask", "add", "--story", "S-001", "--title", "Child")
        for key in ("F-001", "T-001", "I-001"):
            result = self.raw("iteration", "member-add", "I-001", key)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Open Iteration", result.stderr)
        self.cli("action", "I-001", "iteration.opened")
        for key in ("F-001", "T-001", "I-001"):
            result = self.raw("iteration", "member-add", "I-001", key)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("only Ready Stories", result.stderr)
        result = self.raw("iteration", "member-add", "I-001", "S-001")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("only Ready Stories", result.stderr)

    def test_close_rejects_unfinished_members_without_changing_them(self):
        self.cli("iteration", "add", "--title", "Close gate")
        self.cli("action", "I-001", "iteration.opened")
        story = self.ready_story("Member")
        self.cli("iteration", "member-add", "I-001", story)
        rejected = self.raw("action", "I-001", "iteration.closed")
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn(f"{story}=ready", rejected.stderr)
        self.assertIn("Ready", self.cli("show", story))

    def test_remove_preserves_status_and_membership_persists_across_lifecycle(self):
        self.cli("iteration", "add", "--title", "Current")
        self.cli("action", "I-001", "iteration.opened")
        story = self.ready_story("Persistent")
        self.cli("--actor", "membership-agent", "iteration", "member-add", "I-001", story)
        env, cwd = self.open_api()
        with env, cwd, api.open(actor="fixture") as bl:
            task = bl.task(story)
            for status in ("in_progress", "in_review", "needs_work", "accepted", "done", "incomplete"):
                bl._conn.execute("UPDATE task SET status=? WHERE id=?", (status, task.id))
                self.assertEqual([i.key for i in bl.task(story).iterations], ["I-001"])
            bl._conn.execute("UPDATE task SET status='needs_work' WHERE id=?", (task.id,))
        before = self.cli("show", story)
        self.cli("--actor", "membership-agent", "iteration", "member-remove", "I-001", story)
        after = self.cli("show", story)
        self.assertIn("Needs Work", before)
        self.assertIn("Needs Work", after)
        self.assertNotIn("iterations:", after)
        history = self.cli("history", "I-001")
        self.assertIn("iteration.member_added", history)
        self.assertIn("iteration.member_removed", history)
        self.assertIn("membership-agent", history)
        self.assertRegex(history, re.compile(r"20\d\d-\d\d-\d\dT"))

    def test_open_iteration_exclusivity_and_reopen_lists_every_conflict(self):
        self.cli("iteration", "add", "--title", "Original")
        self.cli("iteration", "add", "--title", "Current")
        self.cli("action", "I-001", "iteration.opened")
        self.cli("action", "I-002", "iteration.opened")
        story = self.ready_story("Shared member")
        bug = self.ready_bug("Shared bug")
        self.cli("iteration", "member-add", "I-001", story)
        conflict = self.raw("iteration", "member-add", "I-002", story)
        self.assertNotEqual(conflict.returncode, 0)
        self.assertIn(f"{story} already belongs to Open Iteration I-001", conflict.stderr)
        env, cwd = self.open_api()
        with env, cwd, api.open(actor="legacy-fixture") as bl:
            original = bl.task("I-001")
            current = bl.task("I-002")
            story_task = bl.task(story)
            bug_task = bl.task(bug)
            bl._conn.execute("UPDATE task SET status='closed' WHERE id=?", (original.id,))
            bl._conn.execute("INSERT INTO iteration_member(iteration_id,member_id,created_at) VALUES(?,?,datetime('now'))",
                             (current.id, story_task.id))
            bl._conn.execute("INSERT INTO iteration_member(iteration_id,member_id,created_at) VALUES(?,?,datetime('now'))",
                             (original.id, bug_task.id))
            bl._conn.execute("INSERT INTO iteration_member(iteration_id,member_id,created_at) VALUES(?,?,datetime('now'))",
                             (current.id, bug_task.id))
        rejected = self.raw("action", "I-001", "iteration.reopened")
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn(f"{story} in I-002", rejected.stderr)
        self.assertIn(f"{bug} in I-002", rejected.stderr)
        env, cwd = self.open_api()
        with env, cwd, api.open(actor="verifier") as bl:
            self.assertEqual([i.key for i in bl.task(story).iterations],
                             ["I-001", "I-002"])
            self.assertEqual([i.key for i in bl.task(bug).iterations],
                             ["I-001", "I-002"])

    def test_scoped_next_api_and_board_require_open_iteration_and_show_only_eligible(self):
        self.cli("iteration", "add", "--title", "Scope")
        member = self.ready_story("Member")
        outside = self.ready_bug("Outside")
        self.assertIn(outside, self.cli("next"))
        for command in (("next", "--iteration", "I-001"),
                        ("board", "--iteration", "I-001")):
            rejected = self.raw(*command)
            self.assertNotEqual(rejected.returncode, 0)
            self.assertIn("Open Iteration", rejected.stderr)
        self.cli("action", "I-001", "iteration.opened")
        self.cli("iteration", "member-add", "I-001", member)
        selected = self.cli("next", "--iteration", "I-001")
        board = self.cli("board", "--iteration", "I-001")
        for output in (selected, board):
            self.assertIn(member, output)
            self.assertNotIn(outside, output)
        board_json = self.cli("board", "--iteration", "I-001", json_output=True)
        self.assertEqual([m["key"] for m in board_json["iterations"][0]["eligible_members"]],
                         [member])
        self.cli("action", member, "work.started")
        self.assertIn(member, self.cli("board", "--iteration", "I-001"))

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

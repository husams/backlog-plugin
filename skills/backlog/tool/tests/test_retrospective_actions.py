from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backlog_cli import api
from backlog_cli.db import BacklogError, slugify
from backlog_cli.schema import SCHEMA_VERSION


class RetrospectiveActionIntegrationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.env = {
            **os.environ,
            "BACKLOG_DB": "sqlite",
            "BACK_LOG_URL": "",
            "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
        }
        self.project_slug = slugify(self.root.name)
        self.cli("init", ".")
        self.cli("iteration", "add", "--title", "Iteration one", "--actor", "planner")

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

    def open_api(self, actor="codex", project=None):
        env = patch.dict(os.environ, self.env, clear=False)
        cwd = patch.object(Path, "cwd", return_value=self.root)
        return env, cwd, api.open(project=project, actor=actor)

    def add_action(self, *, actor="facilitator"):
        return self.cli(
            "--actor",
            actor,
            "retrospective",
            "add",
            "--iteration",
            "I-001",
            "--title",
            "Catch missing regression coverage",
            "--issue",
            "The same regression escaped twice.",
            "--solution",
            "Add a reusable test-generation skill and CI check.",
            json_output=True,
        )

    def standup(self, *args):
        script = Path(__file__).resolve().parents[2] / "scripts" / "standup.py"
        return subprocess.run(
            [sys.executable, str(script), *args],
            cwd=self.root,
            env=self.env,
            text=True,
            capture_output=True,
        )

    def test_created_action_blocks_iteration_close_but_ready_done_and_rejected_do_not(
        self,
    ):
        self.cli("action", "I-001", "iteration.opened")
        created = self.add_action()

        refused = self.raw("action", "I-001", "iteration.closed")
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("iteration_retrospective_actions_clear", refused.stderr)
        self.assertIn(created["key"], refused.stderr)

        self.cli(
            "--actor", "product-manager", "retrospective", "accept", created["key"]
        )
        self.cli("action", "I-001", "iteration.closed")

        self.cli("action", "I-001", "iteration.reopened")
        rejected = self.add_action(actor="facilitator-two")
        self.cli(
            "--actor",
            "product-manager",
            "retrospective",
            "reject",
            rejected["key"],
            "--reason",
            "Not worth carrying forward",
        )
        self.cli("action", "I-001", "iteration.closed")

        self.cli("action", "I-001", "iteration.reopened")
        done = self.add_action(actor="facilitator-three")
        self.cli("--actor", "product-manager", "retrospective", "accept", done["key"])
        self.cli(
            "--actor",
            "product-manager",
            "feature",
            "add",
            "--title",
            "Retrospective resolution",
        )
        self.cli(
            "--actor",
            "product-manager",
            "retrospective",
            "close",
            done["key"],
            "--resolution-project",
            self.project_slug,
            "--feature",
            "F-001",
        )
        self.cli("action", "I-001", "iteration.closed")

    def test_board_next_and_standup_surface_open_actions_without_task_counting(self):
        before = self.standup()
        self.assertEqual(before.returncode, 0, before.stderr)
        before_board = next(
            line for line in before.stdout.splitlines() if line.startswith("board")
        )

        created = self.add_action()
        board = self.cli("board")
        next_work = self.cli("next", "--actor", "product-manager")
        self.assertIn("Retrospective actions (1)", board)
        self.assertIn(created["key"], board)
        self.assertIn("accept_or_reject", board)
        self.assertIn("RETROSPECTIVE DECISIONS (1)", next_work)
        self.assertIn("accept_or_reject", next_work)

        board_json = self.cli("board", json_output=True)
        next_json = self.cli("next", "--actor", "product-manager", json_output=True)
        for payload in (board_json, next_json):
            self.assertEqual(
                [
                    (
                        row["key"],
                        row["status"],
                        row["iteration_key"],
                        row["required_decision"],
                    )
                    for row in payload["retrospective_actions"]
                ],
                [(created["key"], "created", "I-001", "accept_or_reject")],
            )

        self.cli(
            "--actor", "product-manager", "retrospective", "accept", created["key"]
        )
        ready_json = self.cli("next", json_output=True)["retrospective_actions"]
        self.assertEqual(ready_json[0]["required_decision"], "close_or_reject")

        after = self.standup("--actor", "product-manager")
        self.assertEqual(after.returncode, 0, after.stderr)
        self.assertIn("open retrospective actions", after.stdout)
        self.assertIn("close_or_reject", after.stdout)
        after_board = next(
            line for line in after.stdout.splitlines() if line.startswith("board")
        )
        self.assertEqual(after_board, before_board)

    def test_cli_create_accept_and_close_against_local_feature(self):
        created = self.add_action()
        self.assertEqual(
            (
                created["key"],
                created["status"],
                created["iteration_key"],
                created["project_slug"],
            ),
            ("R-001", "created", "I-001", self.project_slug),
        )
        self.assertIn("The same regression", created["repeated_issue"])
        self.assertIn("R-001", self.cli("retrospective", "list"))
        self.assertIn("Add a reusable", self.cli("retrospective", "show", "R-001"))

        accepted = self.cli(
            "--actor",
            "product-manager",
            "retrospective",
            "accept",
            "R-001",
            json_output=True,
        )
        self.assertEqual(accepted["status"], "ready")
        self.cli(
            "feature", "add", "--title", "Regression prevention", "--actor", "planner"
        )
        closed = self.cli(
            "--actor",
            "product-manager",
            "retrospective",
            "close",
            "R-001",
            "--resolution-project",
            self.project_slug,
            "--feature",
            "F-001",
            json_output=True,
        )
        self.assertEqual(closed["status"], "done")
        self.assertEqual(
            (
                closed["resolution_project_slug"],
                closed["resolution_task_key"],
                closed["resolution_task_type"],
            ),
            (self.project_slug, "F-001", "feature"),
        )
        history = self.cli("retrospective", "history", "R-001")
        self.assertIn("retrospective.created", history)
        self.assertIn("retrospective.accepted", history)
        self.assertIn("retrospective.closed", history)
        self.assertIn(f"resolution={self.project_slug}:F-001", history)

    def test_creator_cannot_accept_own_action_and_new_action_requires_actor(self):
        missing = self.raw(
            "retrospective",
            "add",
            "--iteration",
            "I-001",
            "--issue",
            "Repeated issue",
            "--solution",
            "Proposed solution",
        )
        self.assertNotEqual(missing.returncode, 0)
        self.assertIn("retrospective action creation requires an actor", missing.stderr)

        created = self.add_action(actor="facilitator")
        self_acceptance = self.raw(
            "--actor", "FACILITATOR", "retrospective", "accept", created["key"]
        )
        self.assertNotEqual(self_acceptance.returncode, 0)
        self.assertIn("cannot perform retrospective acceptance", self_acceptance.stderr)

        actorless = self.raw("retrospective", "accept", created["key"])
        self.assertNotEqual(actorless.returncode, 0)
        self.assertIn("requires an independent actor", actorless.stderr)
        self.assertEqual(
            self.cli("retrospective", "show", created["key"], json_output=True)[
                "status"
            ],
            "created",
        )
        history = self.cli("retrospective", "history", created["key"], json_output=True)
        self.assertEqual(
            [event["kind"] for event in history], ["retrospective.created"]
        )

        accepted = self.cli(
            "--actor",
            "product-manager",
            "retrospective",
            "accept",
            created["key"],
            json_output=True,
        )
        self.assertEqual(accepted["status"], "ready")

    def test_unattributed_v15_retrospective_action_remains_operable(self):
        created = self.add_action(actor="legacy-facilitator")
        database = self.root / ".backlog" / "backlog.db"
        with sqlite3.connect(database) as conn:
            conn.execute(
                "UPDATE retrospective_action SET created_by = NULL WHERE key = ?",
                (created["key"],),
            )
            conn.execute(
                "UPDATE event SET actor = NULL "
                "WHERE entity_key = ? AND kind = 'retrospective.created'",
                (created["key"],),
            )
        accepted = self.cli("retrospective", "accept", created["key"], json_output=True)
        self.assertEqual(accepted["status"], "ready")

    def test_reject_requires_reason_and_is_terminal(self):
        self.add_action()
        env, cwd, session = self.open_api(actor="product-manager")
        with env, cwd, session as bl:
            with self.assertRaisesRegex(
                BacklogError, "reason must be a non-empty string"
            ):
                bl.reject_retrospective_action("R-001", reason="   ")
            self.assertEqual(bl.retrospective_action("R-001").status, "created")
            rejected = bl.reject_retrospective_action(
                "R-001", reason="The recurrence was caused by retired code."
            )
            self.assertEqual(rejected.status, "rejected")
            self.assertEqual(
                rejected.rejection_reason,
                "The recurrence was caused by retired code.",
            )
            with self.assertRaisesRegex(BacklogError, "cannot accept"):
                bl.accept_retrospective_action("R-001")
            self.assertEqual(bl.retrospective_action("R-001").status, "rejected")

    def test_ready_action_can_be_rejected_with_retained_reason(self):
        self.add_action()
        self.cli("--actor", "owner", "retrospective", "accept", "R-001")
        rejected = self.cli(
            "--actor",
            "owner",
            "retrospective",
            "reject",
            "R-001",
            "--reason",
            "The proposed automation is too costly.",
            json_output=True,
        )
        self.assertEqual(rejected["status"], "rejected")
        self.assertEqual(
            rejected["rejection_reason"], "The proposed automation is too costly."
        )
        self.assertEqual(
            self.cli("retrospective", "list", "--status", "rejected").count("R-001"),
            1,
        )

    def test_close_can_reference_feature_in_another_project(self):
        self.add_action()
        self.cli("--actor", "owner", "retrospective", "accept", "R-001")
        self.cli("project", "add", "--name", "Agent Tooling", "--slug", "agent-tooling")
        self.cli(
            "--project",
            "agent-tooling",
            "feature",
            "add",
            "--title",
            "Generate missing tests",
            "--actor",
            "planner",
        )

        env, cwd, session = self.open_api(actor="owner")
        with env, cwd, session as bl:
            closed = bl.close_retrospective_action(
                "R-001", resolution_project="agent-tooling", feature="F-001"
            )
            self.assertEqual(closed.status, "done")
            self.assertEqual(closed.resolution_project_slug, "agent-tooling")
            self.assertEqual(closed.resolution_task_key, "F-001")

    def test_close_can_reference_bug(self):
        self.add_action()
        self.cli("--actor", "owner", "retrospective", "accept", "R-001")
        self.cli(
            "bug", "add", "--title", "Missing release validation", "--actor", "planner"
        )
        env, cwd, session = self.open_api(actor="owner")
        with env, cwd, session as bl:
            closed = bl.close_retrospective_action(
                "R-001", resolution_project=self.project_slug, bug="B-001"
            )
            self.assertEqual(
                (
                    closed.status,
                    closed.resolution_task_key,
                    closed.resolution_task_type,
                ),
                ("done", "B-001", "bug"),
            )

    def test_invalid_resolution_does_not_partially_close(self):
        self.add_action()
        self.cli("--actor", "owner", "retrospective", "accept", "R-001")
        self.cli("feature", "add", "--title", "Feature target", "--actor", "planner")
        self.cli("story", "add", "--title", "Story target", "--actor", "planner")
        env, cwd, session = self.open_api(actor="owner")
        with env, cwd, session as bl:
            with self.assertRaisesRegex(BacklogError, "not a bug"):
                bl.close_retrospective_action(
                    "R-001", resolution_project=self.project_slug, bug="F-001"
                )
            self.assertEqual(bl.retrospective_action("R-001").status, "ready")
            with self.assertRaisesRegex(BacklogError, "Feature or Bug"):
                bl.close_retrospective_action(
                    "R-001", resolution_project=self.project_slug, feature="S-001"
                )
            self.assertEqual(bl.retrospective_action("R-001").status, "ready")

    def test_api_filters_require_typed_status_and_iteration_reference(self):
        self.add_action()
        self.cli("story", "add", "--title", "Not an Iteration", "--actor", "planner")
        env, cwd, session = self.open_api()
        with env, cwd, session as bl:
            self.assertEqual(
                [
                    action.key
                    for action in bl.retrospective_actions(
                        status=api.RetrospectiveStatus.CREATED, iteration="I-001"
                    )
                ],
                ["R-001"],
            )
            with self.assertRaisesRegex(TypeError, "RetrospectiveStatus"):
                bl.retrospective_actions(status="created")
            with self.assertRaisesRegex(BacklogError, "not an Iteration"):
                bl.create_retrospective_action(
                    iteration="S-001",
                    repeated_issue="Repeated issue",
                    proposed_solution="Proposed solution",
                )

    def test_schema_v14_store_migrates_and_can_create_actions(self):
        env, cwd, session = self.open_api()
        with env, cwd, session as bl:
            bl._conn.execute("DROP TABLE retrospective_action")
            bl._conn.execute("UPDATE meta SET value='14' WHERE key='schema_version'")
            bl._conn.commit()

        env, cwd, session = self.open_api()
        with env, cwd, session as bl:
            version = bl._conn.execute(
                "SELECT value FROM meta WHERE key='schema_version'"
            ).fetchone()["value"]
            self.assertEqual(int(version), SCHEMA_VERSION)
            action = bl.create_retrospective_action(
                iteration="I-001",
                repeated_issue="Review feedback repeated",
                proposed_solution="Create a review checklist skill",
            )
            self.assertEqual((action.key, action.status), ("R-001", "created"))

    def test_corrupt_import_is_rejected_without_replacing_current_data(self):
        self.add_action()
        exported = self.root / "export.json"
        self.cli("export", "--out", str(exported))
        payload = json.loads(exported.read_text())
        action = payload["tables"]["retrospective_action"][0]
        story = self.cli(
            "story",
            "add",
            "--title",
            "Wrong target",
            "--actor",
            "planner",
            json_output=True,
        )
        self.cli("export", "--out", str(exported))
        payload = json.loads(exported.read_text())
        action = payload["tables"]["retrospective_action"][0]
        story_row = next(
            row for row in payload["tables"]["task"] if row["id"] == story["id"]
        )
        action.update(
            {
                "status": "done",
                "resolution_project_id": story_row["project_id"],
                "resolution_task_id": story_row["id"],
                "closed_at": action["created_at"],
            }
        )
        exported.write_text(json.dumps(payload))

        imported = self.raw("import", str(exported), "--replace")
        self.assertNotEqual(imported.returncode, 0)
        self.assertIn("requires a Feature or Bug", imported.stderr)
        self.assertIn("R-001", self.cli("retrospective", "show", "R-001"))


if __name__ == "__main__":
    unittest.main()

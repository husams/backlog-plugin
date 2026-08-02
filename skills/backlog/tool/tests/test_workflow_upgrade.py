from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from backlog_cli import db
from _support import attributed_cli_args


class BugIterationWorkflowUpgradeTest(unittest.TestCase):
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
        command = attributed_cli_args(args)
        if json_output:
            command.insert(0, "--json")
        result = self.raw(*command)
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        return json.loads(result.stdout) if json_output else result.stdout

    def connect(self):
        backlog_dir = self.root / ".backlog"
        return db.connect(
            spec=db.StoreSpec(
                dialect="sqlite",
                scope="repo",
                project="backlog",
                artifacts_dir=backlog_dir / "artifacts",
                db_path=backlog_dir / "backlog.db",
                backlog_dir=backlog_dir,
            )
        )

    def test_every_shipped_template_exposes_dedicated_bug_and_iteration_flows(self):
        expected_gates = {
            "dependencies_clear",
            "children_complete",
            "review_threads_closed",
            "iteration_comments_closed",
            "iteration_retrospective_actions_clear",
            "pr_recorded",
            "pr_approved",
            "pr_merged",
            "required_validations_pass",
            "iteration_members_finished",
            "todos_closed",
        }
        rendered = self.cli("workflow", "gates")
        rendered_gates = [
            line.split()[0]
            for line in rendered.splitlines()
            if line.split() and line.split()[0] in expected_gates
        ]
        self.assertEqual(set(rendered_gates), expected_gates)
        self.assertEqual(len(rendered_gates), len(expected_gates))

        for template in ("software-delivery", "lightweight", "research"):
            slug = template.replace("-", "")
            self.cli(
                "project", "add", "--name", slug, "--slug", slug, "--template", template
            )
            bug = self.cli("--project", slug, "statuses", "--type", "bug")
            iteration = self.cli("--project", slug, "statuses", "--type", "iteration")
            self.assertIn("== bug", bug)
            self.assertIn("Planned", iteration)
            self.assertIn("Closed", iteration)

        conn = self.connect()
        try:
            rows = conn.execute(
                "SELECT t.slug,w.task_type,COUNT(s.id) AS statuses,"
                "SUM(CASE WHEN s.satisfies_dependency=1 THEN 1 ELSE 0 END) AS finished "
                "FROM template t JOIN template_workflow w ON w.template_id=t.id "
                "JOIN template_status s ON s.template_workflow_id=w.id "
                "WHERE t.builtin=1 AND w.task_type IN ('bug','iteration') "
                "GROUP BY t.slug,w.task_type"
            ).fetchall()
            self.assertEqual(len(rows), 6)
            self.assertTrue(
                all(row["statuses"] >= 3 and row["finished"] >= 1 for row in rows)
            )
        finally:
            conn.close()

    def test_upgrade_is_idempotent_and_preserves_project_specific_story_flow(self):
        self.cli(
            "workflow",
            "status-add",
            "--type",
            "story",
            "--slug",
            "qa",
            "--display",
            "QA",
            "--category",
            "review",
            "--after",
            "in_review",
        )
        conn = self.connect()
        try:
            project_id = conn.execute(
                "SELECT id FROM project ORDER BY id LIMIT 1"
            ).fetchone()["id"]
            conn.execute(
                "DELETE FROM workflow WHERE project_id=? AND task_type IN ('bug','iteration')",
                (project_id,),
            )
            conn.commit()
        finally:
            conn.close()

        first = self.cli("workflow", "upgrade", json_output=True)
        second = self.cli("workflow", "upgrade", json_output=True)
        self.assertEqual(first["added"], ["bug", "iteration"])
        self.assertEqual(second["added"], [])
        self.assertIn("QA", self.cli("statuses", "--type", "story"))
        self.assertIn("In Review", self.cli("statuses", "--type", "bug"))
        self.assertIn("Planned", self.cli("statuses", "--type", "iteration"))

    def test_doctor_remediates_missing_new_type_flows_and_commands_then_work(self):
        self.cli("bug", "add", "--title", "Defect")
        self.cli("iteration", "add", "--title", "Cycle")
        conn = self.connect()
        try:
            project_id = conn.execute(
                "SELECT id FROM project ORDER BY id LIMIT 1"
            ).fetchone()["id"]
            conn.execute(
                "DELETE FROM workflow WHERE project_id=? AND task_type IN ('bug','iteration')",
                (project_id,),
            )
            conn.commit()
        finally:
            conn.close()

        doctor = self.raw("doctor")
        self.assertNotEqual(doctor.returncode, 0)
        self.assertIn("backlog workflow upgrade", doctor.stdout)
        self.cli("workflow", "upgrade")
        self.assertIn("Created", self.cli("statuses", "--type", "bug"))
        self.assertIn("iteration.opened", self.cli("actions", "I-001"))
        self.assertIn("refinement.accepted", self.cli("actions", "B-001"))
        self.assertIn("OK", self.cli("doctor"))

    def test_export_import_preserves_flows_and_rejects_new_types_without_them(self):
        self.cli(
            "workflow",
            "status-add",
            "--type",
            "bug",
            "--slug",
            "triaged",
            "--display",
            "Triaged",
            "--category",
            "ready",
            "--after",
            "created",
        )
        self.cli("bug", "add", "--title", "Round trip")
        self.cli("iteration", "add", "--title", "Round trip")
        exported = self.root / "export.json"
        self.cli("export", "--out", str(exported))
        payload = json.loads(exported.read_text())
        self.assertIn("workflow", payload["tables"])
        self.assertIn("template_workflow", payload["tables"])

        self.cli("import", str(exported), "--replace")
        self.assertIn("Triaged", self.cli("statuses", "--type", "bug"))
        self.assertIn("iteration.opened", self.cli("actions", "I-001"))

        payload["tables"]["workflow"] = [
            row
            for row in payload["tables"]["workflow"]
            if row["task_type"] not in ("bug", "iteration")
        ]
        broken = self.root / "broken.json"
        broken.write_text(json.dumps(payload))
        rejected = self.raw("import", str(broken), "--replace")
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn("bug tasks require a bug workflow", rejected.stderr)
        self.assertIn("Triaged", self.cli("statuses", "--type", "bug"))

    def test_iteration_feedback_is_state_neutral_and_close_checks_members_and_comments(
        self,
    ):
        for index, (state, actions) in enumerate(
            (
                ("Planned", ()),
                ("Open", ("iteration.opened",)),
                ("Closed", ("iteration.opened", "iteration.closed")),
            ),
            start=1,
        ):
            key = f"I-{index:03d}"
            self.cli(
                "iteration",
                "add",
                "--title",
                state,
                "--assignee",
                "codex",
                "--reviewer",
                "claude",
            )
            for action in actions:
                self.cli("action", key, action)
            opened = self.cli(
                "review",
                "open",
                key,
                "--author",
                "claude",
                "--severity",
                "blocker",
                "--body",
                f"{state} feedback",
                json_output=True,
            )
            root = opened["root"]
            self.assertIn(state, self.cli("show", key))
            fixed = self.cli(
                "review",
                "reply",
                root,
                "--author",
                "codex",
                "--action",
                "fix",
                "--body",
                "Addressed",
                json_output=True,
            )
            latest = fixed["reply_to"]
            self.cli(
                "review",
                "reply",
                latest,
                "--author",
                "claude",
                "--action",
                "accept",
                "--body",
                "Verified",
            )
            self.assertIn(state, self.cli("show", key))
            self.cli(
                "review",
                "reopen",
                root,
                "--author",
                "claude",
                "--body",
                "Recheck",
            )
            self.assertIn(state, self.cli("show", key))

        self.cli(
            "iteration",
            "add",
            "--title",
            "Close gates",
            "--assignee",
            "codex",
            "--reviewer",
            "claude",
        )
        self.cli("action", "I-004", "iteration.opened")
        self.cli("feature", "add", "--title", "Unfinished member")
        conn = self.connect()
        try:
            iteration = conn.execute(
                "SELECT id FROM task WHERE key='I-004'"
            ).fetchone()["id"]
            member = conn.execute("SELECT id FROM task WHERE key='F-001'").fetchone()[
                "id"
            ]
            conn.execute(
                "INSERT INTO iteration_member(iteration_id,member_id,created_at) "
                "VALUES(?,?,datetime('now'))",
                (iteration, member),
            )
            conn.commit()
        finally:
            conn.close()
        self.cli(
            "review",
            "open",
            "I-004",
            "--author",
            "claude",
            "--severity",
            "info",
            "--body",
            "Close this discussion first",
        )
        rejected = self.raw("action", "I-004", "iteration.closed")
        self.assertIn("iteration_members_finished", rejected.stderr)
        self.assertIn("iteration_comments_closed", rejected.stderr)
        self.assertNotIn("dependencies_clear", rejected.stderr)


if __name__ == "__main__":
    unittest.main()

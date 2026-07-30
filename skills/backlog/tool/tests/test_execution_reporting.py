from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from backlog_cli import api, core, db, execution


class ExecutionReportingTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.backlog_dir = self.root / ".backlog"
        self.backlog_dir.mkdir()
        self.store = db.StoreSpec(
            dialect="sqlite", scope="repo", project="sample",
            artifacts_dir=self.backlog_dir / "artifacts",
            db_path=self.root / "backlog.db", backlog_dir=self.backlog_dir,
        )
        self.conn = db.connect(spec=self.store, create=True)
        self.project = db.get_or_create_project(self.conn, "sample", self.store)
        self.task = core.add_task(
            self.conn, self.project["id"], "story", "Reporting",
        )
        self.bl = api.Backlog(self.conn, self.project, self.store, actor="S-010")
        self.policy = execution.ExecutionPolicy(
            shell_enabled=True, max_output_bytes=4096, max_batch_seconds=60,
        )

    def tearDown(self):
        self.conn.close()
        self.temp.cleanup()

    def shell_item(self, *, kind="checklist", required=True, expected=0):
        item = core.add_item(
            self.conn, self.project["id"], self.task["key"], kind, "validate",
        )
        execution.set_executable(self.conn, item["id"], {
            "executor": "shell",
            "requirement": "required" if required else "advisory",
            "shell": {
                "command": (
                    f"{shlex.quote(sys.executable)} -c "
                    + shlex.quote("print('actual')")
                ),
                "timeout_seconds": 5,
                "expected_exit_code": expected,
            },
        })
        return item

    def cli(self, *args):
        env = {
            **os.environ,
            "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
            "BACKLOG_DB": "sqlite",
            "BACK_LOG_URL": str(self.store.db_path),
            "BACKLOG_PROJECT": "sample",
            "BACKLOG_DIR": str(self.backlog_dir),
        }
        return subprocess.run(
            [sys.executable, "-m", "backlog_cli.cli", "--json", *args],
            cwd=self.root, env=env, text=True, capture_output=True,
        )

    def test_history_is_bounded_and_includes_actor_expected_actual_diagnostic(self):
        item = self.shell_item(expected=3)
        for _ in range(3):
            self.bl.run_item(item["id"], self.root, policy=self.policy)
        history = self.bl.execution_history(item["id"], limit=2)
        self.assertEqual(len(history), 2)
        self.assertEqual(history[0]["actor"], "S-010")
        self.assertEqual(history[0]["expected"]["exit_code"], 3)
        self.assertEqual(history[0]["actual"]["exit_code"], 0)
        self.assertIn("exit_code_mismatch", history[0]["diagnostic"])
        result = self.cli(
            "validation", "history", str(item["id"]), "--limit", "1",
            "--project-root", str(self.root),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(json.loads(result.stdout)), 1)
        with self.assertRaisesRegex(db.BacklogError, "between 1 and 100"):
            self.bl.execution_history(item["id"], limit=101)

    def test_pass_auto_checks_required_checklist_but_criteria_remains_non_tickable(self):
        checklist = self.shell_item()
        result = self.bl.run_item(checklist["id"], self.root, policy=self.policy)
        self.assertEqual(result.status, "pass")
        row = self.conn.execute(
            "SELECT done FROM task_item WHERE id=?", (checklist["id"],)
        ).fetchone()
        self.assertEqual(row["done"], 1)

        criterion = self.shell_item(kind="acceptance_criteria")
        self.bl.run_item(criterion["id"], self.root, policy=self.policy)
        with self.assertRaisesRegex(db.BacklogError, "not tickable"):
            core.tick_item(
                self.conn, self.project["id"], criterion["id"], actor="S-010",
            )

    def test_manual_completion_requires_current_pass_or_audited_waiver(self):
        item = self.shell_item(expected=9)
        failed = self.bl.run_item(item["id"], self.root, policy=self.policy)
        self.assertEqual(failed.status, "fail")
        with self.assertRaisesRegex(db.BacklogError, "has fail validation"):
            core.tick_item(
                self.conn, self.project["id"], item["id"], actor="S-010",
            )
        with self.assertRaisesRegex(db.BacklogError, "non-empty reason"):
            core.tick_item(
                self.conn, self.project["id"], item["id"], actor="S-010",
                waive_validation=True, waiver_reason=" ",
            )
        core.tick_item(
            self.conn, self.project["id"], item["id"], actor="S-010",
            waive_validation=True, waiver_reason="Reviewed external evidence",
        )
        waiver = execution.current_waiver(self.conn, item["id"])
        self.assertEqual(waiver["actor"], "S-010")
        self.assertEqual(
            execution.required_validations_pass(self.conn, self.task["id"])[0],
            True,
        )

        spec = execution.executable_item(self.conn, item["id"])["execution_spec"]
        spec["shell"]["expected_exit_code"] = 0
        execution.set_executable(self.conn, item["id"], spec)
        self.bl.run_item(item["id"], self.root, policy=self.policy)
        self.assertIsNone(execution.current_waiver(self.conn, item["id"]))

    def test_stale_spec_blocks_gate_and_waiver_restores_it(self):
        item = self.shell_item()
        self.bl.run_item(item["id"], self.root, policy=self.policy)
        self.assertTrue(
            execution.required_validations_pass(self.conn, self.task["id"])[0]
        )

    def test_source_change_makes_a_previous_pass_stale(self):
        repo = self.root / "checkout"
        repo.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        subprocess.run(
            ["git", "config", "user.email", "test@example.invalid"],
            cwd=repo, check=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Test"], cwd=repo, check=True,
        )
        source = repo / "tracked.txt"
        source.write_text("one\n", encoding="utf-8")
        subprocess.run(["git", "add", "tracked.txt"], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-qm", "fixture"], cwd=repo, check=True)

        item = self.shell_item()
        self.bl.run_item(item["id"], repo, policy=self.policy)
        self.assertEqual(execution.item_state(self.conn, item["id"], repo), "pass")
        source.write_text("two\n", encoding="utf-8")
        self.assertEqual(
            execution.item_state(self.conn, item["id"], repo), "pending"
        )
        history = self.bl.execution_history(
            item["id"], limit=1, project_root=repo,
        )
        self.assertTrue(history[0]["stale"])
        self.assertFalse(
            execution.required_validations_pass(
                self.conn, self.task["id"], repo,
            )[0]
        )
        spec = execution.executable_item(self.conn, item["id"])["execution_spec"]
        spec["shell"]["expected_exit_code"] = 4
        execution.set_executable(self.conn, item["id"], spec)
        self.assertEqual(execution.item_state(self.conn, item["id"]), "pending")
        self.assertFalse(
            execution.required_validations_pass(self.conn, self.task["id"])[0]
        )
        self.bl.waive_validation(item["id"], reason="Temporary release exception")
        self.assertTrue(
            execution.required_validations_pass(self.conn, self.task["id"])[0]
        )

    def test_doctor_reports_skipped_and_waived_until_pass_supersedes(self):
        item = self.shell_item()
        self.bl.run_item(
            item["id"], self.root, policy=execution.ExecutionPolicy(),
        )
        self.bl.waive_validation(item["id"], reason="Policy owner approved")
        report = self.cli("doctor")
        self.assertEqual(report.returncode, 0, report.stderr)
        diagnostics = json.loads(report.stdout)["diagnostics"]
        joined = "\n".join(diagnostics)
        self.assertIn("validation_skipped", joined)
        self.assertIn("validation_waived", joined)
        self.assertIn("Policy owner approved", joined)

        self.bl.run_item(item["id"], self.root, policy=self.policy)
        report = self.cli("doctor")
        joined = "\n".join(json.loads(report.stdout)["diagnostics"])
        self.assertNotIn("validation_skipped", joined)
        self.assertNotIn("validation_waived", joined)

    def test_aggregate_exit_is_nonzero_for_pending_failed_error_or_skipped_required(self):
        passed = self.shell_item()
        failed = self.shell_item(expected=7)
        policy_path = self.backlog_dir / "execution.yaml"
        policy_path.write_text(
            "shell_enabled: true\nmax_output_bytes: 4096\nmax_batch_seconds: 60\n",
            encoding="utf-8",
        )
        result = self.cli(
            "validation", "run-all", self.task["key"],
            "--project-root", str(self.root),
        )
        self.assertEqual(result.returncode, 2)
        statuses = [row["status"] for row in json.loads(result.stdout)]
        self.assertEqual(statuses, ["pass", "fail"])
        self.assertEqual(
            execution.required_results_pass(self.conn, self.task["id"])[1],
            [failed["id"]],
        )
        self.assertEqual(execution.item_state(self.conn, passed["id"]), "pass")

    def test_existing_v10_store_receives_additive_reporting_shape(self):
        self.conn.execute("DROP TABLE validation_waiver")
        self.conn.commit()
        self.conn.close()
        self.conn = db.connect(spec=self.store)
        self.assertTrue(self.conn.table_exists("validation_waiver"))
        columns = {
            row["name"] for row in self.conn.execute(
                "PRAGMA table_info(execution_result)"
            ).fetchall()
        }
        self.assertIn("actor", columns)


if __name__ == "__main__":
    unittest.main()

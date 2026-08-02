from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backlog_cli import api, core, db, execution


class ShellExecutionTest(unittest.TestCase):
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
            self.conn, self.project["id"], "story", "Shell validation",
            actor="fixture-creator",
        )
        self.bl = api.Backlog(self.conn, self.project, self.store, actor="runner")

    def tearDown(self):
        self.conn.close()
        self.temp.cleanup()

    def add_shell(self, command: str, *, timeout: int = 5, exit_code: int = 0,
                  stdout=None, stderr=None, environment=None):
        item = core.add_item(
            self.conn, self.project["id"], self.task["key"],
            "acceptance_criteria", f"execute {command}",
        )
        spec = {
            "executor": "shell",
            "shell": {
                "command": command,
                "timeout_seconds": timeout,
                "working_directory": ".",
                "expected_exit_code": exit_code,
            },
        }
        if stdout is not None:
            spec["shell"]["stdout"] = stdout
        if stderr is not None:
            spec["shell"]["stderr"] = stderr
        if environment is not None:
            spec["shell"]["environment"] = environment
        execution.set_executable(self.conn, item["id"], spec)
        return item

    def policy(self, **changes):
        values = {
            "shell_enabled": True,
            "allowed_working_directories": (".",),
            "max_timeout_seconds": 30,
            "max_output_bytes": 4096,
            "max_batch_seconds": 60,
        }
        values.update(changes)
        return execution.ExecutionPolicy(**values)

    def actions(self):
        return [
            row["to_value"] for row in self.conn.execute(
                "SELECT to_value FROM event WHERE task_id=? AND kind='action' ORDER BY id",
                (self.task["id"],),
            ).fetchall()
        ]

    def python(self, source: str) -> str:
        return f"{shlex.quote(sys.executable)} -c {shlex.quote(source)}"

    def test_matchers_pass_and_argument_safe_spawn_uses_minimal_environment(self):
        marker = self.root / "must-not-exist"
        command = self.python(
            "import os,sys; "
            "print('exact'); "
            "sys.stderr.write('contains regex-42'); "
            f"print({str(marker)!r}); "
            "print('HOME=' + str(os.environ.get('HOME')))"
        )
        item = self.add_shell(
            command,
            stdout={"contains": "HOME=None"},
            stderr={"regex": r"regex-\d+"},
        )
        result = self.bl.run_item(item["id"], self.root, policy=self.policy())
        self.assertEqual(result.status, "pass")
        self.assertFalse(marker.exists())
        self.assertEqual(self.actions(), ["check.started", "check.passed"])
        stored = self.conn.execute(
            "SELECT * FROM execution_result WHERE item_id=?", (item["id"],)
        ).fetchone()
        self.assertEqual(stored["actual_exit_code"], 0)
        self.assertGreaterEqual(stored["duration_ms"], 0)

    def test_runtime_environment_secret_never_enters_shared_or_api_surfaces(self):
        secret = "super-secret-runtime-value"
        item = self.add_shell(
            self.python("import os; print(os.environ['VALIDATION_TOKEN'])"),
            environment=["VALIDATION_TOKEN"],
        )
        policy = self.policy(
            allowed_environment_variables=("VALIDATION_TOKEN",)
        )
        with patch.dict(os.environ, {"VALIDATION_TOKEN": secret}):
            result = self.bl.run_item(
                item["id"], self.root, policy=policy
            )
        self.assertEqual(result.status, "pass")
        self.assertEqual(result.stdout, "[REDACTED]\n")
        executable = execution.executable_item(self.conn, item["id"])
        self.assertEqual(
            executable["execution_spec"]["shell"]["environment"],
            ["VALIDATION_TOKEN"],
        )
        result_row = self.conn.execute(
            "SELECT * FROM execution_result WHERE item_id=?", (item["id"],)
        ).fetchone()
        events = self.conn.execute(
            "SELECT * FROM event WHERE task_id=? ORDER BY id", (self.task["id"],)
        ).fetchall()
        surfaces = [
            json.dumps(executable, default=str),
            json.dumps(result.as_dict(), default=str),
            json.dumps({key: result_row[key] for key in result_row.keys()}, default=str),
            json.dumps([
                {key: row[key] for key in row.keys()} for row in events
            ], default=str),
        ]
        self.assertTrue(all(secret not in surface for surface in surfaces))

        unsafe = executable["execution_spec"]
        unsafe["shell"]["environment"] = {"VALIDATION_TOKEN": secret}
        with self.assertRaisesRegex(
            db.BacklogError, "list of trusted-local variable names"
        ):
            execution.parse_spec(unsafe)

    def test_expectation_mismatch_is_fail_and_timeout_is_error(self):
        mismatch = self.add_shell(
            self.python("print('actual')"), stdout={"equals": "expected\n"}
        )
        failed = self.bl.run_item(mismatch["id"], self.root, policy=self.policy())
        self.assertEqual((failed.status, failed.diagnostic), ("fail", "stdout_mismatch"))

        timed = self.add_shell(
            self.python("import time; time.sleep(2)"), timeout=1
        )
        timeout = self.bl.run_item(timed["id"], self.root, policy=self.policy())
        self.assertEqual((timeout.status, timeout.diagnostic), ("error", "timed_out"))
        self.assertEqual(
            self.actions(),
            ["check.started", "check.failed", "check.started", "check.timed_out"],
        )

    def test_resolution_error_emits_failed_without_started(self):
        item = self.add_shell("command-that-cannot-exist-anywhere")
        result = self.bl.run_item(item["id"], self.root, policy=self.policy())
        self.assertEqual(result.status, "error")
        self.assertEqual(result.diagnostic, "command_unavailable:command-that-cannot-exist-anywhere")
        self.assertEqual(self.actions(), ["check.failed"])

    def test_policy_denial_starts_no_process_and_emits_no_action(self):
        item = self.add_shell(self.python("raise SystemExit('must not run')"))
        with patch("backlog_cli.execution.runner.subprocess.Popen") as popen:
            result = self.bl.run_item(
                item["id"], self.root, policy=execution.ExecutionPolicy()
            )
        popen.assert_not_called()
        self.assertEqual((result.status, result.diagnostic),
                         ("skipped", "policy_denied:shell_disabled"))
        self.assertEqual(self.actions(), [])
        row = self.conn.execute(
            "SELECT status,reason,LENGTH(stdout) AS n FROM execution_result "
            "WHERE item_id=?", (item["id"],),
        ).fetchone()
        self.assertEqual((row["status"], row["reason"], row["n"]),
                         ("skipped", "policy_denied", 0))

    def test_policy_can_deny_command_and_requested_output_limit(self):
        item = self.add_shell(self.python("print('denied')"))
        executable = execution.executable_item(self.conn, item["id"])
        command_policy = self.policy(allowed_commands=("some-other-command",))
        denied = self.bl.run_item(item["id"], self.root, policy=command_policy)
        self.assertEqual(denied.diagnostic, "policy_denied:command_denied")

        value = executable["execution_spec"]
        value["shell"]["output_limit_bytes"] = 5000
        execution.set_executable(self.conn, item["id"], value)
        output_denied = self.bl.run_item(
            item["id"], self.root, policy=self.policy(max_output_bytes=1000)
        )
        self.assertEqual(
            output_denied.diagnostic,
            "policy_denied:output_limit_exceeds_policy",
        )
        self.assertEqual(self.actions(), [])

    def test_output_capture_is_combined_bounded_and_audited(self):
        item = self.add_shell(
            self.python("import sys; print('x'*5000); sys.stderr.write('y'*5000)")
        )
        result = self.bl.run_item(
            item["id"], self.root, policy=self.policy(max_output_bytes=1000)
        )
        self.assertEqual(result.status, "pass")
        self.assertTrue(result.output_truncated)
        self.assertLessEqual(
            len(result.stdout.encode()) + len(result.stderr.encode()), 1000
        )
        self.assertNotIn("x" * 1001, result.stdout)

    def test_batch_budget_skips_current_and_remaining_without_actions(self):
        first = self.add_shell(self.python("print('first')"), timeout=2)
        second = self.add_shell(self.python("print('second')"), timeout=2)
        with patch("backlog_cli.execution.runner.subprocess.Popen") as popen:
            results = self.bl.run_task(
                self.task["key"], self.root,
                policy=self.policy(max_batch_seconds=1),
            )
        popen.assert_not_called()
        self.assertEqual([r.item_id for r in results], [first["id"], second["id"]])
        self.assertEqual([r.status for r in results], ["skipped", "skipped"])
        self.assertTrue(all(r.diagnostic == "batch_budget_exhausted" for r in results))
        self.assertEqual(self.actions(), [])

    def test_batch_defaults_to_run_everything_and_fail_fast_is_explicit(self):
        first = self.add_shell(
            self.python("print('bad')"), stdout={"equals": "good\n"}
        )
        second = self.add_shell(self.python("print('good')"))
        all_results = self.bl.run_task(
            self.task["key"], self.root, policy=self.policy()
        )
        self.assertEqual([r.item_id for r in all_results], [first["id"], second["id"]])
        self.assertEqual([r.status for r in all_results], ["fail", "pass"])

        fast_results = self.bl.run_task(
            self.task["key"], self.root, fail_fast=True, policy=self.policy()
        )
        self.assertEqual([(r.item_id, r.status) for r in fast_results],
                         [(first["id"], "fail")])

    def test_cli_run_uses_same_structured_contract(self):
        item = self.add_shell(self.python("print('cli')"))
        (self.backlog_dir / "execution-policy.yaml").write_text(
            "shell_enabled: true\nmax_output_bytes: 4096\nmax_batch_seconds: 60\n",
            encoding="utf-8",
        )
        env = {
            **os.environ,
            "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
            "BACKLOG_DB": "sqlite",
            "BACK_LOG_URL": str(self.store.db_path),
            "BACKLOG_PROJECT": "sample",
            "BACKLOG_DIR": str(self.backlog_dir),
        }
        result = subprocess.run(
            [
                sys.executable, "-m", "backlog_cli.cli", "--json",
                "validation", "run", str(item["id"]),
                "--project-root", str(self.root), "--actor", "cli-runner",
            ],
            cwd=self.root, env=env, text=True, capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual((payload["status"], payload["stdout"]), ("pass", "cli\n"))


if __name__ == "__main__":
    unittest.main()

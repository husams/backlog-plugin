from __future__ import annotations

import tempfile
import subprocess
import json
import os
import sys
import unittest
from pathlib import Path

from backlog_cli import db, execution
from backlog_cli.db import BacklogError


class ExecutionContractTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        spec = db.StoreSpec(
            dialect="sqlite", scope="repo", project="sample",
            artifacts_dir=self.root / ".backlog" / "artifacts",
            db_path=self.root / "backlog.db", backlog_dir=self.root / ".backlog",
        )
        self.conn = db.connect(spec=spec, create=True)
        project = db.get_or_create_project(self.conn, "sample", spec)
        from backlog_cli import core
        self.task = core.add_task(self.conn, project["id"], "story", "Executable")
        self.item = core.add_item(
            self.conn, project["id"], self.task["key"], "acceptance_criteria", "It works"
        )

    def tearDown(self):
        self.conn.close()
        self.temp.cleanup()

    def shell(self, requirement="required"):
        return {
            "executor": "shell",
            "requirement": requirement,
            "shell": {
                "command": "python -m unittest",
                "timeout_seconds": 30,
                "working_directory": ".",
                "expected_exit_code": 0,
                "stdout": {"contains": "OK"},
                "environment": {"CI": "1"},
            },
        }

    def test_plain_items_are_unchanged_and_executable_is_exactly_one_kind(self):
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) AS n FROM executable_item").fetchone()["n"], 0
        )
        with self.assertRaises(BacklogError):
            execution.parse_spec({
                **self.shell(),
                "hook": {"name": "tests.run", "arguments": {}, "expected_result": True},
            })
        row = execution.set_executable(self.conn, self.item["id"], self.shell())
        self.assertEqual(row["executor"], "shell")
        self.assertEqual(row["requirement"], "required")
        self.assertTrue(row["spec_fingerprint"].startswith("sha256:"))
        from backlog_cli import core
        project = db.require_project(self.conn, "sample")
        note = core.add_item(
            self.conn, project["id"], self.task["key"], "note", "Remember this"
        )
        with self.assertRaisesRegex(BacklogError, "only acceptance criteria and checklist"):
            execution.set_executable(self.conn, note["id"], self.shell())
        self.assertIsNone(
            self.conn.execute(
                "SELECT item_id FROM executable_item WHERE item_id=?", (note["id"],)
            ).fetchone()
        )

    def test_fingerprint_ignores_task_metadata_and_changes_with_spec(self):
        first = execution.set_executable(self.conn, self.item["id"], self.shell())
        self.conn.execute("UPDATE task SET title='Renamed' WHERE id=?", (self.task["id"],))
        same = execution.set_executable(self.conn, self.item["id"], self.shell())
        changed = self.shell()
        changed["shell"]["timeout_seconds"] = 31
        third = execution.set_executable(self.conn, self.item["id"], changed)
        self.assertEqual(first["spec_fingerprint"], same["spec_fingerprint"])
        self.assertNotEqual(first["spec_fingerprint"], third["spec_fingerprint"])

    def test_pending_and_required_vs_advisory_gate_semantics(self):
        required = execution.set_executable(self.conn, self.item["id"], self.shell())
        self.assertEqual(execution.item_state(self.conn, self.item["id"]), "pending")
        self.assertEqual(
            execution.required_validations_pass(self.conn, self.task["id"]),
            (False, [self.item["id"]]),
        )
        execution.record_result(
            self.conn, self.item["id"], required["spec_fingerprint"], "pass"
        )
        self.assertEqual(execution.item_state(self.conn, self.item["id"]), "pass")
        self.assertEqual(
            execution.required_validations_pass(self.conn, self.task["id"]), (True, [])
        )
        advisory = execution.set_executable(self.conn, self.item["id"], self.shell("advisory"))
        self.assertEqual(execution.item_state(self.conn, self.item["id"]), "pending")
        self.assertEqual(
            execution.required_validations_pass(self.conn, self.task["id"]), (True, [])
        )
        self.assertNotEqual(required["spec_fingerprint"], advisory["spec_fingerprint"])

    def test_terminal_statuses_and_policy_denial_are_stable(self):
        row = execution.set_executable(self.conn, self.item["id"], self.shell())
        for status in ("pass", "fail", "error"):
            result = execution.record_result(
                self.conn, self.item["id"], row["spec_fingerprint"], status
            )
            self.assertEqual(result["status"], status)
        with self.assertRaises(BacklogError):
            execution.record_result(
                self.conn, self.item["id"], row["spec_fingerprint"], "skipped",
                reason="timeout",
            )
        result = execution.record_result(
            self.conn, self.item["id"], row["spec_fingerprint"], "skipped",
            reason="policy_denied",
        )
        self.assertEqual((result["status"], result["reason"]), ("skipped", "policy_denied"))

    def test_unavailable_source_is_persisted_reported_and_superseded(self):
        row = execution.set_executable(self.conn, self.item["id"], self.shell())
        first = execution.record_result(
            self.conn, self.item["id"], row["spec_fingerprint"], "pass",
            source=execution.SourceIdentity(unavailable=True),
        )
        self.assertEqual(first["source_revision_unavailable"], 1)
        self.assertEqual(
            execution.source_revision_unavailable_items(self.conn), [self.item["id"]]
        )
        doctor = subprocess.run(
            [sys.executable, "-m", "backlog_cli.cli", "--json", "doctor"],
            cwd=self.root,
            env={
                **os.environ,
                "BACKLOG_DB": "sqlite",
                "BACK_LOG_URL": str(self.conn.spec.db_path),
                "BACKLOG_PROJECT": "sample",
                "BACKLOG_DIR": str(self.root / ".backlog"),
            },
            text=True, capture_output=True, check=True,
        )
        report = json.loads(doctor.stdout)
        self.assertEqual(
            report["diagnostics"],
            [f"source_revision_unavailable: latest fresh result for item "
             f"#{self.item['id']} has no VCS source identity"],
        )
        second = execution.record_result(
            self.conn, self.item["id"], row["spec_fingerprint"], "pass",
            source=execution.SourceIdentity(revision="abc123"),
        )
        self.assertEqual(second["source_revision_unavailable"], 0)
        self.assertEqual(execution.source_revision_unavailable_items(self.conn), [])
        cleared = subprocess.run(
            [sys.executable, "-m", "backlog_cli.cli", "--json", "doctor"],
            cwd=self.root,
            env={
                **os.environ,
                "BACKLOG_DB": "sqlite",
                "BACK_LOG_URL": str(self.conn.spec.db_path),
                "BACKLOG_PROJECT": "sample",
                "BACKLOG_DIR": str(self.root / ".backlog"),
            },
            text=True, capture_output=True, check=True,
        )
        self.assertEqual(json.loads(cleared.stdout)["diagnostics"], [])

    def test_local_policy_defaults_disabled_and_enforces_all_restrictions(self):
        spec = execution.parse_spec(self.shell())
        policy = execution.load_policy(self.root)
        self.assertEqual(policy.denial_reason(spec), "shell_disabled")
        backlog = self.root / ".backlog"
        backlog.mkdir()
        (backlog / "execution-policy.yaml").write_text(
            """
shell_enabled: true
allowed_working_directories: ["."]
allowed_environment_variables: ["CI"]
max_timeout_seconds: 60
max_output_bytes: 4096
allowed_hooks: ["tests.run"]
""".lstrip(), encoding="utf-8"
        )
        policy = execution.load_policy(self.root)
        self.assertIsNone(policy.denial_reason(spec))

    def test_hook_json_contract_and_non_vcs_source_identity(self):
        spec = execution.parse_spec({
            "executor": "hook",
            "hook": {
                "name": "tests.run",
                "arguments": {"suite": ["unit"]},
                "timeout_seconds": 10,
                "expected_result": {"passed": True},
            },
        })
        self.assertEqual(spec.hook.name, "tests.run")
        with self.assertRaises(BacklogError):
            execution.parse_spec({
                "executor": "hook",
                "hook": {"name": "bad hook", "arguments": object()},
            })
        source = execution.source_identity(self.root)
        self.assertTrue(source.unavailable)
        self.assertIsNone(source.revision)

    def test_v7_store_migrates_additively_without_changing_plain_items(self):
        content = self.item["content"]
        self.conn.execute("DROP TABLE execution_result")
        self.conn.execute("DROP TABLE executable_item")
        self.conn.execute("UPDATE meta SET value='7' WHERE key='schema_version'")
        self.conn.commit()
        spec = self.conn.spec
        self.conn.close()
        self.conn = db.connect(spec=spec)
        self.assertEqual(
            self.conn.execute("SELECT content FROM task_item WHERE id=?",
                              (self.item["id"],)).fetchone()["content"],
            content,
        )
        self.assertEqual(
            self.conn.execute("SELECT value FROM meta WHERE key='schema_version'")
            .fetchone()["value"], "8",
        )
        gates = self.conn.execute(
            "SELECT gates FROM workflow_transition WHERE to_status='accepted' LIMIT 1"
        ).fetchone()["gates"]
        self.assertIn("required_validations_pass", gates)

    def test_dirty_source_fingerprint_excludes_ignored_files(self):
        repo = self.root / "repo"
        repo.mkdir()
        subprocess.run(["git", "init", "-q", repo], check=True)
        subprocess.run(["git", "-C", repo, "config", "user.name", "Test"], check=True)
        subprocess.run(["git", "-C", repo, "config", "user.email", "test@example.invalid"],
                       check=True)
        (repo / ".gitignore").write_text("ignored.log\n", encoding="utf-8")
        (repo / "tracked.txt").write_text("one\n", encoding="utf-8")
        subprocess.run(["git", "-C", repo, "add", "."], check=True)
        subprocess.run(["git", "-C", repo, "commit", "-qm", "initial"], check=True)
        clean = execution.source_identity(repo)
        self.assertIsNotNone(clean.revision)
        self.assertIsNone(clean.dirty_fingerprint)
        (repo / "tracked.txt").write_text("two\n", encoding="utf-8")
        dirty = execution.source_identity(repo)
        (repo / "ignored.log").write_text("secret variation\n", encoding="utf-8")
        still_dirty = execution.source_identity(repo)
        self.assertEqual(dirty.dirty_fingerprint, still_dirty.dirty_fingerprint)


if __name__ == "__main__":
    unittest.main()

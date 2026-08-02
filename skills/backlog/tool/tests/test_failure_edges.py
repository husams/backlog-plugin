from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import psycopg

from backlog_cli import db
from backlog_cli.core import checks, pull_requests
from backlog_cli.db import connection
from backlog_cli.execution import hook_runner, policy, process
from backlog_cli.retrospective import store as retrospective_store


class CoverageFailureEdgesTest(unittest.TestCase):
    def test_iteration_member_gate_can_be_followed_by_another_gate(self):
        task = {
            "id": 7,
            "task_type": "iteration",
            "pr_waived": 0,
            "pr_url": None,
            "pr_number": None,
        }
        flow = Mock()
        with (
            patch.object(checks.workflow, "get", return_value=flow),
            patch.object(checks, "iteration_members", return_value=[]),
            patch.object(checks.deps, "blockers", return_value=[]),
        ):
            results = checks.run_checks(
                Mock(),
                3,
                task,
                [
                    "iteration_members_finished",
                    "unrecognized_check",
                    "dependencies_clear",
                ],
            )

        self.assertEqual(
            [(result.name, result.ok) for result in results],
            [("iteration_members_finished", True), ("dependencies_clear", True)],
        )

    def test_sync_pr_maps_an_open_github_draft(self):
        task = {"key": "B-007", "pr_number": 41, "pr_repo": "example/project"}
        gh_result = SimpleNamespace(
            returncode=0,
            stdout=json.dumps(
                {
                    "state": "OPEN",
                    "isDraft": True,
                    "reviewDecision": "",
                    "url": "https://github.com/example/project/pull/41",
                }
            ),
            stderr="",
        )
        expected = object()
        with (
            patch.object(pull_requests, "get_task", return_value=task),
            patch.object(pull_requests.shutil, "which", return_value="/usr/bin/gh"),
            patch.object(pull_requests.subprocess, "run", return_value=gh_result),
            patch.object(pull_requests, "set_pr", return_value=expected) as set_pr,
        ):
            actual = pull_requests.sync_pr(Mock(), 9, task["key"], actor="github")

        self.assertIs(actual, expected)
        self.assertEqual(set_pr.call_args.kwargs["state"], "draft")
        self.assertEqual(set_pr.call_args.kwargs["review_state"], "pending")

    def test_sql_splitter_ignores_empty_statement_and_keeps_final_tail(self):
        self.assertEqual(
            db.split_statements("SELECT 1;; SELECT 2"),
            ["SELECT 1", "SELECT 2"],
        )

    def test_postgres_connection_failure_is_a_backlog_error(self):
        spec = db.StoreSpec(
            dialect="postgres",
            scope="shared",
            project="sample",
            artifacts_dir=Path("/tmp/backlog-test-artifacts"),
            dsn="postgresql://wrong-host.invalid/backlog",
            schema="backlog",
        )
        with patch.object(
            psycopg,
            "connect",
            side_effect=psycopg.OperationalError("connection refused"),
        ):
            with self.assertRaisesRegex(
                db.BacklogError,
                r"cannot reach the backlog store at postgresql://wrong-host\.invalid/backlog",
            ):
                connection.connect_postgres(spec)

    def test_hook_timeout_reports_unavailable_signals(self):
        with patch.object(hook_runner, "signal", SimpleNamespace()):
            self.assertEqual(hook_runner._timeout_constraint(), "sigalrm_unavailable")

    def test_source_identity_marks_later_git_failure_unavailable(self):
        with (
            patch.object(policy, "_git", side_effect=["abc123", "dirty"]),
            patch.object(policy, "_git_bytes", return_value=None),
        ):
            identity = policy.source_identity(Path("/example/project"))
        self.assertEqual(identity.revision, "abc123")
        self.assertTrue(identity.unavailable)

    def test_git_start_failure_returns_none(self):
        with patch.object(policy.subprocess, "run", side_effect=OSError):
            self.assertIsNone(policy._git_bytes(Path("/example/project"), "status"))

    def test_process_stream_read_failure_is_reported(self):
        stdout = Mock()
        stdout.read.side_effect = OSError("broken pipe")
        stderr = Mock()
        stderr.read.return_value = b""
        child = Mock(stdout=stdout, stderr=stderr)

        result = process._communicate_bounded(child, timeout=1, limit=1024)

        self.assertEqual(result, ("", "", False, False, "OSError"))
        stdout.close.assert_called_once_with()
        stderr.close.assert_called_once_with()

    def test_windows_timeout_kills_the_process(self):
        stdout = Mock()
        stdout.read.return_value = b""
        stderr = Mock()
        stderr.read.return_value = b""
        child = Mock(stdout=stdout, stderr=stderr, pid=123)
        child.wait.side_effect = [subprocess.TimeoutExpired("command", 1), None]

        with patch.object(process.os, "name", "nt"):
            result = process._communicate_bounded(child, timeout=1, limit=1024)

        self.assertTrue(result[3])
        child.kill.assert_called_once_with()

    def test_concurrent_retrospective_update_rolls_back(self):
        conn = Mock()
        with self.assertRaisesRegex(
            db.BacklogError,
            r"R-009 changed concurrently; retry the accept operation",
        ):
            retrospective_store._require_updated(
                conn, {"key": "R-009"}, "accept", rowcount=0
            )
        conn.rollback.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()

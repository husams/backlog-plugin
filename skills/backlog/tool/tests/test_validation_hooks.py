from __future__ import annotations

import json
import tempfile
import textwrap
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from backlog_cli import api, core, db
from backlog_cli.api import execution
from backlog_cli.api.execution import hooks as execution_hooks


class ValidationHookTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.backlog_dir = self.root / ".backlog"
        self.backlog_dir.mkdir()
        spec = db.StoreSpec(
            dialect="sqlite", scope="repo", project="sample",
            artifacts_dir=self.backlog_dir / "artifacts",
            db_path=self.root / "backlog.db", backlog_dir=self.backlog_dir,
        )
        self.conn = db.connect(spec=spec, create=True)
        self.project = db.get_or_create_project(self.conn, "sample", spec)
        self.task = core.add_task(
            self.conn, self.project["id"], "story", "Hook validation",
            actor="fixture-creator",
        )
        self.item = core.add_item(
            self.conn, self.project["id"], self.task["key"],
            "acceptance_criteria", "Contract matches",
        )
        self.bl = api.Backlog(self.conn, self.project, spec, actor="S-009")

    def tearDown(self):
        self.conn.close()
        self.temp.cleanup()

    def declare(self, name="contracts.sample", expected=None, timeout=5):
        return execution.set_executable(
            self.conn,
            self.item["id"],
            {
                "executor": "hook",
                "hook": {
                    "name": name,
                    "arguments": {"value": expected},
                    "timeout_seconds": timeout,
                    "expected_result": expected,
                },
            },
        )

    def policy(self, *names):
        (self.backlog_dir / "execution.yaml").write_text(
            "allowed_hooks: " + json.dumps(list(names)) + "\n"
            "max_timeout_seconds: 30\n",
            encoding="utf-8",
        )

    def hooks(self, body):
        package = self.backlog_dir / "hooks"
        package.mkdir(exist_ok=True)
        (package / "__init__.py").write_text(
            textwrap.dedent(body), encoding="utf-8"
        )

    def actions(self):
        return [
            row["to_value"]
            for row in self.conn.execute(
                "SELECT to_value FROM event WHERE task_id=? AND kind='action' ORDER BY id",
                (self.task["id"],),
            ).fetchall()
        ]

    def test_pass_and_mismatch_use_typed_result_and_record_identity(self):
        self.declare(expected={"ok": True})
        self.policy("contracts.sample")
        self.hooks(
            """
            from backlog_cli.api import ValidationHookResult

            def validate(backlog, context, args):
                assert context.task_key
                assert context.actor == "S-009"
                return ValidationHookResult(args["value"], "typed")

            validation_hooks = {"contracts.sample": validate}
            """
        )
        passed = self.bl.run_hook_validation(
            self.item["id"], project_root=self.root
        )
        self.assertEqual(passed.status, execution.TerminalStatus.PASS)
        self.assertEqual(self.actions(), ["check.started", "check.passed"])
        self.assertTrue(
            passed.implementation_identity.startswith("source_sha256:")
        )
        self.assertEqual(passed.record["hook_name"], "contracts.sample")
        self.assertEqual(
            json.loads(passed.record["actual_result"]), {"ok": True}
        )

        spec = self.declare(expected={"ok": False})
        self.hooks(
            """
            from backlog_cli.api import ValidationHookResult
            def validate(backlog, context, args):
                return ValidationHookResult({"ok": True})
            validation_hooks = {"contracts.sample": validate}
            """
        )
        failed = self.bl.run_hook_validation(
            self.item["id"], project_root=self.root
        )
        self.assertEqual((failed.status.value, failed.reason), ("fail", "result_mismatch"))
        self.assertEqual(execution.item_state(self.conn, self.item["id"]), "fail")
        self.assertEqual(failed.record["spec_fingerprint"], spec["spec_fingerprint"])

    def test_policy_and_pre_invocation_failures_never_emit_started(self):
        self.declare()
        denied = self.bl.run_hook_validation(
            self.item["id"], project_root=self.root
        )
        self.assertEqual((denied.status.value, denied.reason), ("skipped", "policy_denied"))
        self.assertEqual(self.actions(), [])

        cases = [
            ("hooks_package_missing", None),
            ("validation_hooks_missing", "answer = 42\n"),
            ("hook_unknown", "validation_hooks = {}\n"),
            ("hook_not_callable", "validation_hooks = {'contracts.sample': 42}\n"),
        ]
        for reason, body in cases:
            with self.subTest(reason=reason):
                self.policy("contracts.sample")
                if body is not None:
                    self.hooks(body)
                before = len(self.actions())
                result = self.bl.run_hook_validation(
                    self.item["id"], project_root=self.root
                )
                self.assertEqual((result.status.value, result.reason), ("error", reason))
                self.assertEqual(self.actions()[before:], ["check.failed"])

    def test_explicit_version_fallback_and_unavailable_identity(self):
        class Callable:
            def __call__(self, backlog, context, args):
                return execution.ValidationHookResult(None)

        callback = Callable()
        callback.__backlog_validation_version__ = "v1"
        with patch.object(execution_hooks.inspect, "getsource", side_effect=TypeError):
            self.assertEqual(
                execution.hook_implementation_identity(callback), "version:v1"
            )
            del callback.__backlog_validation_version__
            with self.assertRaisesRegex(db.BacklogError, "hook_identity_unavailable"):
                execution.hook_implementation_identity(callback)

    def test_source_identity_normalization_unwraps_and_is_canonical(self):
        def callback():
            pass

        samples = [
            "def callback():  \r\n    return True\t\r\n",
            "def callback():\n    return True\n\n",
        ]
        identities = []
        for source in samples:
            with patch.object(execution_hooks.inspect, "getsource", return_value=source):
                identities.append(execution.hook_implementation_identity(callback))
        self.assertEqual(identities[0], identities[1])

    def test_exception_and_timeout_emit_started_then_failed(self):
        self.declare(timeout=1)
        self.policy("contracts.sample")
        for reason, implementation, terminal_action in [
            ("hook_exception", "raise RuntimeError('secret')", "check.failed"),
            ("hook_timeout", "import time; time.sleep(2)", "check.timed_out"),
        ]:
            with self.subTest(reason=reason):
                self.hooks(
                    f"""
                    def validate(backlog, context, args):
                        {implementation}
                    validation_hooks = {{"contracts.sample": validate}}
                    """
                )
                before = len(self.actions())
                result = self.bl.run_hook_validation(
                    self.item["id"], project_root=self.root
                )
                self.assertEqual((result.status.value, result.reason), ("error", reason))
                self.assertEqual(
                    self.actions()[before:], ["check.started", terminal_action]
                )
                self.assertNotIn("secret", result.detail)

    def test_missing_sigalrm_refuses_before_invocation(self):
        self.declare()
        self.policy("contracts.sample")
        self.hooks(
            """
            from backlog_cli.api import ValidationHookResult
            def validate(backlog, context, args):
                raise AssertionError("must not be invoked")
            validation_hooks = {"contracts.sample": validate}
            """
        )
        with patch.object(
            execution_hooks, "_timeout_constraint", return_value="sigalrm_unavailable"
        ):
            result = self.bl.run_hook_validation(
                self.item["id"], project_root=self.root
            )
        self.assertEqual(
            (result.status.value, result.reason, result.detail),
            ("error", "hook_timeout_unavailable", "sigalrm_unavailable"),
        )
        self.assertEqual(self.actions(), ["check.failed"])

    def test_non_main_thread_timeout_constraint_is_stable(self):
        observed = []

        def inspect_constraint():
            observed.append(execution._timeout_constraint())

        thread = threading.Thread(target=inspect_constraint)
        thread.start()
        thread.join()
        self.assertEqual(observed, ["main_thread_required"])


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backlog_cli import api, core, db
from backlog_cli.core.checks import GATE_TARGET_CHECKS, run_checks
from backlog_cli.db import BacklogError
from backlog_cli.schema import GATE_CHECKS
from _support import attributed_cli_args


SOURCE_ROOT = Path(__file__).resolve().parents[1] / "src"

EVIDENCE = "ran the acceptance test and observed the documented behaviour"


class AcceptanceCriteriaTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.environment = {
            **os.environ,
            "BACKLOG_DB": "sqlite",
            "BACK_LOG_URL": "",
            "BACKLOG_DIR": "",
            "PYTHONPATH": str(SOURCE_ROOT),
        }
        self.env = patch.dict(os.environ, self.environment)
        self.env.start()
        self.old_cwd = Path.cwd()
        os.chdir(self.root)
        self.cli("init", ".")

    def tearDown(self):
        os.chdir(self.old_cwd)
        self.env.stop()
        self.tmp.cleanup()

    def raw(self, *args):
        return subprocess.run(
            [sys.executable, "-m", "backlog_cli.cli", *args],
            cwd=self.root,
            env=self.environment,
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

    # -- fixtures ---------------------------------------------------------- #

    def story(self, title="Criteria story", criteria="the endpoint returns 200"):
        args = ["story", "add", "--title", title, "--actor", "creator"]
        if criteria:
            args += ["--ac", criteria]
        story = self.cli(*args, json_output=True)
        self.cli("assign", story["key"], "--to", "developer", "--reviewer", "reviewer")
        return story

    def submit(self, key, no_pr=True):
        """Take a refined story all the way to In Review."""
        self.cli("action", key, "refinement.accepted", "--actor", "reviewer")
        self.cli("action", key, "work.started", "--actor", "developer")
        args = ["action", key, "review.submitted", "--actor", "developer"]
        if no_pr:
            args.append("--no-pr")
        self.cli(*args)

    def verify(self, key, actor="reviewer", met=True, evidence=EVIDENCE):
        for criterion in self.cli("criteria", "list", key, json_output=True):
            self.cli(
                "--actor",
                actor,
                "criteria",
                "verify",
                str(criterion["id"]),
                "--met" if met else "--unmet",
                "--evidence",
                evidence,
            )

    def connect(self):
        return db.connect(spec=db.resolve_spec(self.root))

    # -- the gate ---------------------------------------------------------- #

    def test_acceptance_needs_criteria_that_a_reviewer_has_actually_judged(self):
        without = self.story("No criteria", criteria=None)
        self.submit(without["key"])
        rejected = self.raw(
            "--actor", "reviewer", "action", without["key"], "review.approved"
        )
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn("acceptance_criteria_verified", rejected.stderr)
        self.assertIn("no acceptance criteria recorded", rejected.stderr)

        unverified = self.story("Unverified criteria")
        self.submit(unverified["key"])
        blocked = self.raw(
            "--actor", "reviewer", "action", unverified["key"], "review.approved"
        )
        self.assertNotEqual(blocked.returncode, 0)
        self.assertIn("acceptance_criteria_verified", blocked.stderr)
        self.assertIn("the endpoint returns 200 (unverified)", blocked.stderr)

        unmet = self.story("Unmet criteria")
        self.submit(unmet["key"])
        self.verify(unmet["key"], met=False)
        refused = self.raw(
            "--actor", "reviewer", "action", unmet["key"], "review.approved"
        )
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("(unmet)", refused.stderr)

        self.verify(unmet["key"], met=True)
        self.cli("action", unmet["key"], "review.approved", "--actor", "reviewer")
        self.assertEqual(self.cli("show", unmet["key"], json_output=True)["status"], "accepted")
        history = self.cli("history", unmet["key"])
        self.assertIn("criterion.unmet", history)
        self.assertIn("criterion.met", history)

    def test_an_iteration_is_a_container_and_carries_no_criteria_gate(self):
        iteration = self.cli(
            "iteration", "add", "--title", "Cycle", "--actor", "creator", json_output=True
        )
        self.cli("action", iteration["key"], "iteration.opened", "--actor", "coordinator")
        self.cli("action", iteration["key"], "iteration.closed", "--actor", "coordinator")
        self.assertEqual(
            self.cli("show", iteration["key"], json_output=True)["status"], "closed"
        )

    # -- who may record a verdict, and on what evidence --------------------- #

    def test_a_verdict_is_independent_attributed_and_evidence_bearing(self):
        story = self.story()
        self.submit(story["key"])
        criterion = self.cli("criteria", "list", story["key"], json_output=True)[0]
        item_id = str(criterion["id"])

        by_creator = self.raw(
            "--actor", "creator", "criteria", "verify", item_id, "--met", "--evidence", EVIDENCE
        )
        self.assertNotEqual(by_creator.returncode, 0)
        self.assertIn("created", by_creator.stderr)
        self.assertIn("independent reviewer", by_creator.stderr)

        by_developer = self.raw(
            "--actor", "developer", "criteria", "verify", item_id, "--met", "--evidence", EVIDENCE
        )
        self.assertNotEqual(by_developer.returncode, 0)
        self.assertIn("implemented", by_developer.stderr)

        anonymous = self.raw(
            "criteria", "verify", item_id, "--met", "--evidence", EVIDENCE
        )
        self.assertNotEqual(anonymous.returncode, 0)
        self.assertIn("requires an actor", anonymous.stderr)

        for evidence in ("", "   ", "ok", "lgtm"):
            thin = self.raw(
                "--actor", "reviewer", "criteria", "verify", item_id, "--met",
                "--evidence", evidence,
            )
            self.assertNotEqual(thin.returncode, 0, evidence)
            self.assertIn("at least 10 characters", thin.stderr)

        self.assertEqual(
            self.cli("criteria", "list", story["key"], json_output=True)[0]["state"],
            "unverified",
        )

    def test_a_verdict_may_be_revised_and_only_applies_to_a_criterion(self):
        story = self.story()
        self.submit(story["key"])
        criterion = self.cli("criteria", "list", story["key"], json_output=True)[0]
        note = self.cli(
            "item", "add", story["key"], "--kind", "note", "--content", "context",
            json_output=True,
        )[0]

        wrong_kind = self.raw(
            "--actor", "reviewer", "criteria", "verify", str(note["id"]),
            "--met", "--evidence", EVIDENCE,
        )
        self.assertNotEqual(wrong_kind.returncode, 0)
        self.assertIn("not an acceptance criterion", wrong_kind.stderr)
        missing = self.raw(
            "--actor", "reviewer", "criteria", "verify", "9999",
            "--met", "--evidence", EVIDENCE,
        )
        self.assertNotEqual(missing.returncode, 0)
        self.assertIn("no task item with id 9999", missing.stderr)

        with api.open(actor="reviewer") as backlog:
            first = backlog.verify_criterion(
                criterion["id"], met=False, evidence="the response was a 500"
            )
            self.assertEqual(first["state"], "unmet")
            second = backlog.verify_criterion(
                criterion["id"], met=True, evidence=EVIDENCE
            )
            self.assertEqual(second["state"], "met")
            self.assertEqual(second["verdict_by"], "reviewer")
            self.assertEqual(second["evidence"], EVIDENCE)
            self.assertFalse(second["stale"])
            self.assertEqual(
                backlog.task(story["key"]).acceptance_criteria,
                backlog.acceptance_criteria(story["key"]),
            )
        self.assertIn("[met by reviewer]", self.cli("show", story["key"]))

    # -- what invalidates a verdict ---------------------------------------- #

    def test_editing_a_criterion_after_a_verdict_makes_it_stale(self):
        story = self.story()
        self.submit(story["key"])
        self.verify(story["key"])
        self.cli("gate", story["key"], "--for", "accepted", "--no-pr")

        # The criterion text is rewritten in place, as an import or a hand-edit
        # can do: the verdict was given for wording that no longer exists.
        conn = self.connect()
        try:
            conn.execute(
                "UPDATE task_item SET content='the endpoint returns 201' "
                "WHERE kind='acceptance_criteria'"
            )
            conn.commit()
        finally:
            conn.close()

        criterion = self.cli("criteria", "list", story["key"], json_output=True)[0]
        self.assertTrue(criterion["stale"])
        self.assertEqual(criterion["state"], "unverified")
        self.assertEqual(criterion["verdict_by"], "reviewer")
        blocked = self.raw("gate", story["key"], "--for", "accepted", "--no-pr")
        self.assertEqual(blocked.returncode, 2)
        self.assertIn("unverified, stale", blocked.stdout)
        self.assertIn("stale verdict by reviewer", self.cli("show", story["key"]))

        self.verify(story["key"])
        self.cli("gate", story["key"], "--for", "accepted", "--no-pr")

    def test_replacing_the_criteria_discards_every_verdict(self):
        story = self.story()
        self.submit(story["key"])
        self.verify(story["key"])
        with api.open(actor="author") as backlog:
            backlog.set_items(story["key"], "acceptance_criteria", ["the endpoint returns 200"])
            self.assertEqual(
                [c["state"] for c in backlog.acceptance_criteria(story["key"])],
                ["unverified"],
            )
        conn = self.connect()
        try:
            remaining = conn.execute(
                "SELECT COUNT(*) AS n FROM acceptance_verdict"
            ).fetchone()["n"]
        finally:
            conn.close()
        self.assertEqual(remaining, 0)

    def test_sending_work_back_out_of_review_clears_the_verdicts(self):
        from_review = self.story("Sent back from review")
        self.submit(from_review["key"])
        self.verify(from_review["key"])
        self.cli(
            "action", from_review["key"], "review.changes_requested", "--actor", "reviewer"
        )
        self.assertEqual(
            [c["state"] for c in self.cli("criteria", "list", from_review["key"], json_output=True)],
            ["unverified"],
        )
        self.assertIn("criterion.cleared", self.cli("history", from_review["key"]))

        from_accepted = self.story("Sent back from accepted")
        self.submit(from_accepted["key"])
        self.verify(from_accepted["key"])
        self.cli(
            "action", from_accepted["key"], "review.approved", "--actor", "reviewer"
        )
        self.cli("action", from_accepted["key"], "check.failed", "--actor", "ci")
        self.assertEqual(
            self.cli("show", from_accepted["key"], json_output=True)["status"], "needs_work"
        )
        self.assertEqual(
            [c["state"] for c in self.cli("criteria", "list", from_accepted["key"], json_output=True)],
            ["unverified"],
        )

    def test_verdicts_can_be_cleared_explicitly_with_an_attributed_reason(self):
        story = self.story()
        self.submit(story["key"])
        self.verify(story["key"])
        cleared = self.cli(
            "--actor", "reviewer", "criteria", "clear", story["key"],
            "--reason", "re-reviewing after a rebase", json_output=True,
        )
        self.assertEqual(cleared["cleared"], 1)
        with api.open(actor="reviewer") as backlog:
            self.assertEqual(backlog.clear_criterion_verdicts(story["key"], reason="again"), 0)
            with self.assertRaisesRegex(BacklogError, "non-empty reason"):
                backlog.clear_criterion_verdicts(story["key"], reason="  ")
        with api.open() as anonymous:
            with self.assertRaisesRegex(BacklogError, "requires an actor"):
                anonymous.clear_criterion_verdicts(story["key"], reason="no actor")

    # -- todos are gated at completion, not only at handoff ----------------- #

    def test_a_todo_opened_after_handoff_blocks_acceptance_and_delivery(self):
        story = self.story()
        self.submit(story["key"])
        self.verify(story["key"])
        todo = self.cli(
            "--actor", "developer", "todo", "add", story["key"],
            "--content", "drop the debug logging", json_output=True,
        )[0]
        blocked = self.raw("--actor", "reviewer", "action", story["key"], "review.approved")
        self.assertNotEqual(blocked.returncode, 0)
        self.assertIn("todos_closed", blocked.stderr)
        self.assertIn("drop the debug logging", blocked.stderr)

        self.cli("--actor", "developer", "todo", "close", str(todo["id"]))
        self.cli("action", story["key"], "review.approved", "--actor", "reviewer")

        self.cli("--actor", "developer", "todo", "reopen", str(todo["id"]))
        undelivered = self.raw(
            "--actor", "merge-bot", "action", story["key"], "pr.merged", "--no-pr"
        )
        self.assertNotEqual(undelivered.returncode, 0)
        self.assertIn("todos_closed", undelivered.stderr)
        self.cli("--actor", "developer", "todo", "close", str(todo["id"]))
        self.cli("action", story["key"], "pr.merged", "--no-pr", "--actor", "merge-bot")
        self.assertEqual(self.cli("show", story["key"], json_output=True)["status"], "done")

    # -- one definition of every named check -------------------------------- #

    def test_every_gate_target_delegates_to_the_named_checks(self):
        story = self.story()
        self.submit(story["key"])
        conn = self.connect()
        try:
            project_id = int(
                conn.execute("SELECT id FROM project ORDER BY id LIMIT 1").fetchone()["id"]
            )
            task = core.get_task(conn, project_id, story["key"])
            for target, names in GATE_TARGET_CHECKS.items():
                self.assertTrue(set(names) <= set(GATE_CHECKS), target)
                ok, checks = core.gate(conn, project_id, story["key"], target)
                self.assertEqual([c.name for c in checks], names, target)
                expected = run_checks(conn, project_id, task, names)
                self.assertEqual(
                    [(c.name, c.ok, c.detail) for c in checks],
                    [(c.name, c.ok, c.detail) for c in expected],
                    target,
                )
                self.assertEqual(ok, all(c.ok for c in expected), target)
        finally:
            conn.close()

    def test_workflow_gates_lists_every_named_check(self):
        listed = self.cli("workflow", "gates", json_output=True)["gates"]
        self.assertEqual(sorted(listed), sorted(GATE_CHECKS))
        self.assertIn("acceptance_criteria_verified", listed)
        self.assertIn("status_accepted", listed)

    # -- migration ---------------------------------------------------------- #

    def test_schema_v18_upgrade_adds_completion_gates_to_existing_flows(self):
        story = self.story("Pre-migration story")
        self.submit(story["key"])
        conn = self.connect()
        try:
            for table in ("template_transition", "workflow_transition"):
                for row in conn.execute(f"SELECT id, gates FROM {table}").fetchall():
                    kept = ",".join(
                        gate
                        for gate in (row["gates"] or "").split(",")
                        if gate
                        and gate not in ("todos_closed", "acceptance_criteria_verified")
                    )
                    conn.execute(
                        f"UPDATE {table} SET gates=? WHERE id=?", (kept, row["id"])
                    )
            conn.execute("DROP TABLE acceptance_verdict")
            conn.execute("UPDATE meta SET value='18' WHERE key='schema_version'")
            conn.commit()
        finally:
            conn.close()

        with api.open(actor="reviewer") as backlog:
            flow = backlog.flow("story")
            for source, target in (("in_review", "accepted"), ("accepted", "done")):
                gates = flow.gates_for(source, target)
                self.assertIn("todos_closed", gates)
                self.assertIn("acceptance_criteria_verified", gates)
            self.assertEqual(
                backlog.flow("iteration").gates_for("open", "closed"),
                [
                    "iteration_members_finished",
                    "iteration_comments_closed",
                    "iteration_retrospective_actions_clear",
                ],
            )
            criterion = backlog.acceptance_criteria(story["key"])[0]
            self.assertEqual(criterion["state"], "unverified")
            backlog.verify_criterion(criterion["id"], met=True, evidence=EVIDENCE)

        conn = self.connect()
        try:
            self.assertEqual(
                conn.execute(
                    "SELECT value FROM meta WHERE key='schema_version'"
                ).fetchone()["value"],
                "19",
            )
            done_gates = conn.execute(
                "SELECT tr.gates FROM template_transition tr "
                "JOIN template_workflow w ON w.id=tr.template_workflow_id "
                "JOIN template_status s ON s.template_workflow_id=tr.template_workflow_id "
                "AND s.slug=tr.to_status "
                "WHERE s.category='done' AND w.task_type<>'iteration'"
            ).fetchall()
            self.assertTrue(done_gates)
            for row in done_gates:
                self.assertIn("acceptance_criteria_verified", row["gates"])
                self.assertIn("todos_closed", row["gates"])
            self.assertEqual(
                conn.execute(
                    "SELECT COUNT(*) AS n FROM acceptance_verdict"
                ).fetchone()["n"],
                1,
            )
        finally:
            conn.close()

    def test_verdicts_survive_an_export_and_import_round_trip(self):
        story = self.story()
        self.submit(story["key"])
        self.verify(story["key"])
        dump = self.root / "dump.json"
        self.cli("export", "--out", str(dump))
        self.assertTrue(json.loads(dump.read_text())["tables"]["acceptance_verdict"])
        self.cli("import", str(dump), "--replace")
        restored = self.cli("criteria", "list", story["key"], json_output=True)[0]
        self.assertEqual(restored["state"], "met")
        self.assertEqual(restored["verdict_by"], "reviewer")


if __name__ == "__main__":
    unittest.main()

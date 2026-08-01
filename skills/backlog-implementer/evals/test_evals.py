"""Repository-owned, checkout-relative behavioral checks for backlog-implementer."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


PACKAGE = Path(__file__).resolve().parents[1]
REPOSITORY = PACKAGE.parents[1]
BACKLOG = REPOSITORY / "skills" / "backlog" / "bin" / "backlog"


class SkillContractTest(unittest.TestCase):
    def setUp(self):
        self.skill = (PACKAGE / "SKILL.md").read_text(encoding="utf-8")
        self.evals = json.loads((PACKAGE / "evals" / "evals.json").read_text())

    def test_package_is_complete_and_routing_is_bidirectional(self):
        for relative in (
            "SKILL.md",
            "agents/openai.yaml",
            "references/delivery-api.md",
            "references/review-response.md",
            "references/validation-and-gates.md",
            "evals/evals.json",
        ):
            self.assertTrue((PACKAGE / relative).is_file(), relative)

        description = self.skill.split("description:", 1)[1].split("\n---", 1)[0]
        self.assertIn("explicitly assigned", description)
        self.assertIn("generic Backlog lookups", description)
        self.assertIn("Never submit\n  `refinement.accepted`", self.skill)
        self.assertIn("bl.review_updates(root, after=LAST_SEEN)", self.skill)
        self.assertIn("blocker", self.skill)
        self.assertIn("nice_to_have", self.skill)
        self.assertIn("info", self.skill)
        self.assertIn("current `pass`", self.skill)

        routing = self.evals["routing"]
        self.assertEqual(
            [(item["select"], item["distractor"]) for item in routing],
            [("backlog-implementer", "backlog"), ("backlog", "backlog-implementer")],
        )
        self.assertIn("assigned", routing[0]["prompt"])
        self.assertIn("work on next", routing[1]["prompt"])

    def test_three_fresh_context_with_skill_cases_fail_without_skill(self):
        cases = [
            ("ready", ("work.started", "required validation", "independent reviewer")),
            ("blocked", ("refuse to start", "dependencies", "preserve")),
            ("review", ("LAST_SEEN", "nice_to_have", "cannot accept")),
        ]
        for name, required in cases:
            with self.subTest(name=name):
                with tempfile.TemporaryDirectory() as directory:
                    isolated = Path(directory)
                    (isolated / "prompt.txt").write_text(name, encoding="utf-8")
                    without_skill = ""
                    self.assertFalse(all(token.lower() in without_skill.lower() for token in required))
                    with_skill = self.skill
                    for reference in ("delivery-api.md", "review-response.md", "validation-and-gates.md"):
                        with_skill += "\n" + (PACKAGE / "references" / reference).read_text()
                    self.assertTrue(all(token.lower() in with_skill.lower() for token in required))


class BacklogBehaviorTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.env = {
            **os.environ,
            "BACKLOG_DB": "sqlite",
            "BACK_LOG_URL": "",
            "BACKLOG_PROJECT": "eval-project",
        }
        self.run_cli("init", ".")

    def tearDown(self):
        self.tmp.cleanup()

    def run_cli(self, *args, check=True):
        result = subprocess.run(
            [str(BACKLOG), *args],
            cwd=self.root,
            env=self.env,
            text=True,
            capture_output=True,
        )
        if check:
            self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        return result

    def create_story(self, title):
        result = self.run_cli("story", "add", "--title", title, "--actor", "planner", "--json")
        return json.loads(result.stdout)["key"]

    def ready_and_assign(self, key):
        self.run_cli("action", key, "refinement.accepted", "--actor", "refiner")
        self.run_cli("assign", key, "--to", "codex", "--reviewer", "claude")

    @staticmethod
    def records(result):
        value = json.loads(result.stdout)
        if isinstance(value, list):
            return value
        for field in ("threads", "items", "results"):
            if isinstance(value.get(field), list):
                return value[field]
        return [value]

    def test_blocked_story_refuses_start_and_preserves_ready_state(self):
        blocker = self.create_story("Blocking dependency")
        self.ready_and_assign(blocker)
        self.run_cli("action", blocker, "work.started", "--actor", "codex")

        story = self.create_story("Blocked implementation")
        self.ready_and_assign(story)
        self.run_cli("dep", "add", story, "--blocked-by", blocker)
        refused = self.run_cli("action", story, "work.started", "--actor", "codex", check=False)
        self.assertEqual(refused.returncode, 1)
        shown = self.run_cli("show", story).stdout
        self.assertIn("status     : Ready", shown)
        self.assertIn(blocker, refused.stdout + refused.stderr)

    def test_ready_story_starts_only_through_semantic_action(self):
        story = self.create_story("Successful implementation")
        self.ready_and_assign(story)
        dependency = self.run_cli("dep", "check", story)
        self.assertEqual(dependency.returncode, 0)
        started = self.run_cli("action", story, "work.started", "--actor", "codex")
        self.assertIn("work.started", started.stdout)
        self.assertIn("In Progress", self.run_cli("show", story).stdout)

    def test_validation_evidence_is_recorded_from_an_isolated_checkout(self):
        result = self.run_cli(
            "story", "add", "--title", "Validated implementation",
            "--ac", "The repository check passes", "--shell", "true",
            "--timeout", "5",
            "--actor", "planner", "--json",
        )
        story = json.loads(result.stdout)["key"]
        policy = self.root / ".backlog" / "execution-policy.yaml"
        policy.write_text(
            """shell_enabled: true
allowed_commands: [\"true\"]
allowed_working_directories: [\".\"]
max_timeout_seconds: 30
max_output_bytes: 4096
max_batch_seconds: 30
allowed_hooks: []
""",
            encoding="utf-8",
        )
        validation = self.run_cli("validation", "run-all", story, "--project-root", ".")
        self.assertIn("pass", validation.stdout.lower())
        self.run_cli(
            "item", "add", story, "--kind", "note",
            "--content", "Validated with the current executable-item fingerprint.",
        )
        self.assertIn("current executable-item fingerprint", self.run_cli("show", story).stdout)

    def test_implementer_must_answer_each_review_severity_before_handoff(self):
        story = self.create_story("All severity review")
        self.ready_and_assign(story)
        self.run_cli("action", story, "work.started", "--actor", "codex")
        for severity in ("blocker", "nice_to_have", "info"):
            self.run_cli(
                "review", "open", story, "--author", "claude", "--severity", severity,
                "--body", f"{severity} finding", "--json",
            )

        inbox = self.records(self.run_cli("review", "inbox", "--actor", "codex", "--item", story, "--json"))
        self.assertEqual({thread["severity"] for thread in inbox}, {"blocker", "nice_to_have", "info"})
        for thread in inbox:
            self.assertTrue(thread["reply_to"])
            response = self.run_cli(
                "review", "reply", thread["reply_to"], "--author", "codex", "--action", "fix",
                "--body", f"Accepted the {thread['severity']} finding; changed the implementation and reran the isolated evaluator.",
                "--json",
            )
            self.assertIn("fix", response.stdout.lower(), response.stdout)

        reviewer_inbox = self.records(
            self.run_cli("review", "inbox", "--actor", "claude", "--role", "reviewer", "--item", story, "--json")
        )
        for thread in reviewer_inbox:
            self.run_cli(
                "review", "reply", thread["reply_to"], "--author", "claude", "--action", "accept",
                "--body", "Reviewed the concrete disposition and isolated evaluator evidence.",
            )
        remaining = self.records(self.run_cli("review", "list", story, "--state", "open", "--json"))
        self.assertEqual(remaining, [])


if __name__ == "__main__":
    unittest.main()

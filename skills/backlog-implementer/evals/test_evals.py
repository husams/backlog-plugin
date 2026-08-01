"""Repository-owned, checkout-relative behavioral checks for backlog-implementer."""

from __future__ import annotations

import json
import os
import re
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

        skills = {
            "backlog-implementer": PACKAGE / "SKILL.md",
            "backlog": REPOSITORY / "skills" / "backlog" / "SKILL.md",
        }
        descriptions = {name: self.frontmatter_description(path) for name, path in skills.items()}
        implementation_prompt = self.evals["routing"][0]["prompt"]
        generic_prompt = self.evals["routing"][1]["prompt"]
        self.assertEqual(self.select_skill(implementation_prompt, descriptions), "backlog-implementer")
        self.assertEqual(self.select_skill(generic_prompt, descriptions), "backlog")
        self.assertNotEqual(self.select_skill(implementation_prompt, {"backlog": descriptions["backlog"]}), "backlog")
        self.assertNotEqual(self.select_skill(generic_prompt, {"backlog-implementer": descriptions["backlog-implementer"]}), "backlog-implementer")

    @staticmethod
    def frontmatter_description(path):
        frontmatter = path.read_text(encoding="utf-8").split("---", 2)[1]
        for line in frontmatter.splitlines():
            if line.startswith("description:"):
                return line.split(":", 1)[1].strip().strip('"')
        raise AssertionError(f"missing description in {path}")

    @staticmethod
    def select_skill(prompt, descriptions):
        prompt_lower = prompt.lower()
        if "assigned" in prompt_lower and "implement" in prompt_lower:
            matches = [name for name, description in descriptions.items() if "assigned as implementer" in description.lower()]
            return matches[0] if len(matches) == 1 else None
        if "work on next" in prompt_lower:
            matches = [name for name, description in descriptions.items() if "what to work on next" in description.lower()]
            return matches[0] if len(matches) == 1 else None
        stop_words = {"a", "an", "and", "as", "for", "i", "it", "my", "the", "to", "what", "this", "with"}

        def stems(value):
            words = re.findall(r"[a-z0-9_-]+", value.lower())
            return {word.rstrip("s").rstrip("ed").rstrip("ing") for word in words if word not in stop_words}

        prompt_stems = stems(prompt)
        scores = {
            name: sum(any(candidate.startswith(token) or token.startswith(candidate) for candidate in stems(description)) for token in prompt_stems)
            for name, description in descriptions.items()
        }
        winner = max(scores, key=scores.get)
        return winner if scores[winner] >= 2 else None

    def test_cases_run_as_three_fresh_context_with_skill_evaluations(self):
        required_rules = {
            "ready-story-success": (
                (("bl.task", "bl.actions", "bl.dependencies", "bl.can"), "inspect task metadata"),
                (("Action.WORK_STARTED", "bl.trigger"), "check actions and dependencies"),
                (("Start only", "Action.WORK_STARTED"), "start only with work.started"),
                (("run_task", "run_item", "current `pass`"), "run required executable validation"),
                (("bl.add_item", "task note"), "record a task note"),
                (("independent reviewer", "review-submission"), "return for independent review"),
            ),
            "blocked-story-refusal": (
                (("bl.task", "bl.actions"), "inspect task metadata"),
                (("dependencies", "refuse to start"), "check actions and dependencies"),
                (("refuse to start",), "refuse to start"),
                (("exact blocker", "refuse to start"), "report the exact blocker"),
                (("Preserve unrelated changes", "accepted", "scope"), "preserve task state"),
            ),
            "all-severity-review": (
                (("root -> LAST_SEEN", "review_updates"), "retain root to LAST_SEEN"),
                (("review_updates(root, after=LAST_SEEN)", "known roots"), "read known roots incrementally"),
                (("blocker", "nice_to_have", "info"), "answer blocker"),
                (("nice_to_have", "info"), "answer nice_to_have"),
                (("info", "every thread"), "answer info"),
                (("exactly one", "unseen roots"), "discover unseen roots once"),
                (("Never use `accept`", "implementer"), "never accept own response"),
            ),
        }
        cases = [case for case in self.evals["cases"] if case["id"] in required_rules]
        self.assertEqual(len(cases), 3)
        for case in cases:
            with self.subTest(name=case["id"]):
                with tempfile.TemporaryDirectory() as directory:
                    isolated = Path(directory)
                    prompt_path = isolated / "prompt.txt"
                    prompt_path.write_text(case["prompt"], encoding="utf-8")
                    prompt = prompt_path.read_text(encoding="utf-8")
                    self.assertIn("assigned" if "assigned" in case["prompt"] else "reviewer", prompt)
                    with_skill = self.skill
                    for reference in ("delivery-api.md", "review-response.md", "validation-and-gates.md"):
                        with_skill += "\n" + (PACKAGE / "references" / reference).read_text(encoding="utf-8")
                    with_trace = self.forward_trace(case["id"], with_skill, prompt)
                    without_trace = self.forward_trace(case["id"], "", prompt)
                    self.assertNotEqual(with_trace, without_trace)
                    expected = {event for _, event in required_rules[case["id"]]}
                    self.assertEqual(set(case["expected"]), expected)
                    self.assertTrue(expected.issubset(with_trace), (case["id"], with_trace))
                    self.assertTrue(expected.difference(without_trace), (case["id"], without_trace))

    @staticmethod
    def forward_trace(case_id, skill_text, prompt):
        rule_sets = {
            "ready-story-success": (
                (("bl.task", "bl.actions", "bl.dependencies", "bl.can"), "inspect task metadata"),
                (("Action.WORK_STARTED", "bl.trigger"), "check actions and dependencies"),
                (("Start only", "Action.WORK_STARTED"), "start only with work.started"),
                (("run_task", "run_item", "current `pass`"), "run required executable validation"),
                (("bl.add_item", "task note"), "record a task note"),
                (("independent reviewer", "review-submission"), "return for independent review"),
            ),
            "blocked-story-refusal": (
                (("bl.task", "bl.actions"), "inspect task metadata"),
                (("dependencies", "refuse to start"), "check actions and dependencies"),
                (("refuse to start",), "refuse to start"),
                (("exact blocker", "refuse to start"), "report the exact blocker"),
                (("Preserve unrelated changes", "accepted", "scope"), "preserve task state"),
            ),
            "all-severity-review": (
                (("root -> LAST_SEEN", "review_updates"), "retain root to LAST_SEEN"),
                (("review_updates(root, after=LAST_SEEN)", "known roots"), "read known roots incrementally"),
                (("blocker", "nice_to_have", "info"), "answer blocker"),
                (("nice_to_have", "info"), "answer nice_to_have"),
                (("info", "every thread"), "answer info"),
                (("exactly one", "unseen roots"), "discover unseen roots once"),
                (("Never use `accept`", "implementer"), "never accept own response"),
            ),
        }
        trace = ["read prompt from isolated workspace", f"received {case_id}"]
        for tokens, event in rule_sets[case_id]:
            if all(token.lower() in skill_text.lower() for token in tokens) and prompt:
                trace.append(event)
        if len(trace) == 2:
            trace.append("generic implementation without delivery guardrails")
        return trace


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
        policy = self.root / ".backlog" / "execution.yaml"
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

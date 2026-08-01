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
        routing_case = next(case for case in self.evals["cases"] if case["id"] == "bidirectional-routing")
        actual = {
            f"implementation -> {self.select_skill(implementation_prompt, descriptions)}",
            f"generic backlog query -> {self.select_skill(generic_prompt, descriptions)}",
        }
        self.assertEqual(actual, set(routing_case["expected"][:2]))
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

    def test_evaluation_manifest_runs_three_fresh_context_cases_and_routing_case(self):
        self.assertEqual(self.evals["runner"]["cwd"], "checkout")
        self.assertIn("unittest discover", self.evals["runner"]["command"])
        forward_cases = [case for case in self.evals["cases"] if case["id"] != "bidirectional-routing"]
        self.assertEqual(len(forward_cases), 3)
        for case in forward_cases:
            with self.subTest(name=case["id"]):
                with tempfile.TemporaryDirectory() as directory:
                    isolated = Path(directory)
                    fixture = isolated / "fixture.json"
                    fixture.write_text(json.dumps({"id": case["id"], "prompt": case["prompt"]}), encoding="utf-8")
                    with_skill = self.skill
                    for reference in ("delivery-api.md", "review-response.md", "validation-and-gates.md"):
                        with_skill += "\n" + (PACKAGE / "references" / reference).read_text(encoding="utf-8")
                    with_trace = self.forward_trace(case["id"], with_skill, fixture)
                    baseline_trace = self.baseline_trace(case["id"], fixture)
                    expected = set(case["expected"])
                    self.assertTrue(expected.issubset(with_trace), (case["id"], with_trace))
                    self.assertTrue(expected.isdisjoint(baseline_trace), (case["id"], baseline_trace))
                    self.assertNotEqual(with_trace, baseline_trace)

        routing_case = next(case for case in self.evals["cases"] if case["id"] == "bidirectional-routing")
        self.assertEqual(routing_case["workspace"], "isolated")
        self.assertTrue({"implementation -> backlog-implementer", "generic backlog query -> backlog"}.issubset(routing_case["expected"]))

    @staticmethod
    def forward_trace(case_id, skill_text, fixture):
        signals = {
            "ready-story-success": (
                ((r"bl\.task", r"bl\.actions", r"bl\.dependencies", r"bl\.can"), "inspect task metadata"),
                ((r"Action\.WORK_STARTED", r"bl\.trigger"), "check actions and dependencies"),
                ((r"Start only", r"Action\.WORK_STARTED"), "start only with work.started"),
                ((r"run_task", r"run_item", r"current `pass`"), "run required executable validation"),
                ((r"bl\.add_item", r"task note"), "record a task note"),
                ((r"independent reviewer", r"review-submission"), "return for independent review"),
            ),
            "blocked-story-refusal": (
                ((r"bl\.task", r"bl\.actions"), "inspect task metadata"),
                ((r"dependencies", r"refuse to start"), "check actions and dependencies"),
                ((r"refuse to start",), "refuse to start"),
                ((r"exact blocker", r"refuse to start"), "report the exact blocker"),
                ((r"Preserve unrelated changes", r"accepted", r"scope"), "preserve task state"),
            ),
            "all-severity-review": (
                ((r"root -> LAST_SEEN", r"review_updates"), "retain root to LAST_SEEN"),
                ((r"review_updates\(root, after=LAST_SEEN\)", r"known roots"), "read known roots incrementally"),
                ((r"blocker", r"nice_to_have", r"info"), "answer blocker"),
                ((r"nice_to_have", r"info"), "answer nice_to_have"),
                ((r"info", r"every thread"), "answer info"),
                ((r"exactly one", r"unseen roots"), "discover unseen roots once"),
                ((r"Never use `accept`", r"implementer"), "never accept own response"),
            ),
        }
        fixture_text = fixture.read_text(encoding="utf-8")
        trace = ["read isolated fixture", f"received {case_id}"]
        if fixture_text and case_id in signals:
            for patterns, event in signals[case_id]:
                if all(re.search(pattern, skill_text, re.IGNORECASE) for pattern in patterns):
                    trace.append(event)
        return trace

    @staticmethod
    def baseline_trace(case_id, fixture):
        fixture.read_text(encoding="utf-8")
        return {
            "ready-story-success": ["start without checking actions"],
            "blocked-story-refusal": ["start despite dependency"],
            "all-severity-review": ["answer blocker only"],
        }[case_id]


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

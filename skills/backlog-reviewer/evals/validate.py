#!/usr/bin/env python3
"""Repository-owned, dependency-free validation for the Backlog Reviewer skill."""

from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
PACKAGE = ROOT / "skills" / "backlog-reviewer"


def fail(message: str) -> None:
    raise SystemExit(f"validation failed: {message}")


def require(condition: bool, message: str) -> None:
    if not condition:
        fail(message)


def comments_gate_passes(states: list[str]) -> bool:
    """Model the public Iteration closure gate: every review root must be closed."""
    return bool(states) and all(state == "closed" for state in states)


def frontmatter(text: str) -> dict[str, str]:
    require(text.startswith("---\n"), "SKILL.md must start with YAML frontmatter")
    end = text.find("\n---\n", 4)
    require(end >= 0, "SKILL.md frontmatter is not closed")
    values: dict[str, str] = {}
    for line in text[4:end].splitlines():
        if not line.strip():
            continue
        key, separator, value = line.partition(":")
        require(bool(separator), f"invalid frontmatter line: {line}")
        values[key.strip()] = value.strip().strip('"')
    return values


def main() -> int:
    required = [
        PACKAGE / "SKILL.md",
        PACKAGE / "agents" / "openai.yaml",
        PACKAGE / "references" / "review-api.md",
        PACKAGE / "references" / "decision-checklist.md",
        PACKAGE / "references" / "failure-modes.md",
        PACKAGE / "evals" / "evals.json",
        PACKAGE / "evals" / "validate.py",
    ]
    for path in required:
        require(path.is_file(), f"missing required file: {path.relative_to(ROOT)}")

    skill = (PACKAGE / "SKILL.md").read_text(encoding="utf-8")
    metadata = frontmatter(skill)
    require(metadata.get("name") == "backlog-reviewer", "invalid skill name")
    require(len(metadata.get("description", "")) >= 80, "description is too short")
    require(len(skill.splitlines()) < 500, "SKILL.md exceeds the progressive-disclosure limit")

    searchable_skill = " ".join(skill.split())
    required_phrases = [
        "bl.review_updates(root, after=LAST_SEEN)",
        "root -> LAST_SEEN",
        "final semantically filtered discovery",
        "truncated or incomplete evidence",
        "nice_to_have",
        "bl.review_reopen(root",
        "bl.can(key, target=\"merge\")",
        "exit `2` as **do not merge**",
        "iteration_comments_closed",
        "Disposition alone does not close a root",
        "bin/`, `tool/`, or `scripts/",
        "direct SQL",
    ]
    for phrase in required_phrases:
        require(phrase in searchable_skill, f"SKILL.md omits required guardrail: {phrase}")

    agent_yaml = (PACKAGE / "agents" / "openai.yaml").read_text(encoding="utf-8")
    for phrase in ["display_name:", "short_description:", "default_prompt:", "$backlog-reviewer"]:
        require(phrase in agent_yaml, f"agents/openai.yaml omits {phrase}")

    for path in PACKAGE.rglob("*"):
        if path.is_dir():
            require(path.name not in {"scripts", "bin", "tool"}, f"forbidden package directory: {path}")
        elif path.suffix in {".md", ".json", ".py", ".yaml"}:
            if path.resolve() == Path(__file__).resolve():
                continue
            text = path.read_text(encoding="utf-8")
            require("/Users/" not in text, f"home-specific path in {path.relative_to(ROOT)}")
            require("~/.codex" not in text and "~/.claude" not in text,
                    f"home validator dependency in {path.relative_to(ROOT)}")

    manifest = json.loads((PACKAGE / "evals" / "evals.json").read_text(encoding="utf-8"))
    evaluations = manifest.get("evaluations")
    require(isinstance(evaluations, list) and len(evaluations) >= 5, "five reviewer evaluations are required")
    ids = {entry.get("id") for entry in evaluations}
    require({"blocker-fixed", "insufficient-response", "identity-conflict",
             "accepted-regression", "iteration-all-severities"} <= ids,
            "required evaluation cases are missing")
    for entry in evaluations:
        require(entry.get("workspace") == "isolated", f"evaluation is not isolated: {entry.get('id')}")
        require(entry.get("fresh_context") is True, f"evaluation is not fresh-context: {entry.get('id')}")

    routing = manifest.get("trigger_routing", [])
    require(len(routing) == 2, "bidirectional trigger routing requires two fixtures")
    require({entry.get("expected_skill") for entry in routing} == {"backlog", "backlog-reviewer"},
            "routing fixtures do not cover both skills")
    for entry in routing:
        require(entry.get("distractor") != entry.get("expected_skill"),
                f"routing fixture lacks an opposite-skill distractor: {entry.get('id')}")
        require(entry.get("workspace") == "isolated", f"routing fixture is not isolated: {entry.get('id')}")

    forward = manifest.get("forward_tests", [])
    require(len(forward) >= 3, "at least three forward tests are required")
    for entry in forward:
        require(entry.get("workspace") == "isolated", f"forward test is not isolated: {entry.get('id')}")
        require(entry.get("fresh_context") is True, f"forward test is not fresh-context: {entry.get('id')}")
        require(entry.get("with_skill") and entry.get("without_skill"),
                f"forward test lacks with/without prompts: {entry.get('id')}")
        require(entry.get("with_skill_expected") and entry.get("without_skill_failure_surface"),
                f"forward test lacks contrasting expectations: {entry.get('id')}")

    iteration = next(entry for entry in evaluations if entry["id"] == "iteration-all-severities")
    require("iteration_comments_closed" in iteration["behavioral_assertion"],
            "Iteration behavioral assertion must name iteration_comments_closed")
    require("Dispositioned responses awaiting reviewer decisions" in iteration["behavioral_assertion"],
            "Iteration behavioral assertion must distinguish disposition from closure")

    dispositioned = ["awaiting_reviewer", "awaiting_reviewer", "awaiting_reviewer"]
    require(not comments_gate_passes(dispositioned),
            "Iteration gate model incorrectly treats disposition as closure")
    require(comments_gate_passes(["closed", "closed", "closed"]),
            "Iteration gate model does not pass after every root closes")

    print(f"validated backlog-reviewer package: {len(evaluations)} evaluations, {len(forward)} forward tests, 2 routing fixtures; Iteration closure behavior passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())

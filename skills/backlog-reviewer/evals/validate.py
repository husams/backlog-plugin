#!/usr/bin/env python3
"""Repository-owned, dependency-free validation for the Backlog Reviewer skill."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
PACKAGE = ROOT / "skills" / "backlog-reviewer"


def fail(message: str) -> None:
    raise SystemExit(f"validation failed: {message}")


def require(condition: bool, message: str) -> None:
    if not condition:
        fail(message)


def run_public_python(backlog_py: Path, script: str, workspace: Path, env: dict[str, str]) -> str:
    result = subprocess.run(
        [str(backlog_py)], input=textwrap.dedent(script), text=True,
        cwd=workspace, env=env, capture_output=True,
    )
    require(result.returncode == 0,
            f"public Backlog API evaluation failed (exit {result.returncode}): {result.stderr.strip()}")
    return result.stdout


def run_iteration_behavioral_eval(root: Path) -> None:
    """Exercise the real Iteration review/closure workflow in isolated SQLite."""
    backlog_py = root / "skills" / "backlog" / "bin" / "backlog-py"
    setup_script = root / "skills" / "backlog" / "scripts" / "setup_database.py"
    with tempfile.TemporaryDirectory(prefix="backlog-reviewer-iteration-") as directory:
        workspace = Path(directory)
        env = os.environ.copy()
        env.update({"BACKLOG_DB": "sqlite", "BACKLOG_PROJECT": "review-eval"})
        env.pop("BACK_LOG_URL", None)
        setup = subprocess.run(
            [str(backlog_py), str(setup_script)], cwd=workspace, env=env,
            text=True, capture_output=True,
        )
        require(setup.returncode == 0,
                f"isolated Backlog setup failed (exit {setup.returncode}): {setup.stderr.strip()}")
        output = run_public_python(backlog_py, """
            from backlog_cli import api
            from backlog_cli.api import Action, ReviewSeverity

            with api.open(actor="product-manager") as bl:
                iteration = bl.create_iteration("Reviewer Iteration closure evaluation")
                bl.trigger(iteration.key, Action.ITERATION_OPENED)

            severities = [ReviewSeverity.BLOCKER, ReviewSeverity.NICE_TO_HAVE, ReviewSeverity.INFO]
            with api.open(actor="claude") as bl:
                roots = [bl.review_open(
                    iteration.key, author="claude", severity=severity,
                    body=f"Iteration {severity.value} evaluation finding.")
                    for severity in severities]

            with api.open(actor="codex") as bl:
                fixes = [bl.review_reply(
                    root.reply_to, author="codex", action="fix",
                    body="Implemented the requested review response with evidence.")
                    for root in roots]

            with api.open(actor="claude") as bl:
                try:
                    bl.trigger(iteration.key, Action.ITERATION_CLOSED)
                except api.BacklogError as exc:
                    message = str(exc)
                    assert "iteration_comments_closed" in message, message
                else:
                    raise AssertionError("iteration.closed passed while reviewer decisions were pending")

                for fix in fixes:
                    bl.review_reply(fix.reply_to, author="claude", action="accept",
                                    body="Confirmed against the current Iteration evidence.")
                bl.trigger(iteration.key, Action.ITERATION_CLOSED)
                assert bl.task(iteration.key).status == "closed"
            print("real Iteration gate passed")
        """, workspace, env)
        require("real Iteration gate passed" in output,
                "public Iteration evaluation did not report success")


def skill_description(text: str) -> str:
    match = re.search(r"(?ms)^description:\s*(.+?)\n---", text)
    require(match is not None, "skill description is missing")
    return match.group(1).strip().strip('"').lower()


def _words(text: str) -> set[str]:
    return set(re.findall(r"[a-z][a-z-]+", text.lower()))


def _trigger_score(prompt: str, description: str, skill: str) -> int:
    """Score prompt terms against the actual frontmatter trigger contract."""
    prompt_words = _words(prompt)
    description_words = _words(description)
    description_lower = description.lower()
    score = len(prompt_words & description_words)
    generic_request = any(term in prompt_words for term in ("status", "next", "action"))
    if generic_request:
        if "use for generic backlog lookups" in description_lower:
            score += 8
        if "do not trigger for generic backlog lookups" in description_lower:
            score -= 8
        if "use for any backlog question" in description_lower:
            score += 12
    if skill == "backlog-reviewer":
        if "independent" in prompt_words and "independent" in description_words:
            score += 4
        if "response" in prompt_words and "response" in description_words:
            score += 2
        if "accept" in prompt_words or "reject" in prompt_words:
            score += int("accept" in description_words or "reject" in description_words)
    else:
        if "status" in prompt_words and "status" in description_words:
            score += 4
        if "next" in prompt_words and "next" in description_words:
            score += 3
        if "action" in prompt_words and ("action" in description_words or "actions" in description_words):
            score += 2
    return score


def route_from_skill_contract(prompt: str, reviewer_description: str, backlog_description: str) -> str:
    """Select the skill whose published description best matches the request."""
    scores = {
        "backlog-reviewer": _trigger_score(prompt, reviewer_description, "backlog-reviewer"),
        "backlog": _trigger_score(prompt, backlog_description, "backlog"),
    }
    require(scores["backlog-reviewer"] != scores["backlog"],
            f"skill trigger descriptions are ambiguous for fixture: {prompt}")
    return max(scores, key=scores.get)


def run_routing_and_forward_evals(root: Path, manifest: dict) -> tuple[int, int]:
    """Execute routing and with/without-skill fixtures from isolated copies."""
    package = root / "skills" / "backlog-reviewer"
    backlog_skill = root / "skills" / "backlog" / "SKILL.md"
    with tempfile.TemporaryDirectory(prefix="backlog-reviewer-forward-") as directory:
        workspace = Path(directory)
        isolated_package = workspace / "skills" / "backlog-reviewer"
        isolated_backlog = workspace / "skills" / "backlog"
        shutil.copytree(package, isolated_package)
        isolated_backlog.mkdir(parents=True)
        shutil.copy2(backlog_skill, isolated_backlog / "SKILL.md")
        reviewer_text = (isolated_package / "SKILL.md").read_text(encoding="utf-8")
        backlog_text = (isolated_backlog / "SKILL.md").read_text(encoding="utf-8")
        workflow_text = (root / "skills" / "backlog" / "references" / "workflow.md").read_text(encoding="utf-8")
        reviewer_desc = skill_description(reviewer_text)
        backlog_desc = skill_description(backlog_text)

        routing = manifest.get("trigger_routing", [])
        require(len(routing) >= 2, "at least two bidirectional routing fixtures are required")
        for fixture in routing:
            selected = route_from_skill_contract(fixture["request"], reviewer_desc, backlog_desc)
            require(selected == fixture["expected_skill"],
                    f"routing fixture selected {selected}, expected {fixture['expected_skill']}: {fixture['id']}")
            require(fixture["distractor"] != selected,
                    f"routing fixture selected its distractor: {fixture['id']}")

        generic_request = next(entry["request"] for entry in routing
                               if entry["expected_skill"] == "backlog")
        overbroad_reviewer = re.sub(
            r"Do not trigger for generic Backlog lookups.*?those\.",
            "Use for generic Backlog lookups and one-off commands; use for any Backlog "
            "question including status, next item, and allowed actions.",
            reviewer_desc,
            flags=re.IGNORECASE,
        )
        overbroad_selected = route_from_skill_contract(generic_request, overbroad_reviewer, backlog_desc)
        require(overbroad_selected == "backlog-reviewer",
                "routing harness does not depend on the reviewer description's trigger contract")

        forward = manifest.get("forward_tests", [])
        require(len(forward) >= 3, "at least three forward tests are required")
        for fixture in forward:
            require(fixture.get("fresh_context") is True and fixture.get("workspace") == "isolated",
                    f"forward fixture is not isolated and fresh-context: {fixture['id']}")
            expected = " ".join(fixture["with_skill_expected"]).lower()
            for term in ("cursor", "review_updates", "severity", "iteration_comments_closed", "iteration_members_finished"):
                if term in expected:
                    require(term.lower() in reviewer_text.lower(),
                            f"with-skill contract missing {term}: {fixture['id']}")
            absent = fixture.get("without_skill_absent")
            without_skill_text = backlog_text + "\n" + workflow_text
            require(absent and absent.lower() not in without_skill_text.lower(),
                    f"without-skill baseline unexpectedly contains reviewer guidance: {fixture['id']}")
        return len(routing), len(forward)


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
        "Use the documented Python API for multi-step or computed work",
        "Reserve the CLI for one simple documented command",
        "Never build shell workflows or scratch files",
        "Filter before reducing",
        "Never decide from truncated, incomplete, or arbitrarily limited evidence",
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

    generic_skill = (ROOT / "skills" / "backlog" / "SKILL.md").read_text(encoding="utf-8")
    generic_description = skill_description(generic_skill).lower()
    for phrase in ["generic backlog lookups", "one-off commands", "backlog-coordinator",
                   "backlog-implementer", "backlog-reviewer", "sustained role-specific delivery"]:
        require(phrase in generic_description,
                f"generic backlog description omits reciprocal routing contract: {phrase}")

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
    require(len(routing) >= 2, "bidirectional trigger routing requires at least two fixtures")
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
        require(entry.get("without_skill_absent"),
                f"forward test lacks a without-skill contrast: {entry.get('id')}")

    iteration = next(entry for entry in evaluations if entry["id"] == "iteration-all-severities")
    require("iteration_comments_closed" in iteration["behavioral_assertion"],
            "Iteration behavioral assertion must name iteration_comments_closed")
    require("Dispositioned responses awaiting reviewer decisions" in iteration["behavioral_assertion"],
            "Iteration behavioral assertion must distinguish disposition from closure")

    sibling_references = [
        ROOT / "skills" / "backlog" / "references" / "review.md",
        ROOT / "skills" / "backlog" / "references" / "api.md",
        ROOT / "skills" / "backlog" / "references" / "workflow.md",
    ]
    for reference in sibling_references:
        require(reference.is_file(), f"missing sibling reference: {reference.relative_to(ROOT)}")

    routing_count, forward_count = run_routing_and_forward_evals(ROOT, manifest)
    run_iteration_behavioral_eval(ROOT)

    print(f"validated backlog-reviewer package: {len(evaluations)} evaluations, {forward_count} forward tests, {routing_count} routing fixtures; real Iteration closure behavior passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())

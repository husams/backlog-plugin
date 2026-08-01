"""Repository-owned, isolated behavioral checks for backlog-coordinator.

The harness models the documented public API boundary. It intentionally uses
only standard library data structures and temporary directories; it never
imports the Backlog implementation, reads a database, or depends on a user
home installation. The real Backlog client remains the workflow authority.
"""

from __future__ import annotations

import json
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[3]
PACKAGE = ROOT / "skills" / "backlog-coordinator"


class Refused(Exception):
    pass


@dataclass
class Task:
    key: str
    task_type: str
    status: str
    created_by: str
    parent: str | None = None
    assignee: str | None = None
    reviewer: str | None = None
    pr_state: str | None = None
    children: list[str] = field(default_factory=list)


@dataclass
class ReviewRoot:
    key: str
    severity: str
    reviewer: str
    implementer: str
    awaiting: str
    task_key: str | None = None
    state: str = "open"
    response: str | None = None
    decision: str | None = None


class PublicBacklogModel:
    """Small public-API-shaped model used to exercise coordinator decisions."""

    def __init__(self) -> None:
        self.tasks: dict[str, Task] = {}
        self.dependencies: set[tuple[str, str]] = set()
        self.iteration_members: dict[str, set[str]] = {}
        self.reviews: dict[str, ReviewRoot] = {}
        self.retros: dict[str, dict[str, Any]] = {}
        self.actions: dict[str, set[str]] = {}
        self._next = {"F": 100, "S": 100, "B": 100, "I": 100, "R": 100}

    def _key(self, prefix: str) -> str:
        key = f"{prefix}-{self._next[prefix]}"
        self._next[prefix] += 1
        return key

    def create(self, task_type: str, actor: str, *, parent: str | None = None, status: str = "Ready") -> Task:
        prefix = {"feature": "F", "story": "S", "bug": "B", "iteration": "I"}[task_type]
        task = Task(self._key(prefix), task_type, status, actor, parent=parent)
        self.tasks[task.key] = task
        if parent:
            self.tasks[parent].children.append(task.key)
        if task_type == "iteration":
            self.iteration_members[task.key] = set()
        self.actions[task.key] = {"work.started"} if task_type in {"story", "bug"} else set()
        return task

    def assign(self, key: str, *, to: str, reviewer: str) -> None:
        if to == reviewer:
            raise Refused("implementer and reviewer must be distinct")
        self.tasks[key].assignee = to
        self.tasks[key].reviewer = reviewer

    def refinement_accept(self, key: str, actor: str) -> None:
        task = self.tasks[key]
        if actor == task.created_by or actor == task.assignee:
            raise Refused("refinement acceptance must be independent")
        task.status = "Ready"

    def add_dependency(self, blocker: str, blocked: str) -> None:
        self.dependencies.add((blocker, blocked))

    def startable(self, key: str) -> bool:
        return not any(target == key and self.tasks[source].status not in {"Accepted", "Done"}
                       for source, target in self.dependencies)

    def open_iteration(self, key: str) -> None:
        task = self.tasks[key]
        if task.task_type != "iteration":
            raise Refused("only an Iteration can be opened")
        task.status = "Open"
        self.actions[key] = {"iteration.closed", "iteration.reopened"}

    def add_member(self, iteration: str, member: str) -> None:
        it = self.tasks[iteration]
        item = self.tasks[member]
        if it.task_type != "iteration" or it.status != "Open":
            raise Refused("membership requires an Open Iteration")
        if (item.task_type not in {"story", "bug"}
                or (item.task_type == "bug" and item.parent is not None)
                or (item.task_type == "story" and item.parent is not None
                    and self.tasks[item.parent].task_type != "feature")
                or item.status != "Ready"):
            raise Refused("only Ready Story or standalone Ready Bug may be admitted")
        if any(member in members for other, members in self.iteration_members.items()
               if other != iteration and self.tasks[other].status == "Open"):
            raise Refused("member is already retained by another Open Iteration")
        self.iteration_members[iteration].add(member)

    def set_pr(self, key: str, state: str) -> None:
        if self.tasks[key].task_type in {"feature", "iteration"}:
            raise Refused("container has no PR")
        self.tasks[key].pr_state = state

    def derived_pr_states(self, key: str) -> list[str]:
        task = self.tasks[key]
        child_keys = task.children if task.task_type == "feature" else sorted(self.iteration_members[key])
        return [self.tasks[child].pr_state for child in child_keys if self.tasks[child].pr_state]

    def can_close_iteration(self, key: str) -> bool:
        members = self.iteration_members[key]
        members_finished = all(
            self.tasks[member].status in {"Accepted", "Done"}
            for member in members
        )
        comments_closed = all(
            review.state == "closed"
            for review in self.reviews.values()
            if review.task_key in members
        )
        return members_finished and comments_closed

    def open_review(self, root: str, severity: str, reviewer: str, implementer: str = "codex",
                    task_key: str | None = None) -> None:
        self.reviews[root] = ReviewRoot(root, severity, reviewer, implementer, implementer, task_key)

    def respond(self, root: str, implementer: str, action: str, body: str) -> None:
        review = self.reviews[root]
        if review.awaiting != implementer or review.implementer != implementer or not body or action not in {"fix", "comment", "reject"}:
            raise Refused("implementer response is invalid")
        review.response = body
        review.awaiting = review.reviewer

    def decide(self, root: str, reviewer: str, action: str, body: str) -> None:
        review = self.reviews[root]
        if review.awaiting != reviewer or review.reviewer != reviewer or not body or action not in {"accept", "reject"}:
            raise Refused("opening reviewer decision is invalid")
        review.decision = action
        review.state = "closed"

    def create_retro(self, iteration: str, creator: str) -> str:
        key = self._key("R")
        self.retros[key] = {"iteration": iteration, "created_by": creator, "status": "Created"}
        return key

    def accept_retro(self, key: str, actor: str) -> None:
        item = self.retros[key]
        if item["created_by"] == actor:
            raise Refused("creator cannot accept retrospective action")
        item["status"] = "Ready"

    def reject_retro(self, key: str, reason: str) -> None:
        if not reason.strip():
            raise Refused("rejection requires a reason")
        self.retros[key].update(status="Rejected", rejection_reason=reason)

    def close_retro(self, key: str, project: str, *, feature: str | None = None, bug: str | None = None) -> None:
        if (feature is None) == (bug is None) or not project:
            raise Refused("closure requires a project and exactly one Feature or Bug")
        self.retros[key].update(status="Done", resolution_project=project, resolution_task=feature or bug)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def package_contract() -> None:
    required = [
        PACKAGE / "SKILL.md",
        PACKAGE / "agents" / "openai.yaml",
        PACKAGE / "references" / "feature-iteration-api.md",
        PACKAGE / "references" / "role-handoffs.md",
        PACKAGE / "references" / "retrospective-actions.md",
        PACKAGE / "evals" / "evals.json",
    ]
    missing = [str(path.relative_to(ROOT)) for path in required if not path.is_file()]
    assert not missing, f"missing package files: {missing}"
    skill = _read(PACKAGE / "SKILL.md")
    assert skill.startswith("---\nname: backlog-coordinator\n")
    assert "bl.assign" in skill and "bl.add_iteration_member" in skill
    assert "iteration_members_finished" in skill and "iteration_comments_closed" in skill
    assert "bl.review_updates" in skill and "child Stories/Bugs" in skill
    assert "refinement.accepted" in skill and "created_by" in skill
    compact_skill = " ".join(skill.split())
    for phrase in (
        "Use the documented Python API for multi-step or computed work",
        "Reserve the CLI for one simple documented command",
        "Never build shell workflows or scratch files",
        "Filter before reducing",
        "Never decide from truncated, incomplete, or arbitrarily limited evidence",
    ):
        assert phrase in compact_skill
    assert not re.search(r"\brefiner\s*[:=]", skill, re.IGNORECASE)
    forbidden = ("sqlite3", "psql", "status =", "status=", "cursor.execute")
    assert not any(token in skill.lower() for token in forbidden)
    metadata = _read(PACKAGE / "agents" / "openai.yaml")
    assert "display_name:" in metadata and "short_description:" in metadata
    assert "default_prompt:" in metadata and "$backlog-coordinator" in metadata
    assert "displayName:" not in metadata and "defaultPrompt:" not in metadata
    data = json.loads(_read(PACKAGE / "evals" / "evals.json"))
    assert len(data["evaluations"]) >= 4
    assert data["isolated_workspaces"] is True


def feature_eval() -> None:
    bl = PublicBacklogModel()
    feature = bl.create("feature", "product-manager")
    first = bl.create("story", "business-analyst", parent=feature.key)
    second = bl.create("story", "business-analyst", parent=feature.key)
    bl.assign(first.key, to="codex", reviewer="claude")
    bl.assign(second.key, to="codex", reviewer="claude")
    bl.refinement_accept(first.key, "product-manager")
    bl.add_dependency(first.key, second.key)
    assert not bl.startable(second.key)
    bl.tasks[first.key].status = "Done"
    assert bl.startable(second.key)
    assert bl.tasks[first.key].created_by == "business-analyst"
    bl.tasks[first.key].pr_state = "approved"
    bl.tasks[second.key].pr_state = "merged"
    assert bl.derived_pr_states(feature.key) == ["approved", "merged"]
    try:
        bl.refinement_accept(second.key, "business-analyst")
    except Refused:
        pass
    else:
        raise AssertionError("creator self-acceptance was not refused")


def iteration_eval() -> None:
    bl = PublicBacklogModel()
    it = bl.create("iteration", "product-manager", status="Created")
    ready_story = bl.create("story", "business-analyst")
    ready_bug = bl.create("bug", "business-analyst")
    feature = bl.create("feature", "business-analyst")
    subtask = bl.create("story", "business-analyst", parent=ready_story.key)
    parented_bug = bl.create("bug", "business-analyst", parent=ready_story.key)
    other = bl.create("iteration", "product-manager", status="Created")
    bl.open_iteration(it.key)
    bl.open_iteration(other.key)
    bl.add_member(it.key, ready_story.key)
    bl.add_member(it.key, ready_bug.key)
    for invalid in (feature.key, subtask.key, parented_bug.key, other.key):
        try:
            bl.add_member(it.key, invalid)
        except Refused:
            pass
        else:
            raise AssertionError(f"invalid member admitted: {invalid}")
    try:
        bl.add_member(other.key, ready_story.key)
    except Refused:
        pass
    else:
        raise AssertionError("duplicate Open-Iteration member admitted")
    bl.tasks[ready_story.key].pr_state = "approved"
    bl.tasks[ready_bug.key].pr_state = "merged"
    assert bl.derived_pr_states(it.key) == ["merged", "approved"]
    for container in (feature.key, it.key):
        try:
            bl.set_pr(container, "open")
        except Refused:
            pass
        else:
            raise AssertionError(f"container PR was recorded: {container}")
    assert it.key in bl.iteration_members and ready_story.key in bl.iteration_members[it.key]


def review_and_retro_eval() -> None:
    bl = PublicBacklogModel()
    story = bl.create("story", "business-analyst")
    bl.assign(story.key, to="codex", reviewer="claude")
    iteration = bl.create("iteration", "product-manager", status="Created")
    bl.open_iteration(iteration.key)
    bl.add_member(iteration.key, story.key)
    for index, severity in enumerate(("blocker", "nice_to_have", "info"), start=1):
        root = f"C-{index}"
        bl.open_review(root, severity, "claude", task_key=story.key)
    assert not bl.can_close_iteration(iteration.key)
    bl.tasks[story.key].status = "Done"
    assert not bl.can_close_iteration(iteration.key)
    for index in range(1, 4):
        root = f"C-{index}"
        bl.respond(root, "codex", "fix", f"Resolved {root} with a regression test.")
        bl.decide(root, "claude", "accept", f"Verified {root} in the fresh validation run.")
        assert bl.reviews[root].state == "closed"
    assert bl.can_close_iteration(iteration.key)
    it = bl.create("iteration", "facilitator", status="Created")
    retro = bl.create_retro(it.key, "facilitator")
    try:
        bl.accept_retro(retro, "facilitator")
    except Refused:
        pass
    else:
        raise AssertionError("retrospective creator acceptance was not refused")
    bl.accept_retro(retro, "product-manager")
    bl.close_retro(retro, "agent-tooling", feature="F-200")
    assert bl.retros[retro]["status"] == "Done"
    rejected = bl.create_retro(it.key, "facilitator")
    try:
        bl.reject_retro(rejected, "   ")
    except Refused:
        pass
    else:
        raise AssertionError("blank retrospective rejection reason was accepted")
    bl.reject_retro(rejected, "Superseded by the current workflow package.")
    assert bl.retros[rejected]["rejection_reason"].startswith("Superseded")


def _description(path: Path) -> str:
    for line in _read(path).splitlines():
        if line.startswith("description:"):
            return line.split(":", 1)[1].strip().lower()
    raise AssertionError(f"missing description in {path}")


def select_skill(prompt: str, candidates: dict[str, str]) -> str:
    """Minimal deterministic metadata router used by the trigger eval."""
    words = set(re.findall(r"[a-z0-9-]+", prompt.lower()))
    coordination_markers = {"coordinate", "decomposition", "membership", "handoff", "retrospective"}
    generic_markers = {"status", "next", "action", "lookup"}
    scores = {}
    for name, description in candidates.items():
        score = sum(word in description for word in words)
        if name == "backlog-coordinator":
            score += 3 * sum(word in words for word in coordination_markers)
        if name == "backlog":
            score += 3 * sum(word in words for word in generic_markers)
        scores[name] = score
    best = max(scores.values())
    matches = [name for name, score in scores.items() if score == best]
    if len(matches) != 1:
        raise AssertionError(f"ambiguous skill selection: {scores}")
    return matches[0]


def trigger_routing_eval() -> None:
    descriptions = {
        "backlog-coordinator": _description(PACKAGE / "SKILL.md"),
        "backlog": _description(ROOT / "skills" / "backlog" / "SKILL.md"),
        "backlog-reviewer": "independently review feedback and decide implementer responses",
    }
    for phrase in (
        "generic backlog lookups",
        "one-off commands",
        "backlog-coordinator",
        "backlog-implementer",
        "backlog-reviewer",
        "sustained role-specific delivery",
    ):
        assert phrase in descriptions["backlog"]
    assert select_skill(
        "Coordinate Feature F-100 decomposition into independently reviewable Stories", descriptions
    ) == "backlog-coordinator"
    assert select_skill(
        "What is next for me? Show the status and allowed action for S-100", descriptions
    ) == "backlog"
    assert select_skill(
        "What is next for me? Show the status and allowed action for S-100",
        {"backlog": descriptions["backlog"], "backlog-coordinator": descriptions["backlog-coordinator"]},
    ) == "backlog"
    assert select_skill(
        "Coordinate Iteration I-100 membership and closure gates",
        {"backlog-coordinator": descriptions["backlog-coordinator"], "backlog-reviewer": descriptions["backlog-reviewer"]},
    ) == "backlog-coordinator"


class UnguidedBaseline(PublicBacklogModel):
    """Unsafe baseline: invoke public operations without coordinator guardrails."""

    def assign(self, key: str, *, to: str, reviewer: str) -> None:
        self.tasks[key].assignee = to
        self.tasks[key].reviewer = reviewer

    def refinement_accept(self, key: str, actor: str) -> None:
        self.tasks[key].status = "Ready"

    def add_member(self, iteration: str, member: str) -> None:
        if self.tasks[iteration].status != "Open":
            raise Refused("membership requires an Open Iteration")
        self.iteration_members[iteration].add(member)


def with_without_skill_eval() -> None:
    """Execute three unsafe baselines and compare them with guardrail refusals."""
    with tempfile.TemporaryDirectory(prefix="coordinator-baseline-") as temp:
        baseline = UnguidedBaseline()
        it = baseline.create("iteration", "product-manager", status="Created")
        other = baseline.create("iteration", "product-manager", status="Created")
        story = baseline.create("story", "business-analyst")
        baseline.open_iteration(it.key)
        baseline.open_iteration(other.key)
        baseline.add_member(it.key, story.key)
        baseline.add_member(other.key, story.key)
        assert story.key in baseline.iteration_members[other.key]
        guarded = PublicBacklogModel()
        git = guarded.create("iteration", "product-manager", status="Created")
        gother = guarded.create("iteration", "product-manager", status="Created")
        gstory = guarded.create("story", "business-analyst")
        guarded.open_iteration(git.key)
        guarded.open_iteration(gother.key)
        guarded.add_member(git.key, gstory.key)
        try:
            guarded.add_member(gother.key, gstory.key)
        except Refused:
            pass
        else:
            raise AssertionError("coordinator allowed duplicate Open-Iteration membership")
        assert Path(temp).is_dir()

    baseline_self = UnguidedBaseline()
    self_story = baseline_self.create("story", "business-analyst")
    baseline_self.refinement_accept(self_story.key, "business-analyst")
    assert baseline_self.tasks[self_story.key].status == "Ready"
    guarded_self = PublicBacklogModel()
    guarded_story = guarded_self.create("story", "business-analyst")
    try:
        guarded_self.refinement_accept(guarded_story.key, "business-analyst")
    except Refused:
        pass
    else:
        raise AssertionError("coordinator allowed creator refinement acceptance")

    baseline_roles = UnguidedBaseline()
    roles_story = baseline_roles.create("story", "business-analyst")
    baseline_roles.assign(roles_story.key, to="codex", reviewer="codex")
    assert baseline_roles.tasks[roles_story.key].reviewer == "codex"
    guarded_roles = PublicBacklogModel()
    guarded_roles_story = guarded_roles.create("story", "business-analyst")
    try:
        guarded_roles.assign(guarded_roles_story.key, to="codex", reviewer="codex")
    except Refused:
        pass
    else:
        raise AssertionError("coordinator allowed same implementer and reviewer")


def cross_skill_prerequisite_eval() -> str:
    roles = [ROOT / "skills" / "backlog-implementer" / "SKILL.md", ROOT / "skills" / "backlog-reviewer" / "SKILL.md"]
    if not all(path.is_file() for path in roles):
        return "pending: role packages are not yet available"
    implementer_text = _read(roles[0]).lower()
    reviewer_text = _read(roles[1]).lower()
    assert {"refinement.accepted", "review", "actor"} <= set(
        word.strip("`'\"(),.:;") for word in implementer_text.split()
    )
    reviewer_words = {word.strip("`'\"(),.:;") for word in reviewer_text.split()}
    assert "reviewer" in reviewer_words and "accept" in reviewer_words
    assert any(word.startswith("bl.review_updates") for word in reviewer_words)
    bl = PublicBacklogModel()
    feature = bl.create("feature", "product-manager")
    story = bl.create("story", "business-analyst", parent=feature.key)
    bl.assign(story.key, to="codex", reviewer="claude")
    bl.refinement_accept(story.key, "product-manager")
    bl.tasks[story.key].status = "Ready"
    iteration = bl.create("iteration", "product-manager", status="Created")
    bl.open_iteration(iteration.key)
    bl.add_member(iteration.key, story.key)
    for index, severity in enumerate(("blocker", "nice_to_have", "info"), start=10):
        root = f"C-{index}"
        bl.open_review(root, severity, "claude")
        bl.respond(root, "codex", "fix", f"Response for {root} includes evidence.")
        bl.decide(root, "claude", "accept", f"Opening reviewer verified {root}.")
    bl.tasks[story.key].status = "Done"
    assert bl.can_close_iteration(iteration.key)
    assert all(review.state == "closed" for review in bl.reviews.values())
    return "passed"


def isolated_workspace_eval() -> None:
    for name, evaluation in (("feature", feature_eval), ("iteration", iteration_eval), ("review", review_and_retro_eval)):
        with tempfile.TemporaryDirectory(prefix=f"backlog-coordinator-{name}-") as temp:
            workspace = Path(temp) / "checkout"
            workspace.mkdir()
            marker = workspace / "marker.txt"
            marker.write_text(name, encoding="utf-8")
            assert marker.read_text(encoding="utf-8") == name
            evaluation()


def main() -> int:
    package_contract()
    trigger_routing_eval()
    with_without_skill_eval()
    isolated_workspace_eval()
    print("backlog-coordinator: 3 behavioral evaluations executed in isolated workspaces")
    print(f"backlog-coordinator: F-007 cross-skill evaluation {cross_skill_prerequisite_eval()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

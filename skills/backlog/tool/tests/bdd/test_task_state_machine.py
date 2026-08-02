from __future__ import annotations

from pytest_bdd import given, scenarios, then, when

from .conftest import World


scenarios("features/task_state_machine.feature")


@when("the task delivery is finalized")
def finalize_delivery(world: World) -> None:
    if world.current_type == "feature":
        world.run(
            "action", world.require_key(), "delivery.released", actor="release-manager"
        )
    else:
        world.run(
            "pr", "set", world.require_key(), "--state", "merged", actor="merge-bot"
        )


@given("a feature with an unfinished story")
def feature_with_unfinished_story(world: World) -> None:
    feature = world.run("feature", "add", "--title", "Parent", actor="creator")
    world.run(
        "story",
        "add",
        "--feature",
        feature["key"],
        "--title",
        "Unfinished child",
        actor="creator",
    )
    world.current_key = feature["key"]
    world.current_type = "feature"


@given("the feature is in review")
def feature_in_review(world: World) -> None:
    for action, actor in (
        ("refinement.accepted", "reviewer"),
        ("work.started", "developer"),
        ("work.completed", "developer"),
    ):
        world.run("action", world.require_key(), action, actor=actor)


@when("pull request events and gate outcomes are exercised")
def exercise_pull_requests_and_gates(world: World) -> None:
    blocker = world.run("bug", "add", "--title", "Gate blocker", actor="creator")
    target = world.run(
        "story",
        "add",
        "--title",
        "Gate target",
        "--assignee",
        "developer",
        "--reviewer",
        "reviewer",
        actor="creator",
    )
    world.run("action", target["key"], "refinement.accepted", actor="reviewer")
    world.run("dep", "add", blocker["key"], "--blocks", target["key"])
    blocked = world.run("gate", target["key"], "--for", "start", expected=2)
    assert any(check["check"] == "dependencies_clear" for check in blocked["checks"])
    world.run("board", "--all", json_output=False)
    world.run("next", actor="developer", json_output=False)
    world.run("gate", target["key"], "--for", "start", "--allow-blocked")
    world.run("dep", "rm", blocker["key"], "--blocks", target["key"])
    world.run("next", actor="developer", json_output=False)

    no_pr = world.run("gate", target["key"], "--for", "in_review", expected=2)
    assert any(check["check"] == "pr_recorded" for check in no_pr["checks"])
    world.run("gate", target["key"], "--for", "in_review", "--no-pr")

    recorded = world.run(
        "pr",
        "set",
        target["key"],
        "--url",
        "https://github.com/example/project/pull/123",
        actor="developer",
    )
    assert recorded["pr_repo"] == "example/project"
    assert recorded["pr_number"] == 123
    world.run("gate", target["key"], "--for", "in_review")
    world.run("gate", target["key"], "--for", "accepted", expected=2)
    world.run(
        "pr",
        "set",
        target["key"],
        "--review-state",
        "approved",
        actor="reviewer",
    )
    world.run("list", json_output=False)
    world.run("gate", target["key"], "--for", "accepted")

    child = world.run(
        "subtask",
        "add",
        "--story",
        target["key"],
        "--title",
        "Open child",
        actor="creator",
    )
    child_gate = world.run("gate", target["key"], "--for", "accepted", expected=2)
    assert any(check["check"] == "children_complete" for check in child_gate["checks"])
    world.run(
        "gate",
        target["key"],
        "--for",
        "accepted",
        "--allow-open-subtasks",
    )

    blocking_review = world.run(
        "review",
        "open",
        target["key"],
        "--author",
        "reviewer",
        "--body",
        "Must be corrected",
        "--severity",
        "blocker",
        actor="reviewer",
    )
    thread_gate = world.run("gate", target["key"], "--for", "start", expected=2)
    assert any(
        check["check"] == "review_threads_closed" for check in thread_gate["checks"]
    )
    world.run("board", "--all", json_output=False)
    world.run("next", actor="developer", json_output=False)
    fixed = world.run(
        "review",
        "reply",
        blocking_review["reply_to"],
        "--author",
        "developer",
        "--action",
        "fix",
        "--body",
        "Corrected",
        actor="developer",
    )
    world.run(
        "review",
        "reply",
        fixed["reply_to"],
        "--author",
        "reviewer",
        "--action",
        "accept",
        "--body",
        "Verified",
        actor="reviewer",
    )

    lifecycle = world.run(
        "bug",
        "add",
        "--title",
        "PR lifecycle",
        "--assignee",
        "developer",
        "--reviewer",
        "reviewer",
        actor="creator",
    )
    key = lifecycle["key"]
    world.run(
        "pr",
        "set",
        key,
        "--url",
        "https://gitlab.example/group/project/-/merge_requests/9",
        "--state",
        "draft",
        actor="developer",
    )
    world.run("list", json_output=False)
    parsed = world.task(key)
    assert parsed["pr_repo"] == "group/project"
    assert parsed["pr_number"] == 9
    world.run("pr", "set", key, "--state", "open", actor="developer")
    world.run(
        "pr",
        "set",
        key,
        "--review-state",
        "changes_requested",
        actor="reviewer",
    )
    world.run("list", json_output=False)
    world.run("pr", "set", key, "--state", "closed", actor="developer")
    world.run("pr", "set", key, "--state", "open", actor="developer")
    world.run("pr", "set", key, "--repo", "group/project", actor="developer")

    for action, actor in (
        ("refinement.accepted", "reviewer"),
        ("work.started", "developer"),
    ):
        world.run("action", key, action, actor=actor)
    world.run("gate", key, "--for", "in_review")
    world.run("action", key, "work.completed", actor="developer")
    world.run("pr", "set", key, "--review-state", "approved", actor="reviewer")
    world.run("gate", key, "--for", "accepted")
    world.run("next", actor="reviewer", json_output=False)
    world.run("action", key, "review.approved", actor="reviewer")
    world.run("gate", key, "--for", "merge")
    world.run("gate", key, "--for", "done", expected=2)
    world.run("pr", "set", key, "--state", "merged", actor="merge-bot")
    world.run("gate", key, "--for", "done", expected=2)

    waived = world.run("story", "add", "--title", "Waived PR", actor="creator")
    world.run("gate", waived["key"], "--for", "accepted", "--no-pr")
    world.run("gate", waived["key"], "--for", "done", "--no-pr", expected=2)

    world.run(
        "workflow",
        "move-add",
        "--type",
        "story",
        "--from",
        "in_progress",
        "--to",
        "in_review",
        "--gate",
        "pr_recorded",
    )
    world.run(
        "workflow",
        "move-add",
        "--type",
        "story",
        "--from",
        "in_review",
        "--to",
        "accepted",
        "--gate",
        "review_threads_closed,pr_approved,children_complete,required_validations_pass",
    )
    world.run(
        "workflow",
        "move-add",
        "--type",
        "story",
        "--from",
        "accepted",
        "--to",
        "done",
        "--gate",
        "pr_merged",
    )
    pr_required = world.run(
        "story",
        "add",
        "--title",
        "Approval is required",
        "--assignee",
        "developer",
        "--reviewer",
        "reviewer",
        actor="creator",
    )
    world.run("action", pr_required["key"], "refinement.accepted", actor="reviewer")
    world.run("action", pr_required["key"], "work.started", actor="developer")
    world.run(
        "pr",
        "set",
        pr_required["key"],
        "--url",
        "https://github.com/example/project/pull/456",
        actor="developer",
    )
    world.run("action", pr_required["key"], "work.completed", actor="developer")
    world.run(
        "action", pr_required["key"], "review.approved", actor="reviewer", expected=None
    )
    assert world.last_result is not None
    assert world.last_result.returncode != 0
    world.run(
        "pr", "set", pr_required["key"], "--review-state", "approved", actor="reviewer"
    )
    world.run("action", pr_required["key"], "review.approved", actor="reviewer")

    no_pr_lifecycle = world.run(
        "story",
        "add",
        "--title",
        "Deliberately delivered without a pull request",
        "--assignee",
        "developer",
        "--reviewer",
        "reviewer",
        actor="creator",
    )
    for action, actor in (
        ("refinement.accepted", "reviewer"),
        ("work.started", "developer"),
    ):
        world.run("action", no_pr_lifecycle["key"], action, actor=actor)
    world.run(
        "action",
        no_pr_lifecycle["key"],
        "work.completed",
        "--no-pr",
        actor="developer",
    )
    assert world.task(no_pr_lifecycle["key"])["pr_waived"] == 1
    world.run("next", actor="reviewer", json_output=False)
    world.run("action", no_pr_lifecycle["key"], "review.approved", actor="reviewer")
    world.run("next", actor="developer", json_output=False)
    world.run(
        "action",
        no_pr_lifecycle["key"],
        "pr.merged",
        "--no-pr",
        actor="merge-bot",
    )
    assert world.task(no_pr_lifecycle["key"])["status"] == "done"

    container = world.run(
        "feature", "add", "--title", "No PR container", actor="creator"
    )
    world.run(
        "workflow",
        "move-add",
        "--type",
        "feature",
        "--from",
        "in_progress",
        "--to",
        "in_review",
        "--gate",
        "pr_recorded",
    )
    world.run(
        "workflow",
        "move-add",
        "--type",
        "feature",
        "--from",
        "in_review",
        "--to",
        "accepted",
        "--gate",
        "pr_approved",
    )
    world.run(
        "workflow",
        "move-add",
        "--type",
        "feature",
        "--from",
        "accepted",
        "--to",
        "done",
        "--gate",
        "pr_merged",
    )
    world.run("gate", container["key"], "--for", "in_review")
    world.run("gate", container["key"], "--for", "accepted")
    for action, actor in (
        ("refinement.accepted", "reviewer"),
        ("work.started", "developer"),
        ("work.completed", "developer"),
        ("review.approved", "reviewer"),
    ):
        world.run("action", container["key"], action, actor=actor)
    world.run("action", container["key"], "delivery.released", actor="release-manager")
    world.run("next", actor="developer", json_output=False)
    world.run("pr", "set", container["key"], "--state", "open", expected=None)
    world.run("pr", "set", child["key"], expected=None)
    world.run("pr", "sync", child["key"], expected=None)

    world.last_json = {"ok": True}


@then("the gate exercise succeeds")
def gate_exercise_succeeds(world: World) -> None:
    assert world.last_json == {"ok": True}

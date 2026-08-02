from __future__ import annotations

from pytest_bdd import given, parsers, scenarios, then, when

from .conftest import World


scenarios("features/retrospective_state_machine.feature")


@given("an iteration")
def iteration(world: World) -> None:
    row = world.run("iteration", "add", "--title", "BDD iteration", actor="creator")
    world.iteration_key = row["key"]


@given("the iteration is open")
def open_iteration(world: World) -> None:
    world.run("action", world.iteration_key, "iteration.opened", actor="facilitator")


@when("a facilitator creates a retrospective action")
def create_retrospective(world: World) -> None:
    row = world.run(
        "retrospective",
        "add",
        "--iteration",
        world.iteration_key,
        "--issue",
        "The same defect escaped twice",
        "--solution",
        "Add an end-to-end regression scenario",
        actor="facilitator",
    )
    world.retrospective_key = row["key"]
    world.run("retrospective", "list")
    world.run("retrospective", "list", "--status", "created")
    world.run("retrospective", "list", "--iteration", world.iteration_key)
    world.run("board", json_output=False)
    world.run("next", actor="product-manager", json_output=False)


@when("a product manager accepts the retrospective action")
def accept_retrospective(world: World) -> None:
    world.run(
        "retrospective",
        "accept",
        world.retrospective_key,
        actor="product-manager",
    )


@when("a product manager rejects the retrospective action")
def reject_retrospective(world: World) -> None:
    world.run(
        "retrospective",
        "reject",
        world.retrospective_key,
        "--reason",
        "The proposed change is too broad",
        actor="product-manager",
    )


@when("the facilitator tries to accept the retrospective action")
def creator_accepts_retrospective(world: World) -> None:
    world.run(
        "retrospective",
        "accept",
        world.retrospective_key,
        actor="facilitator",
        expected=None,
    )


@when("the action is closed against a resolution feature")
def close_against_feature(world: World) -> None:
    feature = world.run(
        "feature", "add", "--title", "Resolution feature", actor="product-manager"
    )
    world.run(
        "retrospective",
        "close",
        world.retrospective_key,
        "--resolution-project",
        "bdd-project",
        "--feature",
        feature["key"],
        actor="product-manager",
    )


@when("the action is closed against a resolution bug")
def close_against_bug(world: World) -> None:
    bug = world.run("bug", "add", "--title", "Resolution bug", actor="product-manager")
    world.run(
        "retrospective",
        "close",
        world.retrospective_key,
        "--resolution-project",
        "bdd-project",
        "--bug",
        bug["key"],
        actor="product-manager",
    )


@when("iteration closure is attempted")
def attempt_iteration_close(world: World) -> None:
    world.run(
        "action",
        world.iteration_key,
        "iteration.closed",
        actor="facilitator",
        expected=None,
    )


@when("a reviewer opens an Iteration comment")
def open_iteration_comment(world: World) -> None:
    world.run(
        "assign",
        world.iteration_key,
        "--to",
        "facilitator",
        "--reviewer",
        "reviewer",
    )
    thread = world.run(
        "review",
        "open",
        world.iteration_key,
        "--author",
        "reviewer",
        "--severity",
        "info",
        "--body",
        "Discuss the Iteration outcome before closure",
    )
    world.review_root = thread["root"]
    world.review_comment = thread["reply_to"]


@when("the Iteration comment is resolved")
def resolve_iteration_comment(world: World) -> None:
    reply = world.run(
        "review",
        "reply",
        world.review_comment,
        "--author",
        "facilitator",
        "--action",
        "fix",
        "--body",
        "The outcome is documented",
    )
    world.run(
        "review",
        "reply",
        reply["reply_to"],
        "--author",
        "reviewer",
        "--action",
        "accept",
        "--body",
        "Discussion resolved",
    )


@when("iteration closure is attempted successfully")
def close_iteration(world: World) -> None:
    world.run("action", world.iteration_key, "iteration.closed", actor="facilitator")


@when("retrospective lifecycle validation is exercised")
def exercise_retrospective_validation(world: World) -> None:
    def rejected(*args: str, actor: str | None = None, message: str) -> None:
        world.run(*args, actor=actor, expected=None)
        assert world.last_result is not None
        assert world.last_result.returncode != 0
        assert message in world.output().lower()

    iteration_key = world.iteration_key
    assert iteration_key is not None
    story = world.run("story", "add", "--title", "Not an iteration", actor="creator")
    rejected("retrospective", "show", "R-999999", message="no retrospective action")
    rejected(
        "retrospective",
        "list",
        "--iteration",
        story["key"],
        message="is not an iteration",
    )
    rejected(
        "retrospective",
        "add",
        "--iteration",
        story["key"],
        "--issue",
        "Repeated",
        "--solution",
        "Resolve it",
        actor="facilitator",
        message="is not an iteration",
    )
    rejected(
        "retrospective",
        "add",
        "--iteration",
        iteration_key,
        "--issue",
        "",
        "--solution",
        "Resolve it",
        actor="facilitator",
        message="repeated_issue must be",
    )
    rejected(
        "retrospective",
        "add",
        "--iteration",
        iteration_key,
        "--issue",
        "Repeated",
        "--solution",
        "",
        actor="facilitator",
        message="proposed_solution must be",
    )
    rejected(
        "retrospective",
        "add",
        "--iteration",
        iteration_key,
        "--issue",
        "Repeated",
        "--solution",
        "Resolve it",
        "--title",
        "",
        actor="facilitator",
        message="title must be",
    )

    action = world.run(
        "retrospective",
        "add",
        "--iteration",
        iteration_key,
        "--issue",
        "Repeated issue",
        "--solution",
        "Resolve it",
        actor="facilitator",
    )
    key = action["key"]
    rejected(
        "retrospective",
        "close",
        key,
        "--resolution-project",
        "bdd-project",
        "--feature",
        story["key"],
        actor="product-manager",
        message="cannot close",
    )
    rejected(
        "retrospective",
        "reject",
        key,
        "--reason",
        "",
        actor="product-manager",
        message="reason must be",
    )
    world.run("retrospective", "accept", key, actor="product-manager")
    rejected(
        "retrospective",
        "accept",
        key,
        actor="another-product-manager",
        message="cannot accept",
    )
    rejected(
        "retrospective",
        "close",
        key,
        "--resolution-project",
        "missing-project",
        "--feature",
        story["key"],
        actor="product-manager",
        message="no project",
    )
    rejected(
        "retrospective",
        "close",
        key,
        "--resolution-project",
        "bdd-project",
        "--feature",
        story["key"],
        actor="product-manager",
        message="must close against a feature or bug",
    )
    bug = world.run("bug", "add", "--title", "Wrong expected type", actor="creator")
    rejected(
        "retrospective",
        "close",
        key,
        "--resolution-project",
        "bdd-project",
        "--feature",
        bug["key"],
        actor="product-manager",
        message="not a feature",
    )
    feature = world.run("feature", "add", "--title", "Resolution", actor="creator")
    world.run(
        "retrospective",
        "close",
        key,
        "--resolution-project",
        "bdd-project",
        "--feature",
        feature["key"],
        actor="product-manager",
    )
    rejected(
        "retrospective",
        "reject",
        key,
        "--reason",
        "Too late",
        actor="product-manager",
        message="cannot reject",
    )


@then(parsers.parse('the retrospective status is "{status}"'))
def retrospective_status(world: World, status: str) -> None:
    row = world.run("retrospective", "show", world.retrospective_key)
    assert row["status"] == status


@then("the retrospective rejection reason is retained")
def rejection_reason_retained(world: World) -> None:
    row = world.run("retrospective", "show", world.retrospective_key)
    assert row["rejection_reason"] == "The proposed change is too broad"


@then("the retrospective command is rejected")
def retrospective_rejected(world: World) -> None:
    assert world.last_result is not None
    assert world.last_result.returncode != 0


@then(parsers.parse('retrospective history records "{events}"'))
def retrospective_history(world: World, events: str) -> None:
    rows = world.run("retrospective", "history", world.retrospective_key)
    assert [row["kind"] for row in rows] == events.split(",")


@then(parsers.parse('the iteration command is rejected by "{gate}"'))
def iteration_rejected_by_gate(world: World, gate: str) -> None:
    assert world.last_result is not None
    assert world.last_result.returncode != 0
    assert gate in world.output()


@then(parsers.parse('the iteration status is "{status}"'))
def iteration_status(world: World, status: str) -> None:
    assert world.run("show", world.iteration_key)["status"] == status

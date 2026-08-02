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
    bug = world.run(
        "bug", "add", "--title", "Resolution bug", actor="product-manager"
    )
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


@when("iteration closure is attempted successfully")
def close_iteration(world: World) -> None:
    world.run("action", world.iteration_key, "iteration.closed", actor="facilitator")


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

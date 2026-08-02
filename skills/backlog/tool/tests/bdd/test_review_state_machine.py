from __future__ import annotations

from pytest_bdd import given, scenarios, then, when

from .conftest import World


scenarios("features/review_state_machine.feature")


@given("a ready story assigned to a developer and reviewer")
def ready_assigned_story(world: World) -> None:
    row = world.run("story", "add", "--title", "Review story", actor="creator")
    world.current_key = row["key"]
    world.current_type = "story"
    world.run("assign", row["key"], "--to", "developer", "--reviewer", "reviewer")
    world.run("action", row["key"], "refinement.accepted", actor="reviewer")


@when("the reviewer opens a blocker")
def reviewer_opens_blocker(world: World) -> None:
    thread = world.run(
        "review",
        "open",
        world.require_key(),
        "--author",
        "reviewer",
        "--severity",
        "blocker",
        "--body",
        "The behavior is incomplete",
    )
    world.review_root = thread["root"]
    world.review_comment = thread["reply_to"]


@when('the developer replies with "fix"')
def developer_fixes(world: World) -> None:
    thread = world.run(
        "review",
        "reply",
        world.review_comment or world.review_root,
        "--author",
        "developer",
        "--action",
        "fix",
        "--body",
        "Implemented and tested",
    )
    world.review_comment = thread["reply_to"]


@when('the reviewer replies with "accept"')
def reviewer_accepts(world: World) -> None:
    thread = world.run(
        "review",
        "reply",
        world.review_comment,
        "--author",
        "reviewer",
        "--action",
        "accept",
        "--body",
        "Verified",
    )
    world.review_comment = thread["reply_to"]


@when('the reviewer replies with "reject"')
def reviewer_rejects(world: World) -> None:
    thread = world.run(
        "review",
        "reply",
        world.review_comment,
        "--author",
        "reviewer",
        "--action",
        "reject",
        "--body",
        "Still incomplete",
    )
    world.review_comment = thread["reply_to"]


@when("the reviewer reopens the thread")
def reviewer_reopens(world: World) -> None:
    thread = world.run(
        "review",
        "reopen",
        world.review_root,
        "--author",
        "reviewer",
        "--body",
        "Regression found",
    )
    world.review_comment = thread["reply_to"]


@when("the opening reviewer replies as the developer")
def reviewer_impersonates_developer(world: World) -> None:
    world.run(
        "review",
        "reply",
        world.review_comment,
        "--author",
        "reviewer",
        "--role",
        "developer",
        "--action",
        "fix",
        "--body",
        "Invalid role",
        expected=None,
    )


@then("the review command is rejected")
def review_rejected(world: World) -> None:
    assert world.last_result is not None
    assert world.last_result.returncode != 0


@then('the review state is "awaiting_developer"')
def review_awaits_developer(world: World) -> None:
    _assert_review_field(world, "state", "awaiting_developer")


@then('the review state is "awaiting_reviewer"')
def review_awaits_reviewer(world: World) -> None:
    _assert_review_field(world, "state", "awaiting_reviewer")


@then('the review state is "closed"')
def review_is_closed(world: World) -> None:
    _assert_review_field(world, "state", "closed")


@then('the review resolution is "accepted_by_reviewer"')
def review_is_accepted(world: World) -> None:
    _assert_review_field(world, "resolution", "accepted_by_reviewer")


def _assert_review_field(world: World, field: str, value: str) -> None:
    thread = world.run("review", "thread", world.review_root)
    assert thread[field] == value

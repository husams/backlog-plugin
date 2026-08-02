from __future__ import annotations

from pytest_bdd import given, scenarios, then, when

from .world import World


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


@when("review discovery and severity commands are exercised")
def exercise_review_discovery(world: World) -> None:
    thread = world.run(
        "review",
        "open",
        world.require_key(),
        "--author",
        "reviewer",
        "--severity",
        "nice_to_have",
        "--title",
        "Polish",
        "--file",
        "src/example.py",
        "--line",
        "8",
        "--body",
        "Optional improvement",
        actor="reviewer",
    )
    world.review_root = thread["root"]
    world.review_comment = thread["reply_to"]
    world.run("review", "thread", world.review_root)
    world.run("review", "audit", world.review_root)
    world.run("review", "list", world.require_key())
    world.run("review", "list", world.require_key(), "--state", "all")
    world.run(
        "review",
        "list",
        world.require_key(),
        "--severity",
        "nice_to_have",
    )
    world.run("review", "inbox", actor="developer")
    world.run("review", "inbox", "--role", "developer", actor="developer")
    world.run(
        "review",
        "inbox",
        "--item",
        world.require_key(),
        "--severity",
        "nice_to_have",
        actor="developer",
    )
    changed = world.run(
        "review",
        "severity",
        world.review_root,
        "--severity",
        "info",
        "--author",
        "reviewer",
        actor="reviewer",
    )
    assert changed["severity"] == "info"
    commented = world.run(
        "review",
        "reply",
        world.review_comment,
        "--author",
        "developer",
        "--action",
        "comment",
        "--body",
        "Acknowledged",
        actor="developer",
    )
    world.run(
        "review",
        "reply",
        commented["reply_to"],
        "--author",
        "reviewer",
        "--action",
        "accept",
        "--body",
        "Accepted",
        actor="reviewer",
    )
    world.run("review", "list", world.require_key(), "--state", "closed")
    world.last_json = {"root": world.review_root}


@when("review validation and escalation commands are exercised")
def exercise_review_validation(world: World) -> None:
    def rejected(*args: str, message: str) -> None:
        world.run(*args, expected=None)
        assert world.last_result is not None
        assert world.last_result.returncode != 0
        assert message in world.output().lower()

    key = world.require_key()
    rejected(
        "review",
        "open",
        key,
        "--author",
        "developer",
        "--role",
        "developer",
        "--body",
        "No",
        message="only a reviewer",
    )
    rejected(
        "review",
        "open",
        key,
        "--author",
        "outsider",
        "--body",
        "Cannot infer",
        message="cannot infer role",
    )
    rejected(
        "review",
        "open",
        key,
        "--author",
        "reviewer",
        "--body",
        "",
        message="non-empty body",
    )

    unassigned = world.run(
        "story", "add", "--title", "Unassigned review", actor="creator"
    )
    inferred = world.run(
        "review",
        "open",
        unassigned["key"],
        "--author",
        "independent-reviewer",
        "--body",
        "Inferred reviewer",
        "--severity",
        "info",
    )
    assert inferred["reviewer"] == "independent-reviewer"

    thread = world.run(
        "review",
        "open",
        key,
        "--author",
        "reviewer",
        "--body",
        "Escalate this finding",
        "--severity",
        "nice_to_have",
    )
    world.review_root = thread["root"]
    world.review_comment = thread["reply_to"]
    unchanged = world.run(
        "review",
        "severity",
        world.review_root,
        "--severity",
        "nice_to_have",
        "--author",
        "reviewer",
    )
    assert unchanged["severity"] == "nice_to_have"
    escalated = world.run(
        "review",
        "severity",
        world.review_root,
        "--severity",
        "blocker",
        "--author",
        "reviewer",
    )
    assert escalated["severity"] == "blocker"

    rejected(
        "review",
        "reply",
        world.review_comment,
        "--author",
        "reviewer",
        "--action",
        "comment",
        "--body",
        "Wrong turn",
        message="does not allow reviewer action",
    )
    rejected(
        "review",
        "reply",
        world.review_comment,
        "--author",
        "another-reviewer",
        "--role",
        "reviewer",
        "--action",
        "comment",
        "--body",
        "Take over",
        message="cannot replace",
    )
    rejected(
        "review",
        "reopen",
        world.review_root,
        "--author",
        "reviewer",
        "--body",
        "Still open",
        message="already open",
    )
    rejected(
        "review",
        "reply",
        "RC-999999",
        "--author",
        "developer",
        "--action",
        "fix",
        "--body",
        "Missing",
        message="no review comment",
    )
    rejected(
        "review",
        "thread",
        "RC-999999",
        message="no review thread",
    )
    rejected(
        "review",
        "audit",
        "RC-999999",
        message="no review thread",
    )
    rejected(
        "review",
        "severity",
        "RC-999999",
        "--severity",
        "info",
        "--author",
        "reviewer",
        message="no review thread",
    )

    fixed = world.run(
        "review",
        "reply",
        world.review_comment,
        "--author",
        "developer",
        "--action",
        "fix",
        "--body",
        "Fixed",
    )
    world.review_comment = fixed["reply_to"]
    accepted = world.run(
        "review",
        "reply",
        world.review_comment,
        "--author",
        "reviewer",
        "--action",
        "accept",
        "--body",
        "Accepted",
    )
    world.review_comment = accepted["reply_to"]
    rejected(
        "review",
        "reply",
        world.review_comment,
        "--author",
        "developer",
        "--action",
        "comment",
        "--body",
        "Closed",
        message="is closed",
    )
    rejected(
        "review",
        "reopen",
        world.review_root,
        "--author",
        "developer",
        "--body",
        "Developer reopen",
        message="only a reviewer",
    )
    world.run("review", "thread", world.review_root, "--full")
    world.last_json = {"root": world.review_root}


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


@then("review discovery reports the complete thread")
def review_discovery_complete(world: World) -> None:
    assert world.last_json == {"root": world.review_root}


def _assert_review_field(world: World, field: str, value: str) -> None:
    thread = world.run("review", "thread", world.review_root)
    assert thread[field] == value

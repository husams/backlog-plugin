from __future__ import annotations

from pytest_bdd import given, scenarios, when

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

from __future__ import annotations

from pytest_bdd import given, parsers, then, when

from ..world import World


@given(parsers.parse('a "{task_type}" task'))
def task_of_type(world: World, task_type: str) -> None:
    if task_type == "feature":
        row = world.run("feature", "add", "--title", "BDD feature", actor="creator")
    elif task_type == "story":
        feature = world.run(
            "feature", "add", "--title", "BDD parent feature", actor="creator"
        )
        world.keys["feature"] = feature["key"]
        row = world.run(
            "story",
            "add",
            "--feature",
            feature["key"],
            "--title",
            "BDD story",
            actor="creator",
        )
    elif task_type == "subtask":
        feature = world.run(
            "feature", "add", "--title", "BDD parent feature", actor="creator"
        )
        story = world.run(
            "story",
            "add",
            "--feature",
            feature["key"],
            "--title",
            "BDD parent story",
            actor="creator",
        )
        world.keys.update(feature=feature["key"], story=story["key"])
        row = world.run(
            "subtask",
            "add",
            "--story",
            story["key"],
            "--title",
            "BDD subtask",
            actor="creator",
        )
    else:
        raise AssertionError(f"unsupported BDD task type: {task_type}")
    world.current_key = row["key"]
    world.current_type = task_type
    world.keys[task_type] = row["key"]


@when(parsers.parse('action "{action}" is submitted by "{actor}"'))
def submit_action(world: World, action: str, actor: str) -> None:
    world.run("action", world.require_key(), action, actor=actor)


@when(parsers.parse('action "{action}" is submitted and rejected'))
def submit_rejected_action(world: World, action: str) -> None:
    world.run("action", world.require_key(), action, actor="reviewer", expected=None)


@then(parsers.parse('the task status is "{status}"'))
def task_has_status(world: World, status: str) -> None:
    assert world.task()["status"] == status


@then("the command reports an unavailable action")
def unavailable_action_reported(world: World) -> None:
    assert world.last_result is not None
    assert world.last_result.returncode == 0
    assert world.last_json["transitioned"] is False


@then(parsers.parse('the command reports the "{gate}" gate'))
def gate_reported(world: World, gate: str) -> None:
    assert world.last_result is not None
    assert world.last_result.returncode != 0
    assert gate in world.output()


@then(parsers.parse('the command reports "{message}"'))
def command_reports(world: World, message: str) -> None:
    assert message in world.output()

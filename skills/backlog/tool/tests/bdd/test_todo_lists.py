from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path

from pytest_bdd import scenarios, then, when

from backlog_cli import api

from .world import World


scenarios("features/todo_lists.feature")


def _story(world: World, title: str) -> str:
    return world.run("story", "add", "--title", title, actor="creator")["key"]


def _start(world: World, key: str) -> None:
    world.run("action", key, "refinement.accepted", actor="reviewer")
    world.run("action", key, "work.started", actor="developer")


@contextmanager
def _open_api(world: World, actor: str | None = None):
    names = ("BACKLOG_DB", "BACK_LOG_URL", "BACKLOG_PROJECT", "PYTHONPATH")
    original = {name: os.environ.get(name) for name in names}
    old_cwd = Path.cwd()
    try:
        for name in names:
            if name in world.env:
                os.environ[name] = world.env[name]
            else:
                os.environ.pop(name, None)
        os.chdir(world.root)
        with api.open(actor=actor) as backlog:
            yield backlog
    finally:
        os.chdir(old_cwd)
        for name, value in original.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


@when("ordered todos are created in one or more calls")
def create_ordered(world: World) -> None:
    key = _story(world, "Ordered")
    world.run("todo", "add", key, "--content", "one\ntwo", actor="developer")
    world.run("todo", "add", key, "--content", "three", actor="developer")
    rows = world.run("todo", "list", key)
    assert [row["content"] for row in rows] == ["one", "two", "three"]
    assert [row["position"] for row in rows] == [0, 1, 2]
    assert {row["state"] for row in rows} == {"open"}
    world.last_json = {"ok": True}


@when("mixed-state todos are reordered")
def reorder_mixed(world: World) -> None:
    key = _story(world, "Reordered")
    rows = world.run("todo", "add", key, "--content", "one\ntwo\nthree", actor="developer")
    world.run("todo", "close", str(rows[1]["id"]), actor="developer")
    world.run(
        "todo", "move", str(rows[2]["id"]), "--position", "0", actor="developer"
    )
    reordered = world.run("todo", "list", key)
    assert [row["content"] for row in reordered] == ["three", "one", "two"]
    assert [row["position"] for row in reordered] == [0, 1, 2]
    assert [row["state"] for row in reordered] == ["open", "open", "closed"]
    world.last_json = {"ok": True}


@when("a todo is closed and reopened by attributed actors")
def attributed_state(world: World) -> None:
    key = _story(world, "Attributed")
    todo = world.run("todo", "add", key, "--content", "step", actor="developer")[0]
    closed = world.run("todo", "close", str(todo["id"]), actor="developer")
    reopened = world.run("todo", "reopen", str(todo["id"]), actor="review-fixer")
    assert closed["state"] == "closed" and closed["updated_by"] == "developer"
    assert reopened["state"] == "open" and reopened["updated_by"] == "review-fixer"
    history = world.run("history", key, json_output=False)
    assert "todo.closed" in history and "todo.reopened" in history
    assert "developer" in history and "review-fixer" in history
    world.last_json = {"ok": True}


@when("a todo-free task is submitted for review")
def submit_empty(world: World) -> None:
    key = _story(world, "No todos")
    _start(world, key)
    world.run("action", key, "review.submitted", actor="developer")
    assert world.task(key)["status"] == "in_review"
    world.last_json = {"ok": True}


@when("review is attempted with open todos")
def block_open(world: World) -> None:
    key = _story(world, "Open todos")
    _start(world, key)
    world.run("todo", "add", key, "--content", "alpha\nbeta", actor="developer")
    world.run("action", key, "review.submitted", actor="developer", expected=None)
    assert world.last_result is not None and world.last_result.returncode != 0
    assert "todos_closed" in world.output()
    assert "alpha" in world.output() and "beta" in world.output()
    assert world.task(key)["status"] == "in_progress"
    world.last_json = {"ok": True}


@when("review is attempted after every todo closes")
def submit_closed(world: World) -> None:
    key = _story(world, "Closed todos")
    _start(world, key)
    rows = world.run("todo", "add", key, "--content", "alpha\nbeta", actor="developer")
    for row in rows:
        world.run("todo", "close", str(row["id"]), actor="developer")
    world.run("action", key, "review.submitted", actor="developer")
    assert world.task(key)["status"] == "in_review"
    world.last_json = {"ok": True}


@when("returned work gains open todos before resubmission")
def block_resubmission(world: World) -> None:
    key = _story(world, "Returned")
    _start(world, key)
    todo = world.run("todo", "add", key, "--content", "original", actor="developer")[0]
    world.run("todo", "close", str(todo["id"]), actor="developer")
    world.run("action", key, "review.submitted", actor="developer")
    world.run("action", key, "review.changes_requested", actor="reviewer")
    world.run("action", key, "work.resumed", actor="developer")
    world.run("todo", "reopen", str(todo["id"]), actor="developer")
    world.run("todo", "add", key, "--content", "feedback", actor="developer")
    world.run("action", key, "review.submitted", actor="developer", expected=None)
    assert world.last_result is not None and world.last_result.returncode != 0
    assert "original" in world.output() and "feedback" in world.output()
    world.last_json = {"ok": True}


@when("todos coexist with ordinary items and an unfinished subtask")
def preserve_items_and_children(world: World) -> None:
    key = _story(world, "Mixed task")
    world.run("set", key, "--ac", "criterion")
    checklist = world.run(
        "item", "add", key, "--kind", "checklist", "--content", "check"
    )[0]
    world.run("item", "add", key, "--kind", "note", "--content", "note")
    world.run("todo", "add", key, "--content", "todo", actor="developer")
    child = world.run(
        "subtask", "add", "--story", key, "--title", "child", actor="creator"
    )
    world.run("item", "check", str(checklist["id"]), actor="developer")
    details = world.run("show", key)["items"]
    assert {item["kind"] for item in details} == {
        "acceptance_criteria",
        "checklist",
        "note",
        "todo",
    }
    world.run("gate", key, "--for", "accepted", expected=2)
    assert "children_complete" in world.output() and child["key"] in world.output()
    world.last_json = {"ok": True}


@when("invalid todo operations are attempted")
def invalid_atomic(world: World) -> None:
    key = _story(world, "Atomic")
    checklist = world.run(
        "item", "add", key, "--kind", "checklist", "--content", "check"
    )[0]
    todos = world.run("todo", "add", key, "--content", "one\ntwo", actor="developer")
    before = world.run("todo", "list", key)
    attempts = (
        ("todo", "add", "S-999", "--content", "x"),
        ("todo", "close", "999999"),
        ("todo", "close", str(checklist["id"])),
        ("todo", "move", str(todos[0]["id"]), "--position", "4"),
    )
    for attempt in attempts:
        world.run(*attempt, actor="developer", expected=None)
        assert world.last_result is not None and world.last_result.returncode != 0
    assert world.run("todo", "list", key) == before
    world.last_json = {"ok": True}


@when("todo operations are mixed across the CLI and Python API")
def cli_api_equivalence(world: World) -> None:
    key = _story(world, "Equivalent")
    cli_todo = world.run("todo", "add", key, "--content", "cli", actor="developer")[0]
    with _open_api(world, actor="api-developer") as backlog:
        api_todo = backlog.add_todo(key, "api")
        backlog.close_todo(cli_todo["id"])
        backlog.move_todo(api_todo["id"], 0)
    world.run("todo", "reopen", str(cli_todo["id"]), actor="cli-developer")
    cli_rows = world.run("todo", "list", key)
    with _open_api(world) as backlog:
        api_rows = backlog.todos(key)
    assert cli_rows == api_rows
    assert [row["content"] for row in api_rows] == ["api", "cli"]
    assert [row["state"] for row in api_rows] == ["open", "open"]
    assert api_rows[1]["updated_by"] == "cli-developer"
    world.last_json = {"ok": True}


@then("the todo behavior succeeds")
def todo_succeeded(world: World) -> None:
    assert world.last_json == {"ok": True}

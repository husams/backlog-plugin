from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pytest_bdd import given, parsers, then, when


SOURCE_ROOT = Path(__file__).resolve().parents[2] / "src"


@dataclass
class World:
    root: Path
    env: dict[str, str]
    current_key: str | None = None
    current_type: str | None = None
    keys: dict[str, str] = field(default_factory=dict)
    last_result: subprocess.CompletedProcess[str] | None = None
    last_json: Any = None
    item_id: int | None = None
    review_root: str | None = None
    review_comment: str | None = None
    retrospective_key: str | None = None
    iteration_key: str | None = None

    def run(
        self,
        *args: str,
        actor: str | None = None,
        json_output: bool = True,
        expected: int | None = 0,
    ) -> Any:
        command = [sys.executable, "-m", "backlog_cli.cli"]
        if json_output:
            command.append("--json")
        if actor:
            command.extend(["--actor", actor])
        command.extend(args)
        result = subprocess.run(
            command,
            cwd=self.root,
            env=self.env,
            text=True,
            capture_output=True,
        )
        self.last_result = result
        self.last_json = (
            json.loads(result.stdout) if json_output and result.stdout.strip() else None
        )
        if expected is not None:
            assert result.returncode == expected, result.stderr or result.stdout
        return self.last_json if json_output else result.stdout

    def task(self, key: str | None = None) -> dict[str, Any]:
        previous_result, previous_json = self.last_result, self.last_json
        payload = self.run("show", key or self.require_key())
        self.last_result, self.last_json = previous_result, previous_json
        return payload

    def require_key(self) -> str:
        assert self.current_key is not None
        return self.current_key

    def output(self) -> str:
        assert self.last_result is not None
        return f"{self.last_result.stdout}\n{self.last_result.stderr}"


@given("a new backlog project", target_fixture="world")
def new_backlog_project(tmp_path: Path) -> World:
    env = {
        **os.environ,
        "BACKLOG_DB": "sqlite",
        "BACK_LOG_URL": "",
        "BACKLOG_PROJECT": "bdd-project",
        "PYTHONPATH": str(SOURCE_ROOT),
    }
    world = World(tmp_path, env)
    world.run("init", ".", actor="fixture-creator")
    return world


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

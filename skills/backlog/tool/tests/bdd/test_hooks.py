from __future__ import annotations

import textwrap
import subprocess
import sys

from pytest_bdd import given, parsers, scenarios, then, when

from .conftest import World


scenarios("features/hooks.feature")


@given("transition hooks that record every call")
def recording_hooks(world: World) -> None:
    _write_hooks(
        world,
        """
        def _record(message):
            with open("hooks.log", "a", encoding="utf-8") as stream:
                stream.write(message + "\\n")

        def pre_transition(action, trigger, old, new, backlog):
            _record(f"pre:{action.value}:{old}:{new}")
            return new

        def post_transition(action, trigger, old, new, backlog):
            _record(f"post:{action.value}:{old}:{new}")
        """,
    )


@given("transition hooks that block a requested transition")
def blocking_hooks(world: World) -> None:
    _write_hooks(
        world,
        """
        def _record(message):
            with open("hooks.log", "a", encoding="utf-8") as stream:
                stream.write(message + "\\n")

        def pre_transition(action, trigger, old, new, backlog):
            _record("pre")
            return old if trigger["parameters"].get("block") == "yes" else new

        def post_transition(action, trigger, old, new, backlog):
            _record("post")
        """,
    )


@given("a pre transition hook that raises an error")
def failing_pre_hook(world: World) -> None:
    _write_hooks(
        world,
        """
        def pre_transition(action, trigger, old, new, backlog):
            raise RuntimeError("pre hook failed")
        """,
    )


@given("a post transition hook that raises an error")
def failing_post_hook(world: World) -> None:
    _write_hooks(
        world,
        """
        def pre_transition(action, trigger, old, new, backlog):
            return new

        def post_transition(action, trigger, old, new, backlog):
            raise RuntimeError("post hook failed")
        """,
    )


@when("refinement acceptance requests a block")
def request_block(world: World) -> None:
    world.run(
        "action",
        world.require_key(),
        "refinement.accepted",
        "--parameter",
        "block=yes",
        actor="reviewer",
    )


@when("invalid transition hook contracts are exercised")
def invalid_transition_hooks(world: World) -> None:
    def rejected(source: str, message: str) -> None:
        _write_hooks(world, source)
        world.run(
            "action",
            world.require_key(),
            "refinement.accepted",
            actor="reviewer",
            expected=None,
        )
        assert world.last_result is not None
        assert world.last_result.returncode != 0
        assert message in world.output()

    rejected("raise RuntimeError('package failed')", "cannot load project hooks")
    rejected("pre_transition = 42", "pre_transition is not callable")
    rejected(
        "def pre_transition(action, trigger, old, new, backlog):\n    return ''",
        "pre_transition must return a non-empty state string",
    )
    rejected(
        "def pre_transition(action, trigger, old, new, backlog):\n    return 'done'",
        "illegal transition",
    )
    rejected("post_transition = 42", "post_transition is not callable")
    _write_hooks(
        world,
        "def pre_transition(action, trigger, old, new, backlog):\n    return old",
    )
    nested = world.root / "src" / "nested"
    nested.mkdir(parents=True)
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "backlog_cli.cli",
            "--json",
            "--actor",
            "reviewer",
            "action",
            world.require_key(),
            "refinement.accepted",
        ],
        cwd=nested,
        env=world.env,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    world.last_json = {"ok": True}


@when("custom action workflow configuration is exercised")
def custom_action_workflow(world: World) -> None:
    workflow_path = world.root / ".backlog" / "workflow.yaml"
    valid = """
    name: BDD workflow
    states:
      - slug: created
        display: Created
        category: backlog
        initial: true
      - slug: ready
        display: Ready
        category: backlog
    transitions:
      - task_types: [feature, story, bug, subtask, iteration]
        from: created
        action: refinement.accepted
        to: ready
        gates: []
    """
    workflow_path.write_text(textwrap.dedent(valid).lstrip(), encoding="utf-8")
    world.run("actions", world.require_key())
    world.run("actions", world.require_key())
    world.run("action", world.require_key(), "refinement.accepted", actor="reviewer")
    assert world.task()["status"] == "ready"

    def rejected(source: str, message: str) -> None:
        workflow_path.write_text(textwrap.dedent(source).lstrip(), encoding="utf-8")
        world.run("actions", world.require_key(), expected=None)
        assert world.last_result is not None
        assert world.last_result.returncode != 0
        assert message in world.output()

    rejected("- not-a-mapping\n", "must contain a mapping")
    rejected("states: [\n", "invalid workflow configuration")
    rejected("states: []\n", "requires states and transitions lists")
    rejected(
        """
        states: [{slug: ready, initial: true}]
        transitions: [invalid]
        """,
        "must be a mapping",
    )
    rejected(
        """
        states: [{slug: ready, initial: true}]
        transitions: [{}]
        """,
        "is missing:",
    )
    rejected(
        """
        states: [{slug: ready, initial: true}]
        transitions:
          - {task_types: [story], from: ready, action: not.real, to: ready}
        """,
        "unknown action",
    )
    rejected(
        """
        states: [{slug: ready, initial: true}]
        transitions:
          - {task_types: [story], from: missing, action: refinement.accepted, to: ready}
        """,
        "references an undefined state",
    )
    rejected(
        """
        states: [{slug: created, initial: true}]
        transitions: []
        """,
        "task states are not declared",
    )
    rejected(
        """
        states:
          - {slug: created, initial: true}
          - {slug: ready, task_types: invalid}
        transitions: []
        """,
        "invalid task_types",
    )
    rejected(
        """
        states:
          - {slug: created, initial: true}
          - {slug: ready, category: unknown}
        transitions: []
        """,
        "unknown category",
    )
    rejected(
        """
        states: [{slug: created, initial: true}, {slug: ready}]
        transitions:
          - task_types: [story]
            from: ready
            action: refinement.accepted
            to: ready
            gates: [unknown_gate]
        """,
        "contains an unknown gate",
    )
    rejected(
        """
        states: [{slug: created, initial: true}, {slug: ready}]
        transitions:
          - {task_types: [unknown], from: ready, action: refinement.accepted, to: ready}
        """,
        "unknown task type",
    )
    rejected(
        """
        states: [{slug: created, initial: true}, {slug: ready}]
        transitions:
          - {task_types: [story], from: ready, action: refinement.accepted, to: ready, gates: []}
          - {task_types: [story], from: ready, action: work.started, to: ready, gates: [dependencies_clear]}
        """,
        "assigns different gates",
    )
    rejected(
        """
        states: [{slug: created}, {slug: ready}]
        transitions: []
        """,
        "exactly one initial state",
    )
    workflow_path.write_text(
        textwrap.dedent(
            """
            states: [{slug: created, initial: true}, {slug: ready}]
            transitions:
              - {task_types: [story], from: ready, action: work.started, to: ready, gates: []}
              - {task_types: [story], from: ready, action: work.started, to: ready, gates: []}
            """
        ).lstrip(),
        encoding="utf-8",
    )
    world.run(
        "action", world.require_key(), "work.started", actor="developer", expected=None
    )
    assert "more than one transition" in world.output()
    world.last_json = {"ok": True}


@then(parsers.parse('the hook log contains "{entry}"'))
def hook_log_contains(world: World, entry: str) -> None:
    assert entry in (world.root / "hooks.log").read_text(encoding="utf-8")


@then("the transition is reported as skipped")
def transition_skipped(world: World) -> None:
    assert world.last_json["transitioned"] is False


@then("no post hook was called")
def post_hook_not_called(world: World) -> None:
    assert (world.root / "hooks.log").read_text(encoding="utf-8").splitlines() == [
        "pre"
    ]


@then("hook configuration errors are reported")
def hook_configuration_errors_reported(world: World) -> None:
    assert world.last_json == {"ok": True}


def _write_hooks(world: World, source: str) -> None:
    package = world.root / ".backlog" / "hooks"
    package.mkdir(exist_ok=True)
    (package / "__init__.py").write_text(
        textwrap.dedent(source).lstrip(), encoding="utf-8"
    )

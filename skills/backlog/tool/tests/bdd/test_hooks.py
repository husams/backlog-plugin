from __future__ import annotations

import textwrap

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


def _write_hooks(world: World, source: str) -> None:
    package = world.root / ".backlog" / "hooks"
    package.mkdir(exist_ok=True)
    (package / "__init__.py").write_text(
        textwrap.dedent(source).lstrip(), encoding="utf-8"
    )

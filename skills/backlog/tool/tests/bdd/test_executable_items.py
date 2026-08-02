from __future__ import annotations

import shlex
import sys
import textwrap

from pytest_bdd import given, parsers, scenarios, then, when

from .conftest import World


scenarios("features/executable_items.feature")


@given(parsers.parse("an executable shell item expecting exit code {exit_code:d}"))
def shell_item(world: World, exit_code: int) -> None:
    source = 'print("validated")'
    command = f"{shlex.quote(sys.executable)} -c {shlex.quote(source)}"
    rows = world.run(
        "item",
        "add",
        world.require_key(),
        "--kind",
        "acceptance_criteria",
        "--content",
        "The shell validation passes",
        "--shell",
        command,
        "--expected-exit-code",
        str(exit_code),
        actor="developer",
    )
    world.item_id = int(rows[0]["id"])


@given("shell execution is enabled")
@when("shell execution is enabled")
def enable_shell(world: World) -> None:
    (world.root / ".backlog" / "execution.yaml").write_text(
        "shell_enabled: true\nmax_timeout_seconds: 120\nmax_output_bytes: 4096\n",
        encoding="utf-8",
    )


@given("an executable hook item expecting a matching result")
def hook_item(world: World) -> None:
    rows = world.run(
        "item",
        "add",
        world.require_key(),
        "--kind",
        "acceptance_criteria",
        "--content",
        "The hook validation passes",
        "--hook",
        "checks.contract",
        "--arguments",
        '{"ok": true}',
        "--expected-result",
        '{"ok": true}',
        actor="developer",
    )
    world.item_id = int(rows[0]["id"])


@given("the validation hook is installed and allowlisted")
def install_hook(world: World) -> None:
    package = world.root / ".backlog" / "hooks"
    package.mkdir(exist_ok=True)
    (package / "__init__.py").write_text(
        textwrap.dedent(
            """
            from backlog_cli.api import ValidationHookResult

            def validate(backlog, context, arguments):
                return ValidationHookResult(arguments, "matched")

            validation_hooks = {"checks.contract": validate}
            """
        ).lstrip(),
        encoding="utf-8",
    )
    (world.root / ".backlog" / "execution.yaml").write_text(
        'allowed_hooks: ["checks.contract"]\nmax_timeout_seconds: 120\n',
        encoding="utf-8",
    )


@given("the task is in review")
def task_in_review(world: World) -> None:
    for action, actor in (
        ("refinement.accepted", "reviewer"),
        ("work.started", "developer"),
        ("work.completed", "developer"),
    ):
        world.run("action", world.require_key(), action, actor=actor)


@when("the executable item is run")
def run_item(world: World) -> None:
    world.run(
        "validation",
        "run",
        str(world.item_id),
        "--project-root",
        str(world.root),
        actor="validator",
        expected=None,
    )


@then(parsers.parse('the validation status is "{status}"'))
def validation_status(world: World, status: str) -> None:
    assert world.last_json["status"] == status


@then(parsers.parse('validation history records "{status}"'))
def validation_history(world: World, status: str) -> None:
    rows = world.run(
        "validation",
        "history",
        str(world.item_id),
        "--project-root",
        str(world.root),
    )
    assert rows[0]["status"] == status


@then(parsers.parse('the validation diagnostic contains "{reason}"'))
def validation_diagnostic(world: World, reason: str) -> None:
    assert reason in world.last_json["diagnostic"]

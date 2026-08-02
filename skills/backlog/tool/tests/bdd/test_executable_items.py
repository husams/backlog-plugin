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


def _add_shell_item(world: World, content: str, source: str, *options: str) -> int:
    command = f"{shlex.quote(sys.executable)} -c {shlex.quote(source)}"
    rows = world.run(
        "item",
        "add",
        world.require_key(),
        "--kind",
        "checklist",
        "--content",
        content,
        "--shell",
        command,
        *options,
        actor="developer",
    )
    return int(rows[0]["id"])


@when("shell execution edge cases are exercised")
def shell_execution_edge_cases(world: World) -> None:
    policy = world.root / ".backlog" / "execution.yaml"
    policy.write_text(
        "shell_enabled: true\n"
        "allowed_environment_variables: [VALIDATION_TOKEN]\n"
        "max_timeout_seconds: 120\n"
        "max_output_bytes: 4096\n"
        "max_batch_seconds: 30\n",
        encoding="utf-8",
    )
    world.env["VALIDATION_TOKEN"] = "runtime-secret"

    mismatch = _add_shell_item(
        world,
        "Matcher mismatch",
        "print('actual')",
        "--stdout-equals",
        "expected",
    )
    timeout = _add_shell_item(
        world,
        "Timeout",
        "import time; time.sleep(2)",
        "--timeout",
        "1",
    )
    bounded = _add_shell_item(
        world,
        "Bounded output",
        "import sys; print('x'*10000); sys.stderr.write('y'*10000)",
        "--stdout-regex",
        "x+",
        "--stderr-contains",
        "yyy",
    )
    secret = _add_shell_item(
        world,
        "Environment redaction",
        "import os; print(os.environ['VALIDATION_TOKEN'])",
        "--env",
        "VALIDATION_TOKEN",
    )
    unavailable_rows = world.run(
        "item",
        "add",
        world.require_key(),
        "--kind",
        "checklist",
        "--content",
        "Unavailable command",
        "--shell",
        "command-that-cannot-exist-anywhere",
        actor="developer",
    )
    unavailable = int(unavailable_rows[0]["id"])

    expected = {
        mismatch: ("fail", "stdout_mismatch"),
        timeout: ("error", "timed_out"),
        unavailable: ("error", "command_unavailable"),
    }
    for item_id, (status, diagnostic) in expected.items():
        result = world.run(
            "validation",
            "run",
            str(item_id),
            "--project-root",
            str(world.root),
            actor="validator",
            expected=None,
        )
        assert result["status"] == status
        assert diagnostic in result["diagnostic"]

    captured = world.run(
        "validation",
        "run",
        str(bounded),
        "--project-root",
        str(world.root),
        actor="validator",
        expected=None,
    )
    assert captured["status"] == "fail"
    assert captured["output_truncated"] is True
    redacted = world.run(
        "validation",
        "run",
        str(secret),
        "--project-root",
        str(world.root),
        actor="validator",
    )
    assert redacted["status"] == "pass"
    assert redacted["stdout"] == "[REDACTED]\n"

    history = world.run(
        "validation",
        "history",
        str(mismatch),
        "--limit",
        "1",
        "--project-root",
        str(world.root),
    )
    assert len(history) == 1
    world.run("item", "check", str(mismatch), actor="developer", expected=None)
    world.run(
        "item",
        "check",
        str(mismatch),
        "--waive-validation",
        actor="developer",
        expected=None,
    )
    world.run(
        "item",
        "check",
        str(mismatch),
        "--waive-validation",
        "--reason",
        "External evidence was reviewed",
        actor="developer",
    )
    diagnostics = world.run("doctor")["diagnostics"]
    assert any("validation_waived" in entry for entry in diagnostics)
    world.run("item", "check", str(mismatch), "--undo", actor="developer")
    world.run(
        "validation",
        "history",
        str(mismatch),
        "--limit",
        "101",
        expected=None,
    )
    world.run(
        "validation",
        "run-all",
        world.require_key(),
        "--project-root",
        str(world.root),
        expected=None,
    )
    world.run(
        "validation",
        "run-all",
        world.require_key(),
        "--project-root",
        str(world.root),
        "--fail-fast",
        expected=None,
    )
    world.last_json = {"ok": True}


@when("hook execution edge cases are exercised")
def hook_execution_edge_cases(world: World) -> None:
    rows = world.run(
        "item",
        "add",
        world.require_key(),
        "--kind",
        "acceptance_criteria",
        "--content",
        "Hook edge cases",
        "--hook",
        "checks.edge",
        "--arguments",
        '{"value": true}',
        "--expected-result",
        '{"ok": true}',
        "--timeout",
        "1",
        actor="developer",
    )
    item_id = int(rows[0]["id"])
    policy = world.root / ".backlog" / "execution.yaml"

    skipped = world.run(
        "validation",
        "run",
        str(item_id),
        "--project-root",
        str(world.root),
        expected=None,
    )
    assert skipped["status"] == "skipped"

    policy.write_text(
        'allowed_hooks: ["checks.edge"]\nmax_timeout_seconds: 5\n',
        encoding="utf-8",
    )
    package = world.root / ".backlog" / "hooks"
    missing = world.run(
        "validation",
        "run",
        str(item_id),
        "--project-root",
        str(world.root),
        expected=None,
    )
    assert "trusted hooks package is absent" in missing["diagnostic"]
    world.run("item", "check", str(item_id), actor="developer", expected=None)

    package.mkdir(exist_ok=True)
    hook_file = package / "__init__.py"
    cases = (
        ("answer = 42\n", "validation_hooks"),
        ("validation_hooks = {}\n", "registered hook name was not found"),
        ("validation_hooks = {'checks.edge': 42}\n", "not callable"),
        (
            "def validate(backlog, context, arguments):\n"
            "    raise RuntimeError('private failure')\n"
            "validation_hooks = {'checks.edge': validate}\n",
            "raised an exception",
        ),
        (
            "def validate(backlog, context, arguments):\n"
            "    import time\n"
            "    time.sleep(2)\n"
            "validation_hooks = {'checks.edge': validate}\n",
            "exceeded its timeout",
        ),
        (
            "from backlog_cli.api import ValidationHookResult\n"
            "def validate(backlog, context, arguments):\n"
            "    return ValidationHookResult({'ok': False}, 'different')\n"
            "validation_hooks = {'checks.edge': validate}\n",
            "different",
        ),
    )
    for source, reason in cases:
        hook_file.write_text(source, encoding="utf-8")
        result = world.run(
            "validation",
            "run",
            str(item_id),
            "--project-root",
            str(world.root),
            actor="validator",
            expected=None,
        )
        assert reason in result["diagnostic"]
        assert "private failure" not in result["diagnostic"]
    world.last_json = {"ok": True}


@then("executable edge cases are reported")
def executable_edge_cases_reported(world: World) -> None:
    assert world.last_json == {"ok": True}

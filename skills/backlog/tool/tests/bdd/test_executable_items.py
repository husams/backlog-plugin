from __future__ import annotations

import shlex
import subprocess
import sys
import textwrap

from pytest_bdd import given, parsers, scenarios, then, when

from backlog_cli import api, db

from .world import World


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
        "shell_enabled: true\n"
        f"allowed_commands: [{sys.executable}]\n"
        "max_timeout_seconds: 120\n"
        "max_output_bytes: 4096\n",
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


@when("the validation hook returns a mismatching result")
def install_mismatching_hook(world: World) -> None:
    package = world.root / ".backlog" / "hooks"
    (package / "__init__.py").write_text(
        textwrap.dedent(
            """
            from backlog_cli.api import ValidationHookResult

            def validate(backlog, context, arguments):
                return ValidationHookResult({"ok": False}, "mismatched")

            validation_hooks = {"checks.contract": validate}
            """
        ).lstrip(),
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
    world.verify_criteria()


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


@then(parsers.parse('the validation failure reason is "{reason}"'))
def validation_failure_reason(world: World, reason: str) -> None:
    assert world.last_json["reason"] == reason


@then(parsers.parse('the validation detail contains "{detail}"'))
def validation_detail(world: World, detail: str) -> None:
    assert detail in world.last_json["detail"]


@when("the Done gate is checked through the agent APIs")
def check_done_gate_through_agent_apis(world: World) -> None:
    cli_gate = world.run("gate", world.require_key(), "--for", "done", expected=2)
    cli_result = world.last_result
    backlog_dir = world.root / ".backlog"
    spec = db.StoreSpec(
        dialect="sqlite",
        scope="repo",
        project="bdd-project",
        artifacts_dir=backlog_dir / "artifacts",
        db_path=backlog_dir / "backlog.db",
        backlog_dir=backlog_dir,
    )
    conn = db.connect(spec=spec)
    try:
        project = db.require_project(conn, "bdd-project")
        backlog = api.Backlog(conn, project, spec, actor="agent")
        python_gate = backlog.can(world.require_key(), "done")
    finally:
        conn.close()
    world.last_result = cli_result
    world.last_json = {
        "cli": cli_gate["checks"],
        "python": python_gate.failures,
    }


@then("both APIs identify the failing executable acceptance criterion")
def agent_apis_report_failed_ac(world: World) -> None:
    item = f"#{world.item_id}"
    cli_failure = next(
        check
        for check in world.last_json["cli"]
        if check["check"] == "required_validations_pass"
    )
    assert cli_failure["ok"] is False
    assert item in cli_failure["detail"]
    python_failure = next(
        failure
        for failure in world.last_json["python"]
        if failure.startswith("required_validations_pass:")
    )
    assert item in python_failure


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
        "allowed_environment_variables: [VALIDATION_TOKEN, MISSING_VALIDATION_TOKEN]\n"
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
    invalid_command_rows = world.run(
        "item",
        "add",
        world.require_key(),
        "--kind",
        "checklist",
        "--content",
        "Invalid shell syntax",
        "--shell",
        "'",
        actor="developer",
    )
    invalid_command = int(invalid_command_rows[0]["id"])
    missing_directory = _add_shell_item(
        world,
        "Missing working directory",
        "print('never runs')",
        "--working-directory",
        "missing-directory",
    )
    outside_directory = world.root.parent / f"{world.root.name}-outside"
    outside_directory.mkdir()
    (world.root / "outside-link").symlink_to(
        outside_directory, target_is_directory=True
    )
    outside_working_directory = _add_shell_item(
        world,
        "Symlink outside project",
        "print('never runs')",
        "--working-directory",
        "outside-link",
    )
    missing_environment = _add_shell_item(
        world,
        "Missing environment variable",
        "print('never runs')",
        "--env",
        "MISSING_VALIDATION_TOKEN",
    )
    broken_program = world.root / "broken-executable"
    broken_program.write_text("not an executable format\n", encoding="utf-8")
    broken_program.chmod(0o755)
    broken_rows = world.run(
        "item",
        "add",
        world.require_key(),
        "--kind",
        "checklist",
        "--content",
        "Process start failure",
        "--shell",
        str(broken_program),
        actor="developer",
    )
    broken = int(broken_rows[0]["id"])

    expected = {
        mismatch: ("fail", "stdout_mismatch"),
        timeout: ("error", "timed_out"),
        unavailable: ("error", "command_unavailable"),
        invalid_command: ("error", "invalid_command"),
        missing_directory: ("error", "working_directory_unavailable"),
        outside_working_directory: ("error", "working_directory_outside_project"),
        missing_environment: ("error", "environment_variable_unavailable"),
        broken: ("error", "process_start_failed"),
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
    world.run("item", "check", str(secret), actor="developer")

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
    world.run(
        "validation",
        "waive",
        str(mismatch),
        "--reason",
        "Independent validation evidence",
        actor="validator",
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
    policy.write_text(
        "shell_enabled: true\n"
        "max_timeout_seconds: 120\n"
        "max_output_bytes: 4096\n"
        "max_batch_seconds: 1\n",
        encoding="utf-8",
    )
    budgeted_batch = world.run(
        "validation",
        "run-all",
        world.require_key(),
        "--project-root",
        str(world.root),
        expected=None,
    )
    assert budgeted_batch[0]["status"] == "skipped"
    assert "batch_budget_exhausted" in budgeted_batch[0]["diagnostic"]
    policy.write_text(
        "shell_enabled: true\n"
        "allowed_environment_variables: [VALIDATION_TOKEN, MISSING_VALIDATION_TOKEN]\n"
        "max_timeout_seconds: 120\n"
        "max_output_bytes: 4096\n"
        "max_batch_seconds: 600\n",
        encoding="utf-8",
    )
    world.run(
        "validation",
        "run-all",
        world.require_key(),
        "--project-root",
        str(world.root),
        expected=None,
    )
    failed_batch = world.run(
        "validation",
        "run-all",
        world.require_key(),
        "--project-root",
        str(world.root),
        "--fail-fast",
        expected=None,
    )
    assert len(failed_batch) == 1
    assert failed_batch[0]["status"] in {"fail", "error"}

    policy_cases = (
        ("- not-a-mapping\n", "must contain a mapping"),
        ("unknown_policy_field: true\n", "unknown execution policy fields"),
        ("shell_enabled: enabled\n", "must be true or false"),
        ("allowed_commands: true\n", "allowlists must be lists of strings"),
        ("max_output_bytes: 0\n", "max_output_bytes must be positive"),
        ("max_timeout_seconds: 0\n", "timeout_seconds must be a positive integer"),
        (
            "allowed_working_directories: [../outside]\n",
            "working directory must stay within the project",
        ),
    )
    for source, message in policy_cases:
        policy.write_text(source, encoding="utf-8")
        world.run(
            "validation",
            "run",
            str(mismatch),
            "--project-root",
            str(world.root),
            expected=None,
        )
        assert message in world.output()

    policy.unlink()
    legacy_policy = world.root / ".backlog" / "execution-policy.yaml"
    legacy_policy.write_text(
        "shell_enabled: true\nmax_timeout_seconds: 120\nmax_output_bytes: 4096\n",
        encoding="utf-8",
    )
    legacy_result = world.run(
        "validation",
        "run",
        str(mismatch),
        "--project-root",
        str(world.root),
        expected=None,
    )
    assert legacy_result["status"] == "fail"
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
        ("this is invalid python\n", "trusted hooks package could not be loaded"),
        ("answer = 42\n", "validation_hooks"),
        ("validation_hooks = {}\n", "registered hook name was not found"),
        ("validation_hooks = {'checks.edge': 42}\n", "not callable"),
        ("validation_hooks = {'checks.edge': len}\n", "identity is unavailable"),
        (
            "def validate(backlog, context, arguments):\n"
            "    raise RuntimeError('private failure')\n"
            "validation_hooks = {'checks.edge': validate}\n",
            "raised an exception",
        ),
        (
            "def validate(backlog, context, arguments):\n"
            "    return True\n"
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
        (
            "from backlog_cli.api import ValidationHookResult\n"
            "class Validator:\n"
            "    __backlog_validation_version__ = 'bdd-v1'\n"
            "    def __call__(self, backlog, context, arguments):\n"
            "        return ValidationHookResult({'ok': True}, 'versioned')\n"
            "validation_hooks = {'checks.edge': Validator()}\n",
            "versioned",
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

    policy.write_text(
        'allowed_hooks: ["checks.edge"]\n'
        "max_timeout_seconds: 5\n"
        "max_batch_seconds: 1\n",
        encoding="utf-8",
    )
    batch = world.run(
        "validation",
        "run-all",
        world.require_key(),
        "--project-root",
        str(world.root),
        actor="validator",
        expected=None,
    )
    assert batch[0]["status"] == "skipped"
    assert "batch_budget_exhausted" in batch[0]["diagnostic"]
    diagnostics = world.run("doctor")["diagnostics"]
    assert any("validation_skipped" in entry for entry in diagnostics)
    world.last_json = {"ok": True}


@when("clean and dirty Git validation sources are exercised")
def git_source_identity(world: World) -> None:
    item_id = _add_shell_item(world, "Git source identity", "print('identified')")
    enable_shell(world)
    tracked = world.root / "tracked.txt"
    tracked.write_text("clean\n", encoding="utf-8")

    def git(*args: str) -> None:
        result = subprocess.run(
            ["git", *args], cwd=world.root, text=True, capture_output=True
        )
        assert result.returncode == 0, result.stderr or result.stdout

    git("init")
    git("add", ".")
    git(
        "-c",
        "user.name=BDD",
        "-c",
        "user.email=bdd@example.invalid",
        "commit",
        "-m",
        "clean validation source",
    )
    first = world.run(
        "validation",
        "run",
        str(item_id),
        "--project-root",
        str(world.root),
        actor="validator",
    )
    assert first["status"] == "pass"
    first_history = world.run(
        "validation",
        "history",
        str(item_id),
        "--project-root",
        str(world.root),
    )
    assert first_history[0]["source_revision"]
    assert first_history[0]["source_dirty_fingerprint"] is None
    assert first_history[0]["source_revision_unavailable"] == 0

    git("add", ".")
    git(
        "-c",
        "user.name=BDD",
        "-c",
        "user.email=bdd@example.invalid",
        "commit",
        "-m",
        "record clean validation",
    )
    tracked.write_text("dirty\n", encoding="utf-8")
    second = world.run(
        "validation",
        "run",
        str(item_id),
        "--project-root",
        str(world.root),
        actor="validator",
    )
    assert second["status"] == "pass"
    second_history = world.run(
        "validation",
        "history",
        str(item_id),
        "--project-root",
        str(world.root),
    )
    assert second_history[0]["source_revision"]
    assert second_history[0]["source_dirty_fingerprint"].startswith("sha256:")
    assert second_history[0]["source_revision_unavailable"] == 0
    tracked.unlink()
    stale_history = world.run(
        "validation",
        "history",
        str(item_id),
        "--project-root",
        str(world.root),
    )
    assert stale_history[0]["stale"] is True
    world.run("item", "check", str(item_id), actor="developer", expected=None)
    assert "pending validation" in world.output()
    world.last_json = {"ok": True}


@then("executable edge cases are reported")
def executable_edge_cases_reported(world: World) -> None:
    assert world.last_json == {"ok": True}

from __future__ import annotations

import json
import subprocess
import sys

from pytest_bdd import scenarios, then, when

from .conftest import World


scenarios("features/administration.feature")


@when("all project, template, and workflow operations are exercised")
def exercise_project_configuration(world: World) -> None:
    world.run("where")
    world.run("projects")
    world.run("project", "list")
    world.run(
        "project",
        "add",
        "--name",
        "Secondary project",
        "--slug",
        "secondary",
        "--description",
        "Created by the E2E suite",
    )
    world.run(
        "project",
        "set",
        "secondary",
        "--name",
        "Renamed project",
        "--description",
        "Updated by the E2E suite",
        "--status",
        "archived",
    )

    world.run("templates")
    world.run("template", "list")
    world.run("template", "show", "software-delivery", "--type", "story")
    world.run(
        "template",
        "add",
        "--slug",
        "bdd-template",
        "--name",
        "BDD template",
        "--description",
        "Template exercised end to end",
        "--copy-of",
        "software-delivery",
    )
    world.run(
        "template",
        "status-add",
        "bdd-template",
        "--type",
        "story",
        "--status",
        "verification",
        "--display",
        "Verification",
        "--category",
        "review",
        "--after",
        "in_review",
    )
    world.run(
        "template",
        "move-add",
        "bdd-template",
        "--type",
        "story",
        "--from",
        "in_review",
        "--to",
        "verification",
        "--gate",
        "review_threads_closed",
    )
    world.run("template", "default", "bdd-template")
    world.run(
        "template",
        "add",
        "--slug",
        "project-template",
        "--from-project",
        "bdd-project",
    )
    world.run(
        "project",
        "add",
        "--name",
        "Templated project",
        "--slug",
        "templated",
        "--template",
        "bdd-template",
    )

    world.run("workflow", "show", "--type", "story")
    world.run("workflow", "gates")
    world.run(
        "workflow",
        "status-add",
        "--type",
        "story",
        "--slug",
        "security_review",
        "--display",
        "Security Review",
        "--category",
        "review",
        "--after",
        "in_review",
        "--description",
        "Security verification",
    )
    world.run(
        "workflow",
        "move-add",
        "--type",
        "story",
        "--from",
        "in_review",
        "--to",
        "security_review",
        "--gate",
        "review_threads_closed",
        "--note",
        "Security-sensitive work",
    )
    world.run(
        "workflow",
        "move-rm",
        "--type",
        "story",
        "--from",
        "in_review",
        "--to",
        "security_review",
    )
    world.run(
        "workflow", "status-rm", "--type", "story", "--slug", "security_review"
    )
    world.run("workflow", "copy", "--from", "secondary", "--type", "story")
    world.run("workflow", "reset", "--type", "story")
    world.run("workflow", "apply", "--template", "software-delivery", "--type", "story")
    world.run("workflow", "upgrade")

    world.run("template", "default", "software-delivery")
    world.run("template", "rm", "project-template")
    world.last_json = {"ok": True}


@when("all dependency and artifact operations are exercised")
def exercise_dependencies_and_artifacts(world: World) -> None:
    first = world.run(
        "story", "add", "--title", "Dependency source", actor="creator"
    )
    second = world.run(
        "story", "add", "--title", "Dependency target", actor="creator"
    )
    first_key, second_key = first["key"], second["key"]

    world.run(
        "dep", "add", first_key, "--blocks", second_key, "--note", "Must finish first"
    )
    world.run("dep", "add", first_key, "--blocks", second_key)
    world.run("dep", "list")
    world.run("dep", "list", second_key, "--kind", "blocks")
    world.run("dep", "check", second_key, expected=2)
    world.run("dep", "graph", "--format", "json")
    world.run("dep", "graph", "--format", "dot", json_output=False)
    world.run("dep", "rm", first_key, "--blocks", second_key)
    world.run("dep", "check", second_key)

    world.run("dep", "add", first_key, "--relates", second_key)
    world.run("dep", "rm", first_key, "--relates", second_key)
    world.run("dep", "add", first_key, "--duplicates", second_key)
    world.run("dep", "rm", first_key, "--duplicates", second_key)
    world.run("dep", "add", second_key, "--blocked-by", first_key)
    world.run("dep", "rm", second_key, "--blocked-by", first_key)

    document = world.root / "evidence.txt"
    document.write_text("BDD evidence\n", encoding="utf-8")
    world.run(
        "artifact",
        "add",
        first_key,
        str(document),
        "--title",
        "Test evidence",
        "--kind",
        "spec",
        actor="developer",
    )
    folder = world.root / "evidence-dir"
    folder.mkdir()
    (folder / "result.txt").write_text("passed\n", encoding="utf-8")
    world.run("artifact", "add", first_key, str(folder), "--kind", "report")
    world.run("artifact", "list", first_key)
    world.last_json = {"ok": True}


@when("all store inspection and transfer operations are exercised")
def exercise_store_operations(world: World) -> None:
    row = world.run(
        "bug", "add", "--title", "Transfer regression", actor="creator"
    )
    world.current_key = row["key"]
    world.run("statuses")
    world.run("board", "--all")
    world.run("next")
    world.run("actions", row["key"])
    world.run("history", row["key"])
    world.run("doctor")

    export_path = world.root / "backlog-export.json"
    world.run("export", "--out", str(export_path))
    exported = json.loads(export_path.read_text(encoding="utf-8"))
    assert "tables" in exported
    world.run("import", str(export_path), "--replace")
    world.run("doctor")
    world.last_json = {"ok": True}


@when("all task authoring and planning operations are exercised")
def exercise_task_authoring_and_planning(world: World) -> None:
    feature = world.run(
        "feature",
        "add",
        "--title",
        "CLI feature",
        "--description",
        "Complete authoring coverage",
        "--ac",
        "Feature criterion",
        "--priority",
        "P1",
        "--owner",
        "product",
        actor="creator",
    )
    story = world.run(
        "story",
        "add",
        "--feature",
        feature["key"],
        "--title",
        "CLI story",
        "--description",
        "Created through the concrete command line",
        "--ac",
        "Command succeeds",
        "--priority",
        "P2",
        "--owner",
        "product",
        "--assignee",
        "developer",
        "--reviewer",
        "reviewer",
        "--branch",
        "cli-story",
        actor="creator",
    )
    bug = world.run(
        "bug",
        "add",
        "--title",
        "CLI bug",
        "--assignee",
        "developer",
        actor="creator",
    )
    iteration = world.run(
        "iteration", "add", "--title", "CLI iteration", actor="facilitator"
    )
    subtask = world.run(
        "subtask",
        "add",
        "--story",
        story["key"],
        "--title",
        "CLI subtask",
        actor="creator",
    )
    generic = world.run(
        "task",
        "add",
        "--type",
        "bug",
        "--title",
        "Generic CLI bug",
        actor="creator",
    )

    for command in ("task", "feature", "story", "bug", "subtask"):
        world.run(command, "list")
    world.run("list", "--type", "story")
    world.run("list", "--status", "created")
    world.run("list", "--open")
    world.run("list", "--assignee", "developer")
    world.run("list", "--reviewer", "reviewer")
    world.run("list", "--parent", feature["key"])

    world.run(
        "set",
        story["key"],
        "--title",
        "Updated CLI story",
        "--description",
        "Every editable field is supplied",
        "--priority",
        "P0",
        "--owner",
        "delivery",
        "--branch",
        "updated-cli-story",
        "--parent",
        feature["key"],
        "--ac",
        "Executable criterion",
        "--shell",
        "printf expected",
        "--requirement",
        "advisory",
        "--timeout",
        "5",
        "--working-directory",
        ".",
        "--expected-exit-code",
        "0",
        "--stdout-equals",
        "expected",
        "--env",
        "PATH",
        actor="creator",
    )
    world.run(
        "assign",
        story["key"],
        "--to",
        "developer",
        "--reviewer",
        "reviewer",
        "--to-kind",
        "agent",
        "--reviewer-kind",
        "human",
        actor="coordinator",
    )

    notes = world.run(
        "item",
        "add",
        story["key"],
        "--kind",
        "note",
        "--content",
        "First note\nSecond note",
        actor="developer",
    )
    world.run("item", "list", story["key"])
    world.run("item", "list", story["key"], "--kind", "note")
    world.run(
        "item",
        "set",
        story["key"],
        "--kind",
        "checklist",
        "--content",
        "Waived real check",
        actor="developer",
    )
    checklist = world.run("item", "list", story["key"], "--kind", "checklist")
    item_id = str(checklist[0]["id"])
    world.run(
        "item",
        "check",
        item_id,
        "--waive-validation",
        "--reason",
        "Manual E2E verification",
        actor="developer",
    )
    world.run("item", "check", item_id, "--undo", actor="developer")
    world.run("item", "rm", str(notes[0]["id"]), actor="developer")
    world.run(
        "item",
        "set",
        story["key"],
        "--kind",
        "note",
        "--content",
        "",
        actor="developer",
    )

    world.run("actions", story["key"])
    world.run("gate", story["key"], "--for", "start")
    world.run(
        "gate",
        story["key"],
        "--for",
        "start",
        "--no-pr",
        "--allow-open-subtasks",
        "--allow-blocked",
        expected=0,
    )
    world.run(
        "action",
        story["key"],
        "refinement.accepted",
        "--parameter",
        "scope=complete",
        actor="reviewer",
    )
    world.run("gate", story["key"], "--for", "start")
    world.run("board", "--iteration", iteration["key"], expected=None)
    world.run("next", "--iteration", iteration["key"], expected=None)
    world.run("action", iteration["key"], "iteration.opened", actor="facilitator")
    world.run("iteration", "member-add", iteration["key"], story["key"])
    world.run("board", "--iteration", iteration["key"])
    world.run("next", "--iteration", iteration["key"])
    world.run("iteration", "member-remove", iteration["key"], story["key"])

    world.run(
        "pr",
        "set",
        bug["key"],
        "--url",
        "https://example.invalid/pull/2",
        "--number",
        "2",
        "--repo",
        "example/repo",
        "--state",
        "draft",
        "--review-state",
        "pending",
        actor="developer",
    )
    world.run("pr", "set", bug["key"], "--state", "open", actor="developer")
    world.run(
        "pr",
        "set",
        bug["key"],
        "--review-state",
        "changes_requested",
        actor="reviewer",
    )
    world.run(
        "pr", "set", bug["key"], "--review-state", "approved", actor="reviewer"
    )
    world.run("pr", "set", generic["key"], "--state", "closed", actor="developer")
    world.run("pr", "set", generic["key"], "--state", "none", actor="developer")

    invalid_commands = (
        ("action", story["key"], "work.started", "--parameter", "invalid"),
        ("action", story["key"], "work.started", "--parameter", "=value"),
        ("set", story["key"], "--ac", "one\ntwo", "--shell", "true"),
        ("item", "add", story["key"], "--kind", "note", "--content", "bad", "--shell", "true"),
        ("item", "add", story["key"], "--content", "one\ntwo", "--shell", "true"),
        ("item", "add", story["key"], "--content", "bad env", "--shell", "true", "--env", "A=B"),
        ("item", "add", story["key"], "--content", "bad match", "--shell", "true", "--stdout-equals", "x", "--stdout-regex", "x"),
        ("item", "add", story["key"], "--content", "bad hook", "--hook", "unknown", "--arguments", "{"),
        ("board", "--iteration", feature["key"]),
        ("next", "--iteration", feature["key"]),
    )
    for command in invalid_commands:
        world.run(*command, actor="developer", expected=None)

    assert subtask["parent_id"] is not None
    world.last_json = {"ok": True}


@when("repository and central store configurations are exercised")
def exercise_store_resolution(world: World) -> None:
    def invoke(env: dict[str, str], cwd, *args: str, expected: int = 0):
        result = subprocess.run(
            [sys.executable, "-m", "backlog_cli.cli", "--json", *args],
            cwd=cwd,
            env=env,
            text=True,
            capture_output=True,
        )
        assert result.returncode == expected, result.stderr or result.stdout
        return json.loads(result.stdout) if result.stdout.strip() else None

    central_home = world.root / "central-store"
    central_env = {
        **world.env,
        "BACKLOG_DB": "sqlite",
        "BACK_LOG_URL": str(central_home),
        "BACKLOG_PROJECT": "central-project",
        "BACKLOG_ARTIFACTS": str(world.root / "central-artifacts"),
    }
    invoke(central_env, world.root, "init", ".")
    invoke(central_env, world.root, "init", ".")
    location = invoke(central_env, world.root, "where")
    assert location["scope"] == "central"
    assert (central_home / "backlog.db").is_file()
    invoke(central_env, world.root, "projects")

    explicit_db = world.root / "explicit.db"
    explicit_env = {
        **world.env,
        "BACKLOG_DB": "sqlite",
        "BACK_LOG_URL": f"sqlite://{explicit_db}",
        "BACKLOG_PROJECT": "explicit-project",
    }
    invoke(explicit_env, world.root, "init", ".")
    assert invoke(explicit_env, world.root, "where")["scope"] == "central"

    legacy_db = world.root / "legacy-selector.db"
    legacy_env = {
        **world.env,
        "BACKLOG_DB": str(legacy_db),
        "BACK_LOG_URL": "",
        "BACKLOG_PROJECT": "legacy-selector",
    }
    invoke(legacy_env, world.root, "init", ".")
    assert invoke(legacy_env, world.root, "where")["backend"] == "sqlite"

    missing_env = {
        **world.env,
        "BACKLOG_DB": "sqlite",
        "BACK_LOG_URL": str(world.root / "missing.db"),
    }
    invoke(missing_env, world.root, "projects", expected=1)

    empty = world.root.parent / f"{world.root.name}-without-store"
    empty.mkdir()
    no_store_env = {
        key: value
        for key, value in world.env.items()
        if key not in {"BACKLOG_DB", "BACK_LOG_URL", "BACKLOG_DIR"}
    }
    invoke(no_store_env, empty, "where", expected=1)

    invalid_environments = (
        {**world.env, "BACKLOG_DB": "postgres", "BACK_LOG_URL": ""},
        {
            **world.env,
            "BACKLOG_DB": "postgres",
            "BACK_LOG_URL": "https://example.invalid/backlog",
        },
        {
            **world.env,
            "BACKLOG_DB": "sqlite",
            "BACK_LOG_URL": "postgresql://example.invalid/backlog",
        },
    )
    for env in invalid_environments:
        invoke(env, world.root, "where", expected=1)
    world.last_json = {"ok": True}


@when("the public Python API session is exercised")
def exercise_public_api(world: World) -> None:
    source = """
import json
from backlog_cli import api
from backlog_cli.api import (
    Action,
    BacklogError,
    ExecutionPolicy,
    RetrospectiveStatus,
    ReviewSeverity,
)

with api.open(actor="bdd-agent") as backlog:
    assert backlog.pid > 0
    assert backlog.store.project == "bdd-project"
    assert str(backlog.artifacts_dir).endswith("/.backlog")
    assert "bdd-project" in backlog.projects()
    assert "project=bdd-project" in str(backlog.store)

    feature = backlog.create_feature(
        "API feature",
        branch="ignored-for-features",
        description="Feature created through the public API",
        acceptance_criteria=["Feature criterion"],
    )
    story = backlog.create_story(
        "API story",
        feature=feature.key,
        priority="P1",
        owner="product",
        assignee="developer",
        reviewer="reviewer",
        branch="S-API",
        acceptance_criteria=[
            "Plain criterion",
            {
                "content": "Executable criterion",
                "execution": {
                    "executor": "shell",
                    "shell": {"command": "true", "expected_exit_code": 0},
                },
            },
        ],
    )
    bug = backlog.create_bug("API bug", assignee="developer")
    iteration = backlog.create_iteration("API iteration", branch="ignored")
    generic = backlog.create_task("story", "Generic API task")
    subtask = backlog.create_task("subtask", "API subtask", parent=story.key)

    assert backlog.task(feature.key).children[0].key == story.key
    assert backlog.task(story.key).parent == feature.key
    assert backlog.find("missing") is None
    assert backlog.find(generic.key).key == generic.key
    assert backlog.task_type_counts()["story"] >= 2
    assert backlog.counts()["created"] >= 1
    assert "created" in backlog.statuses("story")
    try:
        backlog.statuses("stories")
    except BacklogError:
        pass
    else:
        raise AssertionError("unknown task types must fail")
    flow = backlog.flow("story")
    assert flow.allows("created", "ready")
    assert flow.next_from("created")
    assert flow.display("created") == "Created"

    backlog.assign(story.key, to="developer", reviewer="reviewer")
    ready = backlog.trigger(
        story.key, Action.REFINEMENT_ACCEPTED, actor="independent-reviewer"
    )
    assert ready.status == "ready"
    opened = backlog.trigger(
        iteration.key, Action.ITERATION_OPENED, actor="facilitator"
    )
    assert opened.status == "open"
    backlog.add_iteration_member(iteration.key, story.key)
    assert backlog.task(iteration.key).iteration_members[0].key == story.key
    assert backlog.task(story.key).iterations[0].key == iteration.key
    assert backlog.startable("developer", iteration.key)[0].key == story.key
    assert all(task.task_type != "iteration" for task in backlog.startable())

    note = backlog.add_item(story.key, "notes", "API note")
    assert note["content"] == "API note"
    checklist = backlog.add_item(
        story.key,
        "checklist",
        "API executable checklist",
        execution_spec={
            "executor": "shell",
            "shell": {"command": "true", "expected_exit_code": 0},
        },
    )
    assert checklist["executor"] == "shell"
    policy = ExecutionPolicy(shell_enabled=True, max_output_bytes=4096)
    result = backlog.run_item(checklist["id"], ".", policy=policy)
    assert result.status == "pass"
    assert backlog.execution_history(checklist["id"], limit=1)
    assert backlog.run_task(story.key, ".", policy=policy)
    assert backlog.execution_policy(".").shell_enabled is False
    assert backlog.source_identity(".").unavailable
    backlog.set_item_execution(
        checklist["id"],
        {
            "executor": "shell",
            "shell": {"command": "true", "expected_exit_code": 0},
        },
    )
    replaced = backlog.set_items(
        story.key,
        "notes",
        ["First note", {"content": "Second note"}],
    )
    assert len(replaced) == 2
    assert backlog.task(story.key).items("notes") == ["First note", "Second note"]
    assert backlog.task(story.key).item_details()

    assert backlog.tasks(status="ready", task_type="story", assignee="developer")
    assert backlog.tasks(reviewer="reviewer", parent=feature.key, open_only=True)
    assert backlog.actions(story.key)
    assert backlog.can(story.key, "start").ok
    assert backlog.blocked() == []
    assert backlog.cycles() == []
    assert backlog.dependencies(story.key) == []
    assert backlog.artifacts(story.key) == []

    updated = backlog.set_pr(
        story.key,
        url="https://example.invalid/pull/1",
        number=1,
        repo="example/repo",
        state="open",
        review_state="pending",
    )
    assert updated.pr_state == "open"

    task = backlog.task(story.key)
    assert task["key"] == story.key
    assert "API story" in str(task)
    assert story.key in repr(task)
    assert task.age_days >= 0
    assert task.idle_days >= 0
    assert task.is_open
    assert task.blockers == []
    assert task.open_threads == []
    assert subtask.parent == story.key
    assert bug.iteration_members == []
    try:
        task.not_a_column
    except AttributeError:
        pass
    else:
        raise AssertionError("missing task attributes must fail")

    for invalid in (
        [123],
        [{"content": "", "unknown": True}],
        [{"content": ""}],
    ):
        try:
            backlog.set_items(story.key, "notes", invalid)
        except (TypeError, BacklogError):
            pass
        else:
            raise AssertionError("invalid API items must fail")
    try:
        backlog.add_item(
            story.key,
            "notes",
            "invalid executable note",
            execution_spec={
                "executor": "shell",
                "shell": {"command": "true", "expected_exit_code": 0},
            },
        )
    except BacklogError:
        pass
    else:
        raise AssertionError("notes cannot be executable")
    try:
        backlog.trigger(story.key, "work.started")
    except TypeError:
        pass
    else:
        raise AssertionError("trigger requires an Action")
    try:
        backlog.trigger(story.key, Action.FEEDBACK_POSTED)
    except BacklogError:
        pass
    else:
        raise AssertionError("thread actions require the review API")

    backlog.remove_iteration_member(iteration.key, story.key)
    assert backlog.task(iteration.key).iteration_members == []
    backlog.commit()
    project_name = backlog.store.project
    story_key = story.key
    feature_key = feature.key
    iteration_key = iteration.key

with api.open(actor="reviewer") as backlog:
    thread = backlog.review_open(
        story_key,
        author="reviewer",
            body="Review root\\nadditional detail",
        title="BDD API review",
        file="src/example.py",
        line=12,
        severity=ReviewSeverity.BLOCKER,
    )
    assert thread.task_key == story_key
    assert thread.where == "src/example.py:12"
    assert "Review root" in str(thread)
    assert backlog.inbox(actor="developer", role="developer")
    assert backlog.threads(story_key, severity=ReviewSeverity.BLOCKER)
    assert backlog.review_updates(thread.root_key)
    assert backlog.review_audit(thread.root_key)["root"] == thread.root_key
    changed = backlog.review_set_severity(
        thread.root_key, severity=ReviewSeverity.INFO, author="reviewer"
    )
    assert changed.severity is ReviewSeverity.INFO
    try:
        backlog.review_open(
            story_key,
            author="someone-else",
            body="wrong actor",
        )
    except BacklogError:
        pass
    else:
        raise AssertionError("review authors must match the session")
    for invalid_call in (
        lambda: backlog.inbox(severity="blocker"),
        lambda: backlog.threads(story_key, severity="blocker"),
        lambda: backlog.review_open(
            story_key,
            author="reviewer",
            body="bad severity",
            severity="blocker",
        ),
        lambda: backlog.review_set_severity(
            thread.root_key, severity="info", author="reviewer"
        ),
    ):
        try:
            invalid_call()
        except TypeError:
            pass
        else:
            raise AssertionError("review severity must be typed")
    root_key = thread.root_key
    reply_to = thread.reply_to

with api.open(actor="developer") as backlog:
    fixed = backlog.review_reply(
        reply_to,
        author="developer",
        action="fix",
        body="Implemented",
    )
    fixed_comment = fixed.reply_to

with api.open(actor="reviewer") as backlog:
    accepted = backlog.review_reply(
        fixed_comment,
        author="reviewer",
        action="accept",
        body="Verified",
    )
    assert accepted.state == "closed"
    reopened = backlog.review_reopen(
        root_key,
        author="reviewer",
        body="Regression found",
    )
    assert reopened.state == "awaiting_developer"
    updates = backlog.review_updates(root_key, after=fixed_comment)
    assert updates
    update = updates[-1]
    assert update.root_key == root_key
    assert update.reviewer == "reviewer"

with api.open(actor="facilitator") as backlog:
    action = backlog.create_retrospective_action(
        iteration=iteration_key,
        repeated_issue="Repeated API issue",
        proposed_solution="Exercise the real API",
        title="API retrospective",
    )
    assert action.status == "created"
    assert action.required_decision
    assert action.is_open
    assert action.age_days >= 0
    assert action.idle_days >= 0
    assert action.key in repr(action)
    assert action.key in str(action)
    assert backlog.retrospective_action(action.key)["key"] == action.key
    assert backlog.retrospective_actions(
        status=RetrospectiveStatus.CREATED, iteration=iteration_key
    )
    try:
        backlog.retrospective_actions(status="created")
    except TypeError:
        pass
    else:
        raise AssertionError("retrospective status must be typed")
    retrospective_key = action.key
    rejected = backlog.create_retrospective_action(
        iteration=iteration_key,
        repeated_issue="Rejected issue",
        proposed_solution="Rejected solution",
    )
    rejected_key = rejected.key

with api.open(actor="product-manager") as backlog:
    ready_action = backlog.accept_retrospective_action(retrospective_key)
    assert ready_action.status == "ready"
    closed_action = backlog.close_retrospective_action(
        retrospective_key,
        resolution_project="bdd-project",
        feature=feature_key,
    )
    assert closed_action.status == "done"
    rejected_action = backlog.reject_retrospective_action(
        rejected_key, reason="Not actionable"
    )
    assert rejected_action.status == "rejected"
    try:
        backlog.close_retrospective_action(
            retrospective_key,
            resolution_project="bdd-project",
        )
    except BacklogError:
        pass
    else:
        raise AssertionError("a resolution reference is required")

print(json.dumps({"project": project_name}))
"""
    result = subprocess.run(
        [sys.executable, "-c", source],
        cwd=world.root,
        env=world.env,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    world.last_result = result
    world.last_json = json.loads(result.stdout)


@then("the administrative commands succeed")
def administrative_commands_succeed(world: World) -> None:
    assert world.last_json == {"ok": True}


@then("the public API reports the active project")
def public_api_reports_project(world: World) -> None:
    assert world.last_json == {"project": "bdd-project"}

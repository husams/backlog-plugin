from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import uuid

from pytest_bdd import scenarios, then, when

from .world import World


scenarios("features/administration.feature")


@when("all project, template, and workflow operations are exercised")
def exercise_project_configuration(world: World) -> None:
    def rejected(*args: str, message: str) -> None:
        world.run(*args, expected=None)
        assert world.last_result is not None
        assert world.last_result.returncode != 0
        assert message in world.output().lower()

    world.run("where")
    world.run("init", ".")
    world.run("projects")
    assert "(none)" in world.run("list", json_output=False)
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
    rejected("project", "set", "secondary", message="nothing to set")

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
        "add",
        "--slug",
        "default-copy",
        "--name",
        "Default workflow copy",
    )
    world.run("template", "show", "default-copy")
    rejected(
        "template",
        "add",
        "--slug",
        "default-copy",
        message="already exists",
    )
    rejected("template", "show", "missing-template", message="no template")
    world.run("template", "rm", "default-copy")
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
        "status-add",
        "bdd-template",
        "--type",
        "story",
        "--status",
        "queued_for_security",
        "--display",
        "Queued for Security",
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
    rejected("template", "rm", "bdd-template", message="default template")
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

    rejected(
        "template",
        "status-add",
        "bdd-template",
        "--type",
        "story",
        "--status",
        "verification",
        message="already has a status",
    )
    rejected(
        "template",
        "status-add",
        "bdd-template",
        "--type",
        "story",
        "--status",
        "misplaced",
        "--after",
        "missing-status",
        message="no status",
    )
    rejected(
        "template",
        "move-add",
        "bdd-template",
        "--type",
        "story",
        "--from",
        "created",
        "--to",
        "verification",
        "--gate",
        "unknown-gate",
        message="unknown gate",
    )

    world.run("workflow", "show", "--type", "story")
    world.run("workflow", "gates")
    world.run(
        "workflow",
        "status-add",
        "--type",
        "story",
        "--slug",
        "appended",
    )
    world.run("workflow", "status-rm", "--type", "story", "--slug", "appended")
    world.run(
        "workflow",
        "status-add",
        "--type",
        "story",
        "--slug",
        "qa",
        "--display",
        "Quality Assurance",
    )
    world.run(
        "workflow",
        "move-add",
        "--type",
        "story",
        "--from",
        "Quality Assurance",
        "--to",
        "created",
    )
    world.run("workflow", "status-rm", "--type", "story", "--slug", "qa")
    rejected(
        "workflow",
        "status-add",
        "--type",
        "story",
        "--slug",
        "created",
        message="already has a status",
    )
    rejected(
        "workflow",
        "status-add",
        "--type",
        "story",
        "--slug",
        "misplaced",
        "--after",
        "missing-status",
        message="unknown status",
    )
    rejected(
        "workflow",
        "move-add",
        "--type",
        "story",
        "--from",
        "created",
        "--to",
        "in_progress",
        "--gate",
        "unknown-gate",
        message="unknown gate",
    )
    rejected(
        "workflow",
        "move-rm",
        "--type",
        "story",
        "--from",
        "done",
        "--to",
        "created",
        message="no done -> created transition",
    )
    world.run("story", "add", "--title", "Workflow status user", actor="creator")
    rejected(
        "workflow",
        "status-rm",
        "--type",
        "story",
        "--slug",
        "created",
        message="move them first",
    )
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
        "Security Review",
    )
    world.run("workflow", "status-rm", "--type", "story", "--slug", "security_review")
    world.run("workflow", "copy", "--from", "secondary", "--type", "story")
    world.run("workflow", "reset", "--type", "story")
    world.run("workflow", "apply", "--template", "software-delivery", "--type", "story")
    world.run("workflow", "upgrade")

    world.run("template", "default", "software-delivery")
    rejected("template", "rm", "bdd-template", message="project(s) were created")
    world.run("template", "rm", "project-template")
    world.last_json = {"ok": True}


@when("all dependency and artifact operations are exercised")
def exercise_dependencies_and_artifacts(world: World) -> None:
    world.run("dep", "graph", "--format", "text", json_output=False)
    first = world.run("story", "add", "--title", "Dependency source", actor="creator")
    second = world.run("story", "add", "--title", "Dependency target", actor="creator")
    first_key, second_key = first["key"], second["key"]

    world.run(
        "dep", "add", first_key, "--blocks", second_key, "--note", "Must finish first"
    )
    world.run("dep", "add", first_key, "--blocks", second_key)
    world.run(
        "dep",
        "add",
        first_key,
        "--blocks",
        second_key,
        "--note",
        "Updated dependency note",
    )
    world.run("dep", "list")
    world.run("dep", "list", second_key, "--kind", "blocks")
    world.run("dep", "check", second_key, expected=2)
    world.run("dep", "graph", "--format", "json")
    world.run("dep", "graph", "--format", "dot", json_output=False)
    world.run("dep", "rm", first_key, "--blocks", second_key)
    world.run("dep", "check", second_key)

    world.run("dep", "add", first_key, "--relates", second_key)
    world.run("dep", "rm", first_key, "--relates", second_key)
    world.run("dep", "add", second_key, "--relates", first_key)
    world.run("dep", "rm", second_key, "--relates", first_key)
    world.run("dep", "add", first_key, "--duplicates", second_key)
    world.run("dep", "rm", first_key, "--duplicates", second_key)
    world.run("dep", "add", second_key, "--blocked-by", first_key)
    world.run("dep", "rm", second_key, "--blocked-by", first_key)

    third = world.run("bug", "add", "--title", "Cycle third", actor="creator")
    world.run("dep", "add", first_key, "--blocks", second_key)
    world.run("dep", "add", second_key, "--blocks", third["key"])
    world.run("dep", "graph", "--format", "dot", json_output=False)
    cycle = world.run("dep", "add", third["key"], "--blocks", first_key, expected=None)
    assert "cycle" in world.output().lower()
    world.run("dep", "add", first_key, "--blocks", first_key, expected=None)
    assert "itself" in world.output().lower()
    world.run("dep", "rm", third["key"], "--blocks", first_key, expected=None)
    world.run("dep", "add", first_key, expected=None)
    world.run("dep", "rm", first_key, expected=None)
    world.run("dep", "list", first_key)
    world.run("dep", "list", first_key, "--kind", "blocks")
    world.run("dep", "list", "--kind", "blocks")
    world.run("dep", "graph", "--format", "text", json_output=False)
    world.run("dep", "rm", first_key, "--blocks", second_key)
    world.run("dep", "rm", second_key, "--blocks", third["key"])

    left = world.run("bug", "add", "--title", "Diamond left", actor="creator")
    right = world.run("bug", "add", "--title", "Diamond right", actor="creator")
    join = world.run("bug", "add", "--title", "Diamond join", actor="creator")
    world.run("dep", "add", second_key, "--blocks", left["key"])
    world.run("dep", "add", second_key, "--blocks", right["key"])
    world.run("dep", "add", left["key"], "--blocks", join["key"])
    world.run("dep", "add", right["key"], "--blocks", join["key"])
    world.run("dep", "add", first_key, "--blocks", second_key)
    world.run("dep", "graph", "--format", "text", json_output=False)
    for source, target in (
        (first_key, second_key),
        (second_key, left["key"]),
        (second_key, right["key"]),
        (left["key"], join["key"]),
        (right["key"], join["key"]),
    ):
        world.run("dep", "rm", source, "--blocks", target)

    completed = world.run(
        "feature", "add", "--title", "Completed dependency", actor="creator"
    )
    for action, actor in (
        ("refinement.accepted", "reviewer"),
        ("work.started", "developer"),
        ("work.completed", "developer"),
        ("review.approved", "reviewer"),
        ("delivery.released", "release-manager"),
    ):
        world.run("action", completed["key"], action, actor=actor)
        if action == "review.approved":
            world.run("gate", completed["key"], "--for", "done")
    world.run("dep", "add", completed["key"], "--blocks", second_key)
    world.run("dep", "check", second_key)
    world.run("board", "--all")
    world.run("dep", "rm", completed["key"], "--blocks", second_key)

    document = world.root / "evidence.txt"
    document.write_text("BDD evidence\n", encoding="utf-8")
    copied = world.run(
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
    world.run(
        "artifact",
        "add",
        first_key,
        copied["abs_path"],
        "--title",
        "Test evidence",
        "--kind",
        "spec",
        actor="developer",
    )
    world.run(
        "artifact",
        "add",
        first_key,
        str(world.root / "missing-evidence.txt"),
        expected=None,
    )
    assert "artifact source not found" in world.output()
    folder = world.root / "evidence-dir"
    folder.mkdir()
    (folder / "result.txt").write_text("passed\n", encoding="utf-8")
    world.run("artifact", "add", first_key, str(folder), "--kind", "report")
    world.run("artifact", "list", first_key)
    world.last_json = {"ok": True}


@when("all store inspection and transfer operations are exercised")
def exercise_store_operations(world: World) -> None:
    row = world.run("bug", "add", "--title", "Transfer regression", actor="creator")
    iteration = world.run(
        "iteration", "add", "--title", "Transfer iteration", actor="creator"
    )
    story = world.run("story", "add", "--title", "Transfer story", actor="creator")
    world.run(
        "retrospective",
        "add",
        "--iteration",
        iteration["key"],
        "--issue",
        "An open import issue",
        "--solution",
        "Keep the action open",
        actor="facilitator",
    )
    closed_action = world.run(
        "retrospective",
        "add",
        "--iteration",
        iteration["key"],
        "--issue",
        "A resolved import issue",
        "--solution",
        "Track the resolution",
        actor="facilitator",
    )
    world.run("retrospective", "accept", closed_action["key"], actor="reviewer")
    world.run(
        "retrospective",
        "close",
        closed_action["key"],
        "--resolution-project",
        "bdd-project",
        "--bug",
        row["key"],
        actor="facilitator",
    )
    world.current_key = row["key"]
    world.run("statuses")
    world.run("board", "--all")
    world.run("next")
    world.run("actions", row["key"])
    world.run("history", row["key"])
    world.run("doctor")
    world.run("show", "S-999999", json_output=False, expected=None)
    assert world.last_result is not None
    assert world.last_result.returncode == 1
    assert "error:" in world.last_result.stderr

    export_path = world.root / "backlog-export.json"
    world.run("export", "--out", str(export_path))
    world.run("export", json_output=False)
    exported = json.loads(export_path.read_text(encoding="utf-8"))
    assert "tables" in exported
    world.run("import", str(export_path), expected=None)

    def rejected_import(name: str, payload: dict, message: str) -> None:
        path = world.root / f"invalid-{name}.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        world.run("import", str(path), "--replace", expected=None)
        assert message in world.output()

    wrong_format = json.loads(json.dumps(exported))
    wrong_format["format"] = "not-a-backlog-export"
    rejected_import("format", wrong_format, "not a backlog export")

    future = json.loads(json.dumps(exported))
    future["schema_version"] = int(exported["schema_version"]) + 1
    rejected_import("future", future, "newer than this tool")

    bug_row = next(
        task for task in exported["tables"]["task"] if task["key"] == row["key"]
    )
    project_id = bug_row["project_id"]
    iteration_row = next(
        task for task in exported["tables"]["task"] if task["key"] == iteration["key"]
    )
    story_row = next(
        task for task in exported["tables"]["task"] if task["key"] == story["key"]
    )

    orphan_subtask = json.loads(json.dumps(exported))
    invalid_task = {
        **bug_row,
        "id": 9001,
        "key": "T-900",
        "task_type": "subtask",
        "parent_id": None,
    }
    orphan_subtask["tables"]["task"].append(invalid_task)
    rejected_import("orphan-subtask", orphan_subtask, "requires a parent")

    missing_parent = json.loads(json.dumps(exported))
    missing_parent["tables"]["task"].append(
        {**invalid_task, "id": 9002, "key": "T-901", "parent_id": 999999}
    )
    rejected_import("missing-parent", missing_parent, "missing parent")

    wrong_parent = json.loads(json.dumps(exported))
    wrong_parent["tables"]["task"].append(
        {
            **story_row,
            "id": 9003,
            "key": "S-901",
            "parent_id": bug_row["id"],
        }
    )
    rejected_import("wrong-parent", wrong_parent, "cannot sit under")

    missing_workflow = json.loads(json.dumps(exported))
    missing_workflow["tables"]["workflow"] = [
        workflow
        for workflow in missing_workflow["tables"]["workflow"]
        if not (workflow["project_id"] == project_id and workflow["task_type"] == "bug")
    ]
    rejected_import(
        "missing-workflow", missing_workflow, "bug tasks require a bug workflow"
    )

    no_owner = json.loads(json.dumps(exported))
    no_owner["tables"]["retrospective_action"].append(
        {"key": "R-900", "project_id": 999999, "iteration_id": iteration_row["id"]}
    )
    rejected_import("retrospective-owner", no_owner, "has no owning project")

    wrong_iteration = json.loads(json.dumps(exported))
    wrong_iteration["tables"]["retrospective_action"].append(
        {"key": "R-901", "project_id": project_id, "iteration_id": bug_row["id"]}
    )
    rejected_import("retrospective-iteration", wrong_iteration, "requires an Iteration")

    wrong_resolution = json.loads(json.dumps(exported))
    wrong_resolution["tables"]["retrospective_action"].append(
        {
            "key": "R-902",
            "project_id": project_id,
            "iteration_id": iteration_row["id"],
            "resolution_project_id": project_id,
            "resolution_task_id": story_row["id"],
        }
    )
    rejected_import(
        "retrospective-resolution", wrong_resolution, "requires a Feature or Bug"
    )

    world.run("import", str(export_path), "--replace")
    world.run("doctor")

    cyclic = json.loads(json.dumps(exported))
    cyclic["tables"]["dependency"].extend(
        [
            {
                "id": 9001,
                "from_task_id": bug_row["id"],
                "to_task_id": story_row["id"],
                "kind": "blocks",
                "note": "imported cycle",
                "created_at": "2024-01-01T00:00:00Z",
                "created_by": "legacy-import",
            },
            {
                "id": 9002,
                "from_task_id": story_row["id"],
                "to_task_id": bug_row["id"],
                "kind": "blocks",
                "note": "imported cycle",
                "created_at": "2024-01-01T00:00:00Z",
                "created_by": "legacy-import",
            },
            {
                "id": 9003,
                "from_task_id": story_row["id"],
                "to_task_id": iteration_row["id"],
                "kind": "blocks",
                "note": "imported complex cycle",
                "created_at": "2024-01-01T00:00:00Z",
                "created_by": "legacy-import",
            },
            {
                "id": 9004,
                "from_task_id": iteration_row["id"],
                "to_task_id": bug_row["id"],
                "kind": "blocks",
                "note": "imported complex cycle",
                "created_at": "2024-01-01T00:00:00Z",
                "created_by": "legacy-import",
            },
            {
                "id": 9005,
                "from_task_id": bug_row["id"],
                "to_task_id": iteration_row["id"],
                "kind": "blocks",
                "note": "imported complex cycle",
                "created_at": "2024-01-01T00:00:00Z",
                "created_by": "legacy-import",
            },
            {
                "id": 9006,
                "from_task_id": iteration_row["id"],
                "to_task_id": story_row["id"],
                "kind": "blocks",
                "note": "imported complex cycle",
                "created_at": "2024-01-01T00:00:00Z",
                "created_by": "legacy-import",
            },
        ]
    )
    cyclic_path = world.root / "cyclic-backlog.json"
    cyclic_path.write_text(json.dumps(cyclic), encoding="utf-8")
    world.run("import", str(cyclic_path), "--replace")
    world.run("dep", "graph", json_output=False)
    assert "CYCLES" in world.output()
    world.run("doctor", expected=None)
    assert "dependency cycle" in world.output()

    database = world.root / ".backlog" / "backlog.db"
    conn = sqlite3.connect(database)
    try:
        conn.execute("PRAGMA ignore_check_constraints=ON")
        conn.execute("UPDATE task SET priority='INVALID' WHERE key=?", (row["key"],))
        conn.commit()
    finally:
        conn.close()
    world.run("doctor", expected=None)
    assert "integrity check failed" in world.output()
    conn = sqlite3.connect(database)
    try:
        conn.execute("PRAGMA ignore_check_constraints=ON")
        conn.execute("UPDATE task SET priority='P2' WHERE key=?", (row["key"],))
        conn.commit()
    finally:
        conn.close()
    world.last_json = {"ok": True}


@when("all task authoring and planning operations are exercised")
def exercise_task_authoring_and_planning(world: World) -> None:
    def rejected(*args: str, message: str) -> None:
        world.run(*args, expected=None)
        assert world.last_result is not None
        assert world.last_result.returncode != 0
        assert message in world.output().lower()

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
    world.run("assign", bug["key"], "--reviewer", "reviewer", actor="manager")
    world.run("gate", feature["key"], "done", expected=None)
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
    board_story = world.run(
        "story",
        "add",
        "--title",
        "Actor-filtered review",
        "--assignee",
        "developer",
        "--reviewer",
        "reviewer",
        actor="creator",
    )
    world.run("action", board_story["key"], "refinement.accepted", actor="reviewer")
    world.run("action", board_story["key"], "work.started", actor="developer")
    world.run(
        "action",
        board_story["key"],
        "work.completed",
        "--no-pr",
        actor="developer",
    )
    world.run("board", "--all", actor="another-reviewer")
    executable_story = world.run(
        "story",
        "add",
        "--title",
        "CLI executable story",
        "--ac",
        "Executable creation criterion",
        "--shell",
        "true",
        actor="creator",
    )
    world.run(
        "item",
        "set",
        executable_story["key"],
        "--kind",
        "checklist",
        "--content",
        "Executable replacement",
        "--shell",
        "true",
        actor="developer",
    )

    rejected(
        "task",
        "add",
        "--type",
        "subtask",
        "--title",
        "Orphan subtask",
        message="requires a parent",
    )
    rejected(
        "task",
        "add",
        "--type",
        "feature",
        "--parent",
        story["key"],
        "--title",
        "Nested feature",
        message="cannot sit under",
    )
    rejected(
        "story",
        "add",
        "--feature",
        bug["key"],
        "--title",
        "Wrong parent",
        message="cannot sit under",
    )
    rejected(
        "set",
        story["key"],
        "--parent",
        bug["key"],
        message="cannot sit under",
    )
    world.run("set", story["key"])
    world.run("set", story["key"], "--ac", "Non-executable criterion", actor="creator")
    rejected("assign", story["key"], message="nothing to assign")

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
    rejected("item", "check", "999999", message="no task item")
    rejected("item", "rm", "999999", message="no task item")
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
    rejected(
        "iteration",
        "member-add",
        story["key"],
        bug["key"],
        message="is not an iteration",
    )
    rejected(
        "iteration",
        "member-add",
        iteration["key"],
        story["key"],
        message="open iteration",
    )
    world.run("action", iteration["key"], "iteration.opened", actor="facilitator")
    rejected(
        "iteration",
        "member-add",
        iteration["key"],
        feature["key"],
        message="only ready stories",
    )
    rejected(
        "iteration",
        "member-add",
        iteration["key"],
        generic["key"],
        message="only ready stories",
    )
    world.run("iteration", "member-add", iteration["key"], story["key"])
    world.run("iteration", "member-add", iteration["key"], story["key"])
    world.run("show", iteration["key"], json_output=False)
    world.run("show", story["key"], json_output=False)
    other_iteration = world.run(
        "iteration", "add", "--title", "Other open iteration", actor="facilitator"
    )
    world.run("action", other_iteration["key"], "iteration.opened", actor="facilitator")
    rejected(
        "iteration",
        "member-add",
        other_iteration["key"],
        story["key"],
        message="already belongs",
    )
    rejected(
        "iteration",
        "member-remove",
        other_iteration["key"],
        story["key"],
        message="is not a member",
    )
    rejected(
        "iteration",
        "member-remove",
        story["key"],
        bug["key"],
        message="is not an iteration",
    )
    world.run("action", other_iteration["key"], "iteration.closed", actor="facilitator")
    rejected(
        "iteration",
        "member-remove",
        other_iteration["key"],
        story["key"],
        message="open iteration",
    )
    world.run("board", "--iteration", iteration["key"])
    world.run("next", "--iteration", iteration["key"])
    rejected(
        "action",
        iteration["key"],
        "iteration.closed",
        message="iteration_members_finished",
    )
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
    world.run("pr", "set", bug["key"], "--review-state", "approved", actor="reviewer")
    world.run("pr", "set", generic["key"], "--state", "closed", actor="developer")
    world.run("pr", "set", generic["key"], "--state", "open", actor="developer")
    world.run("pr", "set", generic["key"], "--state", "none", actor="developer")
    rejected("pr", "sync", generic["key"], message="has no pr number")
    number_only_pr = world.run(
        "bug", "add", "--title", "PR without repository", actor="creator"
    )
    world.run(
        "pr", "set", number_only_pr["key"], "--number", "999999", actor="developer"
    )
    rejected("pr", "sync", number_only_pr["key"], message="gh failed")

    no_gh_pr = world.run("bug", "add", "--title", "PR without gh", actor="creator")
    world.run("pr", "set", no_gh_pr["key"], "--number", "1", actor="developer")
    original_path = world.env.get("PATH")
    world.env["PATH"] = ""
    try:
        rejected("pr", "sync", no_gh_pr["key"], message="is not installed")
    finally:
        if original_path is None:
            world.env.pop("PATH", None)
        else:
            world.env["PATH"] = original_path

    live_pr = world.run("bug", "add", "--title", "Live draft PR", actor="creator")
    world.run(
        "pr",
        "set",
        live_pr["key"],
        "--url",
        "https://github.com/husams/backlog-plugin/pull/21",
        actor="developer",
    )
    synced_draft = world.run("pr", "sync", live_pr["key"], actor="github")
    assert synced_draft["pr_state"] in {"draft", "open", "merged"}
    expected_review = "none" if synced_draft["pr_state"] == "merged" else "pending"
    assert synced_draft["pr_review_state"] == expected_review

    merged_pr = world.run("bug", "add", "--title", "Live merged PR", actor="creator")
    world.run(
        "pr",
        "set",
        merged_pr["key"],
        "--url",
        "https://github.com/husams/backlog-plugin/pull/20",
        actor="developer",
    )
    synced_merged = world.run("pr", "sync", merged_pr["key"], actor="github")
    assert synced_merged["pr_state"] == "merged"
    assert synced_merged["pr_review_state"] == "none"

    missing_live_pr = world.run(
        "bug", "add", "--title", "Missing live PR", actor="creator"
    )
    world.run(
        "pr",
        "set",
        missing_live_pr["key"],
        "--number",
        "999999",
        "--repo",
        "husams/backlog-plugin",
        actor="developer",
    )
    rejected("pr", "sync", missing_live_pr["key"], message="gh failed")

    invalid_commands = (
        ("story", "add", "--title", "Missing executor", "--requirement", "required"),
        (
            "story",
            "add",
            "--title",
            "Multiple executable criteria",
            "--ac",
            "one\ntwo",
            "--shell",
            "true",
        ),
        ("action", story["key"], "work.started", "--parameter", "invalid"),
        ("action", story["key"], "work.started", "--parameter", "=value"),
        ("set", story["key"], "--ac", "one\ntwo", "--shell", "true"),
        (
            "item",
            "add",
            story["key"],
            "--kind",
            "note",
            "--content",
            "bad",
            "--shell",
            "true",
        ),
        ("item", "add", story["key"], "--content", "one\ntwo", "--shell", "true"),
        (
            "item",
            "add",
            story["key"],
            "--content",
            "bad env",
            "--shell",
            "true",
            "--env",
            "A=B",
        ),
        (
            "item",
            "add",
            story["key"],
            "--content",
            "blank env",
            "--shell",
            "true",
            "--env",
            " ",
        ),
        (
            "item",
            "add",
            story["key"],
            "--content",
            "bad match",
            "--shell",
            "true",
            "--stdout-equals",
            "x",
            "--stdout-regex",
            "x",
        ),
        (
            "item",
            "add",
            story["key"],
            "--content",
            "bad hook",
            "--hook",
            "unknown",
            "--arguments",
            "{",
        ),
        (
            "item",
            "set",
            story["key"],
            "--kind",
            "note",
            "--content",
            "bad",
            "--shell",
            "true",
        ),
        (
            "item",
            "set",
            story["key"],
            "--kind",
            "checklist",
            "--content",
            "one\ntwo",
            "--shell",
            "true",
        ),
        ("board", "--iteration", feature["key"]),
        ("next", "--iteration", feature["key"]),
    )
    world.run(
        "item",
        "add",
        story["key"],
        "--kind",
        "checklist",
        "--content",
        "Hook with default arguments",
        "--hook",
        "checks.default",
        actor="developer",
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
    central_story = invoke(
        central_env,
        world.root,
        "story",
        "add",
        "--title",
        "Central store story",
        "--actor",
        "creator",
    )

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
    invoke(
        central_env,
        empty,
        "action",
        central_story["key"],
        "refinement.accepted",
        "--actor",
        "reviewer",
    )
    no_store_env = {
        key: value
        for key, value in world.env.items()
        if key not in {"BACKLOG_DB", "BACK_LOG_URL", "BACKLOG_DIR"}
    }
    invoke(no_store_env, empty, "where", expected=1)
    invoke(no_store_env, empty, "doctor", expected=1)
    invoke(no_store_env, empty, "init", "missing-directory", expected=1)

    repository = world.root / "named-repository"
    nested = repository / "src" / "component"
    nested.mkdir(parents=True)
    git_init = subprocess.run(
        ["git", "init"], cwd=repository, text=True, capture_output=True
    )
    assert git_init.returncode == 0, git_init.stderr or git_init.stdout
    repository_env = {
        key: value
        for key, value in world.env.items()
        if key not in {"BACKLOG_DB", "BACK_LOG_URL", "BACKLOG_DIR", "BACKLOG_PROJECT"}
    }
    repository_backlog = repository / ".backlog"
    repository_backlog.mkdir()
    invoke(
        {**repository_env, "BACKLOG_DIR": str(repository_backlog)},
        repository,
        "init",
        ".",
    )
    repository_location = invoke(repository_env, nested, "where")
    assert repository_location["project"] == "named-repository"
    assert repository_location["scope"] == "repo"

    explicit_dir_env = {
        **repository_env,
        "BACKLOG_DIR": str(repository_backlog),
    }
    assert invoke(explicit_dir_env, nested, "where")["scope"] == "repo"
    invalid_dir_env = {
        **repository_env,
        "BACKLOG_DIR": str(repository / "missing-backlog-directory"),
    }
    invoke(invalid_dir_env, empty, "where", expected=1)

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
    blocker = world.run("bug", "add", "--title", "API blocker", actor="creator")
    blocked = world.run("bug", "add", "--title", "API blocked task", actor="creator")
    world.run("dep", "add", blocker["key"], "--blocks", blocked["key"])
    world.run("project", "add", "--name", "API Secondary")
    source = """
import json
from backlog_cli import api
from backlog_cli.api import (
    Action,
    BacklogError,
    ExecutionPolicy,
    RetrospectiveStatus,
    ReviewSeverity,
    ValidationHookResult,
    validation_hook,
)
from backlog_cli.execution import parse_spec

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
    outside_iteration = backlog.create_story(
        "Ready work outside the iteration", assignee="developer"
    )
    subtask = backlog.create_task("subtask", "API subtask", parent=story.key)

    for invalid_task_call in (
        lambda: backlog.create_task("unknown", "Unknown type"),
        lambda: backlog.create_bug("Invalid priority", priority="urgent"),
        lambda: backlog.create_task("subtask", "Orphan API subtask"),
        lambda: backlog.tasks(status="unknown"),
        lambda: backlog.tasks(task_type="unknown"),
        lambda: backlog.add_item(story.key, "unknown", "Invalid kind"),
    ):
        try:
            invalid_task_call()
        except BacklogError:
            pass
        else:
            raise AssertionError("invalid task API input accepted")

    assert backlog.task(feature.key).children[0].key == story.key
    assert backlog.task(story.key).parent == feature.key
    assert backlog.find("missing") is None
    assert backlog.find(generic.key).key == generic.key
    assert backlog.task_type_counts()["story"] >= 2
    assert backlog.counts()["created"] >= 1
    assert backlog.tasks()
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
    assert flow.terminal == "done"
    assert flow.display("unknown") == "unknown"
    assert flow.category("unknown") == "active"
    assert flow.satisfies("unknown") is False
    assert flow.resolve("In Review") == "in_review"
    assert flow.resolve("In  Review") == "in_review"
    assert flow.gates_for("done", "created") == []
    try:
        flow.resolve("not a status")
    except BacklogError:
        pass
    else:
        raise AssertionError("unknown workflow statuses must fail")

    backlog.assign(story.key, to="developer", reviewer="reviewer")
    for invalid_iteration in (feature.key, iteration.key):
        try:
            backlog.startable("developer", invalid_iteration)
        except BacklogError:
            pass
        else:
            raise AssertionError("work can only be selected from an open iteration")
    ready = backlog.trigger(
        story.key, Action.REFINEMENT_ACCEPTED, actor="independent-reviewer"
    )
    assert ready.status == "ready"
    backlog.trigger(
        outside_iteration.key,
        Action.REFINEMENT_ACCEPTED,
        actor="another-independent-reviewer",
    )
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
    hook_item = backlog.add_item(
        story.key,
        "checklist",
        "API project-isolated hook",
        execution_spec={
            "executor": "hook",
            "hook": {"name": "checks.valid"},
        },
    )
    shell_declaration = {
        "executor": "shell",
        "shell": {"command": "true", "expected_exit_code": 0},
    }
    replaced_checklist = backlog.set_items(
        generic.key,
        "checklist",
        [{"content": "API replaced executable", "execution": shell_declaration}],
    )
    assert replaced_checklist[0]["executor"] == "shell"
    for invalid_execution_call in (
        lambda: backlog.set_item_execution(999999, shell_declaration),
        lambda: backlog.set_item_execution(note["id"], shell_declaration),
        lambda: backlog.execution_history(note["id"]),
        lambda: backlog.waive_validation(
            999999, reason="No such item", actor="developer"
        ),
        lambda: backlog.run_hook_validation(
            checklist["id"], actor="developer", project_root="."
        ),
    ):
        try:
            invalid_execution_call()
        except BacklogError:
            pass
        else:
            raise AssertionError("invalid execution API operation accepted")
    blocked_gate = backlog.can(story.key, "accepted")
    assert blocked_gate.ok is False
    assert blocked_gate.failures
    assert "BLOCKED" in str(blocked_gate)
    policy = ExecutionPolicy(shell_enabled=True, max_output_bytes=4096)
    result = backlog.run_item(checklist["id"], ".", policy=policy)
    assert result.status == "pass"
    assert backlog.execution_history(checklist["id"], limit=1)
    assert backlog.run_task(story.key, ".", policy=policy)
    assert "READY" in str(backlog.can(story.key, "start"))
    assert backlog.execution_policy(".").shell_enabled is False
    assert backlog.source_identity(".").unavailable
    backlog.set_item_execution(
        checklist["id"],
        {
            "executor": "shell",
            "shell": {"command": "true", "expected_exit_code": 0},
        },
    )
    invalid_specs = (
        None,
        {"executor": "shell", "unknown": True},
        {},
        {"executor": "invalid"},
        {"executor": "shell", "requirement": "invalid", "shell": {"command": "true"}},
        {"executor": "shell"},
        {
            "executor": "shell",
            "shell": {"command": "true"},
            "hook": {"name": "checks.extra"},
        },
        {"executor": "shell", "shell": "true"},
        {"executor": "shell", "shell": {"command": "true", "environment": {"A": "secret"}}},
        {"executor": "shell", "shell": {"command": "true", "unknown": True}},
        {"executor": "shell", "hook": {"name": "checks.valid"}},
        {"executor": "shell", "shell": {"command": ""}},
        {"executor": "shell", "shell": {"command": "true", "timeout_seconds": 0}},
        {"executor": "shell", "shell": {"command": "true", "timeout_seconds": True}},
        {"executor": "shell", "shell": {"command": "true", "output_limit_bytes": 0}},
        {"executor": "shell", "shell": {"command": "true", "working_directory": "/tmp"}},
        {"executor": "shell", "shell": {"command": "true", "working_directory": "../outside"}},
        {"executor": "shell", "shell": {"command": "true", "working_directory": ""}},
        {"executor": "shell", "shell": {"command": "true", "environment": ["A", "A"]}},
        {"executor": "shell", "shell": {"command": "true", "environment": [""]}},
        {"executor": "shell", "shell": {"command": "true", "stdout": "text"}},
        {"executor": "shell", "shell": {"command": "true", "stdout": {}}},
        {"executor": "shell", "shell": {"command": "true", "stdout": {"unknown": "x"}}},
        {"executor": "shell", "shell": {"command": "true", "stdout": {"regex": "["}}},
        {"executor": "hook", "hook": "checks.invalid"},
        {"executor": "hook", "hook": {"name": "checks.valid", "unknown": True}},
        {"executor": "hook", "hook": {"name": "not valid!"}},
        {"executor": "hook", "hook": {"name": "checks.valid", "timeout_seconds": 0}},
        {"executor": "hook", "hook": {"name": "checks.valid", "arguments": {1, 2}}},
        {"executor": "hook", "shell": {"command": "true"}},
    )
    for invalid_spec in invalid_specs:
        try:
            backlog.set_item_execution(checklist["id"], invalid_spec)
        except (TypeError, BacklogError):
            pass
        else:
            raise AssertionError(f"invalid execution specification accepted: {invalid_spec!r}")

    shell_spec = parse_spec(
        {
            "executor": "shell",
            "shell": {
                "command": "true",
                "timeout_seconds": 10,
                "output_limit_bytes": 100,
                "working_directory": ".",
                "environment": ["PATH"],
            },
        }
    )
    hook_spec = parse_spec(
        {
            "executor": "hook",
            "hook": {"name": "checks.valid", "timeout_seconds": 10},
        }
    )
    assert ExecutionPolicy().denial_reason(shell_spec) == "shell_disabled"
    assert ExecutionPolicy(shell_enabled=True, max_timeout_seconds=1).denial_reason(shell_spec) == "timeout_exceeds_policy"
    assert ExecutionPolicy(shell_enabled=True, max_output_bytes=10).denial_reason(shell_spec) == "output_limit_exceeds_policy"
    assert ExecutionPolicy(shell_enabled=True, allowed_commands=("false",)).denial_reason(shell_spec) == "command_denied"
    malformed_command = parse_spec({"executor": "shell", "shell": {"command": "'"}})
    assert ExecutionPolicy(shell_enabled=True, allowed_commands=("true",)).denial_reason(malformed_command) == "command_denied"
    assert ExecutionPolicy(shell_enabled=True, allowed_working_directories=("scripts",)).denial_reason(shell_spec) == "working_directory_denied"
    assert ExecutionPolicy(shell_enabled=True).denial_reason(shell_spec) == "environment_variable_denied:PATH"
    assert ExecutionPolicy().denial_reason(hook_spec) == "hook_not_allowed"
    assert ExecutionPolicy(allowed_hooks=("checks.valid",), max_timeout_seconds=1).denial_reason(hook_spec) == "timeout_exceeds_policy"
    assert ExecutionPolicy(
        shell_enabled=True,
        allowed_environment_variables=("PATH",),
        allowed_hooks=("checks.valid",),
    ).denial_reason(shell_spec) is None

    for constructor in (
        lambda: ExecutionPolicy(max_output_bytes=0),
        lambda: ExecutionPolicy(max_timeout_seconds=0),
        lambda: ExecutionPolicy(max_batch_seconds=0),
        lambda: ExecutionPolicy(allowed_working_directories=("../outside",)),
    ):
        try:
            constructor()
        except BacklogError:
            pass
        else:
            raise AssertionError("invalid execution policy accepted")

    @validation_hook(version="v1")
    def local_hook(backlog, context, arguments):
        return ValidationHookResult(arguments, "typed")

    assert local_hook.__backlog_validation_version__ == "v1"
    assert validation_hook()(local_hook) is local_hook
    for invalid_hook in (
        lambda: validation_hook(version=" "),
        lambda: validation_hook(version="v2")(42),
        lambda: ValidationHookResult({1, 2}),
        lambda: ValidationHookResult({}, 42),
    ):
        try:
            invalid_hook()
        except BacklogError:
            pass
        else:
            raise AssertionError("invalid validation hook contract accepted")
    for invalid_result in (
        lambda: backlog.record_execution_result(checklist["id"], "manual", "invalid"),
        lambda: backlog.record_execution_result(checklist["id"], "manual", "skipped"),
        lambda: backlog.record_execution_result(
            checklist["id"], "manual", "pass", hook_name=42
        ),
        lambda: backlog.record_execution_result(
            checklist["id"], "manual", "pass", implementation_identity=42
        ),
        lambda: backlog.record_execution_result(
            checklist["id"], "manual", "pass", expected={1, 2}
        ),
    ):
        try:
            invalid_result()
        except BacklogError:
            pass
        else:
            raise AssertionError("invalid execution result contract accepted")
    replaced = backlog.set_items(
        story.key,
        "notes",
        ["First note", {"content": "Second note"}],
    )
    assert len(replaced) == 2
    assert backlog.task(story.key).items("notes") == ["First note", "Second note"]
    assert backlog.task(story.key).item_details()

    assert backlog.tasks(status="ready", task_type="story", assignee="developer")
    assert backlog.tasks(status="in__review") == []
    assert backlog.tasks(reviewer="reviewer", parent=feature.key, open_only=True)
    assert backlog.actions(story.key)
    assert backlog.can(story.key, "start").ok
    try:
        backlog.can(story.key, "not-a-gate")
    except BacklogError:
        pass
    else:
        raise AssertionError("unknown public gate targets must fail")
    assert backlog.blocked()
    assert backlog.cycles() == []
    assert backlog.dependencies(story.key) == []
    try:
        backlog.dependencies(story.key, kind="not-a-dependency")
    except BacklogError:
        pass
    else:
        raise AssertionError("unknown dependency kinds must fail")
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
    gitlab_pr = backlog.set_pr(
        bug.key,
        url="https://gitlab.example/group/project/-/merge_requests/7",
    )
    assert gitlab_pr.pr_repo == "group/project"
    assert gitlab_pr.pr_number == 7
    assert backlog.set_pr(bug.key, state="closed").pr_state == "closed"
    assert backlog.set_pr(bug.key, state="open").pr_state == "open"
    assert backlog.set_pr(bug.key, state="merged").pr_state == "merged"
    unparsed_pr = backlog.set_pr(
        subtask.key, url="https://example.invalid/change/8"
    )
    assert unparsed_pr.pr_url.endswith("/change/8")
    for invalid_pr_call in (
        lambda: backlog.set_pr(feature.key, state="open"),
        lambda: backlog.set_pr(story.key),
        lambda: backlog.set_pr(story.key, state="unknown"),
        lambda: backlog.set_pr(story.key, review_state="unknown"),
    ):
        try:
            invalid_pr_call()
        except BacklogError:
            pass
        else:
            raise AssertionError("invalid pull-request update accepted")

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
        [{"content": "Executable note", "execution": shell_declaration}],
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
    checklist_id = checklist["id"]
    hook_item_id = hook_item["id"]
    generic_key = generic.key

with api.open(project="api-secondary", actor="developer") as backlog:
    for foreign_item in (checklist_id, hook_item_id):
        try:
            backlog.run_item(foreign_item, ".", policy=policy)
        except BacklogError:
            pass
        else:
            raise AssertionError("another project's executable item was accessible")

with api.open() as backlog:
    for anonymous_call in (
        lambda: backlog.waive_validation(checklist_id, reason="Anonymous waiver"),
        lambda: backlog.create_story("Anonymous task"),
        lambda: backlog.trigger(generic_key, Action.REFINEMENT_ACCEPTED),
    ):
        try:
            anonymous_call()
        except BacklogError:
            pass
        else:
            raise AssertionError("anonymous accountable work must fail")

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
    try:
        backlog.review_open(
            story_key,
            author="reviewer",
            role="observer",
            body="invalid role",
        )
    except BacklogError:
        pass
    else:
        raise AssertionError("invalid review roles must fail")
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
    for invalid_query in (
        lambda: backlog.inbox(role="observer"),
        lambda: backlog.threads(story_key, state="invalid"),
        lambda: backlog.review_updates("RC-999999"),
        lambda: backlog.review_updates(thread.root_key, after="RC-999999"),
    ):
        try:
            invalid_query()
        except BacklogError:
            pass
        else:
            raise AssertionError("invalid review query must fail")
    try:
        backlog.review_reply(
            thread.reply_to,
            author="different-reviewer",
            action="comment",
            body="wrong session actor",
        )
    except BacklogError:
        pass
    else:
        raise AssertionError("review reply authors must match the session")
    root_key = thread.root_key
    reply_to = thread.reply_to

with api.open(actor="developer") as backlog:
    try:
        backlog.review_open(
            story_key,
            author="developer",
            body="developer cannot open a review thread",
        )
    except BacklogError:
        pass
    else:
        raise AssertionError("developers cannot open review threads")
    for action, role in (("open", "auto"), ("comment", "observer")):
        try:
            backlog.review_reply(
                reply_to,
                author="developer",
                action=action,
                role=role,
                body="invalid reply contract",
            )
        except BacklogError:
            pass
        else:
            raise AssertionError("invalid review replies must fail")
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
    assert backlog.review_audit(root_key)["decisions"]
    try:
        backlog.review_reopen(
            root_key,
            author="different-reviewer",
            body="wrong session actor",
        )
    except BacklogError:
        pass
    else:
        raise AssertionError("review reopen authors must match the session")
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
    iteration_thread = backlog.review_open(
        iteration_key,
        author="facilitator",
        body="Iteration process comment",
        severity=ReviewSeverity.INFO,
    )
    assert iteration_thread.where == ""
    assert "Iteration process comment" in str(iteration_thread)
    iteration_root = iteration_thread.root_key
    iteration_reply_to = iteration_thread.reply_to

with api.open(actor="developer") as backlog:
    iteration_fixed = backlog.review_reply(
        iteration_reply_to,
        author="developer",
        action="comment",
        body="Iteration feedback addressed",
    )
    iteration_accept_to = iteration_fixed.reply_to

with api.open(actor="facilitator") as backlog:
    iteration_accepted = backlog.review_reply(
        iteration_accept_to,
        author="facilitator",
        action="accept",
        body="Iteration feedback verified",
    )
    assert iteration_accepted.state == "closed"
    assert backlog.review_audit(iteration_root)["decisions"]
    try:
        backlog.review_reopen(
            "RC-999999",
            author="facilitator",
            body="Missing iteration thread",
        )
    except BacklogError:
        pass
    else:
        raise AssertionError("missing review threads cannot be reopened")

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
    try:
        action.not_a_column
    except AttributeError:
        pass
    else:
        raise AssertionError("missing retrospective attributes must fail")
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


@when("the production PostgreSQL store is exercised")
def exercise_production_postgres(world: World) -> None:
    production_selector = os.environ.get("BACKLOG_DB", "").strip()
    assert production_selector.startswith(("postgres://", "postgresql://")), (
        "the Backlog skill's production PostgreSQL configuration is required"
    )
    pg_root = world.root / "postgres-project"
    pg_root.mkdir()
    schema = f"backlog_bdd_{uuid.uuid4().hex[:12]}"
    pg_world = World(
        pg_root,
        {
            **world.env,
            "BACKLOG_DB": production_selector,
            "BACK_LOG_URL": "",
            "BACKLOG_SCHEMA": schema,
            "BACKLOG_PROJECT": "backlog-plugin-e2e",
            "BACKLOG_ARTIFACTS": str(world.root / "postgres-artifacts"),
        },
    )
    import psycopg
    from psycopg import sql

    try:
        pg_world.run("init", ".", actor="bdd-agent")
        with psycopg.connect(production_selector, autocommit=True) as connection:
            connection.execute(
                sql.SQL("SET search_path TO {}").format(sql.Identifier(schema))
            )
            connection.execute("UPDATE meta SET value='2' WHERE key='schema_version'")
        pg_world.run("doctor")
        location = pg_world.run("where")
        assert location["backend"] == "postgres"
        assert location["scope"] == "shared"
        feature = pg_world.run(
            "feature", "add", "--title", "PostgreSQL feature", actor="creator"
        )
        story = pg_world.run(
            "story",
            "add",
            "--feature",
            feature["key"],
            "--title",
            "PostgreSQL story",
            actor="creator",
        )
        pg_world.run("assign", story["key"], "--to", "developer")
        pg_world.run("action", story["key"], "refinement.accepted", actor="reviewer")
        pg_world.run("show", story["key"])
        pg_world.run("projects")
        pg_world.run("templates")
        export_path = world.root / "postgres-export.json"
        pg_world.run("export", "--out", str(export_path))
        pg_world.run("import", str(export_path), "--replace")
        doctor = pg_world.run("doctor")
        assert doctor["ok"] is True
    finally:
        with psycopg.connect(production_selector, autocommit=True) as connection:
            connection.execute(
                sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(
                    sql.Identifier(schema)
                )
            )
    world.last_json = {"ok": True}


@then("the administrative commands succeed")
def administrative_commands_succeed(world: World) -> None:
    assert world.last_json == {"ok": True}


@then("the public API reports the active project")
def public_api_reports_project(world: World) -> None:
    assert world.last_json == {"project": "bdd-project"}

from __future__ import annotations

import json
from pathlib import Path

from pytest_bdd import scenarios, then, when

from backlog_cli import api

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


@when("the public Python API session is exercised")
def exercise_public_api(world: World, monkeypatch) -> None:
    monkeypatch.chdir(world.root)
    for name, value in world.env.items():
        monkeypatch.setenv(name, value)
    with api.open(actor="bdd-agent") as backlog:
        assert backlog.pid > 0
        assert backlog.store.project == "bdd-project"
        assert backlog.artifacts_dir == world.root / ".backlog"
        assert "bdd-project" in backlog.projects()
        backlog.commit()
        world.last_json = {"project": backlog.store.project}


@then("the administrative commands succeed")
def administrative_commands_succeed(world: World) -> None:
    assert world.last_json == {"ok": True}


@then("the public API reports the active project")
def public_api_reports_project(world: World) -> None:
    assert world.last_json == {"project": "bdd-project"}

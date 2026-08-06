from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path

from pytest_bdd import scenarios, then, when

from backlog_cli import api, db

from .world import World


scenarios("features/acceptance_criteria.feature")

EVIDENCE = "ran the regression test and watched the documented behaviour"

# Long enough that the gate diagnostic has to truncate it.
LONG_CRITERION = (
    "the recovery link stays usable until its recorded expiry, and every "
    "request after that expiry is rejected with a 410"
)


def _story(world: World, title: str, criteria: str | None = EVIDENCE) -> str:
    args = [
        "story",
        "add",
        "--title",
        title,
        "--assignee",
        "developer",
        "--reviewer",
        "reviewer",
    ]
    if criteria:
        args += ["--ac", criteria]
    return world.run(*args, actor="creator")["key"]


def _submit_for_review(world: World, key: str) -> None:
    world.run("action", key, "refinement.accepted", actor="reviewer")
    world.run("action", key, "work.started", actor="developer")
    world.run("action", key, "review.submitted", "--no-pr", actor="developer")


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


@when("acceptance criteria are verified by an independent reviewer")
def verified_by_reviewer(world: World) -> None:
    key = _story(world, "Verified", criteria="the endpoint returns 200")
    _submit_for_review(world, key)
    criterion = world.run("criteria", "list", key)[0]
    assert criterion["state"] == "unverified"
    assert criterion["stale"] is False

    rejected = world.run(
        "criteria",
        "verify",
        str(criterion["id"]),
        "--unmet",
        "--evidence",
        "the endpoint still answers 500",
        actor="reviewer",
    )
    assert rejected["state"] == "unmet"

    with _open_api(world, actor="reviewer") as backlog:
        accepted = backlog.verify_criterion(
            criterion["id"], met=True, evidence=EVIDENCE
        )
        assert accepted["state"] == "met"
        assert accepted["verdict_by"] == "reviewer"
        assert accepted["evidence"] == EVIDENCE
        assert backlog.task(key).acceptance_criteria == backlog.acceptance_criteria(key)
    assert "[met by reviewer]" in world.run("show", key, json_output=False)
    world.run("gate", key, "--for", "accepted", "--no-pr")
    world.last_json = {"ok": True}


@when("invalid acceptance verdicts are attempted")
def invalid_verdicts(world: World) -> None:
    key = _story(world, "Refused", criteria="the endpoint returns 200")
    _submit_for_review(world, key)
    criterion = world.run("criteria", "list", key)[0]
    note = world.run("item", "add", key, "--kind", "note", "--content", "context")[0]
    attempts = (
        ("creator", str(criterion["id"]), EVIDENCE),  # created the task
        ("developer", str(criterion["id"]), EVIDENCE),  # implemented it
        ("reviewer", str(criterion["id"]), "ok"),  # no real evidence
        ("reviewer", str(note["id"]), EVIDENCE),  # not a criterion
        ("reviewer", "999999", EVIDENCE),  # not an item at all
    )
    for actor, item_id, evidence in attempts:
        world.run(
            "criteria",
            "verify",
            item_id,
            "--met",
            "--evidence",
            evidence,
            actor=actor,
            expected=None,
        )
        assert world.last_result is not None and world.last_result.returncode != 0
    assert world.run("criteria", "list", key)[0]["state"] == "unverified"
    world.last_json = {"ok": True}


@when("a verified criterion is rewritten")
def rewritten_criterion(world: World) -> None:
    key = _story(world, "Rewritten", criteria=LONG_CRITERION)
    _submit_for_review(world, key)
    criterion = world.run("criteria", "list", key)[0]
    world.run(
        "criteria",
        "verify",
        str(criterion["id"]),
        "--met",
        "--evidence",
        EVIDENCE,
        actor="reviewer",
    )
    world.run("gate", key, "--for", "accepted", "--no-pr")

    # Rewriting in place is what an import or a hand-edit does: the verdict was
    # given for wording that no longer exists.
    backlog_dir = world.root / ".backlog"
    conn = db.connect(
        spec=db.StoreSpec(
            dialect="sqlite",
            scope="repo",
            project="bdd-project",
            artifacts_dir=backlog_dir / "artifacts",
            db_path=backlog_dir / "backlog.db",
            backlog_dir=backlog_dir,
        )
    )
    try:
        conn.execute(
            "UPDATE task_item SET content=? WHERE id=?",
            (LONG_CRITERION.replace("410", "404"), criterion["id"]),
        )
        conn.commit()
    finally:
        conn.close()

    stale = world.run("criteria", "list", key)[0]
    assert stale["stale"] is True
    assert stale["state"] == "unverified"
    assert stale["verdict_by"] == "reviewer"
    assert "stale" in world.run("criteria", "list", key, json_output=False)
    assert "stale verdict by reviewer" in world.run("show", key, json_output=False)
    world.run("gate", key, "--for", "accepted", "--no-pr", expected=2)
    assert "unverified, stale" in world.output()
    assert "..." in world.output()
    world.last_json = {"ok": True}


@when("acceptance verdicts are cleared")
def cleared_verdicts(world: World) -> None:
    key = _story(world, "Cleared", criteria="the endpoint returns 200")
    _submit_for_review(world, key)
    criterion = world.run("criteria", "list", key)[0]
    world.run(
        "criteria",
        "verify",
        str(criterion["id"]),
        "--met",
        "--evidence",
        EVIDENCE,
        actor="reviewer",
    )
    world.run("criteria", "clear", key, "--reason", "   ", actor="reviewer", expected=None)
    assert world.last_result is not None and world.last_result.returncode != 0
    cleared = world.run(
        "criteria", "clear", key, "--reason", "re-reviewing after a rebase", actor="reviewer"
    )
    assert cleared["cleared"] == 1
    assert world.run("criteria", "list", key)[0]["state"] == "unverified"
    with _open_api(world, actor="reviewer") as backlog:
        assert backlog.clear_criterion_verdicts(key, reason="nothing left") == 0
    world.last_json = {"ok": True}


@when("a task without acceptance criteria is gated")
def gated_without_criteria(world: World) -> None:
    key = _story(world, "Criteria free", criteria=None)
    assert world.run("criteria", "list", key) == []
    assert "(no acceptance criteria)" in world.run(
        "criteria", "list", key, json_output=False
    )
    world.run("gate", key, "--for", "accepted", "--no-pr", expected=2)
    assert "no acceptance criteria recorded" in world.output()
    world.last_json = {"ok": True}


@when("an Iteration is gated for acceptance")
def gated_iteration(world: World) -> None:
    iteration = world.run("iteration", "add", "--title", "Cycle", actor="creator")
    gate = world.run("gate", iteration["key"], "--for", "accepted", "--no-pr")
    criteria = next(
        check
        for check in gate["checks"]
        if check["check"] == "acceptance_criteria_verified"
    )
    assert criteria["ok"] is True
    assert criteria["detail"] == "not applicable to an Iteration"
    world.last_json = {"ok": True}


@then("the acceptance behavior succeeds")
def acceptance_succeeded(world: World) -> None:
    assert world.last_json == {"ok": True}

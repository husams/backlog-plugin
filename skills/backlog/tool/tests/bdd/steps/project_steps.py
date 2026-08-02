from __future__ import annotations

import os
from pathlib import Path

from pytest_bdd import given

from ..world import World


SOURCE_ROOT = Path(__file__).resolve().parents[3] / "src"


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

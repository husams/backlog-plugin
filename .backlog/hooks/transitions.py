"""Project checks and destination overrides before a transition."""

from typing import Any

from backlog_cli.api import Backlog
from backlog_cli.hooks import Action


def pre_transition(
    action: Action,
    trigger: dict[str, Any],
    current_state: str,
    new_state: str,
    backlog: Backlog,
) -> str:
    task = backlog.task(trigger["task_key"])
    if task.status != current_state:
        raise ValueError(
            f"{task.key} changed from {current_state} to {task.status} before transition"
        )
    return new_state

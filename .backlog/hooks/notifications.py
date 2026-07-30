"""Project follow-up behavior after a committed transition."""

from typing import Any

from backlog_cli.api import Backlog
from backlog_cli.hooks import Action


def post_transition(
    action: Action,
    trigger: dict[str, Any],
    previous_state: str,
    current_state: str,
    backlog: Backlog,
) -> None:
    task = backlog.task(trigger["task_key"])
    operation = trigger.get("operation", "unknown")
    actor = trigger.get("actor") or "unknown"
    print(
        f"backlog transition: {task.key} {action.value} "
        f"{previous_state} -> {current_state} "
        f"via {operation} by {actor}"
    )

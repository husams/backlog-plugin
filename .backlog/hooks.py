"""Project transition hooks for backlog-plugin."""


def pre_transition(action, trigger, current_state, new_state):
    """Allow the configured workflow to select and validate the destination."""
    return new_state


def post_transition(action, trigger, previous_state, current_state):
    """Make automated transitions visible in local-runner logs."""
    operation = trigger.get("operation", "unknown")
    actor = trigger.get("actor") or "unknown"
    print(
        f"backlog transition: {action.value} "
        f"{previous_state} -> {current_state} "
        f"via {operation} by {actor}"
    )

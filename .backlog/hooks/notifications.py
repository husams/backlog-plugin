"""Project follow-up behavior after a committed transition."""


def post_transition(action, trigger, previous_state, current_state):
    operation = trigger.get("operation", "unknown")
    actor = trigger.get("actor") or "unknown"
    print(
        f"backlog transition: {action.value} "
        f"{previous_state} -> {current_state} "
        f"via {operation} by {actor}"
    )

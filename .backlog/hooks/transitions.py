"""Project checks and destination overrides before a transition."""


def pre_transition(action, trigger, current_state, new_state):
    return new_state

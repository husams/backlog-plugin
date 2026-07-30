# Python transition hooks

> Design status: proposed. The hook loader and interfaces described here are
> not implemented yet.

A project may add one optional Python file at:

```text
<project>/.backlog/hooks.py
```

If the file does not exist, Backlog continues normally without hooks.

Hooks receive simple transition information. Project developers decide what
policy to enforce with that information. There are no policy classes,
extension schemas, or required base classes.

Hooks must use the public Backlog Python API when they need backlog data or
operations. They must not access database tables directly.

## Standard actions

Backlog provides one enum containing the actions that can cause a transition:

```python
from enum import Enum


class Action(str, Enum):
    CREATE = "create"
    MARK_INCOMPLETE = "mark_incomplete"
    MARK_READY = "mark_ready"
    START = "start"
    SUBMIT_REVIEW = "submit_review"
    REQUEST_CHANGES = "request_changes"
    ACCEPT = "accept"
    COMPLETE = "complete"
```

The enum is owned by Backlog and imported by project hooks:

```python
from backlog_cli.hooks import Action
```

Backlog may add standard actions as its public APIs grow. Projects do not need
to define action classes.

## Hook functions

The project may define either or both functions:

```python
def pre_transition(
    action: Action,
    trigger: dict,
    current_state: str,
    new_state: str,
) -> str:
    return new_state


def post_transition(
    action: Action,
    trigger: dict,
    previous_state: str,
    current_state: str,
) -> None:
    pass
```

The arguments are:

- `action`: why the transition was requested;
- `trigger`: how it was triggered, including the API operation, actor, and
  operation parameters;
- `current_state`: the state before the transition;
- `new_state`: the state selected by the workflow.

For `post_transition`, `previous_state` is the state before the committed
transition and `current_state` is the state after it.

`trigger` uses ordinary Python data rather than a custom class. For example:

```python
{
    "operation": "review_feedback",
    "actor": "reviewer@example.com",
    "parameters": {
        "comments": "The error case needs an acceptance criterion."
    },
}
```

## Pre-transition hook

`pre_transition` runs after the workflow has selected the normal
destination but before Backlog changes the task status.

It returns the state Backlog should use:

```python
def pre_transition(action, trigger, current_state, new_state):
    return new_state
```

A project can override the destination by returning another state:

```python
from backlog_cli.hooks import Action


def pre_transition(action, trigger, current_state, new_state):
    if action == Action.ACCEPT and not project_checks_passed():
        return "needs_work"
    return new_state
```

The returned state must exist in the active workflow. Backlog validates that
state before updating the task.

If the hook raises an exception or returns an invalid state, Backlog rejects
the transition and leaves the task unchanged.

## Post-transition hook

`post_transition` runs after Backlog has committed the state change. It can
notify another system, record project-specific information, or trigger any
other project logic:

```python
from backlog_cli.hooks import Action


def post_transition(action, trigger, previous_state, current_state):
    if action == Action.COMPLETE:
        notify_release_system(trigger["actor"], current_state)
```

The return value is ignored.

If the hook raises an exception, Backlog reports the hook failure but does not
undo the committed transition. This prevents external follow-up failures from
corrupting backlog state.

## Execution order

For every API operation that causes a state transition, Backlog:

1. Converts the API operation to a standard `Action`.
2. Uses the active workflow to find the normal destination state.
3. Calls `pre_transition`.
4. Validates the state returned by the hook.
5. Commits the transition.
6. Calls `post_transition`.

All command-line operations use the same Python APIs, so hooks apply equally
to API and command-line transitions. Direct status updates must not bypass
this sequence.

## Example

This complete `.backlog/hooks.py` keeps accepted work out of `done` until the
project's own release check passes, then publishes an event after completion:

```python
from backlog_cli.hooks import Action
from project_tools import publish_event, release_check_passed


def pre_transition(action, trigger, current_state, new_state):
    if action == Action.COMPLETE and not release_check_passed():
        return current_state
    return new_state


def post_transition(action, trigger, previous_state, current_state):
    if action == Action.COMPLETE and current_state == "done":
        publish_event(
            "backlog.completed",
            actor=trigger.get("actor"),
        )
```

The hook is normal trusted Python code in the project. It can import project
modules and use any logic the project owner needs.

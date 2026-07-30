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
    # Backlog item lifecycle
    ITEM_CREATED = "item.created"
    ITEM_UPDATED = "item.updated"
    ITEM_CANCELLED = "item.cancelled"
    ITEM_REOPENED = "item.reopened"
    ITEM_ARCHIVED = "item.archived"

    # Backlog refinement
    REFINEMENT_SUBMITTED = "refinement.submitted"
    REFINEMENT_MARKED_INCOMPLETE = "refinement.marked_incomplete"
    REFINEMENT_ACCEPTED = "refinement.accepted"

    # Implementation
    WORK_STARTED = "work.started"
    WORK_PAUSED = "work.paused"
    WORK_RESUMED = "work.resumed"
    WORK_BLOCKED = "work.blocked"
    WORK_UNBLOCKED = "work.unblocked"
    WORK_COMPLETED = "work.completed"

    # Implementation review
    REVIEW_SUBMITTED = "review.submitted"
    REVIEW_APPROVED = "review.approved"
    REVIEW_CHANGES_REQUESTED = "review.changes_requested"
    REVIEW_DISMISSED = "review.dismissed"

    # Review feedback
    FEEDBACK_POSTED = "feedback.posted"
    FEEDBACK_ACCEPTED = "feedback.accepted"
    FEEDBACK_REJECTED = "feedback.rejected"
    FEEDBACK_REPLIED = "feedback.replied"
    FEEDBACK_RESOLVED = "feedback.resolved"
    FEEDBACK_REOPENED = "feedback.reopened"

    # Pull requests
    PR_CREATED = "pr.created"
    PR_UPDATED = "pr.updated"
    PR_MARKED_READY = "pr.marked_ready"
    PR_APPROVED = "pr.approved"
    PR_CHANGES_REQUESTED = "pr.changes_requested"
    PR_MERGED = "pr.merged"
    PR_CLOSED = "pr.closed"
    PR_REOPENED = "pr.reopened"

    # Automated checks
    CHECK_STARTED = "check.started"
    CHECK_PASSED = "check.passed"
    CHECK_FAILED = "check.failed"
    CHECK_CANCELLED = "check.cancelled"
    CHECK_TIMED_OUT = "check.timed_out"

    # Delivery
    DELIVERY_ACCEPTED = "delivery.accepted"
    DELIVERY_REJECTED = "delivery.rejected"
    DELIVERY_RELEASED = "delivery.released"
```

The enum is owned by Backlog and imported by project hooks:

```python
from backlog_cli.hooks import Action
```

These actions describe facts that can affect workflow, regardless of whether
they came from a person, an agent, a command, an API, source control, or a CI
system. The active workflow decides whether an action causes a transition from
the task's current state and which state it selects.

For example:

```text
Created     + refinement.marked_incomplete → Incomplete
Created     + refinement.accepted          → Ready
Ready       + work.started                 → In Progress
In Progress + review.submitted             → In Review
In Review   + review.changes_requested     → Need Work
In Review   + review.approved              → Accepted
In Review   + check.failed                 → Need Work
Accepted    + pr.merged                     → Done
```

An action does not have one hard-coded destination. A project may map the same
action differently from different current states. Actions such as
`feedback.posted` or `pr.updated` may be recorded without changing state when
the active workflow has no transition for them.

Backlog may add standard actions as supported integrations and APIs grow.
Projects do not need to define action classes.

## Transition configuration

Backlog ships a default action-to-state configuration at:

```text
skills/backlog/assets/default-workflow.yaml
```

The loader resolves the configuration in this order:

1. Use `<project>/.backlog/workflow.yaml` when it exists.
2. Otherwise, use the bundled default workflow.

There is no configuration merge. A project file replaces the bundled default
as one complete workflow, which keeps the active transition table clear and
predictable.

The bundled workflow provides the default flow for features, stories, and
subtasks:

```text
Created → Incomplete → Ready
    └────────────────→ Ready

Ready → In Progress → In Review → Needs Work → In Progress
                               └→ Accepted → Done
```

It maps standard actions such as `refinement.accepted`, `work.started`,
`review.submitted`, `review.approved`, `review.changes_requested`,
`check.failed`, `pr.created`, `pr.approved`, and `pr.merged` to those
transitions.

A custom workflow uses the same simple primitives:

```yaml
version: 1
name: project-workflow

states:
  - slug: created
    display: Created
    category: backlog
    initial: true
  - slug: ready
    display: Ready
    category: ready

transitions:
  - task_types: [feature, story, subtask]
    from: created
    action: refinement.accepted
    to: ready
```

Each transition contains only the applicable task types, current state,
standard action, destination state, and optional existing gates.

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
    if action == Action.REVIEW_APPROVED and not project_checks_passed():
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
    if action == Action.WORK_COMPLETED:
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
    if action == Action.DELIVERY_ACCEPTED and not release_check_passed():
        return current_state
    return new_state


def post_transition(action, trigger, previous_state, current_state):
    if action == Action.DELIVERY_RELEASED and current_state == "done":
        publish_event(
            "backlog.completed",
            actor=trigger.get("actor"),
        )
```

The hook is normal trusted Python code in the project. It can import project
modules and use any logic the project owner needs.

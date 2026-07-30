# Python project extensions

> Design status: proposed. The extension loader and hook interfaces described
> here are not implemented yet.

Backlog project extensions will let a project enforce custom workflow policy
with Python while keeping the backlog state machine authoritative. Extensions
can inspect backlog data, run project-specific checks, add evidence, allow or
deny transitions, and react after a transition.

An extension must use the documented Backlog Python API. It must not query or
update database tables directly.

## Design principles

1. APIs submit semantic actions, not destination states.
2. The active workflow maps the current state and action to a destination.
3. Standard guards run before project extension code.
4. A Python before hook can allow or deny the proposed transition.
5. Only the workflow engine writes task status.
6. Every action, check, decision, and transition is audited.
7. A project extension is trusted project code and requires explicit
   enablement.

The intended execution path is:

```text
API call
  → semantic action
  → workflow transition lookup
  → standard guards
  → Python before hook
  → transactional state update
  → audit event
  → Python after hook
```

## Default flow

The proposed default state machine is:

```text
Initial → Created
Created → Incomplete
Created → Ready
Incomplete → Ready

Ready → In Progress
In Progress → In Review
In Review → Need Work
Need Work → In Progress
In Review → Accepted
Accepted → Done
```

`Initial` identifies the configured initial state; it does not need to be
stored as a task status.

Backlog refinement happens while a task is `Created` or `Incomplete`.
Implementation review happens while a task is `In Review`. Review feedback
provides evidence and an action; it does not update status directly.

## Actions drive transitions

An API operation emits a standard action:

```python
from enum import Enum


class ReviewAction(str, Enum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    COMMENTED = "commented"
```

For example:

```python
result = backlog.review_feedback(
    task_key="S-001",
    reviewer="husam",
    comments="The failure path is not covered.",
    action=ReviewAction.REJECTED,
)
```

The API emits `review.rejected`. The workflow determines the result from the
current state:

```text
Created   + review.rejected → Incomplete
In Review + review.rejected → Need Work
Created   + review.accepted → Ready
In Review + review.accepted → Accepted
```

There must be at most one transition for each combination of task type,
current state, and action.

## Workflow configuration

Projects will author workflows in `.backlog/workflow.yaml`. Backlog will
validate and compile the file into a database snapshot for transactional
runtime use.

```yaml
schema_version: 1

extension:
  enabled: true
  path: .backlog/extension

states:
  - slug: created
    display: Created
    category: backlog
    initial: true

  - slug: incomplete
    display: Incomplete
    category: backlog

  - slug: ready
    display: Ready
    category: ready

  - slug: in_progress
    display: In Progress
    category: active

  - slug: in_review
    display: In Review
    category: review

  - slug: needs_work
    display: Need Work
    category: active

  - slug: accepted
    display: Accepted
    category: done
    satisfies_dependencies: true

  - slug: done
    display: Done
    category: done
    satisfies_dependencies: true
    terminal: true

transitions:
  - task_type: feature
    from: created
    action: review.accepted
    to: ready
    allowed_roles: [product_manager]
    guards:
      - description_present
      - acceptance_criteria_present

  - task_type: story
    from: in_review
    action: review.rejected
    to: needs_work
    allowed_roles: [reviewer]

  - task_type: story
    from: in_review
    action: review.accepted
    to: accepted
    allowed_roles: [reviewer]
    guards:
      - checklist_complete
      - acceptance_criteria_verified
      - pr_approved
```

Changing the YAML file must not silently change a running project. The intended
activation workflow is:

```text
backlog workflow validate
backlog workflow plan
backlog workflow apply
```

`plan` should report changes to states, transitions, guards, roles, and tasks
affected by the new definition. The compiled database snapshot should record
the workflow version and source hash.

## Extension layout

The conventional project layout is:

```text
<repository>/
└── .backlog/
    ├── workflow.yaml
    └── extension/
        ├── __init__.py
        ├── actions.py
        ├── transitions.py
        ├── reviews.py
        ├── checks.py
        └── settings.py
```

Only `extension/__init__.py` is required. It must export an object named
`extension`:

```python
from .transitions import ProjectPolicy

extension = ProjectPolicy()
```

The other module names are conventions, not mandatory loader entry points.
Project owners may organize their implementation differently.

If the configured path does not exist or extensions are disabled, Backlog uses
`NoOpExtension`.

## Extension contract

The extension object implements three lifecycle methods:

```python
from typing import Protocol


class ProjectExtension(Protocol):
    def on_action(
        self,
        context: "ExtensionContext",
        action: "ActionEvent",
    ) -> "ActionResponse":
        ...

    def before_transition(
        self,
        context: "ExtensionContext",
        proposal: "TransitionProposal",
    ) -> "HookDecision":
        ...

    def after_transition(
        self,
        context: "ExtensionContext",
        result: "TransitionResult",
    ) -> None:
        ...
```

A project does not need to implement every method. The loader may adapt missing
methods to no-op behavior.

## Typed inputs

Hooks receive immutable, named objects instead of loose positional parameters:

```python
from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class ActionEvent:
    name: str
    task_key: str
    actor: str
    parameters: Mapping[str, Any]
    correlation_id: str


@dataclass(frozen=True)
class TransitionProposal:
    task_key: str
    task_type: str
    action: str
    actor: str
    current_state: str
    proposed_state: str
    parameters: Mapping[str, Any]
    configured_guards: tuple[str, ...]
    correlation_id: str
```

For a review API, `parameters` may contain:

```python
{
    "comments": "The error path is missing a test.",
    "reviewer": "husam",
    "review_kind": "implementation",
}
```

## Backlog API access

Hooks receive an extension context containing the public Backlog API:

```python
@dataclass
class ExtensionContext:
    backlog: "BacklogAPI"
    project: "ProjectView"
    logger: "ExtensionLogger"
    filesystem: "ProjectFilesystem"
```

Example:

```python
class ProjectPolicy:
    def before_transition(self, context, proposal):
        task = context.backlog.tasks.get(proposal.task_key)
        children = context.backlog.tasks.children(proposal.task_key)
        open_reviews = context.backlog.reviews.list(
            proposal.task_key,
            state="open",
        )

        if open_reviews:
            return HookDecision.deny(
                reason=f"{len(open_reviews)} review threads remain open"
            )

        return HookDecision.allow()
```

Extensions receive full documented API access, but not the internal database
connection. Mutations called by hooks must still pass workflow checks and
produce audit events.

## Before-transition hooks

A before hook runs after the workflow resolves the proposed destination and
after standard guards run, but before the transition commits.

It returns a structured decision:

```python
class ProjectPolicy:
    def before_transition(self, context, proposal):
        if proposal.action != "review.accepted":
            return HookDecision.allow()

        result = run_unit_tests()

        if not result.passed:
            return HookDecision.deny(
                reason="Unit tests failed",
                evidence=[
                    Evidence(
                        kind="unit-test-result",
                        value=result.summary,
                    )
                ],
            )

        return HookDecision.allow(
            evidence=[
                Evidence(
                    kind="unit-test-result",
                    value="All unit tests passed",
                )
            ]
        )
```

A before hook may:

- inspect backlog entities;
- call project code;
- run tests or other Python checks;
- add structured evidence;
- allow or deny the proposal;
- emit another semantic action.

It must not assign a task status directly.

If a before hook raises an exception, Backlog fails closed: the transition is
not committed, status remains unchanged, and the failure is audited.

## After-transition hooks

An after hook runs only after the transition and audit event commit:

```python
class ProjectPolicy:
    def after_transition(self, context, result):
        if result.to_state == "accepted":
            context.backlog.notes.add(
                result.task_key,
                "Implementation accepted by project policy.",
            )
```

After hooks may create follow-up work, add notes, publish notifications, or
call other documented APIs.

An after-hook failure cannot roll back an already committed transition. The
failure must be recorded and made available for retry. External network actions
should use an outbox so they can be retried safely.

## Emitting actions instead of setting status

An extension may emit an action when complex project logic discovers another
condition:

```python
return HookDecision.emit(
    ActionEvent(
        name="project.tests_failed",
        task_key=proposal.task_key,
        actor="project-extension",
        parameters={"failed_suites": ["unit"]},
        correlation_id=proposal.correlation_id,
    )
)
```

The workflow remains responsible for mapping that action:

```yaml
- task_type: story
  from: in_review
  action: project.tests_failed
  to: needs_work
```

This preserves the state machine as the visible source of transition behavior.

## Review feedback and blocked transitions

Human feedback must not be lost because a separate transition guard fails.

For example, a reviewer accepts the implementation but a project test fails:

```text
feedback saved: yes
review decision: accepted
transition: blocked
status: In Review
reason: unit tests failed
```

APIs should return both outcomes:

```python
@dataclass(frozen=True)
class TransitionResult:
    action: str
    from_state: str
    proposed_state: str | None
    current_state: str
    feedback_saved: bool
    transitioned: bool
    checks: tuple["CheckResult", ...]
    hook_results: tuple["HookResult", ...]
    event_id: str
```

## Re-entrancy

Hooks with API access can accidentally create recursive action loops:

```text
review_feedback
  → before_transition
    → review_feedback
      → before_transition
```

Every operation therefore carries:

- `correlation_id`;
- `caused_by`;
- `hook_depth`.

The runtime should enforce these rules:

1. Reads are always allowed.
2. Hook mutations still pass through the public API and workflow engine.
3. The same action cannot be dispatched for the same task twice in one
   correlation chain.
4. Hook depth has a configurable maximum.
5. Recursive refusal is audited with the complete causal chain.

## Loading and dependencies

The loader should use `importlib` with a unique internal package name. It should
not permanently modify global `sys.path`, because multiple projects may define
extensions with the same module names.

Backlog runs in its own Python environment. Adding project source to the import
path does not install the project's third-party dependencies.

The initial extension contract should support Python's standard library, the
Backlog public API, and project source that has no unavailable dependencies.
A later version may support an isolated extension environment declared by:

```text
.backlog/extension/pyproject.toml
```

Project dependencies should never be installed into Backlog's own runtime
environment.

## Trust and security

A Python extension is trusted project code. Loading it is equivalent to running
code from the repository.

The runtime should:

- require `extension.enabled: true`;
- display the extension path before first execution;
- record a source hash;
- require approval again when the source hash changes, unless project policy
  explicitly trusts changes;
- avoid passing secrets automatically;
- apply configured timeouts to checks;
- record hook inputs, decisions, evidence, duration, and failures;
- redact configured sensitive fields from logs and audit output.

## Developer walkthrough

### 1. Declare the extension

Create `.backlog/workflow.yaml`:

```yaml
schema_version: 1

extension:
  enabled: true
  path: .backlog/extension
```

Add the project's states and action-driven transitions to the same file.

### 2. Export the extension object

Create `.backlog/extension/__init__.py`:

```python
from .transitions import ProjectPolicy

extension = ProjectPolicy()
```

### 3. Implement policy

Create `.backlog/extension/transitions.py`:

```python
from backlog_cli.extensions import Evidence, HookDecision


class ProjectPolicy:
    def on_action(self, context, action):
        context.logger.info(
            "action received",
            action=action.name,
            task=action.task_key,
        )
        return None

    def before_transition(self, context, proposal):
        if proposal.action != "review.accepted":
            return HookDecision.allow()

        task = context.backlog.tasks.get(proposal.task_key)

        if not task.acceptance_criteria:
            return HookDecision.deny(
                reason="Acceptance criteria are required before review acceptance."
            )

        return HookDecision.allow(
            evidence=[
                Evidence(
                    kind="policy-check",
                    value="Acceptance criteria are present.",
                )
            ]
        )

    def after_transition(self, context, result):
        context.logger.info(
            "transition committed",
            task=result.task_key,
            from_state=result.from_state,
            to_state=result.to_state,
        )
```

### 4. Validate before activation

The proposed developer workflow is:

```bash
backlog workflow validate
backlog extension check
backlog workflow plan
```

Validation should check:

- Python package import;
- exported `extension` object;
- supported method signatures;
- state and transition references;
- unique `(task_type, from, action)` mappings;
- registered standard and project actions;
- known guards and roles.

### 5. Apply explicitly

After reviewing the plan:

```bash
backlog workflow apply
```

The command should store the workflow source hash and compiled snapshot. A
changed extension hash may require a new trust approval before hooks execute.

### 6. Test with an action

```python
from backlog_cli import api
from backlog_cli.actions import ReviewAction


with api.open() as backlog:
    result = backlog.review_feedback(
        task_key="S-001",
        reviewer="husam",
        comments="Implementation matches the acceptance criteria.",
        action=ReviewAction.ACCEPTED,
    )

    print(result.current_state)
    print(result.transitioned)
    print(result.checks)
    print(result.hook_results)
```

The action is successful only when the workflow mapping, standard guards, and
Python before hook all allow the transition.

## Open design decisions

The following details must be resolved before implementation:

- the initial standard-action catalog;
- whether `review_feedback.action` is mandatory or defaults to `COMMENTED`;
- the exact guard-composition format in YAML;
- how extension trust approval is stored;
- whether hook timeouts run in-process or through an isolated runner;
- the dependency format for isolated extension environments;
- retry and idempotency rules for after hooks;
- whether custom actions require a `project.` namespace;
- compatibility and migration behavior for existing database-defined flows.


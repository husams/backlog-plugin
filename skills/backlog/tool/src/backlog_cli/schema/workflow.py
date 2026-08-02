"""Workflow states, gates, and default transitions."""

from __future__ import annotations

# --------------------------------------------------------------------------- #

# A status belongs to a semantic category so the engine can reason about a
# custom status it has never seen: which column of the board it sits in, and
# whether work in it counts as started or finished.
STATUS_CATEGORIES = ["backlog", "ready", "active", "review", "done", "dropped"]

# The gate checks a transition may demand. The names are fixed because each one
# is a piece of code; which of them apply to which transition is data.
GATE_CHECKS = [
    "dependencies_clear",  # nothing that blocks this task is still open
    "children_complete",  # every child task is finished
    "review_threads_closed",  # no open blocker review thread
    "iteration_comments_closed",  # no open Iteration thread of any severity
    "iteration_retrospective_actions_clear",  # no untriaged retrospective action
    "pr_recorded",  # a pull request is referenced
    "pr_approved",  # the pull request is approved
    "pr_merged",  # the pull request is merged
    "required_validations_pass",  # required executable items have a fresh pass
    "iteration_members_finished",  # every Iteration member is finished
    "todos_closed",  # no implementation todo remains open
]

GATE_DESCRIPTIONS = {
    "dependencies_clear": "nothing that blocks this task is still open",
    "children_complete": "every child task has reached a finished status",
    "review_threads_closed": "no blocking review thread is still open",
    "iteration_comments_closed": "no Iteration comment of any severity is still open",
    "iteration_retrospective_actions_clear": (
        "no retrospective action for the Iteration is still Created"
    ),
    "pr_recorded": "a pull request is referenced (waivable with --no-pr)",
    "pr_approved": "the pull request is approved (waivable with --no-pr)",
    "pr_merged": "the pull request is merged (waivable with --no-pr)",
    "required_validations_pass": "every required executable item has a fresh passing result",
    "iteration_members_finished": "every Iteration member has reached a finished status",
    "todos_closed": "every implementation todo is closed",
}

# The workflow every new project starts with: today's behaviour, expressed as
# data so a project can change it without touching code.
#   (slug, display, category, satisfies_dependency, initial, terminal)
DEFAULT_STATUS_ROWS = [
    ("created", "Created", "backlog", 0, 1, 0),
    ("incomplete", "In-complete", "backlog", 0, 0, 0),
    ("ready", "Ready", "ready", 0, 0, 0),
    ("in_progress", "In Progress", "active", 0, 0, 0),
    ("in_review", "In Review", "review", 0, 0, 0),
    ("needs_work", "Need work", "active", 0, 0, 0),
    ("accepted", "Accepted", "done", 1, 0, 0),
    ("done", "Done", "done", 1, 0, 1),
]

# (from, to, gates)
DEFAULT_TRANSITIONS = {
    "story": [
        ("created", "ready", ""),
        ("created", "incomplete", ""),
        ("incomplete", "ready", ""),
        ("ready", "in_progress", "dependencies_clear"),
        ("in_progress", "in_review", "pr_recorded,todos_closed"),
        (
            "in_review",
            "accepted",
            "review_threads_closed,pr_approved,children_complete,required_validations_pass",
        ),
        ("in_review", "needs_work", ""),
        ("needs_work", "in_progress", ""),
        ("accepted", "needs_work", ""),
        ("accepted", "done", "required_validations_pass,pr_merged"),
    ],
    # Features use the same delivery states but carry no pull request gates.
    "feature": [
        ("created", "ready", ""),
        ("created", "incomplete", ""),
        ("incomplete", "ready", ""),
        ("ready", "in_progress", "dependencies_clear"),
        ("in_progress", "in_review", "todos_closed"),
        ("in_review", "needs_work", ""),
        ("needs_work", "in_progress", ""),
        (
            "in_review",
            "accepted",
            "review_threads_closed,children_complete,required_validations_pass",
        ),
        ("accepted", "needs_work", ""),
        ("accepted", "done", "children_complete,required_validations_pass"),
    ],
}
DEFAULT_TRANSITIONS["subtask"] = DEFAULT_TRANSITIONS["story"]
DEFAULT_TRANSITIONS["bug"] = DEFAULT_TRANSITIONS["story"]

ITERATION_STATUS_ROWS = [
    ("planned", "Planned", "backlog", 0, 1, 0),
    ("open", "Open", "active", 0, 0, 0),
    ("closed", "Closed", "done", 1, 0, 1),
]
DEFAULT_TRANSITIONS["iteration"] = [
    ("planned", "open", ""),
    (
        "open",
        "closed",
        "iteration_members_finished,iteration_comments_closed,iteration_retrospective_actions_clear",
    ),
    ("closed", "open", "iteration_members_finished"),
]

# --------------------------------------------------------------------------- #

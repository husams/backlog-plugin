"""Database schema, and the status/type vocabularies the CLI enforces.

Shape (schema v3):

    project  ─┬─ task ─┬─ task_item      acceptance criteria / checklist / notes
              │        ├─ artifact       files attached to the task
              │        └─ review_thread ─ review_comment
              └─ dependency              task -> task edges

`task` is one table for features, stories, bugs and subtasks, discriminated by
`task_type` and nested through `parent_id`. Everything that used to need a
union across two tables — dependencies most of all — is now a plain foreign
key.
"""

from __future__ import annotations


SCHEMA_VERSION = 18

# --------------------------------------------------------------------------- #
# tasks
# --------------------------------------------------------------------------- #

TASK_TYPES = ["feature", "story", "bug", "subtask", "iteration"]

TASK_TYPE_DISPLAY = {
    "feature": "Feature",
    "story": "Story",
    "bug": "Bug",
    "subtask": "Subtask",
    "iteration": "Iteration",
}

# Which type may sit under which. A feature is a root; a story sits under a
# feature or stands alone; a bug is always a root; a subtask belongs to a
# story or a bug.
TASK_PARENT_TYPES: dict[str, set[str]] = {
    "feature": set(),
    "story": {"feature"},
    "bug": set(),
    "subtask": {"story", "bug"},
    "iteration": set(),
}

TASK_KEY_PREFIX = {
    "feature": "F",
    "story": "S",
    "bug": "B",
    "subtask": "T",
    "iteration": "I",
}

TASK_TYPE_ALIASES = {
    "epic": "feature",
    "feat": "feature",
    "user_story": "story",
    "defect": "bug",
    "sprint": "iteration",
    "task": "subtask",
    "sub_task": "subtask",
    "subtask": "subtask",
    "sub": "subtask",
}

STATUSES = [
    "created",
    "incomplete",
    "ready",
    "in_progress",
    "in_review",
    "needs_work",
    "accepted",
    "done",
    "planned",
    "open",
    "closed",
]

STATUS_DISPLAY = {
    "created": "Created",
    "incomplete": "In-complete",
    "ready": "Ready",
    "in_progress": "In Progress",
    "in_review": "In Review",
    "needs_work": "Need work",
    "accepted": "Accepted",
    "done": "Done",
    "planned": "Planned",
    "open": "Open",
    "closed": "Closed",
}

# The status *vocabulary* is common to every task type and is deliberately NOT
# constrained in the database — the store records whatever it is told, and the
# CLI is the thing that enforces a legal path. That keeps the flow a policy
# decision you can change without a schema migration.
#
# The *flow* is per type. A story or a subtask is delivered through review and
# a pull request; a feature is a container whose progress is the progress of
# its children, so it has no review stage of its own.
TRANSITIONS: dict[str, set[str]] = {
    "created": {"ready", "incomplete"},
    "incomplete": {"ready", "accepted"},
    "ready": {"in_progress"},
    "in_progress": {"in_review"},
    "in_review": {"accepted", "needs_work"},
    "needs_work": {"in_review"},
    "accepted": {"done"},
    "done": set(),
}

FEATURE_TRANSITIONS: dict[str, set[str]] = {
    "created": {"ready", "incomplete", "in_review"},
    "incomplete": {"ready", "accepted", "in_review"},
    "ready": {"in_progress"},
    "in_progress": {"accepted", "incomplete"},
    "in_review": {"ready", "incomplete"},
    "needs_work": {"in_progress"},
    "accepted": {"done"},
    "done": set(),
}

TRANSITIONS_BY_TYPE: dict[str, dict[str, set[str]]] = {
    "feature": FEATURE_TRANSITIONS,
    "story": TRANSITIONS,
    "bug": TRANSITIONS,
    "subtask": TRANSITIONS,
    "iteration": {"planned": {"open"}, "open": {"closed"}, "closed": {"open"}},
}
STATUS_ALIASES = {
    "in_complete": "incomplete",
    "need_work": "needs_work",
    "needs_works": "needs_work",
    "inprogress": "in_progress",
    "inreview": "in_review",
    "wip": "in_progress",
    "review": "in_review",
    "approved": "accepted",
    "merged": "done",
    "new": "created",
    "todo": "ready",
    # the retired feature vocabulary, so old commands and imports still parse.
    # `planned` is now a first-class Iteration state and must not be rewritten.
    "active": "in_progress",
    "shipped": "done",
    "dropped": "incomplete",
}

# A task stops blocking its dependents once it reaches one of these. This is
# only the fallback for a store with no workflow rows; the live answer comes
# from `workflow_status.satisfies_dependency`.
SATISFIED_STATUSES = {"accepted", "done"}

# --------------------------------------------------------------------------- #

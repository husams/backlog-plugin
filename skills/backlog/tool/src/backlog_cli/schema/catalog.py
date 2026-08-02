"""Built-in project templates and actor catalogues."""

from __future__ import annotations

from .task import TASK_TYPES
from .workflow import DEFAULT_STATUS_ROWS, DEFAULT_TRANSITIONS, ITERATION_STATUS_ROWS

# --------------------------------------------------------------------------- #

# A template is the pre-defined shape a project is created from: one workflow
# per task type, with its statuses and transitions. Projects instantiate a copy
# at creation, so editing a project's flow never disturbs the template and
# editing a template never disturbs a project already running.
#
# These ship with the skill and are installed into the `template` tables on
# first use, which makes them listable, copyable and editable like any other.

_SOFTWARE_WORKFLOWS = {
    ttype: {
        "statuses": ITERATION_STATUS_ROWS if ttype == "iteration" else DEFAULT_STATUS_ROWS,
        "transitions": DEFAULT_TRANSITIONS[ttype],
    }
    for ttype in TASK_TYPES
}

_LIGHTWEIGHT_STATUSES = [
    ("created", "Created", "backlog", 0, 1, 0),
    ("ready", "Ready", "ready", 0, 0, 0),
    ("in_progress", "In Progress", "active", 0, 0, 0),
    ("done", "Done", "done", 1, 0, 1),
    ("dropped", "Dropped", "dropped", 1, 0, 1),
]
_LIGHTWEIGHT_TRANSITIONS = [
    ("created", "ready", ""),
    ("created", "dropped", ""),
    ("ready", "in_progress", "dependencies_clear"),
    ("ready", "dropped", ""),
    ("in_progress", "done", "children_complete"),
    ("in_progress", "dropped", ""),
]

_RESEARCH_STATUSES = [
    ("proposed", "Proposed", "backlog", 0, 1, 0),
    ("investigating", "Investigating", "active", 0, 0, 0),
    ("drafted", "Drafted", "review", 0, 0, 0),
    ("reviewed", "Reviewed", "review", 0, 0, 0),
    ("published", "Published", "done", 1, 0, 1),
    ("parked", "Parked", "dropped", 1, 0, 1),
]
_RESEARCH_TRANSITIONS = [
    ("proposed", "investigating", "dependencies_clear"),
    ("proposed", "parked", ""),
    ("investigating", "drafted", ""),
    ("investigating", "parked", ""),
    ("drafted", "reviewed", "review_threads_closed"),
    ("drafted", "investigating", ""),
    ("reviewed", "published", "children_complete"),
    ("reviewed", "investigating", ""),
]

BUILTIN_TEMPLATES = [
    {
        "slug": "software-delivery",
        "name": "Software delivery",
        "description": ("Feature / story / subtask delivered through review and a "
                        "pull request. Features are containers and carry no PR."),
        "is_default": 1,
        "workflows": _SOFTWARE_WORKFLOWS,
    },
    {
        "slug": "lightweight",
        "name": "Lightweight",
        "description": ("No review stage and no pull-request gates — for work "
                        "tracked for visibility rather than delivered through review."),
        "is_default": 0,
        "workflows": {t: {
            "statuses": ITERATION_STATUS_ROWS if t == "iteration" else _LIGHTWEIGHT_STATUSES,
            "transitions": DEFAULT_TRANSITIONS["iteration"] if t == "iteration" else _LIGHTWEIGHT_TRANSITIONS,
        } for t in TASK_TYPES},
    },
    {
        "slug": "research",
        "name": "Research",
        "description": ("Propose, investigate, draft, review, publish — for "
                        "investigation rather than shipped code."),
        "is_default": 0,
        "workflows": {t: {
            "statuses": ITERATION_STATUS_ROWS if t == "iteration" else _RESEARCH_STATUSES,
            "transitions": DEFAULT_TRANSITIONS["iteration"] if t == "iteration" else _RESEARCH_TRANSITIONS,
        } for t in TASK_TYPES},
    },
]

DEFAULT_TEMPLATE_SLUG = "software-delivery"

PRIORITIES = ["P0", "P1", "P2", "P3"]

PROJECT_STATUSES = ["active", "archived"]

# --------------------------------------------------------------------------- #
# who is doing the work
# --------------------------------------------------------------------------- #

# Names stay free text; this records *what kind of worker* the name refers to,
# so a board can tell agent work from human work without a name convention.
ACTOR_KINDS = ["human", "agent", "unknown"]

# Names that are agents unless stated otherwise.
KNOWN_AGENTS = {
    "claude", "codex", "cursor", "copilot", "devin", "aider", "gemini",
    "developer", "senior-developer", "qa-engineer", "architect",
    "product-manager", "business-analyst", "doc-writer", "devops",
    "backlog",
}

# --------------------------------------------------------------------------- #

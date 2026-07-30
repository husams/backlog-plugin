"""Database schema, and the status/type vocabularies the CLI enforces.

Shape (schema v3):

    project  ─┬─ task ─┬─ task_item      acceptance criteria / checklist / notes
              │        ├─ artifact       files attached to the task
              │        └─ review_thread ─ review_comment
              └─ dependency              task -> task edges

`task` is one table for features, stories and subtasks, discriminated by
`task_type` and nested through `parent_id`. Everything that used to need a
union across two tables — dependencies most of all — is now a plain foreign
key.
"""

from __future__ import annotations

from enum import Enum

SCHEMA_VERSION = 8

# --------------------------------------------------------------------------- #
# tasks
# --------------------------------------------------------------------------- #

TASK_TYPES = ["feature", "story", "subtask"]

TASK_TYPE_DISPLAY = {"feature": "Feature", "story": "Story", "subtask": "Subtask"}

# Which type may sit under which. A feature is a root; a story sits under a
# feature or stands alone; a subtask always belongs to a story.
TASK_PARENT_TYPES: dict[str, set[str]] = {
    "feature": set(),
    "story": {"feature"},
    "subtask": {"story"},
}

TASK_KEY_PREFIX = {"feature": "F", "story": "S", "subtask": "T"}

TASK_TYPE_ALIASES = {
    "epic": "feature",
    "feat": "feature",
    "user_story": "story",
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
    "subtask": TRANSITIONS,
}


def transitions_for(task_type: str) -> dict[str, set[str]]:
    return TRANSITIONS_BY_TYPE.get(task_type, TRANSITIONS)

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
    # the retired feature vocabulary, so old commands and imports still parse
    "planned": "created",
    "active": "in_progress",
    "shipped": "done",
    "dropped": "incomplete",
}

# A task stops blocking its dependents once it reaches one of these. This is
# only the fallback for a store with no workflow rows; the live answer comes
# from `workflow_status.satisfies_dependency`.
SATISFIED_STATUSES = {"accepted", "done"}

# --------------------------------------------------------------------------- #
# workflow
# --------------------------------------------------------------------------- #

# A status belongs to a semantic category so the engine can reason about a
# custom status it has never seen: which column of the board it sits in, and
# whether work in it counts as started or finished.
STATUS_CATEGORIES = ["backlog", "ready", "active", "review", "done", "dropped"]

# The gate checks a transition may demand. The names are fixed because each one
# is a piece of code; which of them apply to which transition is data.
GATE_CHECKS = [
    "dependencies_clear",     # nothing that blocks this task is still open
    "children_complete",      # every child task is finished
    "review_threads_closed",  # no open blocker review thread
    "pr_recorded",            # a pull request is referenced
    "pr_approved",            # the pull request is approved
    "pr_merged",              # the pull request is merged
    "required_validations_pass",  # required executable items have a fresh pass
]

GATE_DESCRIPTIONS = {
    "dependencies_clear": "nothing that blocks this task is still open",
    "children_complete": "every child task has reached a finished status",
    "review_threads_closed": "no blocking review thread is still open",
    "pr_recorded": "a pull request is referenced (waivable with --no-pr)",
    "pr_approved": "the pull request is approved (waivable with --no-pr)",
    "pr_merged": "the pull request is merged (waivable with --no-pr)",
    "required_validations_pass": "every required executable item has a fresh passing result",
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
        ("in_progress", "in_review", "pr_recorded"),
        ("in_review", "accepted", "review_threads_closed,pr_approved,children_complete,required_validations_pass"),
        ("in_review", "needs_work", ""),
        ("needs_work", "in_progress", ""),
        ("accepted", "needs_work", ""),
        ("accepted", "done", "pr_merged"),
    ],
    # Features use the same delivery states but carry no pull request gates.
    "feature": [
        ("created", "ready", ""),
        ("created", "incomplete", ""),
        ("incomplete", "ready", ""),
        ("ready", "in_progress", "dependencies_clear"),
        ("in_progress", "in_review", ""),
        ("in_review", "needs_work", ""),
        ("needs_work", "in_progress", ""),
        ("in_review", "accepted", "review_threads_closed,children_complete,required_validations_pass"),
        ("accepted", "needs_work", ""),
        ("accepted", "done", "children_complete"),
    ],
}
DEFAULT_TRANSITIONS["subtask"] = DEFAULT_TRANSITIONS["story"]

# --------------------------------------------------------------------------- #
# templates
# --------------------------------------------------------------------------- #

# A template is the pre-defined shape a project is created from: one workflow
# per task type, with its statuses and transitions. Projects instantiate a copy
# at creation, so editing a project's flow never disturbs the template and
# editing a template never disturbs a project already running.
#
# These ship with the skill and are installed into the `template` tables on
# first use, which makes them listable, copyable and editable like any other.

_SOFTWARE_WORKFLOWS = {
    ttype: {"statuses": DEFAULT_STATUS_ROWS, "transitions": DEFAULT_TRANSITIONS[ttype]}
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
        "workflows": {t: {"statuses": _LIGHTWEIGHT_STATUSES,
                          "transitions": _LIGHTWEIGHT_TRANSITIONS} for t in TASK_TYPES},
    },
    {
        "slug": "research",
        "name": "Research",
        "description": ("Propose, investigate, draft, review, publish — for "
                        "investigation rather than shipped code."),
        "is_default": 0,
        "workflows": {t: {"statuses": _RESEARCH_STATUSES,
                          "transitions": _RESEARCH_TRANSITIONS} for t in TASK_TYPES},
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
# task items (sections of a task)
# --------------------------------------------------------------------------- #

ITEM_KINDS = ["acceptance_criteria", "checklist", "note"]

ITEM_KIND_DISPLAY = {
    "acceptance_criteria": "acceptance criteria",
    "checklist": "checklist",
    "note": "note",
}

ITEM_KIND_ALIASES = {
    "ac": "acceptance_criteria",
    "acceptance": "acceptance_criteria",
    "criteria": "acceptance_criteria",
    "check": "checklist",
    "todo": "checklist",
    "notes": "note",
}

# Only a checklist entry is tickable; criteria are proven by review, notes by
# nothing at all.
TICKABLE_ITEM_KINDS = {"checklist"}

# --------------------------------------------------------------------------- #
# pull requests, dependencies, review
# --------------------------------------------------------------------------- #

PR_STATES = ["none", "draft", "open", "merged", "closed"]
PR_REVIEW_STATES = ["none", "pending", "changes_requested", "approved"]

# A feature is a container: it has no branch and no PR of its own, so the PR
# gates simply do not apply to it.
PR_BEARING_TYPES = {"story", "subtask"}

DEPENDENCY_KINDS = ["blocks", "relates", "duplicates"]
HARD_DEPENDENCY_KINDS = {"blocks"}
SYMMETRIC_DEPENDENCY_KINDS = {"relates"}

DEPENDENCY_KIND_ALIASES = {
    "block": "blocks",
    "blocking": "blocks",
    "depends_on": "blocks",
    "dependson": "blocks",
    "relate": "relates",
    "related": "relates",
    "relates_to": "relates",
    "duplicate": "duplicates",
    "dupe": "duplicates",
}

REVIEW_ACTIONS = ["open", "comment", "fix", "reject", "accept"]
REVIEW_ROLES = ["reviewer", "developer"]
THREAD_STATES = ["awaiting_developer", "awaiting_reviewer", "closed"]


class ReviewSeverity(str, Enum):
    """Fixed impact levels for a review thread."""

    BLOCKER = "blocker"
    NICE_TO_HAVE = "nice_to_have"
    INFO = "info"


REVIEW_SEVERITIES = [severity.value for severity in ReviewSeverity]

ARTIFACT_KINDS = ["doc", "spec", "design", "log", "report", "patch", "data"]

SCHEMA_SQL = """
PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- A template is the pre-defined project shape. Creating a project copies its
-- workflows, so the project can then diverge without touching the template.
CREATE TABLE IF NOT EXISTS template (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    slug        TEXT NOT NULL UNIQUE,
    name        TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    is_default  INTEGER NOT NULL DEFAULT 0,
    builtin     INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS template_workflow (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    template_id INTEGER NOT NULL REFERENCES template(id) ON DELETE CASCADE,
    task_type   TEXT NOT NULL,
    name        TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    UNIQUE (template_id, task_type)
);

CREATE TABLE IF NOT EXISTS template_status (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    template_workflow_id INTEGER NOT NULL REFERENCES template_workflow(id) ON DELETE CASCADE,
    slug                 TEXT NOT NULL,
    display              TEXT NOT NULL,
    category             TEXT NOT NULL DEFAULT 'active',
    position             INTEGER NOT NULL DEFAULT 0,
    satisfies_dependency INTEGER NOT NULL DEFAULT 0,
    is_initial           INTEGER NOT NULL DEFAULT 0,
    is_terminal          INTEGER NOT NULL DEFAULT 0,
    description          TEXT NOT NULL DEFAULT '',
    UNIQUE (template_workflow_id, slug)
);

CREATE TABLE IF NOT EXISTS template_transition (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    template_workflow_id INTEGER NOT NULL REFERENCES template_workflow(id) ON DELETE CASCADE,
    from_status          TEXT NOT NULL,
    to_status            TEXT NOT NULL,
    gates                TEXT NOT NULL DEFAULT '',
    note                 TEXT NOT NULL DEFAULT '',
    UNIQUE (template_workflow_id, from_status, to_status)
);

CREATE TABLE IF NOT EXISTS project (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    template_id INTEGER REFERENCES template(id),
    slug        TEXT NOT NULL UNIQUE,
    name        TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    status      TEXT NOT NULL DEFAULT 'active',
    repo_path   TEXT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

-- One workflow per (project, task type). Statuses and the legal moves between
-- them are rows, so a project can define its own flow -- extra statuses, a
-- different route -- without a code change or a schema migration.
CREATE TABLE IF NOT EXISTS workflow (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id  INTEGER NOT NULL REFERENCES project(id) ON DELETE CASCADE,
    task_type   TEXT NOT NULL,
    name        TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    UNIQUE (project_id, task_type)
);

CREATE TABLE IF NOT EXISTS workflow_status (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    workflow_id          INTEGER NOT NULL REFERENCES workflow(id) ON DELETE CASCADE,
    slug                 TEXT NOT NULL,
    display              TEXT NOT NULL,
    category             TEXT NOT NULL DEFAULT 'active',
    position             INTEGER NOT NULL DEFAULT 0,
    satisfies_dependency INTEGER NOT NULL DEFAULT 0,
    is_initial           INTEGER NOT NULL DEFAULT 0,
    is_terminal          INTEGER NOT NULL DEFAULT 0,
    description          TEXT NOT NULL DEFAULT '',
    UNIQUE (workflow_id, slug)
);

CREATE INDEX IF NOT EXISTS idx_wf_status ON workflow_status(workflow_id, position);

CREATE TABLE IF NOT EXISTS workflow_transition (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    workflow_id INTEGER NOT NULL REFERENCES workflow(id) ON DELETE CASCADE,
    from_status TEXT NOT NULL,
    to_status   TEXT NOT NULL,
    gates       TEXT NOT NULL DEFAULT '',
    note        TEXT NOT NULL DEFAULT '',
    UNIQUE (workflow_id, from_status, to_status)
);

CREATE INDEX IF NOT EXISTS idx_wf_transition ON workflow_transition(workflow_id, from_status);

-- Key sequences are per project, so every project starts at F-001 / S-001.
CREATE TABLE IF NOT EXISTS key_counter (
    project_id INTEGER NOT NULL REFERENCES project(id) ON DELETE CASCADE,
    prefix     TEXT NOT NULL,
    next_value INTEGER NOT NULL,
    PRIMARY KEY (project_id, prefix)
);

CREATE TABLE IF NOT EXISTS task (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id          INTEGER NOT NULL REFERENCES project(id) ON DELETE CASCADE,
    key                 TEXT NOT NULL,
    task_type           TEXT NOT NULL CHECK (task_type IN ('feature','story','subtask')),
    parent_id           INTEGER REFERENCES task(id) ON DELETE SET NULL,
    title               TEXT NOT NULL,
    description         TEXT NOT NULL DEFAULT '',
    -- Deliberately unconstrained: the status vocabulary is common but the
    -- legal *flow* is per task type and enforced by the CLI, so it can change
    -- without a schema migration.
    status              TEXT NOT NULL DEFAULT 'created',
    priority            TEXT NOT NULL DEFAULT 'P2'
                        CHECK (priority IN ('P0','P1','P2','P3')),
    owner               TEXT,
    assignee            TEXT,
    assignee_kind       TEXT NOT NULL DEFAULT 'unknown'
                        CHECK (assignee_kind IN ('human','agent','unknown')),
    reviewer            TEXT,
    reviewer_kind       TEXT NOT NULL DEFAULT 'unknown'
                        CHECK (reviewer_kind IN ('human','agent','unknown')),
    branch              TEXT,
    pr_url              TEXT,
    pr_number           INTEGER,
    pr_repo             TEXT,
    pr_state            TEXT NOT NULL DEFAULT 'none'
                        CHECK (pr_state IN ('none','draft','open','merged','closed')),
    pr_review_state     TEXT NOT NULL DEFAULT 'none'
                        CHECK (pr_review_state IN ('none','pending','changes_requested','approved')),
    pr_waived           INTEGER NOT NULL DEFAULT 0,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL,
    closed_at           TEXT,
    UNIQUE (project_id, key)
);

CREATE INDEX IF NOT EXISTS idx_task_project ON task(project_id, status);
CREATE INDEX IF NOT EXISTS idx_task_parent  ON task(parent_id);
CREATE INDEX IF NOT EXISTS idx_task_type    ON task(project_id, task_type);

-- Sections of a task: acceptance criteria, checklist entries, loose notes.
CREATE TABLE IF NOT EXISTS task_item (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id     INTEGER NOT NULL REFERENCES task(id) ON DELETE CASCADE,
    kind        TEXT NOT NULL CHECK (kind IN ('acceptance_criteria','checklist','note')),
    position    INTEGER NOT NULL DEFAULT 0,
    content     TEXT NOT NULL,
    done        INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    created_by  TEXT
);

CREATE INDEX IF NOT EXISTS idx_item_task ON task_item(task_id, kind, position);

-- Execution is optional. NULL execution_spec preserves the exact historical
-- meaning of plain criteria/checklist/note rows.
CREATE TABLE IF NOT EXISTS executable_item (
    item_id          INTEGER PRIMARY KEY REFERENCES task_item(id) ON DELETE CASCADE,
    executor         TEXT NOT NULL CHECK (executor IN ('shell','hook')),
    requirement      TEXT NOT NULL DEFAULT 'required'
                     CHECK (requirement IN ('required','advisory')),
    execution_spec   TEXT NOT NULL,
    spec_fingerprint TEXT NOT NULL,
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS execution_result (
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id                  INTEGER NOT NULL REFERENCES task_item(id) ON DELETE CASCADE,
    spec_fingerprint         TEXT NOT NULL,
    status                   TEXT NOT NULL CHECK (status IN ('pass','fail','error','skipped')),
    reason                   TEXT NOT NULL DEFAULT '',
    detail                   TEXT NOT NULL DEFAULT '',
    source_revision          TEXT,
    source_dirty_fingerprint TEXT,
    source_revision_unavailable INTEGER NOT NULL DEFAULT 0,
    started_at               TEXT NOT NULL,
    finished_at              TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_execution_result_item
    ON execution_result(item_id, id);

-- Dependency edges, now a plain foreign key on both ends.
CREATE TABLE IF NOT EXISTS dependency (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    from_task_id INTEGER NOT NULL REFERENCES task(id) ON DELETE CASCADE,
    to_task_id   INTEGER NOT NULL REFERENCES task(id) ON DELETE CASCADE,
    kind         TEXT NOT NULL CHECK (kind IN ('blocks','relates','duplicates')),
    note         TEXT NOT NULL DEFAULT '',
    external_id  TEXT,
    created_at   TEXT NOT NULL,
    created_by   TEXT,
    UNIQUE (from_task_id, to_task_id, kind),
    CHECK (from_task_id <> to_task_id)
);

CREATE INDEX IF NOT EXISTS idx_dep_from ON dependency(from_task_id, kind);
CREATE INDEX IF NOT EXISTS idx_dep_to   ON dependency(to_task_id, kind);

CREATE TABLE IF NOT EXISTS artifact (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id     INTEGER NOT NULL REFERENCES task(id) ON DELETE CASCADE,
    rel_path    TEXT NOT NULL,
    title       TEXT NOT NULL DEFAULT '',
    kind        TEXT NOT NULL DEFAULT 'doc',
    created_at  TEXT NOT NULL,
    created_by  TEXT,
    UNIQUE (task_id, rel_path)
);

CREATE TABLE IF NOT EXISTS review_thread (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id          INTEGER NOT NULL REFERENCES task(id) ON DELETE CASCADE,
    root_key         TEXT NOT NULL UNIQUE,
    state            TEXT NOT NULL
                     CHECK (state IN ('awaiting_developer','awaiting_reviewer','closed')),
    resolution       TEXT CHECK (resolution IN ('accepted_by_reviewer','accepted_by_developer')),
    severity         TEXT NOT NULL DEFAULT 'blocker'
                     CHECK (severity IN ('blocker','nice_to_have','info')),
    title            TEXT NOT NULL DEFAULT '',
    file_path        TEXT,
    line             INTEGER,
    last_comment_key TEXT NOT NULL,
    comment_count    INTEGER NOT NULL DEFAULT 1,
    opened_by        TEXT NOT NULL,
    opened_at        TEXT NOT NULL,
    updated_at       TEXT NOT NULL,
    closed_by        TEXT,
    closed_at        TEXT
);

CREATE INDEX IF NOT EXISTS idx_thread_task ON review_thread(task_id, state);

CREATE TABLE IF NOT EXISTS review_comment (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id     INTEGER NOT NULL REFERENCES task(id) ON DELETE CASCADE,
    key         TEXT NOT NULL UNIQUE,
    root_key    TEXT NOT NULL,
    parent_key  TEXT,
    seq         INTEGER NOT NULL,
    author      TEXT NOT NULL,
    author_kind TEXT NOT NULL DEFAULT 'unknown'
                CHECK (author_kind IN ('human','agent','unknown')),
    role        TEXT NOT NULL CHECK (role IN ('reviewer','developer')),
    action      TEXT NOT NULL CHECK (action IN ('open','comment','fix','reject','accept')),
    body        TEXT NOT NULL,
    file_path   TEXT,
    line        INTEGER,
    created_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_comment_root ON review_comment(root_key, seq);
CREATE INDEX IF NOT EXISTS idx_comment_task ON review_comment(task_id);

CREATE TABLE IF NOT EXISTS event (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          TEXT NOT NULL,
    project_id  INTEGER REFERENCES project(id) ON DELETE CASCADE,
    task_id     INTEGER REFERENCES task(id) ON DELETE CASCADE,
    entity_key  TEXT NOT NULL DEFAULT '',
    actor       TEXT,
    actor_kind  TEXT NOT NULL DEFAULT 'unknown'
                CHECK (actor_kind IN ('human','agent','unknown')),
    kind        TEXT NOT NULL,
    from_value  TEXT,
    to_value    TEXT,
    detail      TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_event_task    ON event(task_id, id);
CREATE INDEX IF NOT EXISTS idx_event_project ON event(project_id, id);
"""

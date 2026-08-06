
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
    task_type           TEXT NOT NULL CHECK (task_type IN ('feature','story','bug','subtask','iteration')),
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
    created_by          TEXT,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL,
    closed_at           TEXT,
    UNIQUE (project_id, key)
);

CREATE INDEX IF NOT EXISTS idx_task_project ON task(project_id, status);
CREATE INDEX IF NOT EXISTS idx_task_parent  ON task(parent_id);
CREATE INDEX IF NOT EXISTS idx_task_type    ON task(project_id, task_type);

CREATE TABLE IF NOT EXISTS iteration_member (
    iteration_id INTEGER NOT NULL REFERENCES task(id) ON DELETE CASCADE,
    member_id    INTEGER NOT NULL REFERENCES task(id) ON DELETE CASCADE,
    created_at   TEXT NOT NULL,
    PRIMARY KEY (iteration_id, member_id),
    CHECK (iteration_id != member_id)
);
CREATE INDEX IF NOT EXISTS idx_iteration_member_member ON iteration_member(member_id);

-- Improvement work discovered during an Iteration retrospective. These are
-- deliberately separate from delivery tasks: their small lifecycle is fixed,
-- while task workflows remain project-configurable. A completed action points
-- at the Feature or Bug opened to deliver the improvement, which may live in a
-- different project in the same store.
CREATE TABLE IF NOT EXISTS retrospective_action (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id            INTEGER NOT NULL REFERENCES project(id) ON DELETE CASCADE,
    iteration_id          INTEGER NOT NULL REFERENCES task(id),
    key                   TEXT NOT NULL,
    title                 TEXT NOT NULL,
    repeated_issue        TEXT NOT NULL,
    proposed_solution     TEXT NOT NULL,
    status                TEXT NOT NULL DEFAULT 'created'
                          CHECK (status IN ('created','ready','done','rejected')),
    rejection_reason      TEXT,
    resolution_project_id INTEGER REFERENCES project(id),
    resolution_task_id    INTEGER REFERENCES task(id),
    created_by            TEXT,
    accepted_by           TEXT,
    rejected_by           TEXT,
    closed_by             TEXT,
    created_at            TEXT NOT NULL,
    updated_at            TEXT NOT NULL,
    accepted_at           TEXT,
    rejected_at           TEXT,
    closed_at             TEXT,
    UNIQUE (project_id, key),
    CHECK (
        (status IN ('created','ready') AND rejection_reason IS NULL
         AND resolution_project_id IS NULL AND resolution_task_id IS NULL
         AND closed_at IS NULL)
        OR
        (status = 'rejected' AND rejection_reason IS NOT NULL
         AND TRIM(rejection_reason) <> ''
         AND resolution_project_id IS NULL AND resolution_task_id IS NULL
         AND closed_at IS NOT NULL)
        OR
        (status = 'done' AND rejection_reason IS NULL
         AND resolution_project_id IS NOT NULL AND resolution_task_id IS NOT NULL
         AND closed_at IS NOT NULL)
    )
);

CREATE INDEX IF NOT EXISTS idx_retrospective_project
    ON retrospective_action(project_id, status, key);
CREATE INDEX IF NOT EXISTS idx_retrospective_iteration
    ON retrospective_action(iteration_id, key);
CREATE INDEX IF NOT EXISTS idx_retrospective_resolution
    ON retrospective_action(resolution_project_id, resolution_task_id);

-- Sections of a task: acceptance criteria, checklist entries, loose notes,
-- and flat ordered implementation todos.
CREATE TABLE IF NOT EXISTS task_item (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id     INTEGER NOT NULL REFERENCES task(id) ON DELETE CASCADE,
    kind        TEXT NOT NULL CHECK (kind IN ('acceptance_criteria','checklist','note','todo')),
    position    INTEGER NOT NULL DEFAULT 0,
    content     TEXT NOT NULL,
    done        INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    created_by  TEXT,
    updated_by  TEXT
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

-- An acceptance criterion is proven by review, never by an implementer's tick,
-- so its verification state lives here rather than in `task_item.done`: one
-- attributed, evidence-bearing verdict per criterion. `content_hash` records
-- the criterion text the verdict was given for, so editing the criterion makes
-- the verdict stale instead of silently inheriting it.
CREATE TABLE IF NOT EXISTS acceptance_verdict (
    item_id      INTEGER PRIMARY KEY REFERENCES task_item(id) ON DELETE CASCADE,
    state        TEXT NOT NULL CHECK (state IN ('met','unmet')),
    actor        TEXT NOT NULL,
    evidence     TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    created_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS execution_result (
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id                  INTEGER NOT NULL REFERENCES task_item(id) ON DELETE CASCADE,
    spec_fingerprint         TEXT NOT NULL,
    status                   TEXT NOT NULL CHECK (status IN ('pass','fail','error','skipped')),
    reason                   TEXT NOT NULL DEFAULT '',
    detail                   TEXT NOT NULL DEFAULT '',
    expected_result          TEXT,
    actual_result            TEXT,
    hook_name                TEXT,
    implementation_identity  TEXT,
    actual_exit_code         INTEGER,
    stdout                   TEXT NOT NULL DEFAULT '',
    stderr                   TEXT NOT NULL DEFAULT '',
    duration_ms              INTEGER NOT NULL DEFAULT 0,
    source_revision          TEXT,
    source_dirty_fingerprint TEXT,
    source_revision_unavailable INTEGER NOT NULL DEFAULT 0,
    actor                    TEXT NOT NULL DEFAULT 'unknown',
    started_at               TEXT NOT NULL,
    finished_at              TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_execution_result_item
    ON execution_result(item_id, id);

CREATE TABLE IF NOT EXISTS validation_waiver (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id          INTEGER NOT NULL REFERENCES task_item(id) ON DELETE CASCADE,
    spec_fingerprint TEXT NOT NULL,
    actor            TEXT NOT NULL,
    reason           TEXT NOT NULL,
    created_at       TEXT NOT NULL,
    superseded_at    TEXT
);

CREATE INDEX IF NOT EXISTS idx_validation_waiver_item
    ON validation_waiver(item_id, id);

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

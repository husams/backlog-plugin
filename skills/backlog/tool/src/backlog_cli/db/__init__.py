"""Backlog database connections, stores, migrations, and project helpers."""

from .common import (
    ARTIFACTS_DIR_NAME,
    BACKLOG_DIR_NAME,
    DB_NAME,
    DEFAULT_PG_SCHEMA,
    POSTGRES,
    SQLITE,
    BacklogError,
    Conn,
    Row,
    actor_kind,
    database_errors,
    split_statements,
    utcnow,
)
from .connection import connect
from .integrity import expected_schema, repair_schema, schema_drift
from .legacy import load_v2_export
from .migrations import migrate
from .projects import (
    get_or_create_project,
    get_project,
    init_store,
    list_projects,
    log_event,
    next_comment_key,
    next_key,
    require_project,
    resync_sequences,
)
from .resolution import (
    StoreSpec,
    default_project,
    find_backlog_dir,
    require_backlog_dir,
    resolve_spec,
    slugify,
)

__all__ = [name for name in globals() if not name.startswith("_")]

"""Core task, item, gate, pull-request, and artifact operations."""

OPEN_STATUSES = {
    "created",
    "incomplete",
    "ready",
    "in_progress",
    "in_review",
    "needs_work",
}
ACTIONABLE_BY_DEV = {"ready", "in_progress", "needs_work"}

from .artifacts import add_artifact, list_artifacts
from .gates import (
    Check,
    GATE_TARGETS,
    gate,
    gate_for_move,
    normalize_gate,
    run_checks,
    trigger_action,
)
from .items import add_item, remove_item, set_items, tick_item
from .normalization import (
    normalize_item_kind,
    normalize_key,
    normalize_priority,
    normalize_status,
    normalize_type,
    require_actor,
    require_independent_actor,
)
from .pull_requests import set_pr, sync_pr
from .iterations import add_iteration_member, iteration_members, remove_iteration_member
from .task_queries import (
    blocking_threads,
    children_of,
    find_task,
    get_task,
    get_task_by_id,
    open_threads,
    task_items,
)
from .tasks import (
    add_task,
    assign,
    update_task,
)
__all__ = [name for name in globals() if not name.startswith("_")]

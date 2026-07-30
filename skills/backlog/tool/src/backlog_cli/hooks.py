"""Action-driven transitions and optional project transition hooks."""

from __future__ import annotations

import hashlib
import importlib.util
import sys
from enum import Enum
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING, Any

import yaml

from .db import BacklogError, Conn, utcnow
from .schema import GATE_CHECKS, STATUS_CATEGORIES, TASK_TYPES

if TYPE_CHECKING:
    from .api import Backlog

Trigger = dict[str, Any]


class Action(str, Enum):
    ITEM_CREATED = "item.created"
    ITEM_UPDATED = "item.updated"
    ITEM_CANCELLED = "item.cancelled"
    ITEM_REOPENED = "item.reopened"
    ITEM_ARCHIVED = "item.archived"

    REFINEMENT_SUBMITTED = "refinement.submitted"
    REFINEMENT_MARKED_INCOMPLETE = "refinement.marked_incomplete"
    REFINEMENT_ACCEPTED = "refinement.accepted"

    WORK_STARTED = "work.started"
    WORK_PAUSED = "work.paused"
    WORK_RESUMED = "work.resumed"
    WORK_BLOCKED = "work.blocked"
    WORK_UNBLOCKED = "work.unblocked"
    WORK_COMPLETED = "work.completed"

    REVIEW_SUBMITTED = "review.submitted"
    REVIEW_APPROVED = "review.approved"
    REVIEW_CHANGES_REQUESTED = "review.changes_requested"
    REVIEW_DISMISSED = "review.dismissed"

    FEEDBACK_POSTED = "feedback.posted"
    FEEDBACK_ACCEPTED = "feedback.accepted"
    FEEDBACK_REJECTED = "feedback.rejected"
    FEEDBACK_REPLIED = "feedback.replied"
    FEEDBACK_RESOLVED = "feedback.resolved"
    FEEDBACK_REOPENED = "feedback.reopened"

    PR_CREATED = "pr.created"
    PR_UPDATED = "pr.updated"
    PR_MARKED_READY = "pr.marked_ready"
    PR_APPROVED = "pr.approved"
    PR_CHANGES_REQUESTED = "pr.changes_requested"
    PR_MERGED = "pr.merged"
    PR_CLOSED = "pr.closed"
    PR_REOPENED = "pr.reopened"

    CHECK_STARTED = "check.started"
    CHECK_PASSED = "check.passed"
    CHECK_FAILED = "check.failed"
    CHECK_CANCELLED = "check.cancelled"
    CHECK_TIMED_OUT = "check.timed_out"

    DELIVERY_ACCEPTED = "delivery.accepted"
    DELIVERY_REJECTED = "delivery.rejected"
    DELIVERY_RELEASED = "delivery.released"


def normalize_action(value: Action | str) -> Action:
    if isinstance(value, Action):
        return value
    text = str(value).strip()
    try:
        return Action(text.lower())
    except ValueError:
        try:
            return Action[text.upper().replace(".", "_").replace("-", "_")]
        except KeyError:
            raise BacklogError(
                f"unknown action {value!r}. Valid: "
                + ", ".join(action.value for action in Action)
            ) from None
def bundled_workflow_path() -> Path:
    return Path(__file__).resolve().parents[3] / "assets" / "default-workflow.yaml"


def project_backlog_dir(fallback: Path) -> Path:
    """Find repository hook/config files independently of store location."""
    current = Path.cwd().resolve()
    for root in (current, *current.parents):
        candidate = root / ".backlog"
        if candidate.is_dir():
            return candidate
    return fallback


def workflow_path(backlog_dir: Path) -> Path:
    custom = backlog_dir / "workflow.yaml"
    return custom if custom.is_file() else bundled_workflow_path()


def load_workflow(backlog_dir: Path) -> dict[str, Any]:
    path = workflow_path(backlog_dir)
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise BacklogError(f"cannot read workflow configuration {path}: {exc}") from None
    except yaml.YAMLError as exc:
        raise BacklogError(f"invalid workflow configuration {path}: {exc}") from None
    if not isinstance(data, dict):
        raise BacklogError(f"workflow configuration {path} must contain a mapping")
    states = data.get("states")
    transitions = data.get("transitions")
    if not isinstance(states, list) or not isinstance(transitions, list):
        raise BacklogError(f"workflow configuration {path} requires states and transitions lists")
    known_states = {
        row.get("slug") for row in states if isinstance(row, dict) and row.get("slug")
    }
    for index, row in enumerate(transitions, start=1):
        if not isinstance(row, dict):
            raise BacklogError(f"transition {index} in {path} must be a mapping")
        missing = {"task_types", "from", "action", "to"} - set(row)
        if missing:
            raise BacklogError(
                f"transition {index} in {path} is missing: {', '.join(sorted(missing))}"
            )
        normalize_action(row["action"])
        if row["from"] not in known_states or row["to"] not in known_states:
            raise BacklogError(
                f"transition {index} in {path} references an undefined state"
            )
    data["_path"] = str(path)
    return data


def apply_workflow(conn: Conn, project_id: int, backlog_dir: Path) -> bool:
    """Materialize a changed YAML configuration into the project's live flow."""
    data = load_workflow(backlog_dir)
    source = Path(data["_path"])
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    marker = f"action-workflow:{digest}"
    rows = conn.execute(
        "SELECT task_type, description FROM workflow WHERE project_id = ?",
        (project_id,),
    ).fetchall()
    if len(rows) == len(TASK_TYPES) and all(row["description"] == marker for row in rows):
        return False

    states = data["states"]
    state_names = {row["slug"] for row in states}
    invalid = conn.execute(
        "SELECT key, task_type, status FROM task WHERE project_id = ?",
        (project_id,),
    ).fetchall()
    invalid = [row for row in invalid if row["status"] not in state_names]
    if invalid:
        details = ", ".join(f"{row['key']}={row['status']}" for row in invalid[:10])
        raise BacklogError(
            f"cannot apply {source}: task states are not declared: {details}"
        )

    initial = [row["slug"] for row in states if row.get("initial")]
    if len(initial) != 1:
        raise BacklogError(f"{source} must define exactly one initial state")
    for row in states:
        category = row.get("category", "active")
        if category not in STATUS_CATEGORIES:
            raise BacklogError(
                f"state {row['slug']} in {source} has unknown category {category!r}"
            )

    transitions_by_type: dict[str, dict[tuple[str, str], str]] = {
        task_type: {} for task_type in TASK_TYPES
    }
    for row in data["transitions"]:
        gates = row.get("gates") or []
        if not isinstance(gates, list) or any(gate not in GATE_CHECKS for gate in gates):
            raise BacklogError(
                f"transition {row['from']} -> {row['to']} in {source} "
                "contains an unknown gate"
            )
        gate_text = ",".join(gates)
        for task_type in row["task_types"]:
            if task_type not in TASK_TYPES:
                raise BacklogError(
                    f"transition in {source} has unknown task type {task_type!r}"
                )
            pair = (row["from"], row["to"])
            previous = transitions_by_type[task_type].get(pair)
            if previous is not None and previous != gate_text:
                raise BacklogError(
                    f"{source} assigns different gates to {task_type} "
                    f"{row['from']} -> {row['to']}"
                )
            transitions_by_type[task_type][pair] = gate_text

    ts = utcnow()
    conn.execute("DELETE FROM workflow WHERE project_id = ?", (project_id,))
    for task_type in TASK_TYPES:
        workflow_id = conn.insert_returning_id(
            "INSERT INTO workflow(project_id, task_type, name, description, created_at, "
            "updated_at) VALUES(?,?,?,?,?,?)",
            (project_id, task_type, data.get("name", "action workflow"), marker, ts, ts),
        )
        conn.executemany(
            "INSERT INTO workflow_status(workflow_id, slug, display, category, position, "
            "satisfies_dependency, is_initial, is_terminal, description) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            [
                (
                    workflow_id,
                    row["slug"],
                    row.get("display", row["slug"].replace("_", " ").title()),
                    row.get("category", "active"),
                    position,
                    int(bool(row.get("satisfies_dependencies"))),
                    int(bool(row.get("initial"))),
                    int(bool(row.get("terminal"))),
                    row.get("description", ""),
                )
                for position, row in enumerate(states)
            ],
        )
        conn.executemany(
            "INSERT INTO workflow_transition(workflow_id, from_status, to_status, gates) "
            "VALUES(?,?,?,?)",
            [
                (workflow_id, from_state, to_state, gates)
                for (from_state, to_state), gates
                in transitions_by_type[task_type].items()
            ],
        )
    conn.commit()
    return True


def resolve_transition(
    backlog_dir: Path,
    task_type: str,
    current_state: str,
    action: Action | str,
) -> str | None:
    wanted = normalize_action(action).value
    matches = []
    for row in load_workflow(backlog_dir)["transitions"]:
        if (
            task_type in row["task_types"]
            and row["from"] == current_state
            and row["action"] == wanted
        ):
            matches.append(row["to"])
    if len(matches) > 1:
        raise BacklogError(
            f"workflow has more than one transition for "
            f"{task_type} + {current_state} + {wanted}"
        )
    return matches[0] if matches else None


def available_actions(backlog_dir: Path, task_type: str,
                      current_state: str) -> list[Action]:
    """Semantic actions configured for this task type and current state."""
    found = {
        normalize_action(row["action"])
        for row in load_workflow(backlog_dir)["transitions"]
        if task_type in row["task_types"] and row["from"] == current_state
    }
    return sorted(found, key=lambda action: action.value)


def _load_project_hooks(backlog_dir: Path) -> ModuleType | None:
    package_dir = backlog_dir / "hooks"
    path = package_dir / "__init__.py"
    if not path.is_file():
        return None
    digest = hashlib.sha256(str(package_dir.resolve()).encode()).hexdigest()[:16]
    name = f"_backlog_project_hooks_{digest}"
    spec = importlib.util.spec_from_file_location(
        name,
        path,
        submodule_search_locations=[str(package_dir)],
    )
    if spec is None or spec.loader is None:
        raise BacklogError(f"cannot load project hooks from {path}")
    for loaded_name in [
        loaded_name for loaded_name in sys.modules
        if loaded_name == name or loaded_name.startswith(f"{name}.")
    ]:
        del sys.modules[loaded_name]
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    project_root = str(backlog_dir.parent)
    added = project_root not in sys.path
    if added:
        sys.path.insert(0, project_root)
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        sys.modules.pop(name, None)
        raise BacklogError(f"cannot load project hooks from {path}: {exc}") from None
    finally:
        if added:
            sys.path.remove(project_root)
    return module


def pre_transition(
    backlog_dir: Path,
    action: Action,
    trigger: Trigger,
    current_state: str,
    new_state: str,
    backlog: "Backlog",
) -> str:
    module = _load_project_hooks(backlog_dir)
    callback = getattr(module, "pre_transition", None) if module else None
    if callback is None:
        return new_state
    if not callable(callback):
        raise BacklogError(
            f"{backlog_dir / 'hooks' / '__init__.py'}: pre_transition is not callable"
        )
    try:
        result = callback(action, trigger, current_state, new_state, backlog)
    except Exception as exc:
        raise BacklogError(f"pre_transition blocked the transition: {exc}") from None
    if not isinstance(result, str) or not result.strip():
        raise BacklogError("pre_transition must return a non-empty state string")
    return result


def post_transition(
    backlog_dir: Path,
    action: Action,
    trigger: Trigger,
    previous_state: str,
    current_state: str,
    backlog: "Backlog",
) -> None:
    module = _load_project_hooks(backlog_dir)
    callback = getattr(module, "post_transition", None) if module else None
    if callback is None:
        return
    if not callable(callback):
        raise BacklogError(
            f"{backlog_dir / 'hooks' / '__init__.py'}: post_transition is not callable"
        )
    try:
        callback(action, trigger, previous_state, current_state, backlog)
    except Exception as exc:
        raise BacklogError(
            f"transition committed, but post_transition failed: {exc}"
        ) from None

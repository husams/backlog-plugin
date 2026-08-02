"""Action-workflow configuration loading and materialization."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import yaml

from ..db import BacklogError, Conn, utcnow
from ..schema import GATE_CHECKS, STATUS_CATEGORIES, TASK_TYPES
from .actions import Action, THREAD_MANAGED_ACTIONS, normalize_action


def bundled_workflow_path() -> Path:
    return Path(__file__).resolve().parents[4] / "assets" / "default-workflow.yaml"


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
    except yaml.YAMLError as exc:
        raise BacklogError(f"invalid workflow configuration {path}: {exc}") from None
    if not isinstance(data, dict):
        raise BacklogError(f"workflow configuration {path} must contain a mapping")
    states = data.get("states")
    transitions = data.get("transitions")
    if not isinstance(states, list) or not isinstance(transitions, list):
        raise BacklogError(
            f"workflow configuration {path} requires states and transitions lists"
        )
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
    current_types = {row["task_type"] for row in rows if row["description"] == marker}
    if len(current_types) == len(TASK_TYPES):
        return False
    task_types = (
        [task_type for task_type in TASK_TYPES if task_type not in current_types]
        if current_types
        else TASK_TYPES
    )

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

    for row in states:
        declared_types = row.get("task_types", TASK_TYPES)
        if not isinstance(declared_types, list) or any(
            t not in TASK_TYPES for t in declared_types
        ):
            raise BacklogError(
                f"state {row['slug']} in {source} has invalid task_types"
            )
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
        if not isinstance(gates, list) or any(
            gate not in GATE_CHECKS for gate in gates
        ):
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
    if current_types:
        for task_type in task_types:
            conn.execute(
                "DELETE FROM workflow WHERE project_id = ? AND task_type = ?",
                (project_id, task_type),
            )
    else:
        conn.execute("DELETE FROM workflow WHERE project_id = ?", (project_id,))
    for task_type in task_types:
        typed_states = [
            row for row in states if task_type in row.get("task_types", TASK_TYPES)
        ]
        initial = [row["slug"] for row in typed_states if row.get("initial")]
        if len(initial) != 1:
            raise BacklogError(
                f"{source} must define exactly one initial state for {task_type}"
            )
        workflow_id = conn.insert_returning_id(
            "INSERT INTO workflow(project_id, task_type, name, description, created_at, "
            "updated_at) VALUES(?,?,?,?,?,?)",
            (
                project_id,
                task_type,
                data.get("name", "action workflow"),
                marker,
                ts,
                ts,
            ),
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
                for position, row in enumerate(typed_states)
            ],
        )
        conn.executemany(
            "INSERT INTO workflow_transition(workflow_id, from_status, to_status, gates) "
            "VALUES(?,?,?,?)",
            [
                (workflow_id, from_state, to_state, gates)
                for (from_state, to_state), gates in transitions_by_type[
                    task_type
                ].items()
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


def available_actions(
    backlog_dir: Path, task_type: str, current_state: str
) -> list[Action]:
    """Semantic actions configured for this task type and current state."""
    found = {
        normalize_action(row["action"])
        for row in load_workflow(backlog_dir)["transitions"]
        if task_type in row["task_types"] and row["from"] == current_state
    }
    return sorted(
        (action for action in found if action not in THREAD_MANAGED_ACTIONS),
        key=lambda action: action.value,
    )

"""Named workflow gate checks."""

from __future__ import annotations

from .. import deps, workflow
from ..db import BacklogError, Conn, Row
from ..schema import PR_BEARING_TYPES, STATUS_DISPLAY
from .acceptance import criteria_state
from .iterations import iteration_members
from .task_queries import blocking_threads, children_of, open_threads

# What each public gate target demands. A target is a name for a set of named
# checks, nothing more — the checks themselves are defined once, in
# `run_checks`, so `gate()` and a workflow transition can never drift apart.
_ACCEPTED_CHECKS = [
    "required_validations_pass",
    "review_threads_closed",
    "pr_approved",
    "children_complete",
    "todos_closed",
    "acceptance_criteria_verified",
]

GATE_TARGET_CHECKS: dict[str, list[str]] = {
    "start": ["dependencies_clear", "review_threads_closed"],
    "in_review": ["todos_closed", "pr_recorded"],
    "accepted": _ACCEPTED_CHECKS,
    "done": [*_ACCEPTED_CHECKS, "status_accepted", "pr_merged"],
    "merge": ["status_accepted", *_ACCEPTED_CHECKS],
}

GATE_TARGETS = list(GATE_TARGET_CHECKS)
_GATE_ALIASES = {"in_progress": "start", "begin": "start"}

# An Iteration is a container: the work carrying acceptance criteria is its
# members, so the criteria gate does not apply to the Iteration itself.
_CRITERIA_EXEMPT_TYPES = {"iteration"}


class Check:
    __slots__ = ("name", "ok", "detail")

    def __init__(self, name: str, ok: bool, detail: str = ""):
        self.name, self.ok, self.detail = name, ok, detail

    def as_dict(self) -> dict:
        return {"check": self.name, "ok": self.ok, "detail": self.detail}


def normalize_gate(target: str) -> str:
    t = target.strip().lower().replace("-", "_")
    t = _GATE_ALIASES.get(t, t)
    if t not in GATE_TARGETS:
        raise BacklogError(f"unknown gate {target!r}. Valid: {', '.join(GATE_TARGETS)}")
    return t


def run_checks(
    conn: Conn,
    project_id: int,
    task: Row,
    names: list[str],
    allow_open_children: bool = False,
    no_pr: bool = False,
    allow_blocked: bool = False,
) -> list[Check]:
    """Evaluate a named set of gate checks against one task.

    The names come from the project's workflow, so which checks apply to which
    move is configuration; what each one *means* is here.
    """
    wf = workflow.get(conn, project_id, task["task_type"])
    waived = bool(task["pr_waived"]) or no_pr
    has_pr = bool(task["pr_url"] or task["pr_number"])
    bears_pr = task["task_type"] in PR_BEARING_TYPES
    checks: list[Check] = []

    for name in names:
        if name == "dependencies_clear":
            blockers = deps.blockers(conn, task["id"])
            checks.append(
                Check(
                    name,
                    not blockers or allow_blocked,
                    "no unfinished blockers"
                    if not blockers
                    else (
                        "waived (--allow-blocked): " if allow_blocked else "blocked by "
                    )
                    + ", ".join(
                        f"{b['other_key']}={b['other_status']}" for b in blockers
                    ),
                )
            )
        elif name == "children_complete":
            kids = children_of(conn, task["id"])
            open_kids = [
                k
                for k in kids
                if not workflow.get(conn, project_id, k["task_type"]).satisfies(
                    k["status"]
                )
            ]
            checks.append(
                Check(
                    name,
                    not open_kids or allow_open_children,
                    "no open children"
                    if not open_kids
                    else (
                        "waived (--allow-open-subtasks): "
                        if allow_open_children
                        else ""
                    )
                    + ", ".join(f"{k['key']}={k['status']}" for k in open_kids),
                )
            )
        elif name == "review_threads_closed":
            opens = blocking_threads(conn, task["id"])
            checks.append(
                Check(
                    name,
                    not opens,
                    "no blocking review threads open"
                    if not opens
                    else f"{len(opens)} blocking: "
                    + ", ".join(t["root_key"] for t in opens),
                )
            )
        elif name == "iteration_comments_closed":
            opens = open_threads(conn, task["id"])
            checks.append(
                Check(
                    name,
                    not opens,
                    "no Iteration comments open"
                    if not opens
                    else f"{len(opens)} open: "
                    + ", ".join(t["root_key"] for t in opens),
                )
            )
        elif name == "iteration_retrospective_actions_clear":
            created = conn.execute(
                "SELECT key FROM retrospective_action "
                "WHERE project_id=? AND iteration_id=? AND status='created' ORDER BY key",
                (project_id, task["id"]),
            ).fetchall()
            checks.append(
                Check(
                    name,
                    not created,
                    "no Created retrospective actions"
                    if not created
                    else "Created retrospective actions: "
                    + ", ".join(row["key"] for row in created),
                )
            )
        elif name == "pr_recorded":
            if not bears_pr:
                checks.append(
                    Check(name, True, f"not applicable to a {task['task_type']}")
                )
            else:
                checks.append(
                    Check(
                        name,
                        has_pr or waived,
                        task["pr_url"]
                        or (
                            "waived (--no-pr)" if waived else "no PR reference recorded"
                        ),
                    )
                )
        elif name == "pr_approved":
            if not bears_pr:
                checks.append(
                    Check(name, True, f"not applicable to a {task['task_type']}")
                )
            elif waived and not has_pr:
                checks.append(Check(name, True, "waived (--no-pr, no PR reference)"))
            else:
                checks.append(
                    Check(
                        name,
                        task["pr_review_state"] == "approved",
                        f"pr_review_state={task['pr_review_state']}"
                        + ("" if has_pr else " (no PR recorded)"),
                    )
                )
        elif name == "pr_merged":
            if not bears_pr:
                checks.append(
                    Check(name, True, f"not applicable to a {task['task_type']}")
                )
            elif waived and not has_pr:
                checks.append(Check(name, True, "waived (--no-pr, no PR reference)"))
            else:
                checks.append(
                    Check(
                        name,
                        task["pr_state"] == "merged",
                        f"pr_state={task['pr_state']}",
                    )
                )
        elif name == "required_validations_pass":
            from pathlib import Path
            from ..execution import required_validations_pass

            ok, pending_or_failed = required_validations_pass(
                conn, task["id"], Path.cwd()
            )
            checks.append(
                Check(
                    name,
                    ok,
                    "all required executable items have a current pass or audited waiver"
                    if ok
                    else "pending or non-passing required items: "
                    + ", ".join(f"#{item_id}" for item_id in pending_or_failed),
                )
            )
        elif name == "iteration_members_finished":
            members = iteration_members(conn, task["id"])
            unfinished = [
                m
                for m in members
                if not workflow.get(conn, project_id, m["task_type"]).satisfies(
                    m["status"]
                )
            ]
            checks.append(
                Check(
                    name,
                    not unfinished,
                    "all members finished"
                    if not unfinished
                    else "unfinished members: "
                    + ", ".join(f"{m['key']}={m['status']}" for m in unfinished),
                )
            )
        elif name == "todos_closed":
            open_todos = conn.execute(
                "SELECT id,content FROM task_item "
                "WHERE task_id=? AND kind='todo' AND done=0 ORDER BY position,id",
                (task["id"],),
            ).fetchall()
            checks.append(
                Check(
                    name,
                    not open_todos,
                    "all todos closed"
                    if not open_todos
                    else "open todos: "
                    + "; ".join(
                        f"#{todo['id']} {todo['content']}" for todo in open_todos
                    ),
                )
            )
        elif name == "acceptance_criteria_verified":
            if task["task_type"] in _CRITERIA_EXEMPT_TYPES:
                checks.append(Check(name, True, "not applicable to an Iteration"))
                continue
            criteria = criteria_state(conn, task["id"])
            outstanding = [c for c in criteria if c["state"] != "met"]
            if not criteria:
                # An empty list must never pass: unrecorded criteria are not
                # satisfied criteria, they are criteria nobody wrote down.
                detail = "no acceptance criteria recorded"
            elif outstanding:
                detail = "unverified or unmet criteria: " + "; ".join(
                    _criterion_summary(c) for c in outstanding
                )
            else:
                detail = f"all {len(criteria)} acceptance criteria verified"
            checks.append(Check(name, bool(criteria) and not outstanding, detail))
        elif name == "status_accepted":
            checks.append(
                Check(
                    name,
                    task["status"] == "accepted",
                    f"status={STATUS_DISPLAY.get(task['status'], task['status'])}",
                )
            )
    return checks


def _criterion_summary(criterion: dict) -> str:
    """`#12 the API rejects an empty body (unverified, stale)`."""
    content = criterion["content"]
    if len(content) > 60:
        content = content[:57] + "..."
    state = criterion["state"]
    if criterion["stale"]:
        state += ", stale"
    return f"#{criterion['id']} {content} ({state})"

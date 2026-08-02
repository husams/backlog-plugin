"""Named workflow gate checks."""

from __future__ import annotations

from .. import deps, workflow
from ..db import BacklogError, Conn, Row
from ..schema import PR_BEARING_TYPES
from .iterations import iteration_members
from .task_queries import blocking_threads, children_of, open_threads

GATE_TARGETS = ["start", "in_review", "accepted", "done", "merge"]
_GATE_ALIASES = {"in_progress": "start", "begin": "start"}


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
    return checks

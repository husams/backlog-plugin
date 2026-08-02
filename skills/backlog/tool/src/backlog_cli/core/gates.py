"""Completion gates and semantic workflow transitions."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .. import deps, workflow
from ..db import BacklogError, Conn, Row, actor_kind, log_event, require_backlog_dir, utcnow
from ..schema import PR_BEARING_TYPES, PR_REVIEW_STATES, SATISFIED_STATUSES, STATUS_DISPLAY
from .normalization import normalize_status, require_independent_actor
from .tasks import (
    blocking_threads,
    children_of,
    get_task,
    get_task_by_id,
    iteration_members,
    open_threads,
)

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


def run_checks(conn: Conn, project_id: int, task: Row, names: list[str],
               allow_open_children: bool = False, no_pr: bool = False,
               allow_blocked: bool = False) -> list[Check]:
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
            checks.append(Check(
                name, not blockers or allow_blocked,
                "no unfinished blockers" if not blockers
                else ("waived (--allow-blocked): " if allow_blocked else "blocked by ")
                + ", ".join(f"{b['other_key']}={b['other_status']}" for b in blockers),
            ))
        elif name == "children_complete":
            kids = children_of(conn, task["id"])
            open_kids = [k for k in kids
                         if not workflow.get(conn, project_id, k["task_type"]).satisfies(k["status"])]
            checks.append(Check(
                name, not open_kids or allow_open_children,
                "no open children" if not open_kids
                else ("waived (--allow-open-subtasks): " if allow_open_children else "")
                + ", ".join(f"{k['key']}={k['status']}" for k in open_kids),
            ))
        elif name == "review_threads_closed":
            opens = blocking_threads(conn, task["id"])
            checks.append(Check(
                name, not opens,
                "no blocking review threads open" if not opens
                else f"{len(opens)} blocking: " + ", ".join(t["root_key"] for t in opens),
            ))
        elif name == "iteration_comments_closed":
            opens = open_threads(conn, task["id"])
            checks.append(Check(
                name, not opens,
                "no Iteration comments open" if not opens
                else f"{len(opens)} open: " + ", ".join(t["root_key"] for t in opens),
            ))
        elif name == "iteration_retrospective_actions_clear":
            created = conn.execute(
                "SELECT key FROM retrospective_action "
                "WHERE project_id=? AND iteration_id=? AND status='created' ORDER BY key",
                (project_id, task["id"]),
            ).fetchall()
            checks.append(Check(
                name, not created,
                "no Created retrospective actions" if not created
                else "Created retrospective actions: "
                + ", ".join(row["key"] for row in created),
            ))
        elif name == "pr_recorded":
            if not bears_pr:
                checks.append(Check(name, True, f"not applicable to a {task['task_type']}"))
            else:
                checks.append(Check(
                    name, has_pr or waived,
                    task["pr_url"] or ("waived (--no-pr)" if waived
                                       else "no PR reference recorded"),
                ))
        elif name == "pr_approved":
            if not bears_pr:
                checks.append(Check(name, True, f"not applicable to a {task['task_type']}"))
            elif waived and not has_pr:
                checks.append(Check(name, True, "waived (--no-pr, no PR reference)"))
            else:
                checks.append(Check(
                    name, task["pr_review_state"] == "approved",
                    f"pr_review_state={task['pr_review_state']}"
                    + ("" if has_pr else " (no PR recorded)"),
                ))
        elif name == "pr_merged":
            if not bears_pr:
                checks.append(Check(name, True, f"not applicable to a {task['task_type']}"))
            elif waived and not has_pr:
                checks.append(Check(name, True, "waived (--no-pr, no PR reference)"))
            else:
                checks.append(Check(name, task["pr_state"] == "merged",
                                    f"pr_state={task['pr_state']}"))
        elif name == "required_validations_pass":
            from pathlib import Path
            from ..execution import required_validations_pass

            ok, pending_or_failed = required_validations_pass(
                conn, task["id"], Path.cwd()
            )
            checks.append(Check(
                name, ok,
                "all required executable items have a current pass or audited waiver" if ok
                else "pending or non-passing required items: "
                + ", ".join(f"#{item_id}" for item_id in pending_or_failed),
            ))
        elif name == "iteration_members_finished":
            members = iteration_members(conn, task["id"])
            unfinished = [m for m in members if not workflow.get(
                conn, project_id, m["task_type"]
            ).satisfies(m["status"])]
            if task["status"] == "closed":
                conflicts = conn.execute(
                    "SELECT DISTINCT member.key AS member_key,i.key AS iteration_key "
                    "FROM iteration_member mine "
                    "JOIN task member ON member.id=mine.member_id "
                    "JOIN iteration_member other ON other.member_id=mine.member_id "
                    "JOIN task i ON i.id=other.iteration_id "
                    "WHERE mine.iteration_id=? AND other.iteration_id!=? AND i.status='open' "
                    "ORDER BY member.key,i.key", (task["id"], task["id"]),
                ).fetchall()
                checks.append(Check(
                    name, not conflicts,
                    "no membership conflicts" if not conflicts else
                    "members also belong to open Iterations: " + ", ".join(
                        f"{r['member_key']} in {r['iteration_key']}" for r in conflicts),
                ))
            else:
                checks.append(Check(
                    name, not unfinished,
                    "all members finished" if not unfinished else "unfinished members: "
                    + ", ".join(f"{m['key']}={m['status']}" for m in unfinished),
                ))
        else:
            checks.append(Check(name, False, "unknown gate check"))
    return checks


def gate_for_move(conn: Conn, project_id: int, key: str, to_status: str,
                  allow_open_children: bool = False, no_pr: bool = False,
                  allow_blocked: bool = False) -> tuple[bool, list[Check], str]:
    """The gate for one concrete move, as the project's workflow defines it."""
    task = get_task(conn, project_id, key)
    wf = workflow.get(conn, project_id, task["task_type"])
    target = wf.resolve(to_status)
    names = wf.gates_for(task["status"], target)
    checks = run_checks(conn, project_id, task, names,
                        allow_open_children=allow_open_children, no_pr=no_pr,
                        allow_blocked=allow_blocked)
    return all(c.ok for c in checks), checks, target


def gate(conn: Conn, project_id: int, key: str, target: str,
         allow_open_children: bool = False, no_pr: bool = False,
         allow_blocked: bool = False) -> tuple[bool, list[Check]]:
    """Evaluate the gate for `start`, `in_review`, `accepted`, `done` or `merge`.

    Which checks apply depends on the task type: a feature carries no pull
    request of its own, so the PR checks are simply not part of its flow.
    """
    task = get_task(conn, project_id, key)
    target = normalize_gate(target)
    ttype = task["task_type"]
    bears_pr = ttype in PR_BEARING_TYPES
    checks: list[Check] = []
    waived = bool(task["pr_waived"]) or no_pr
    has_pr = bool(task["pr_url"] or task["pr_number"])

    if target == "start":
        blockers = deps.blockers(conn, task["id"])
        checks.append(Check(
            "dependencies_clear",
            not blockers or allow_blocked,
            "no unfinished blockers" if not blockers
            else ("waived (--allow-blocked): " if allow_blocked else "blocked by ")
            + ", ".join(f"{b['other_key']}={b['other_status']}" for b in blockers),
        ))
        opens = blocking_threads(conn, task["id"])
        checks.append(Check(
            "review_threads_closed", not opens,
            "no blocking review threads open" if not opens
            else f"{len(opens)} blocking: " + ", ".join(t["root_key"] for t in opens),
        ))

    if target in ("accepted", "merge"):
        from ..execution import required_validations_pass

        validations_ok, pending_or_failed = required_validations_pass(conn, task["id"])
        checks.append(Check(
            "required_validations_pass", validations_ok,
            "all required executable items have a fresh pass" if validations_ok
            else "pending or non-passing required items: "
            + ", ".join(f"#{item_id}" for item_id in pending_or_failed),
        ))
        opens = blocking_threads(conn, task["id"])
        checks.append(Check(
            "review_threads_closed", not opens,
            "no blocking review threads open" if not opens
            else f"{len(opens)} blocking: " + ", ".join(t["root_key"] for t in opens),
        ))
        if bears_pr:
            if waived and not has_pr:
                checks.append(Check("pr_approved", True, "waived (--no-pr, no PR reference)"))
            else:
                checks.append(Check(
                    "pr_approved", task["pr_review_state"] == "approved",
                    f"pr_review_state={task['pr_review_state']}"
                    + ("" if has_pr else " (no PR recorded)"),
                ))
        kids = children_of(conn, task["id"])
        if kids:
            open_kids = [k for k in kids if k["status"] not in SATISFIED_STATUSES]
            label = "subtasks" if ttype in ("story", "bug") else "stories"
            checks.append(Check(
                "children_complete",
                not open_kids or allow_open_children,
                f"no open {label}" if not open_kids
                else ("waived (--allow-open-subtasks): " if allow_open_children else "")
                + ", ".join(f"{k['key']}={k['status']}" for k in open_kids),
            ))

    if target == "merge":
        checks.insert(0, Check("status_accepted", task["status"] == "accepted",
                               f"status={STATUS_DISPLAY.get(task['status'], task['status'])}"))

    if target == "done":
        checks.append(Check("status_accepted", task["status"] == "accepted",
                            f"status={STATUS_DISPLAY.get(task['status'], task['status'])}"))
        if bears_pr:
            if waived and not has_pr:
                checks.append(Check("pr_merged", True, "waived (--no-pr, no PR reference)"))
            else:
                checks.append(Check("pr_merged", task["pr_state"] == "merged",
                                    f"pr_state={task['pr_state']}"))

    if target == "in_review":
        if bears_pr:
            checks.append(Check(
                "pr_recorded", has_pr or waived,
                task["pr_url"] or ("waived (--no-pr)" if waived else "no PR reference recorded"),
            ))
        else:
            checks.append(Check("pr_recorded", True, f"not applicable to a {ttype}"))

    return all(c.ok for c in checks), checks


def _transition(conn: Conn, project_id: int, key: str, to_status: str,
                actor: str | None = None, reason: str = "", no_pr: bool = False,
                allow_open_children: bool = False,
                allow_blocked: bool = False, action=None,
                trigger: dict[str, Any] | None = None) -> tuple[Row, list[Check]]:
    """Private transition executor reached only after resolving an action."""
    task = get_task(conn, project_id, key)
    wf = workflow.get(conn, project_id, task["task_type"])
    target = wf.resolve(to_status) if _known(wf, to_status) else normalize_status(to_status, wf)
    current = task["status"]

    from .. import hooks
    from ..api import Backlog

    if action is None:
        raise BacklogError("internal transition requires a semantic action")
    hook_action = hooks.normalize_action(action)
    hook_trigger = dict(trigger or {})
    hook_trigger.setdefault("operation", "action")
    hook_trigger.setdefault("actor", actor)
    hook_trigger.setdefault("task_key", task["key"])
    hook_trigger.setdefault("parameters", {})
    hook_trigger["parameters"].setdefault("resolved_state", target)
    hook_trigger["parameters"].setdefault("reason", reason)
    backlog_dir = hooks.project_backlog_dir(require_backlog_dir())
    project = conn.execute(
        "SELECT * FROM project WHERE id = ?", (project_id,)
    ).fetchone()
    assert project is not None and conn.spec is not None
    hook_backlog = Backlog(conn, project, conn.spec, actor=actor)
    proposed = hooks.pre_transition(
        backlog_dir, hook_action, hook_trigger, current, target, hook_backlog
    )
    target = wf.resolve(proposed)

    if target == current:
        log_event(
            conn, "transition_skipped", project_id, task["id"], task["key"], actor,
            from_value=current, to_value=current,
            detail=f"{hook_action.value}: pre_transition kept current state",
        )
        conn.commit()
        return get_task_by_id(conn, task["id"]), []
    if not wf.allows(current, target):
        legal = ", ".join(sorted(wf.display(t) for t in wf.next_from(current))) \
            or "(none — terminal state)"
        raise BacklogError(
            f"illegal transition for {task['key']} (a {task['task_type']}): "
            f"{wf.display(current)} -> {wf.display(target)}.\n"
            f"Legal next states from {wf.display(current)}: {legal}\n"
            f"(`backlog workflow show --type {task['task_type']}` prints this project's flow)"
        )

    names = wf.gates_for(current, target)
    checks = run_checks(conn, project_id, task, names,
                        allow_open_children=allow_open_children, no_pr=no_pr,
                        allow_blocked=allow_blocked)
    if not all(c.ok for c in checks):
        failed = "\n".join(f"  FAIL {c.name}: {c.detail}" for c in checks if not c.ok)
        raise BacklogError(
            f"gate for {wf.display(target)} not satisfied on {task['key']}:\n{failed}"
        )

    ts = utcnow()
    if no_pr and "pr_recorded" in names:
        conn.execute("UPDATE task SET pr_waived = 1 WHERE id = ?", (task["id"],))
    closed = ts if wf.category(target) in ("done", "dropped") else None
    conn.execute(
        "UPDATE task SET status = ?, updated_at = ?, closed_at = ? "
        "WHERE id = ?",
        (target, ts, closed, task["id"]),
    )
    log_event(conn, "status", project_id, task["id"], task["key"], actor,
              from_value=current, to_value=target, detail=reason)
    conn.commit()
    updated = get_task_by_id(conn, task["id"])
    hooks.post_transition(
        backlog_dir, hook_action, hook_trigger, current, updated["status"], hook_backlog
    )
    return updated, checks


def trigger_action(
    conn: Conn,
    project_id: int,
    key: str,
    action,
    *,
    actor: str | None = None,
    operation: str = "api.trigger",
    parameters: dict[str, Any] | None = None,
    no_pr: bool = False,
    allow_open_children: bool = False,
    allow_blocked: bool = False,
) -> tuple[Row, list[Check], bool]:
    """Submit a semantic action and let the configured workflow select state."""
    from .. import hooks

    task = get_task(conn, project_id, key)
    action = hooks.normalize_action(action)
    if action is hooks.Action.REFINEMENT_ACCEPTED:
        actor = require_independent_actor(
            task["key"], task["created_by"], actor, "refinement.accepted"
        )
    trigger = {
        "operation": operation,
        "actor": actor,
        "task_key": task["key"],
        "parameters": dict(parameters or {}),
    }
    backlog_dir = hooks.project_backlog_dir(require_backlog_dir())
    destination = hooks.resolve_transition(
        backlog_dir, task["task_type"], task["status"], action
    )
    log_event(
        conn, "action", project_id, task["id"], task["key"], actor,
        from_value=task["status"], to_value=action.value,
        detail=operation,
    )
    conn.commit()
    if destination is None:
        return get_task_by_id(conn, task["id"]), [], False
    if destination == task["status"]:
        return get_task_by_id(conn, task["id"]), [], False
    row, checks = _transition(
        conn,
        project_id,
        task["key"],
        destination,
        actor=actor,
        reason=f"{action.value} via {operation}",
        no_pr=no_pr,
        allow_open_children=allow_open_children,
        allow_blocked=allow_blocked,
        action=action,
        trigger=trigger,
    )
    return row, checks, row["status"] != task["status"]


def _known(wf, value: str) -> bool:
    try:
        wf.resolve(value)
        return True
    except BacklogError:
        return False

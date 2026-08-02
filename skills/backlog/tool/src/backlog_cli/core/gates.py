"""Gate evaluation for concrete moves and public targets."""

from __future__ import annotations

from .. import deps
from ..db import Conn
from ..schema import PR_BEARING_TYPES, SATISFIED_STATUSES, STATUS_DISPLAY
from .checks import Check, normalize_gate
from .task_queries import blocking_threads, children_of, get_task


def gate(
    conn: Conn,
    project_id: int,
    key: str,
    target: str,
    allow_open_children: bool = False,
    no_pr: bool = False,
    allow_blocked: bool = False,
) -> tuple[bool, list[Check]]:
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
        checks.append(
            Check(
                "dependencies_clear",
                not blockers or allow_blocked,
                "no unfinished blockers"
                if not blockers
                else ("waived (--allow-blocked): " if allow_blocked else "blocked by ")
                + ", ".join(f"{b['other_key']}={b['other_status']}" for b in blockers),
            )
        )
        opens = blocking_threads(conn, task["id"])
        checks.append(
            Check(
                "review_threads_closed",
                not opens,
                "no blocking review threads open"
                if not opens
                else f"{len(opens)} blocking: "
                + ", ".join(t["root_key"] for t in opens),
            )
        )

    if target in ("accepted", "merge"):
        from ..execution import required_validations_pass

        validations_ok, pending_or_failed = required_validations_pass(conn, task["id"])
        checks.append(
            Check(
                "required_validations_pass",
                validations_ok,
                "all required executable items have a fresh pass"
                if validations_ok
                else "pending or non-passing required items: "
                + ", ".join(f"#{item_id}" for item_id in pending_or_failed),
            )
        )
        opens = blocking_threads(conn, task["id"])
        checks.append(
            Check(
                "review_threads_closed",
                not opens,
                "no blocking review threads open"
                if not opens
                else f"{len(opens)} blocking: "
                + ", ".join(t["root_key"] for t in opens),
            )
        )
        if bears_pr:
            if waived and not has_pr:
                checks.append(
                    Check("pr_approved", True, "waived (--no-pr, no PR reference)")
                )
            else:
                checks.append(
                    Check(
                        "pr_approved",
                        task["pr_review_state"] == "approved",
                        f"pr_review_state={task['pr_review_state']}"
                        + ("" if has_pr else " (no PR recorded)"),
                    )
                )
        kids = children_of(conn, task["id"])
        if kids:
            open_kids = [k for k in kids if k["status"] not in SATISFIED_STATUSES]
            label = "subtasks" if ttype in ("story", "bug") else "stories"
            checks.append(
                Check(
                    "children_complete",
                    not open_kids or allow_open_children,
                    f"no open {label}"
                    if not open_kids
                    else (
                        "waived (--allow-open-subtasks): "
                        if allow_open_children
                        else ""
                    )
                    + ", ".join(f"{k['key']}={k['status']}" for k in open_kids),
                )
            )

    if target == "merge":
        checks.insert(
            0,
            Check(
                "status_accepted",
                task["status"] == "accepted",
                f"status={STATUS_DISPLAY.get(task['status'], task['status'])}",
            ),
        )

    if target == "done":
        checks.append(
            Check(
                "status_accepted",
                task["status"] == "accepted",
                f"status={STATUS_DISPLAY.get(task['status'], task['status'])}",
            )
        )
        if bears_pr:
            if waived and not has_pr:
                checks.append(
                    Check("pr_merged", True, "waived (--no-pr, no PR reference)")
                )
            else:
                checks.append(
                    Check(
                        "pr_merged",
                        task["pr_state"] == "merged",
                        f"pr_state={task['pr_state']}",
                    )
                )

    if target == "in_review":
        if bears_pr:
            checks.append(
                Check(
                    "pr_recorded",
                    has_pr or waived,
                    task["pr_url"]
                    or ("waived (--no-pr)" if waived else "no PR reference recorded"),
                )
            )
        else:
            checks.append(Check("pr_recorded", True, f"not applicable to a {ttype}"))

    return all(c.ok for c in checks), checks

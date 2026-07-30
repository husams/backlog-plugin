"""Tasks, their sections, status transitions and the completion gates.

One `task` table carries features, stories and subtasks. The status vocabulary
is shared, but the legal flow and the gates are per task type — a story is
delivered through review and a pull request, a feature is a container whose
progress is the progress of its children.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

from . import deps, workflow
from .db import (
    BacklogError,
    Conn,
    Row,
    actor_kind,
    log_event,
    next_key,
    require_backlog_dir,
    utcnow,
)
from .schema import (
    ITEM_KIND_ALIASES,
    ITEM_KINDS,
    PR_BEARING_TYPES,
    PR_REVIEW_STATES,
    PR_STATES,
    PRIORITIES,
    SATISFIED_STATUSES,
    STATUS_ALIASES,
    STATUS_DISPLAY,
    STATUSES,
    TASK_KEY_PREFIX,
    TASK_PARENT_TYPES,
    TASK_TYPE_ALIASES,
    TASK_TYPES,
    TICKABLE_ITEM_KINDS,
)

OPEN_STATUSES = {"created", "incomplete", "ready", "in_progress", "in_review", "needs_work"}
ACTIONABLE_BY_DEV = {"ready", "in_progress", "needs_work"}


# --------------------------------------------------------------------------- #
# normalisation
# --------------------------------------------------------------------------- #

def normalize_status(value: str, wf: "workflow.Workflow | None" = None) -> str:
    """Resolve a status against a workflow when one is given.

    Without a workflow this only normalises spelling and applies the built-in
    aliases; it deliberately does not reject an unknown value, because a
    project may define statuses this code has never heard of.
    """
    slug = value.strip().lower().replace("-", "_").replace(" ", "_")
    while "__" in slug:
        slug = slug.replace("__", "_")
    if wf is not None:
        try:
            return wf.resolve(slug)
        except BacklogError:
            return wf.resolve(STATUS_ALIASES.get(slug, slug))
    slug = STATUS_ALIASES.get(slug, slug)
    if slug not in STATUSES:
        raise BacklogError(
            f"unknown status {value!r}. Valid: " + ", ".join(STATUS_DISPLAY.get(s, s) for s in STATUSES)
        )
    return slug


def normalize_key(value: str) -> str:
    return value.strip().upper()


def normalize_priority(value: str) -> str:
    p = value.strip().upper()
    if p not in PRIORITIES:
        raise BacklogError(f"unknown priority {value!r}. Valid: {', '.join(PRIORITIES)}")
    return p


def normalize_type(value: str) -> str:
    t = value.strip().lower().replace("-", "_").replace(" ", "_")
    t = TASK_TYPE_ALIASES.get(t, t)
    if t not in TASK_TYPES:
        raise BacklogError(f"unknown task type {value!r}. Valid: {', '.join(TASK_TYPES)}")
    return t


def normalize_item_kind(value: str) -> str:
    k = value.strip().lower().replace("-", "_").replace(" ", "_")
    k = ITEM_KIND_ALIASES.get(k, k)
    if k not in ITEM_KINDS:
        raise BacklogError(f"unknown item kind {value!r}. Valid: {', '.join(ITEM_KINDS)}")
    return k


# --------------------------------------------------------------------------- #
# lookups
# --------------------------------------------------------------------------- #

def get_task(conn: Conn, project_id: int, key: str) -> Row:
    row = conn.execute(
        "SELECT * FROM task WHERE project_id = ? AND key = ?",
        (project_id, normalize_key(key)),
    ).fetchone()
    if row is None:
        raise BacklogError(f"no task with key {normalize_key(key)} in this project")
    return row


def get_task_by_id(conn: Conn, task_id: int) -> Row:
    row = conn.execute("SELECT * FROM task WHERE id = ?", (task_id,)).fetchone()
    if row is None:
        raise BacklogError(f"no task with id {task_id}")
    return row


def find_task(conn: Conn, project_id: int, key: str) -> Row | None:
    return conn.execute(
        "SELECT * FROM task WHERE project_id = ? AND key = ?",
        (project_id, normalize_key(key)),
    ).fetchone()


def children_of(conn: Conn, task_id: int) -> list[Row]:
    return conn.execute(
        "SELECT * FROM task WHERE parent_id = ? ORDER BY key", (task_id,)
    ).fetchall()


def open_threads(conn: Conn, task_id: int) -> list[Row]:
    return conn.execute(
        "SELECT * FROM review_thread WHERE task_id = ? AND state != 'closed' ORDER BY root_key",
        (task_id,),
    ).fetchall()


def blocking_threads(conn: Conn, task_id: int) -> list[Row]:
    return conn.execute(
        "SELECT * FROM review_thread WHERE task_id = ? "
        "AND (state != 'closed' OR COALESCE(resolution, '') != 'accepted_by_reviewer') "
        "ORDER BY root_key",
        (task_id,),
    ).fetchall()


def task_items(conn: Conn, task_id: int, kind: str | None = None) -> list[Row]:
    sql = "SELECT * FROM task_item WHERE task_id = ?"
    params: list = [task_id]
    if kind:
        sql += " AND kind = ?"
        params.append(normalize_item_kind(kind))
    return conn.execute(sql + " ORDER BY kind, position, id", params).fetchall()


# --------------------------------------------------------------------------- #
# creation / mutation
# --------------------------------------------------------------------------- #

def add_task(
    conn: Conn,
    project_id: int,
    task_type: str,
    title: str,
    parent: str | None = None,
    description: str = "",
    priority: str = "P2",
    owner: str | None = None,
    assignee: str | None = None,
    reviewer: str | None = None,
    branch: str | None = None,
    actor: str | None = None,
) -> Row:
    task_type = normalize_type(task_type)
    parent_id = None
    if parent:
        prow = get_task(conn, project_id, parent)
        allowed = TASK_PARENT_TYPES[task_type]
        if prow["task_type"] not in allowed:
            raise BacklogError(
                f"a {task_type} cannot sit under a {prow['task_type']} ({prow['key']}). "
                + (f"Its parent must be a {' or '.join(sorted(allowed))}."
                   if allowed else "It is a root and takes no parent.")
            )
        parent_id = prow["id"]
    elif task_type == "subtask":
        raise BacklogError("a subtask requires a parent story (--story <KEY>)")

    key = next_key(conn, project_id, TASK_KEY_PREFIX[task_type])
    initial = workflow.get(conn, project_id, task_type).initial
    ts = utcnow()
    conn.execute(
        "INSERT INTO task(project_id, key, task_type, parent_id, title, description, "
        "status, priority, owner, assignee, assignee_kind, reviewer, reviewer_kind, "
        "branch, created_at, updated_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (project_id, key, task_type, parent_id, title, description, initial,
         normalize_priority(priority), owner, assignee, actor_kind(assignee),
         reviewer, actor_kind(reviewer), branch, ts, ts),
    )
    row = get_task(conn, project_id, key)
    log_event(conn, "created", project_id, row["id"], key, actor,
              to_value=initial, detail=f"{task_type}: {title}")
    conn.commit()
    return row


_TASK_FIELDS = {"title", "description", "branch", "owner"}


def update_task(conn: Conn, project_id: int, key: str, actor: str | None = None,
                **fields) -> Row:
    task = get_task(conn, project_id, key)
    sets, values, notes = [], [], []
    for name, value in fields.items():
        if value is None:
            continue
        if name == "priority":
            value = normalize_priority(value)
        elif name == "parent":
            prow = get_task(conn, project_id, value)
            allowed = TASK_PARENT_TYPES[task["task_type"]]
            if prow["task_type"] not in allowed:
                raise BacklogError(
                    f"a {task['task_type']} cannot sit under a {prow['task_type']}"
                )
            if _would_loop(conn, task["id"], prow["id"]):
                raise BacklogError(f"{prow['key']} is below {task['key']}; that would loop")
            name, value = "parent_id", prow["id"]
        elif name not in _TASK_FIELDS:
            raise BacklogError(f"cannot set field {name!r}")
        sets.append(f"{name} = ?")
        values.append(value)
        notes.append(f"{name}={value}")
    if not sets:
        return task
    sets.append("updated_at = ?")
    values += [utcnow(), task["id"]]
    conn.execute(f"UPDATE task SET {', '.join(sets)} WHERE id = ?", values)
    log_event(conn, "update", project_id, task["id"], task["key"], actor,
              detail="; ".join(notes))
    conn.commit()
    return get_task_by_id(conn, task["id"])


def _would_loop(conn: Conn, task_id: int, new_parent_id: int) -> bool:
    seen = set()
    cur: int | None = new_parent_id
    while cur is not None and cur not in seen:
        if cur == task_id:
            return True
        seen.add(cur)
        row = conn.execute("SELECT parent_id FROM task WHERE id = ?", (cur,)).fetchone()
        cur = row["parent_id"] if row else None
    return False


def assign(conn: Conn, project_id: int, key: str, to: str | None = None,
           reviewer: str | None = None, actor: str | None = None,
           to_kind: str | None = None, reviewer_kind: str | None = None) -> Row:
    task = get_task(conn, project_id, key)
    if to is None and reviewer is None:
        raise BacklogError("nothing to assign: pass --to and/or --reviewer")
    sets, values, notes = [], [], []
    if to is not None:
        sets += ["assignee = ?", "assignee_kind = ?"]
        values += [to, to_kind or actor_kind(to)]
        notes.append(f"assignee {task['assignee'] or '-'} -> {to}")
    if reviewer is not None:
        sets += ["reviewer = ?", "reviewer_kind = ?"]
        values += [reviewer, reviewer_kind or actor_kind(reviewer)]
        notes.append(f"reviewer {task['reviewer'] or '-'} -> {reviewer}")
    sets.append("updated_at = ?")
    values += [utcnow(), task["id"]]
    conn.execute(f"UPDATE task SET {', '.join(sets)} WHERE id = ?", values)
    log_event(conn, "assign", project_id, task["id"], task["key"], actor,
              detail="; ".join(notes))
    conn.commit()
    return get_task_by_id(conn, task["id"])


# --------------------------------------------------------------------------- #
# task items
# --------------------------------------------------------------------------- #

def add_item(conn: Conn, project_id: int, key: str, kind: str, content: str,
             position: int | None = None, actor: str | None = None) -> Row:
    task = get_task(conn, project_id, key)
    kind = normalize_item_kind(kind)
    if position is None:
        row = conn.execute(
            "SELECT COALESCE(MAX(position), -1) AS p FROM task_item WHERE task_id = ? AND kind = ?",
            (task["id"], kind),
        ).fetchone()
        position = int(row["p"]) + 1
    ts = utcnow()
    item_id = conn.insert_returning_id(
        "INSERT INTO task_item(task_id, kind, position, content, done, created_at, "
        "updated_at, created_by) VALUES(?,?,?,?,0,?,?,?)",
        (task["id"], kind, position, content, ts, ts, actor),
    )
    log_event(conn, "item", project_id, task["id"], task["key"], actor,
              to_value=kind, detail=content[:120])
    conn.commit()
    return conn.execute("SELECT * FROM task_item WHERE id = ?", (item_id,)).fetchone()


def set_items(conn: Conn, project_id: int, key: str, kind: str, lines: list[str],
              actor: str | None = None) -> list[Row]:
    """Replace every item of one kind on a task. One line, one item."""
    task = get_task(conn, project_id, key)
    kind = normalize_item_kind(kind)
    conn.execute("DELETE FROM task_item WHERE task_id = ? AND kind = ?", (task["id"], kind))
    ts = utcnow()
    conn.executemany(
        "INSERT INTO task_item(task_id, kind, position, content, done, created_at, "
        "updated_at, created_by) VALUES(?,?,?,?,0,?,?,?)",
        [(task["id"], kind, i, line.strip(), ts, ts, actor)
         for i, line in enumerate(l for l in lines if l.strip())],
    )
    log_event(conn, "item", project_id, task["id"], task["key"], actor,
              to_value=kind, detail=f"replaced with {len(lines)} line(s)")
    conn.commit()
    return task_items(conn, task["id"], kind)


def tick_item(conn: Conn, project_id: int, item_id: int, done: bool = True,
              actor: str | None = None) -> Row:
    row = conn.execute("SELECT * FROM task_item WHERE id = ?", (item_id,)).fetchone()
    if row is None:
        raise BacklogError(f"no task item with id {item_id}")
    if row["kind"] not in TICKABLE_ITEM_KINDS:
        raise BacklogError(
            f"a {row['kind']} entry is not tickable; only a checklist entry is. "
            "Acceptance criteria are proven by review, not by a tick."
        )
    task = get_task_by_id(conn, row["task_id"])
    conn.execute(
        "UPDATE task_item SET done = ?, updated_at = ? WHERE id = ?",
        (1 if done else 0, utcnow(), item_id),
    )
    log_event(conn, "item", project_id, task["id"], task["key"], actor,
              to_value="done" if done else "open", detail=row["content"][:120])
    conn.commit()
    return conn.execute("SELECT * FROM task_item WHERE id = ?", (item_id,)).fetchone()


def remove_item(conn: Conn, project_id: int, item_id: int, actor: str | None = None) -> Row:
    row = conn.execute("SELECT * FROM task_item WHERE id = ?", (item_id,)).fetchone()
    if row is None:
        raise BacklogError(f"no task item with id {item_id}")
    task = get_task_by_id(conn, row["task_id"])
    conn.execute("DELETE FROM task_item WHERE id = ?", (item_id,))
    log_event(conn, "item_removed", project_id, task["id"], task["key"], actor,
              detail=row["content"][:120])
    conn.commit()
    return row


# --------------------------------------------------------------------------- #
# pull requests
# --------------------------------------------------------------------------- #

def set_pr(conn: Conn, project_id: int, key: str, url: str | None = None,
           number: int | None = None, repo: str | None = None, state: str | None = None,
           review_state: str | None = None, actor: str | None = None,
           emit_action: bool = True) -> Row:
    task = get_task(conn, project_id, key)
    if task["task_type"] not in PR_BEARING_TYPES:
        raise BacklogError(
            f"{task['key']} is a {task['task_type']}; a pull request belongs to a "
            "story or a subtask, not to a container."
        )
    sets, values, notes = [], [], []

    if url is not None:
        sets.append("pr_url = ?")
        values.append(url)
        notes.append(f"url={url}")
        if number is None or repo is None:
            parsed = _parse_pr_url(url)
            if parsed:
                p_repo, p_num = parsed
                repo = repo or p_repo
                number = number if number is not None else p_num
    if number is not None:
        sets.append("pr_number = ?")
        values.append(int(number))
        notes.append(f"number={number}")
    if repo is not None:
        sets.append("pr_repo = ?")
        values.append(repo)
        notes.append(f"repo={repo}")
    if state is not None:
        state = state.strip().lower()
        if state not in PR_STATES:
            raise BacklogError(f"pr state must be one of {', '.join(PR_STATES)}")
        sets.append("pr_state = ?")
        values.append(state)
        notes.append(f"state={state}")
    if review_state is not None:
        review_state = review_state.strip().lower().replace("-", "_")
        if review_state not in PR_REVIEW_STATES:
            raise BacklogError(f"pr review state must be one of {', '.join(PR_REVIEW_STATES)}")
        sets.append("pr_review_state = ?")
        values.append(review_state)
        notes.append(f"review_state={review_state}")

    if not sets:
        raise BacklogError(
            "nothing to set: pass at least one of --url/--number/--repo/--state/--review-state"
        )
    # Recording a real PR cancels any earlier no-PR waiver.
    sets += ["pr_waived = 0", "updated_at = ?"]
    values += [utcnow(), task["id"]]
    conn.execute(f"UPDATE task SET {', '.join(sets)} WHERE id = ?", values)
    log_event(conn, "pr", project_id, task["id"], task["key"], actor, detail="; ".join(notes))
    conn.commit()
    updated = get_task_by_id(conn, task["id"])
    if emit_action:
        from .hooks import Action

        if state == "merged":
            action = Action.PR_MERGED
        elif review_state == "approved":
            action = Action.PR_APPROVED
        elif review_state == "changes_requested":
            action = Action.PR_CHANGES_REQUESTED
        elif state == "closed":
            action = Action.PR_CLOSED
        elif state == "open" and task["pr_state"] == "closed":
            action = Action.PR_REOPENED
        elif state == "open" and task["pr_state"] == "draft":
            action = Action.PR_MARKED_READY
        elif url is not None and not task["pr_url"]:
            action = Action.PR_CREATED
        else:
            action = Action.PR_UPDATED
        updated, _, _ = trigger_action(
            conn,
            project_id,
            task["key"],
            action,
            actor=actor,
            operation="pr.set",
            parameters={
                "url": updated["pr_url"],
                "state": updated["pr_state"],
                "review_state": updated["pr_review_state"],
            },
        )
    return updated


def _parse_pr_url(url: str) -> tuple[str, int] | None:
    import re

    m = re.search(r"github\.com/([^/]+/[^/]+)/pull/(\d+)", url)
    if m:
        return m.group(1), int(m.group(2))
    m = re.search(r"([^/]+/[^/]+)/-/merge_requests/(\d+)", url)
    if m:
        return m.group(1), int(m.group(2))
    return None


def sync_pr(conn: Conn, project_id: int, key: str, actor: str | None = None) -> Row:
    """Refresh PR state from the `gh` CLI. Optional; `pr set` always works."""
    task = get_task(conn, project_id, key)
    if not task["pr_number"]:
        raise BacklogError(
            f"{task['key']} has no PR number. "
            f"Use `backlog pr set {task['key']} --url <PR-URL>` first."
        )
    if shutil.which("gh") is None:
        raise BacklogError("`gh` is not installed; set PR state manually with `backlog pr set`.")
    cmd = ["gh", "pr", "view", str(task["pr_number"]),
           "--json", "state,isDraft,reviewDecision,url"]
    if task["pr_repo"]:
        cmd += ["--repo", task["pr_repo"]]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise BacklogError(f"gh failed: {proc.stderr.strip() or proc.stdout.strip()}")
    import json as _json

    data = _json.loads(proc.stdout)
    gh_state = (data.get("state") or "").upper()
    state = {"OPEN": "open", "MERGED": "merged", "CLOSED": "closed"}.get(gh_state, "none")
    if state == "open" and data.get("isDraft"):
        state = "draft"
    decision = (data.get("reviewDecision") or "").upper()
    review_state = {
        "APPROVED": "approved",
        "CHANGES_REQUESTED": "changes_requested",
        "REVIEW_REQUIRED": "pending",
    }.get(decision, "pending" if state in ("open", "draft") else "none")
    return set_pr(conn, project_id, task["key"], url=data.get("url"), state=state,
                  review_state=review_state, actor=actor)


# --------------------------------------------------------------------------- #
# gates
# --------------------------------------------------------------------------- #

class Check:
    __slots__ = ("name", "ok", "detail")

    def __init__(self, name: str, ok: bool, detail: str = ""):
        self.name, self.ok, self.detail = name, ok, detail

    def as_dict(self) -> dict:
        return {"check": self.name, "ok": self.ok, "detail": self.detail}


GATE_TARGETS = ["start", "in_review", "accepted", "done", "merge"]
_GATE_ALIASES = {"in_progress": "start", "begin": "start"}


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

    if target in ("accepted", "merge"):
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
            label = "subtasks" if ttype == "story" else "stories"
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

    if target == current:
        raise BacklogError(f"{task['key']} is already {wf.display(current)}")

    from . import hooks
    from .api import Backlog

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
    from . import hooks

    task = get_task(conn, project_id, key)
    action = hooks.normalize_action(action)
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


# --------------------------------------------------------------------------- #
# artifacts
# --------------------------------------------------------------------------- #

def add_artifact(conn: Conn, backlog_dir: Path, project_id: int, key: str, source: Path,
                 title: str = "", kind: str = "doc", actor: str | None = None) -> dict:
    task = get_task(conn, project_id, key)
    src = Path(source).expanduser()
    if not src.exists():
        raise BacklogError(f"artifact source not found: {src}")
    dest_dir = backlog_dir / "artifacts" / task["key"]
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / src.name
    if src.resolve() != dest.resolve():
        if src.is_dir():
            shutil.copytree(src, dest, dirs_exist_ok=True)
        else:
            shutil.copy2(src, dest)
    rel = f"artifacts/{task['key']}/{src.name}"
    conn.execute(
        "INSERT INTO artifact(task_id, rel_path, title, kind, created_at, created_by) "
        "VALUES(?,?,?,?,?,?) ON CONFLICT(task_id, rel_path) DO UPDATE SET "
        "title = excluded.title, kind = excluded.kind",
        (task["id"], rel, title or src.name, kind, utcnow(), actor),
    )
    log_event(conn, "artifact", project_id, task["id"], task["key"], actor,
              to_value=rel, detail=title)
    conn.commit()
    return {"key": task["key"], "task_type": task["task_type"], "rel_path": rel,
            "abs_path": str(dest), "title": title or src.name, "kind": kind}


def list_artifacts(conn: Conn, task_id: int) -> list[Row]:
    return conn.execute(
        "SELECT * FROM artifact WHERE task_id = ? ORDER BY rel_path", (task_id,)
    ).fetchall()

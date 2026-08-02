"""Pull-request state recording and synchronization."""

from __future__ import annotations

import shutil
import subprocess

from ..db import BacklogError, Conn, Row, actor_kind, log_event, utcnow
from ..schema import PR_BEARING_TYPES, PR_REVIEW_STATES, PR_STATES
from .gates import trigger_action
from .tasks import get_task, get_task_by_id


def set_pr(conn: Conn, project_id: int, key: str, url: str | None = None,
           number: int | None = None, repo: str | None = None, state: str | None = None,
           review_state: str | None = None, actor: str | None = None,
           emit_action: bool = True) -> Row:
    task = get_task(conn, project_id, key)
    if task["task_type"] not in PR_BEARING_TYPES:
        raise BacklogError(
            f"{task['key']} is a {task['task_type']}; a pull request belongs to a "
            "story, bug, or subtask, not to a container."
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
        from ..hooks import Action

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

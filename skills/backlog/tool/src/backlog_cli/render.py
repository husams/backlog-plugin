"""Deterministic human-readable rendering. Every command also has --json."""

from __future__ import annotations

from .db import Conn, Row
from .schema import (
    ITEM_KIND_DISPLAY,
    STATUS_DISPLAY,
    TASK_KEY_PREFIX,
    TASK_TYPE_DISPLAY,
)


def row_to_dict(row: Row) -> dict:
    return {k: row[k] for k in row.keys()}


def table(headers: list[str], rows: list[list[str]]) -> str:
    if not rows:
        return "(none)"
    widths = [len(h) for h in headers]
    cells = [[("" if c is None else str(c)) for c in r] for r in rows]
    for r in cells:
        for i, c in enumerate(r):
            widths[i] = max(widths[i], len(c))
    out = ["  ".join(h.ljust(widths[i]) for i, h in enumerate(headers)).rstrip()]
    out.append("  ".join("-" * widths[i] for i in range(len(headers))))
    for r in cells:
        out.append("  ".join(c.ljust(widths[i]) for i, c in enumerate(r)).rstrip())
    return "\n".join(out)


def who(name: str | None, kind: str | None) -> str:
    """`husam` for a person, `claude*` for an agent — the star marks the agent."""
    if not name:
        return "-"
    return f"{name}*" if kind == "agent" else name


def task_line(row: Row) -> list[str]:
    keys = row.keys()
    pr = ""
    if row["pr_number"]:
        pr = f"#{row['pr_number']}"
        if row["pr_state"] != "none":
            pr += f" {row['pr_state']}"
        if row["pr_review_state"] == "approved":
            pr += "/approved"
        elif row["pr_review_state"] == "changes_requested":
            pr += "/changes"
    elif row["pr_waived"]:
        pr = "(no PR)"
    return [
        row["key"],
        TASK_KEY_PREFIX[row["task_type"]],
        (row["parent_key"] or "") if "parent_key" in keys else "",
        STATUS_DISPLAY.get(row["status"], row["status"]),
        row["priority"],
        who(row["assignee"], row["assignee_kind"]),
        who(row["reviewer"], row["reviewer_kind"]),
        pr,
        row["title"],
    ]


TASK_HEADERS = ["KEY", "T", "PARENT", "STATUS", "PRI", "ASSIGNEE", "REVIEWER", "PR", "TITLE"]


def tasks_table(rows: list[Row]) -> str:
    return table(TASK_HEADERS, [task_line(r) for r in rows])


def projects_table(rows: list[Row], active: str | None = None) -> str:
    return table(
        ["", "PROJECT", "TASKS", "OPEN", "NAME"],
        [["*" if r["slug"] == active else "", r["slug"], str(r["tasks"]),
          str(r["open_tasks"]), r["name"]] for r in rows],
    )


def deps_block(conn: Conn, task_id: int, indent: str = "  ") -> list[str]:
    """The dependency section shared by every task rendering."""
    from . import deps

    edges = deps.edges_for(conn, task_id)
    if not edges:
        return []
    label = {
        ("in", "blocks"): "blocked by",
        ("out", "blocks"): "blocks",
        ("in", "relates"): "relates to",
        ("out", "relates"): "relates to",
        ("in", "duplicates"): "duplicated by",
        ("out", "duplicates"): "duplicates",
    }
    out = [f"{indent}dependencies:"]
    for e in sorted(edges, key=lambda e: (e["kind"] != "blocks", e["direction"], e["other_key"])):
        mark = "ok  " if e["satisfied"] else ("WAIT" if e["kind"] == "blocks" else "    ")
        if e["direction"] == "out" and e["kind"] == "blocks":
            mark = "    "
        note = f"  — {e['note']}" if e["note"] else ""
        out.append(
            f"{indent}  {mark} {label[(e['direction'], e['kind'])]:<14} "
            f"{e['other_key']:<7} [{e['other_status']}] {e['other_title']}{note}"
        )
    return out


def items_block(items: list[Row], indent: str = "  ", conn: Conn | None = None) -> list[str]:
    if not items:
        return []
    out: list[str] = []
    current = None
    for it in items:
        if conn is not None:
            from .execution import public_item
            it = public_item(conn, it)
        if it["kind"] != current:
            current = it["kind"]
            out.append(f"{indent}{ITEM_KIND_DISPLAY[current]}:")
        box = ""
        if it["kind"] == "checklist":
            box = "[x] " if it["done"] else "[ ] "
        suffix = ""
        if it.get("executor") and it["executor"] != "plain":
            suffix = (
                f"  [{it['executor']}, {it['requirement']}, {it['state']}]"
            )
        out.append(f"{indent}  #{it['id']:<4} {box}{it['content']}{suffix}")
        spec = it.get("execution_spec")
        if spec and spec.get("shell"):
            shell = spec["shell"]
            out.append(f"{indent}        command: hidden")
            out.append(
                f"{indent}        expected: exit {shell.get('expected_exit_code', 0)}"
            )
            if shell.get("stdout"):
                out.append(f"{indent}        stdout: {_matcher_text(shell['stdout'])}")
            if shell.get("stderr"):
                out.append(f"{indent}        stderr: {_matcher_text(shell['stderr'])}")
            if shell.get("environment"):
                out.append(
                    f"{indent}        environment: "
                    + ", ".join(shell["environment"])
                    + " (values hidden)"
                )
        elif spec and spec.get("hook"):
            hook = spec["hook"]
            out.append(f"{indent}        hook: {hook['name']}")
            out.append(f"{indent}        arguments: hidden")
            out.append(f"{indent}        expected: hidden")
    return out


def _matcher_text(value: dict) -> str:
    name = next(iter(value))
    return f"{name} (value hidden)"


def render_task(conn: Conn, row: Row) -> str:
    from . import core, workflow
    from .core import children_of, list_artifacts, open_threads, task_items

    parent = None
    if row["parent_id"]:
        parent = conn.execute(
            "SELECT key FROM task WHERE id = ?", (row["parent_id"],)
        ).fetchone()

    out = [f"{row['key']}  [{TASK_TYPE_DISPLAY[row['task_type']]}]  {row['title']}"]
    out.append(
        f"  status     : {workflow.get(conn, row['project_id'], row['task_type']).display(row['status'])}"
    )
    out.append(f"  priority   : {row['priority']}")
    out.append(f"  parent     : {parent['key'] if parent else '-'}")
    out.append(f"  owner      : {row['owner'] or '-'}")
    out.append(f"  assignee   : {who(row['assignee'], row['assignee_kind'])}")
    out.append(f"  reviewer   : {who(row['reviewer'], row['reviewer_kind'])}")
    out.append(f"  branch     : {row['branch'] or '-'}")
    if row["pr_url"] or row["pr_number"]:
        out.append(
            f"  pr         : {row['pr_url'] or ''} "
            f"(#{row['pr_number'] or '?'}, {row['pr_state']}, review={row['pr_review_state']})"
        )
    elif row["pr_waived"]:
        out.append("  pr         : none (waived with --no-pr)")
    else:
        out.append("  pr         : -")
    out.append(f"  creator    : {row['created_by'] or '-'}")
    out.append(f"  created    : {row['created_at']}")
    out.append(f"  updated    : {row['updated_at']}")
    if row["closed_at"]:
        out.append(f"  closed     : {row['closed_at']}")
    if row["description"]:
        out.append("  description:")
        out += [f"    {ln}" for ln in row["description"].splitlines()]

    out += items_block(task_items(conn, row["id"]), conn=conn)
    out += deps_block(conn, row["id"])

    threads = open_threads(conn, row["id"])
    out.append(f"  open review threads: {len(threads)}")
    for t in threads:
        out.append(
            f"    {t['root_key']}  {t['severity']:<12} {t['state']:<18} {t['title']}"
        )

    kids = children_of(conn, row["id"])
    if kids:
        out.append(f"  {'stories' if row['task_type'] == 'feature' else 'subtasks'}:")
        for k in kids:
            out.append(f"    {k['key']}  "
                       f"{STATUS_DISPLAY.get(k['status'], k['status']):<12} {k['title']}")

    if row["task_type"] == "iteration":
        members = core.iteration_members(conn, row["id"])
        out.append(f"  members: {len(members)}")
        for member in members:
            member_flow = workflow.get(conn, row["project_id"], member["task_type"])
            out.append(
                f"    {member['key']}  {member_flow.display(member['status']):<12} "
                f"{member['title']}"
            )
    else:
        iterations = conn.execute(
            "SELECT i.key,i.title,i.status FROM iteration_member m "
            "JOIN task i ON i.id=m.iteration_id WHERE m.member_id=? ORDER BY i.priority,i.key",
            (row["id"],),
        ).fetchall()
        if iterations:
            out.append("  iterations:")
            for iteration in iterations:
                out.append(f"    {iteration['key']}  {iteration['status']:<12} {iteration['title']}")

    arts = list_artifacts(conn, row["id"])
    if arts:
        out.append("  artifacts:")
        for a in arts:
            out.append(f"    .backlog/{a['rel_path']}  [{a['kind']}] {a['title']}")
    return "\n".join(out)


def render_thread(t: dict, full: bool = False) -> str:
    head = f"{t['root']}  on {t['target']}  [{t['severity']}] [{t['state']}]"
    if t["resolution"]:
        head += f"  ({t['resolution']})"
    if t["awaiting_actor"]:
        head += f"  awaiting: {t['awaiting_actor']}"
    elif t["awaiting_role"]:
        head += f"  awaiting: <{t['awaiting_role']} unassigned>"
    lines = [head]
    if t["file"]:
        lines.append(f"  location : {t['file']}:{t['line'] or ''}")
    lines.append(f"  comments : {t['comment_count']}")

    def block(label: str, c: dict) -> list[str]:
        b = [f"  --- {label}: {c['key']} by {who(c['author'], c.get('author_kind'))} "
             f"({c['role']}/{c['action']}) {c['at']}"]
        b += [f"      {ln}" for ln in c["body"].splitlines()]
        return b

    if full:
        for c in t.get("comments", []):
            lines += block(f"#{c['seq']}", c)
    else:
        lines += block("ROOT", t["root_comment"])
        if t["parent_comment"]:
            lines += block("PARENT OF LATEST", t["parent_comment"])
        if t["last_comment"]["key"] != t["root_comment"]["key"]:
            lines += block("LATEST", t["last_comment"])
        if t["hidden_comments"] > 0:
            lines.append(
                f"  ({t['hidden_comments']} intermediate comment(s) hidden — "
                f"use `backlog review thread {t['root']} --full` only if truly needed)"
            )
    if t["state"] != "closed":
        lines.append(f"  reply to : {t['reply_to']}")
    return "\n".join(lines)

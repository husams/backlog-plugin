"""Project workflow editing and rendering."""

from __future__ import annotations

from ..db import BacklogError, Conn, Row
from ..schema import GATE_CHECKS, STATUS_CATEGORIES
from .model import Workflow
from .store import get

# --------------------------------------------------------------------------- #


def add_status(
    conn: Conn,
    project_id: int,
    task_type: str,
    slug: str,
    display: str,
    category: str = "active",
    after: str | None = None,
    satisfies: bool = False,
    terminal: bool = False,
    description: str = "",
) -> Row:
    wf = get(conn, project_id, task_type)
    slug = slug.strip().lower().replace("-", "_").replace(" ", "_")
    if slug in wf.statuses:
        raise BacklogError(f"{task_type} already has a status {slug!r}")
    assert category in STATUS_CATEGORIES
    if after:
        anchor = wf.resolve(after)
        position = int(wf.statuses[anchor]["position"]) + 1
        conn.execute(
            "UPDATE workflow_status SET position = position + 1 "
            "WHERE workflow_id = ? AND position >= ?",
            (wf.id, position),
        )
    else:
        position = len(wf.ordered)
    conn.execute(
        "INSERT INTO workflow_status(workflow_id, slug, display, category, position, "
        "satisfies_dependency, is_initial, is_terminal, description) VALUES(?,?,?,?,?,?,0,?,?)",
        (
            wf.id,
            slug,
            display or slug.replace("_", " ").title(),
            category,
            position,
            1 if satisfies else 0,
            1 if terminal else 0,
            description,
        ),
    )
    conn.commit()
    return conn.execute(
        "SELECT * FROM workflow_status WHERE workflow_id = ? AND slug = ?",
        (wf.id, slug),
    ).fetchone()


def remove_status(conn: Conn, project_id: int, task_type: str, slug: str) -> None:
    wf = get(conn, project_id, task_type)
    slug = wf.resolve(slug)
    used = conn.execute(
        "SELECT COUNT(*) AS n FROM task WHERE project_id = ? AND task_type = ? AND status = ?",
        (project_id, task_type, slug),
    ).fetchone()["n"]
    if used:
        raise BacklogError(
            f"{used} {task_type}(s) are currently in {slug!r}; move them first "
            "(the store keeps whatever status it is given, so removing it here "
            "would leave them unreachable)"
        )
    conn.execute(
        "DELETE FROM workflow_status WHERE workflow_id = ? AND slug = ?", (wf.id, slug)
    )
    conn.execute(
        "DELETE FROM workflow_transition WHERE workflow_id = ? AND (from_status = ? OR to_status = ?)",
        (wf.id, slug, slug),
    )
    conn.commit()


def set_transition(
    conn: Conn,
    project_id: int,
    task_type: str,
    from_status: str,
    to_status: str,
    gates: str = "",
    note: str = "",
) -> None:
    wf = get(conn, project_id, task_type)
    f, t = wf.resolve(from_status), wf.resolve(to_status)
    for g in (x.strip() for x in gates.split(",") if x.strip()):
        if g not in GATE_CHECKS:
            raise BacklogError(
                f"unknown gate {g!r}. Valid: {', '.join(GATE_CHECKS)} "
                "(`backlog workflow gates` explains each one)"
            )
    conn.execute(
        "INSERT INTO workflow_transition(workflow_id, from_status, to_status, gates, note) "
        "VALUES(?,?,?,?,?) ON CONFLICT(workflow_id, from_status, to_status) "
        "DO UPDATE SET gates = excluded.gates, note = excluded.note",
        (wf.id, f, t, ",".join(x.strip() for x in gates.split(",") if x.strip()), note),
    )
    conn.commit()


def remove_transition(
    conn: Conn, project_id: int, task_type: str, from_status: str, to_status: str
) -> None:
    wf = get(conn, project_id, task_type)
    f, t = wf.resolve(from_status), wf.resolve(to_status)
    cur = conn.execute(
        "DELETE FROM workflow_transition WHERE workflow_id = ? AND from_status = ? "
        "AND to_status = ?",
        (wf.id, f, t),
    )
    conn.commit()
    if not cur.rowcount:
        raise BacklogError(f"no {f} -> {t} transition on the {task_type} flow")


def render(wf: Workflow) -> str:
    """The flow as a table an agent can read before moving anything."""
    rows = []
    for s in wf.ordered:
        nxt = wf.next_from(s["slug"])
        moves = (
            ", ".join(
                wf.display(t) + (f" ({g.replace(',', ' + ')})" if g else "")
                for t, g in sorted(nxt.items())
            )
            or "(terminal)"
        )
        flags = []
        if s["is_initial"]:
            flags.append("initial")
        if s["satisfies_dependency"]:
            flags.append("counts as finished")
        if s["is_terminal"]:
            flags.append("terminal")
        rows.append([s["display"], s["slug"], s["category"], ", ".join(flags), moves])
    from ..render import table

    return table(["STATUS", "SLUG", "CATEGORY", "FLAGS", "LEGAL NEXT (gates)"], rows)

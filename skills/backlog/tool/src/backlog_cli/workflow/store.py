"""Workflow loading, seeding, copying, and reset operations."""

from __future__ import annotations

from ..db import BacklogError, Conn, utcnow
from ..schema import TASK_TYPES
from .model import Workflow

# --------------------------------------------------------------------------- #

def get(conn: Conn, project_id: int, task_type: str) -> Workflow:
    row = conn.execute(
        "SELECT * FROM workflow WHERE project_id = ? AND task_type = ?",
        (project_id, task_type),
    ).fetchone()
    if row is None:
        seed(conn, project_id, task_type)
        row = conn.execute(
            "SELECT * FROM workflow WHERE project_id = ? AND task_type = ?",
            (project_id, task_type),
        ).fetchone()
    statuses = conn.execute(
        "SELECT * FROM workflow_status WHERE workflow_id = ? ORDER BY position, id",
        (row["id"],),
    ).fetchall()
    transitions = conn.execute(
        "SELECT * FROM workflow_transition WHERE workflow_id = ? ORDER BY from_status, to_status",
        (row["id"],),
    ).fetchall()
    return Workflow(row, statuses, transitions)


def all_for(conn: Conn, project_id: int) -> dict[str, Workflow]:
    return {t: get(conn, project_id, t) for t in TASK_TYPES}


def template_of(conn: Conn, project_id: int):
    """The template a project was created from, or the default."""
    from .. import templates

    row = conn.execute("SELECT template_id FROM project WHERE id = ?", (project_id,)).fetchone()
    if row is not None and row["template_id"]:
        tpl = conn.execute("SELECT * FROM template WHERE id = ?", (row["template_id"],)).fetchone()
        if tpl is not None:
            return tpl
    return templates.default(conn)


def seed(conn: Conn, project_id: int, task_type: str, name: str = "") -> int:
    """Instantiate one task type's flow from the project's template."""
    from .. import templates

    tpl = template_of(conn, project_id)
    templates.instantiate(conn, int(tpl["id"]), project_id, task_type)
    row = conn.execute(
        "SELECT id FROM workflow WHERE project_id = ? AND task_type = ?",
        (project_id, task_type),
    ).fetchone()
    if row is None:
        raise BacklogError(
            f"template '{tpl['slug']}' defines no {task_type} workflow; "
            "pick another with `backlog project set <slug> --template <t>`"
        )
    return int(row["id"])


def seed_all(conn: Conn, project_id: int) -> None:
    from .. import templates

    tpl = template_of(conn, project_id)
    templates.instantiate(conn, int(tpl["id"]), project_id)


def upgrade(conn: Conn, project_id: int) -> list[str]:
    """Add missing shipped task-type flows without replacing local flows."""
    from .. import templates

    existing = conn.execute(
        "SELECT description FROM workflow WHERE project_id=? ORDER BY task_type",
        (project_id,),
    ).fetchall()
    markers = {row["description"] for row in existing}
    action_marker = next(
        (value for value in markers if value.startswith("action-workflow:")), None
    ) if len(markers) == 1 else None
    tpl = template_of(conn, project_id)
    added = templates.instantiate(conn, int(tpl["id"]), project_id, replace=False)
    if action_marker and added:
        placeholders = ",".join("?" for _ in added)
        conn.execute(
            f"UPDATE workflow SET description=? WHERE project_id=? "
            f"AND task_type IN ({placeholders})",
            (action_marker, project_id, *added),
        )
        conn.commit()
    return added


def reset(conn: Conn, project_id: int, task_type: str | None = None) -> None:
    """Back to the project's template, discarding local edits to the flow."""
    from .. import templates

    tpl = template_of(conn, project_id)
    templates.instantiate(conn, int(tpl["id"]), project_id, task_type, replace=True)


def copy_from(conn: Conn, src_project_id: int, dst_project_id: int,
              task_type: str | None = None) -> list[str]:
    """Adopt another project's flow, so a house style is defined once."""
    done = []
    for ttype in ([task_type] if task_type else TASK_TYPES):
        src = get(conn, src_project_id, ttype)
        conn.execute("DELETE FROM workflow WHERE project_id = ? AND task_type = ?",
                     (dst_project_id, ttype))
        ts = utcnow()
        wf_id = conn.insert_returning_id(
            "INSERT INTO workflow(project_id, task_type, name, description, created_at, "
            "updated_at) VALUES(?,?,?,?,?,?)",
            (dst_project_id, ttype, src.name, src.description, ts, ts),
        )
        conn.executemany(
            "INSERT INTO workflow_status(workflow_id, slug, display, category, position, "
            "satisfies_dependency, is_initial, is_terminal, description) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            [(wf_id, s["slug"], s["display"], s["category"], s["position"],
              s["satisfies_dependency"], s["is_initial"], s["is_terminal"], s["description"])
             for s in src.ordered],
        )
        conn.executemany(
            "INSERT INTO workflow_transition(workflow_id, from_status, to_status, gates, note) "
            "VALUES(?,?,?,?,?)",
            [(wf_id, f, t, g, "") for f, tos in src.transitions.items() for t, g in tos.items()],
        )
        conn.commit()
        done.append(ttype)
    return done


# --------------------------------------------------------------------------- #

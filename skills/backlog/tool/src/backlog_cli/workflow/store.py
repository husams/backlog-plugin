"""Workflow loading, seeding, copying, and reset operations."""

from __future__ import annotations

from ..db import Conn, utcnow
from ..schema import TASK_TYPES
from .model import Workflow

# --------------------------------------------------------------------------- #


def get(conn: Conn, project_id: int, task_type: str) -> Workflow:
    row = conn.execute(
        "SELECT * FROM workflow WHERE project_id = ? AND task_type = ?",
        (project_id, task_type),
    ).fetchone()
    assert row is not None
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
    """The template a project was created from."""
    row = conn.execute(
        "SELECT t.* FROM project p JOIN template t ON t.id=p.template_id WHERE p.id=?",
        (project_id,),
    ).fetchone()
    assert row is not None
    return row


def seed_all(conn: Conn, project_id: int) -> None:
    from .. import templates

    tpl = template_of(conn, project_id)
    templates.instantiate(conn, int(tpl["id"]), project_id)


def upgrade(conn: Conn, project_id: int) -> list[str]:
    """Add missing shipped task-type flows without replacing local flows."""
    from .. import templates

    tpl = template_of(conn, project_id)
    return templates.instantiate(conn, int(tpl["id"]), project_id, replace=False)


def reset(conn: Conn, project_id: int, task_type: str | None = None) -> None:
    """Back to the project's template, discarding local edits to the flow."""
    from .. import templates

    tpl = template_of(conn, project_id)
    templates.instantiate(conn, int(tpl["id"]), project_id, task_type, replace=True)


def copy_from(
    conn: Conn, src_project_id: int, dst_project_id: int, task_type: str | None = None
) -> list[str]:
    """Adopt another project's flow, so a house style is defined once."""
    done = []
    for ttype in [task_type] if task_type else TASK_TYPES:
        src = get(conn, src_project_id, ttype)
        conn.execute(
            "DELETE FROM workflow WHERE project_id = ? AND task_type = ?",
            (dst_project_id, ttype),
        )
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
            [
                (
                    wf_id,
                    s["slug"],
                    s["display"],
                    s["category"],
                    s["position"],
                    s["satisfies_dependency"],
                    s["is_initial"],
                    s["is_terminal"],
                    s["description"],
                )
                for s in src.ordered
            ],
        )
        conn.executemany(
            "INSERT INTO workflow_transition(workflow_id, from_status, to_status, gates, note) "
            "VALUES(?,?,?,?,?)",
            [
                (wf_id, f, t, g, "")
                for f, tos in src.transitions.items()
                for t, g in tos.items()
            ],
        )
        conn.commit()
        done.append(ttype)
    return done


# --------------------------------------------------------------------------- #

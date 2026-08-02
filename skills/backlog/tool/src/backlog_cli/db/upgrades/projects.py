"""Additive database upgrades grouped by responsibility."""

from __future__ import annotations

from ..common import Conn


def adopt_default_template(conn: Conn) -> list[str]:
    """Point projects that predate templates at the default one."""
    from ... import templates

    tpl = templates.default(conn)
    cur = conn.execute(
        "UPDATE project SET template_id = ? WHERE template_id IS NULL", (tpl["id"],)
    )
    conn.commit()
    n = cur.rowcount or 0
    return [f"{n} project(s) adopted the '{tpl['slug']}' template"] if n else []


def seed_missing_workflows(conn: Conn) -> list[str]:
    """Give every project the built-in flow it does not yet have."""
    from ... import workflow

    notes = []
    for proj in conn.execute("SELECT id, slug FROM project ORDER BY id").fetchall():
        before = conn.execute(
            "SELECT COUNT(*) AS n FROM workflow WHERE project_id = ?", (proj["id"],)
        ).fetchone()["n"]
        workflow.seed_all(conn, proj["id"])
        after = conn.execute(
            "SELECT COUNT(*) AS n FROM workflow WHERE project_id = ?", (proj["id"],)
        ).fetchone()["n"]
        if after > before:
            notes.append(
                f"seeded {after - before} workflow(s) for project '{proj['slug']}'"
            )
    return notes

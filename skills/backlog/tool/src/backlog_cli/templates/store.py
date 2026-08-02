"""Project templates: the pre-defined shape a project's workflows come from.

A template holds one workflow per task type, with its statuses and transitions.
Creating a project **copies** the template into the project's own workflow
rows, which is what makes the two independent afterwards: a project can adapt
its flow without disturbing the template, and a template can be revised without
rewriting projects already running on it.

The built-ins ship with the skill and are installed on first use, so they are
listable, copyable and editable exactly like one you write yourself.
"""

from __future__ import annotations

from ..db import BacklogError, Conn, Row, slugify, utcnow
from ..schema import (
    BUILTIN_TEMPLATES,
    TASK_TYPES,
)


# --------------------------------------------------------------------------- #
# installation and lookup
# --------------------------------------------------------------------------- #


def install_builtins(conn: Conn) -> list[str]:
    """Put the shipped templates in the store. Idempotent; never overwrites a
    template the user has edited."""
    added = []
    for spec in BUILTIN_TEMPLATES:
        if get(conn, spec["slug"]) is not None:
            continue
        ts = utcnow()
        tid = conn.insert_returning_id(
            "INSERT INTO template(slug, name, description, is_default, builtin, "
            "created_at, updated_at) VALUES(?,?,?,?,1,?,?)",
            (
                spec["slug"],
                spec["name"],
                spec["description"],
                spec["is_default"],
                ts,
                ts,
            ),
        )
        for task_type, wf in spec["workflows"].items():
            _write_workflow(conn, tid, task_type, wf["statuses"], wf["transitions"])
        added.append(spec["slug"])
    if added:
        conn.commit()
    return added


def _write_workflow(
    conn: Conn,
    template_id: int,
    task_type: str,
    statuses,
    transitions,
    name: str = "",
    description: str = "",
) -> int:
    wf_id = conn.insert_returning_id(
        "INSERT INTO template_workflow(template_id, task_type, name, description) "
        "VALUES(?,?,?,?)",
        (template_id, task_type, name or f"{task_type} flow", description),
    )
    conn.executemany(
        "INSERT INTO template_status(template_workflow_id, slug, display, category, "
        "position, satisfies_dependency, is_initial, is_terminal) VALUES(?,?,?,?,?,?,?,?)",
        [
            (wf_id, slug, display, cat, pos, sat, init, term)
            for pos, (slug, display, cat, sat, init, term) in enumerate(statuses)
        ],
    )
    conn.executemany(
        "INSERT INTO template_transition(template_workflow_id, from_status, to_status, gates) "
        "VALUES(?,?,?,?)",
        [(wf_id, f, t, g) for f, t, g in transitions],
    )
    return wf_id


def get(conn: Conn, slug: str) -> Row | None:
    return conn.execute(
        "SELECT * FROM template WHERE slug = ?", (slugify(slug),)
    ).fetchone()


def require(conn: Conn, slug: str) -> Row:
    row = get(conn, slug)
    if row is None:
        known = ", ".join(r["slug"] for r in list_all(conn)) or "(none installed)"
        raise BacklogError(f"no template '{slugify(slug)}'. Available: {known}")
    return row


def default(conn: Conn) -> Row:
    row = conn.execute(
        "SELECT * FROM template WHERE is_default = 1 ORDER BY id LIMIT 1"
    ).fetchone()
    assert row is not None
    return row


def list_all(conn: Conn) -> list[Row]:
    return conn.execute(
        "SELECT t.*, "
        "  (SELECT COUNT(*) FROM template_workflow w WHERE w.template_id = t.id) AS workflows, "
        "  (SELECT COUNT(*) FROM project p WHERE p.template_id = t.id) AS projects "
        "FROM template t ORDER BY t.is_default DESC, t.slug"
    ).fetchall()


def workflows_of(conn: Conn, template_id: int) -> dict[str, Row]:
    return {
        r["task_type"]: r
        for r in conn.execute(
            "SELECT * FROM template_workflow WHERE template_id = ? ORDER BY task_type",
            (template_id,),
        ).fetchall()
    }


def statuses_of(conn: Conn, template_workflow_id: int) -> list[Row]:
    return conn.execute(
        "SELECT * FROM template_status WHERE template_workflow_id = ? ORDER BY position, id",
        (template_workflow_id,),
    ).fetchall()


def transitions_of(conn: Conn, template_workflow_id: int) -> list[Row]:
    return conn.execute(
        "SELECT * FROM template_transition WHERE template_workflow_id = ? "
        "ORDER BY from_status, to_status",
        (template_workflow_id,),
    ).fetchall()


# --------------------------------------------------------------------------- #
# instantiation — template -> a project's own workflow rows
# --------------------------------------------------------------------------- #


def instantiate(
    conn: Conn,
    template_id: int,
    project_id: int,
    task_type: str | None = None,
    replace: bool = False,
) -> list[str]:
    """Copy a template's workflows onto a project.

    This is a copy, not a reference: the project owns the result and may edit
    it freely, and a later change to the template does not reach back.
    """
    tpl_workflows = workflows_of(conn, template_id)
    done: list[str] = []
    for ttype in [task_type] if task_type else TASK_TYPES:
        tpl = tpl_workflows.get(ttype)
        assert tpl is not None
        exists = conn.execute(
            "SELECT id FROM workflow WHERE project_id = ? AND task_type = ?",
            (project_id, ttype),
        ).fetchone()
        if exists is not None:
            if not replace:
                continue
            conn.execute("DELETE FROM workflow WHERE id = ?", (exists["id"],))
        ts = utcnow()
        wf_id = conn.insert_returning_id(
            "INSERT INTO workflow(project_id, task_type, name, description, created_at, "
            "updated_at) VALUES(?,?,?,?,?,?)",
            (project_id, ttype, tpl["name"], tpl["description"], ts, ts),
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
                for s in statuses_of(conn, tpl["id"])
            ],
        )
        conn.executemany(
            "INSERT INTO workflow_transition(workflow_id, from_status, to_status, gates, note) "
            "VALUES(?,?,?,?,?)",
            [
                (wf_id, t["from_status"], t["to_status"], t["gates"], t["note"])
                for t in transitions_of(conn, tpl["id"])
            ],
        )
        done.append(ttype)
    conn.commit()
    return done


# --------------------------------------------------------------------------- #

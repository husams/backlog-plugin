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

from .db import BacklogError, Conn, Row, slugify, utcnow
from .schema import (
    BUILTIN_TEMPLATES,
    DEFAULT_TEMPLATE_SLUG,
    GATE_CHECKS,
    STATUS_CATEGORIES,
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
            (spec["slug"], spec["name"], spec["description"], spec["is_default"], ts, ts),
        )
        for task_type, wf in spec["workflows"].items():
            _write_workflow(conn, tid, task_type, wf["statuses"], wf["transitions"])
        added.append(spec["slug"])
    if added:
        conn.commit()
    return added


def _write_workflow(conn: Conn, template_id: int, task_type: str,
                    statuses, transitions, name: str = "", description: str = "") -> int:
    wf_id = conn.insert_returning_id(
        "INSERT INTO template_workflow(template_id, task_type, name, description) "
        "VALUES(?,?,?,?)",
        (template_id, task_type, name or f"{task_type} flow", description),
    )
    conn.executemany(
        "INSERT INTO template_status(template_workflow_id, slug, display, category, "
        "position, satisfies_dependency, is_initial, is_terminal) VALUES(?,?,?,?,?,?,?,?)",
        [(wf_id, slug, display, cat, pos, sat, init, term)
         for pos, (slug, display, cat, sat, init, term) in enumerate(statuses)],
    )
    conn.executemany(
        "INSERT INTO template_transition(template_workflow_id, from_status, to_status, gates) "
        "VALUES(?,?,?,?)",
        [(wf_id, f, t, g) for f, t, g in transitions],
    )
    return wf_id


def get(conn: Conn, slug: str) -> Row | None:
    return conn.execute("SELECT * FROM template WHERE slug = ?", (slugify(slug),)).fetchone()


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
    if row is not None:
        return row
    install_builtins(conn)
    row = get(conn, DEFAULT_TEMPLATE_SLUG)
    if row is None:
        raise BacklogError("no default template is installed; run `backlog doctor`")
    return row


def list_all(conn: Conn) -> list[Row]:
    return conn.execute(
        "SELECT t.*, "
        "  (SELECT COUNT(*) FROM template_workflow w WHERE w.template_id = t.id) AS workflows, "
        "  (SELECT COUNT(*) FROM project p WHERE p.template_id = t.id) AS projects "
        "FROM template t ORDER BY t.is_default DESC, t.slug"
    ).fetchall()


def workflows_of(conn: Conn, template_id: int) -> dict[str, Row]:
    return {r["task_type"]: r for r in conn.execute(
        "SELECT * FROM template_workflow WHERE template_id = ? ORDER BY task_type",
        (template_id,),
    ).fetchall()}


def statuses_of(conn: Conn, template_workflow_id: int) -> list[Row]:
    return conn.execute(
        "SELECT * FROM template_status WHERE template_workflow_id = ? ORDER BY position, id",
        (template_workflow_id,),
    ).fetchall()


def transitions_of(conn: Conn, template_workflow_id: int) -> list[Row]:
    return conn.execute(
        "SELECT * FROM template_transition WHERE template_workflow_id = ? "
        "ORDER BY from_status, to_status", (template_workflow_id,),
    ).fetchall()


# --------------------------------------------------------------------------- #
# instantiation — template -> a project's own workflow rows
# --------------------------------------------------------------------------- #

def instantiate(conn: Conn, template_id: int, project_id: int,
                task_type: str | None = None, replace: bool = False) -> list[str]:
    """Copy a template's workflows onto a project.

    This is a copy, not a reference: the project owns the result and may edit
    it freely, and a later change to the template does not reach back.
    """
    tpl_workflows = workflows_of(conn, template_id)
    done: list[str] = []
    for ttype in ([task_type] if task_type else TASK_TYPES):
        tpl = tpl_workflows.get(ttype)
        if tpl is None:
            continue
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
            [(wf_id, s["slug"], s["display"], s["category"], s["position"],
              s["satisfies_dependency"], s["is_initial"], s["is_terminal"], s["description"])
             for s in statuses_of(conn, tpl["id"])],
        )
        conn.executemany(
            "INSERT INTO workflow_transition(workflow_id, from_status, to_status, gates, note) "
            "VALUES(?,?,?,?,?)",
            [(wf_id, t["from_status"], t["to_status"], t["gates"], t["note"])
             for t in transitions_of(conn, tpl["id"])],
        )
        done.append(ttype)
    conn.commit()
    return done


# --------------------------------------------------------------------------- #
# authoring
# --------------------------------------------------------------------------- #

def create(conn: Conn, slug: str, name: str, description: str = "",
           copy_of: str | None = None, from_project: int | None = None) -> Row:
    slug = slugify(slug)
    if get(conn, slug) is not None:
        raise BacklogError(f"a template '{slug}' already exists")
    ts = utcnow()
    tid = conn.insert_returning_id(
        "INSERT INTO template(slug, name, description, is_default, builtin, created_at, "
        "updated_at) VALUES(?,?,?,0,0,?,?)",
        (slug, name or slug, description, ts, ts),
    )
    if copy_of:
        src = require(conn, copy_of)
        for ttype, wf in workflows_of(conn, int(src["id"])).items():
            new_id = conn.insert_returning_id(
                "INSERT INTO template_workflow(template_id, task_type, name, description) "
                "VALUES(?,?,?,?)", (tid, ttype, wf["name"], wf["description"]),
            )
            _copy_rows(conn, wf["id"], new_id)
    elif from_project is not None:
        for ttype in TASK_TYPES:
            wf = conn.execute(
                "SELECT * FROM workflow WHERE project_id = ? AND task_type = ?",
                (from_project, ttype),
            ).fetchone()
            if wf is None:
                continue
            new_id = conn.insert_returning_id(
                "INSERT INTO template_workflow(template_id, task_type, name, description) "
                "VALUES(?,?,?,?)", (tid, ttype, wf["name"], wf["description"]),
            )
            conn.executemany(
                "INSERT INTO template_status(template_workflow_id, slug, display, category, "
                "position, satisfies_dependency, is_initial, is_terminal, description) "
                "VALUES(?,?,?,?,?,?,?,?,?)",
                [(new_id, s["slug"], s["display"], s["category"], s["position"],
                  s["satisfies_dependency"], s["is_initial"], s["is_terminal"],
                  s["description"])
                 for s in conn.execute(
                     "SELECT * FROM workflow_status WHERE workflow_id = ? ORDER BY position",
                     (wf["id"],)).fetchall()],
            )
            conn.executemany(
                "INSERT INTO template_transition(template_workflow_id, from_status, "
                "to_status, gates, note) VALUES(?,?,?,?,?)",
                [(new_id, t["from_status"], t["to_status"], t["gates"], t["note"])
                 for t in conn.execute(
                     "SELECT * FROM workflow_transition WHERE workflow_id = ?",
                     (wf["id"],)).fetchall()],
            )
    else:
        # An empty template would be a trap; start from the default.
        src = default(conn)
        for ttype, wf in workflows_of(conn, int(src["id"])).items():
            new_id = conn.insert_returning_id(
                "INSERT INTO template_workflow(template_id, task_type, name, description) "
                "VALUES(?,?,?,?)", (tid, ttype, wf["name"], wf["description"]),
            )
            _copy_rows(conn, wf["id"], new_id)
    conn.commit()
    row = get(conn, slug)
    assert row is not None
    return row


def _copy_rows(conn: Conn, src_wf_id: int, dst_wf_id: int) -> None:
    conn.executemany(
        "INSERT INTO template_status(template_workflow_id, slug, display, category, "
        "position, satisfies_dependency, is_initial, is_terminal, description) "
        "VALUES(?,?,?,?,?,?,?,?,?)",
        [(dst_wf_id, s["slug"], s["display"], s["category"], s["position"],
          s["satisfies_dependency"], s["is_initial"], s["is_terminal"], s["description"])
         for s in statuses_of(conn, src_wf_id)],
    )
    conn.executemany(
        "INSERT INTO template_transition(template_workflow_id, from_status, to_status, "
        "gates, note) VALUES(?,?,?,?,?)",
        [(dst_wf_id, t["from_status"], t["to_status"], t["gates"], t["note"])
         for t in transitions_of(conn, src_wf_id)],
    )


def add_status(conn: Conn, slug: str, task_type: str, status_slug: str, display: str,
               category: str = "active", after: str | None = None,
               satisfies: bool = False, terminal: bool = False) -> Row:
    tpl = require(conn, slug)
    wf = workflows_of(conn, int(tpl["id"])).get(task_type)
    if wf is None:
        raise BacklogError(f"template '{tpl['slug']}' has no {task_type} workflow")
    if category not in STATUS_CATEGORIES:
        raise BacklogError(f"category must be one of {', '.join(STATUS_CATEGORIES)}")
    rows = statuses_of(conn, int(wf["id"]))
    status_slug = status_slug.strip().lower().replace("-", "_").replace(" ", "_")
    if any(r["slug"] == status_slug for r in rows):
        raise BacklogError(f"{task_type} flow already has a status {status_slug!r}")
    if after:
        anchor = next((r for r in rows if r["slug"] == after), None)
        if anchor is None:
            raise BacklogError(f"no status {after!r} to place it after")
        position = int(anchor["position"]) + 1
        conn.execute(
            "UPDATE template_status SET position = position + 1 "
            "WHERE template_workflow_id = ? AND position >= ?", (wf["id"], position),
        )
    else:
        position = len(rows)
    conn.execute(
        "INSERT INTO template_status(template_workflow_id, slug, display, category, "
        "position, satisfies_dependency, is_initial, is_terminal) VALUES(?,?,?,?,?,?,0,?)",
        (wf["id"], status_slug, display or status_slug.replace("_", " ").title(),
         category, position, 1 if satisfies else 0, 1 if terminal else 0),
    )
    conn.commit()
    return conn.execute(
        "SELECT * FROM template_status WHERE template_workflow_id = ? AND slug = ?",
        (wf["id"], status_slug),
    ).fetchone()


def set_transition(conn: Conn, slug: str, task_type: str, from_status: str,
                   to_status: str, gates: str = "") -> None:
    tpl = require(conn, slug)
    wf = workflows_of(conn, int(tpl["id"])).get(task_type)
    if wf is None:
        raise BacklogError(f"template '{tpl['slug']}' has no {task_type} workflow")
    for g in (x.strip() for x in gates.split(",") if x.strip()):
        if g not in GATE_CHECKS:
            raise BacklogError(f"unknown gate {g!r}. Valid: {', '.join(GATE_CHECKS)}")
    conn.execute(
        "INSERT INTO template_transition(template_workflow_id, from_status, to_status, gates) "
        "VALUES(?,?,?,?) ON CONFLICT(template_workflow_id, from_status, to_status) "
        "DO UPDATE SET gates = excluded.gates",
        (wf["id"], from_status, to_status,
         ",".join(x.strip() for x in gates.split(",") if x.strip())),
    )
    conn.commit()


def remove(conn: Conn, slug: str) -> None:
    tpl = require(conn, slug)
    if tpl["is_default"]:
        raise BacklogError(
            f"'{tpl['slug']}' is the default template; make another one the default first"
        )
    used = conn.execute(
        "SELECT COUNT(*) AS n FROM project WHERE template_id = ?", (tpl["id"],)
    ).fetchone()["n"]
    if used:
        raise BacklogError(
            f"{used} project(s) were created from '{tpl['slug']}'. Removing it would lose "
            "the record of where their flow came from; point them elsewhere first."
        )
    conn.execute("DELETE FROM template WHERE id = ?", (tpl["id"],))
    conn.commit()


def set_default(conn: Conn, slug: str) -> Row:
    tpl = require(conn, slug)
    conn.execute("UPDATE template SET is_default = 0")
    conn.execute("UPDATE template SET is_default = 1, updated_at = ? WHERE id = ?",
                 (utcnow(), tpl["id"]))
    conn.commit()
    return require(conn, slug)


def render(conn: Conn, template_id: int, task_type: str) -> str:
    from .render import table

    wf = workflows_of(conn, template_id).get(task_type)
    if wf is None:
        return f"(no {task_type} workflow in this template)"
    statuses = statuses_of(conn, int(wf["id"]))
    display_of = {s["slug"]: s["display"] for s in statuses}
    transitions: dict[str, list[str]] = {}
    for t in transitions_of(conn, int(wf["id"])):
        label = display_of.get(t["to_status"], t["to_status"]) + (
            f" ({t['gates'].replace(',', ' + ')})" if t["gates"] else "")
        transitions.setdefault(t["from_status"], []).append(label)
    rows = []
    for s in statuses:
        flags = []
        if s["is_initial"]:
            flags.append("initial")
        if s["satisfies_dependency"]:
            flags.append("counts as finished")
        if s["is_terminal"]:
            flags.append("terminal")
        rows.append([s["display"], s["slug"], s["category"], ", ".join(flags),
                     ", ".join(sorted(transitions.get(s["slug"], []))) or "(terminal)"])
    return table(["STATUS", "SLUG", "CATEGORY", "FLAGS", "LEGAL NEXT (gates)"], rows)

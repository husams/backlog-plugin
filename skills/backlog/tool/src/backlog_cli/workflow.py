"""Per-project, per-task-type workflows.

A workflow is data: a set of statuses and the legal moves between them, stored
per (project, task type). Two projects can therefore run completely different
flows — extra statuses, a different route, different gates — without a code
change, and an agent working in either one reads the flow rather than assuming
it.

What stays in code is the *meaning* of things the engine has to reason about:

    category  which column of the board a status sits in, and whether work in
              it counts as not-started / started / finished
    gates     the named checks a transition may demand; each one is a piece of
              code, but which of them apply to which move is a row
"""

from __future__ import annotations

from .db import BacklogError, Conn, Row, utcnow
from .schema import GATE_CHECKS, STATUS_CATEGORIES, TASK_TYPES


class Workflow:
    """One task type's flow, loaded from the store."""

    __slots__ = ("id", "project_id", "task_type", "name", "description",
                 "statuses", "ordered", "transitions")

    def __init__(self, row: Row, statuses: list[Row], transitions: list[Row]):
        self.id = row["id"]
        self.project_id = row["project_id"]
        self.task_type = row["task_type"]
        self.name = row["name"]
        self.description = row["description"]
        self.statuses = {s["slug"]: s for s in statuses}
        self.ordered = statuses
        self.transitions: dict[str, dict[str, str]] = {}
        for t in transitions:
            self.transitions.setdefault(t["from_status"], {})[t["to_status"]] = t["gates"]

    # -- statuses ----------------------------------------------------------- #

    @property
    def initial(self) -> str:
        for s in self.ordered:
            if s["is_initial"]:
                return s["slug"]
        return self.ordered[0]["slug"] if self.ordered else "created"

    @property
    def terminal(self) -> str | None:
        """The single terminal status, or None when the flow has zero or many."""
        terminal = [s["slug"] for s in self.ordered if s["is_terminal"]]
        return terminal[0] if len(terminal) == 1 else None

    def display(self, slug: str) -> str:
        row = self.statuses.get(slug)
        return row["display"] if row else slug

    def category(self, slug: str) -> str:
        row = self.statuses.get(slug)
        return row["category"] if row else "active"

    def satisfies(self, slug: str) -> bool:
        """Does a task in this status stop blocking its dependents?"""
        row = self.statuses.get(slug)
        return bool(row["satisfies_dependency"]) if row else False

    def is_open(self, slug: str) -> bool:
        return self.category(slug) not in ("done", "dropped")

    def resolve(self, value: str) -> str:
        """Accept a slug or a display name, in any casing."""
        want = value.strip().lower().replace("-", "_").replace(" ", "_")
        while "__" in want:
            want = want.replace("__", "_")
        if want in self.statuses:
            return want
        for slug, row in self.statuses.items():
            if row["display"].strip().lower().replace(" ", "_").replace("-", "_") == want:
                return slug
        raise BacklogError(
            f"unknown status {value!r} for a {self.task_type} in this project. "
            "Valid: " + ", ".join(s["display"] for s in self.ordered)
            + f"\n(`backlog workflow show --type {self.task_type}` prints the flow)"
        )

    # -- transitions -------------------------------------------------------- #

    def next_from(self, slug: str) -> dict[str, str]:
        return self.transitions.get(slug, {})

    def gates_for(self, from_slug: str, to_slug: str) -> list[str]:
        gates = self.next_from(from_slug).get(to_slug)
        if gates is None:
            return []
        return [g.strip() for g in gates.split(",") if g.strip()]

    def allows(self, from_slug: str, to_slug: str) -> bool:
        return to_slug in self.next_from(from_slug)


# --------------------------------------------------------------------------- #
# loading and seeding
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
    from . import templates

    row = conn.execute("SELECT template_id FROM project WHERE id = ?", (project_id,)).fetchone()
    if row is not None and row["template_id"]:
        tpl = conn.execute("SELECT * FROM template WHERE id = ?", (row["template_id"],)).fetchone()
        if tpl is not None:
            return tpl
    return templates.default(conn)


def seed(conn: Conn, project_id: int, task_type: str, name: str = "") -> int:
    """Instantiate one task type's flow from the project's template."""
    from . import templates

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
    from . import templates

    tpl = template_of(conn, project_id)
    templates.instantiate(conn, int(tpl["id"]), project_id)


def reset(conn: Conn, project_id: int, task_type: str | None = None) -> None:
    """Back to the project's template, discarding local edits to the flow."""
    from . import templates

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
# editing
# --------------------------------------------------------------------------- #

def add_status(conn: Conn, project_id: int, task_type: str, slug: str, display: str,
               category: str = "active", after: str | None = None,
               satisfies: bool = False, terminal: bool = False,
               description: str = "") -> Row:
    wf = get(conn, project_id, task_type)
    slug = slug.strip().lower().replace("-", "_").replace(" ", "_")
    if slug in wf.statuses:
        raise BacklogError(f"{task_type} already has a status {slug!r}")
    if category not in STATUS_CATEGORIES:
        raise BacklogError(f"category must be one of {', '.join(STATUS_CATEGORIES)}")
    if after:
        anchor = wf.resolve(after)
        position = int(wf.statuses[anchor]["position"]) + 1
        conn.execute(
            "UPDATE workflow_status SET position = position + 1 "
            "WHERE workflow_id = ? AND position >= ?", (wf.id, position),
        )
    else:
        position = len(wf.ordered)
    conn.execute(
        "INSERT INTO workflow_status(workflow_id, slug, display, category, position, "
        "satisfies_dependency, is_initial, is_terminal, description) VALUES(?,?,?,?,?,?,0,?,?)",
        (wf.id, slug, display or slug.replace("_", " ").title(), category, position,
         1 if satisfies else 0, 1 if terminal else 0, description),
    )
    conn.commit()
    return conn.execute(
        "SELECT * FROM workflow_status WHERE workflow_id = ? AND slug = ?", (wf.id, slug)
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
    conn.execute("DELETE FROM workflow_status WHERE workflow_id = ? AND slug = ?", (wf.id, slug))
    conn.execute(
        "DELETE FROM workflow_transition WHERE workflow_id = ? AND (from_status = ? OR to_status = ?)",
        (wf.id, slug, slug),
    )
    conn.commit()


def set_transition(conn: Conn, project_id: int, task_type: str, from_status: str,
                   to_status: str, gates: str = "", note: str = "") -> None:
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


def remove_transition(conn: Conn, project_id: int, task_type: str,
                      from_status: str, to_status: str) -> None:
    wf = get(conn, project_id, task_type)
    f, t = wf.resolve(from_status), wf.resolve(to_status)
    cur = conn.execute(
        "DELETE FROM workflow_transition WHERE workflow_id = ? AND from_status = ? "
        "AND to_status = ?", (wf.id, f, t),
    )
    conn.commit()
    if not cur.rowcount:
        raise BacklogError(f"no {f} -> {t} transition on the {task_type} flow")


def render(wf: Workflow) -> str:
    """The flow as a table an agent can read before moving anything."""
    rows = []
    for s in wf.ordered:
        nxt = wf.next_from(s["slug"])
        moves = ", ".join(
            wf.display(t) + (f" ({g.replace(',', ' + ')})" if g else "")
            for t, g in sorted(nxt.items())
        ) or "(terminal)"
        flags = []
        if s["is_initial"]:
            flags.append("initial")
        if s["satisfies_dependency"]:
            flags.append("counts as finished")
        if s["is_terminal"]:
            flags.append("terminal")
        rows.append([s["display"], s["slug"], s["category"], ", ".join(flags), moves])
    from .render import table

    return table(["STATUS", "SLUG", "CATEGORY", "FLAGS", "LEGAL NEXT (gates)"], rows)

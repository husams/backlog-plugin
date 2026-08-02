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

from ..db import BacklogError, Conn, Row, utcnow
from ..schema import GATE_CHECKS, STATUS_CATEGORIES, TASK_TYPES


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

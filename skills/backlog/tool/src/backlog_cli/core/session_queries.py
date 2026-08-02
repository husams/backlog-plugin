"""Read-only task, workflow, and dependency API methods."""

from .. import deps, hooks, workflow
from ..db import BacklogError, require_backlog_dir
from ..hooks import Action
from ..types import Task
from . import ACTIONABLE_BY_DEV
from .artifacts import list_artifacts
from .normalization import normalize_type


def counts(self) -> dict[str, int]:
    """How many tasks sit in each status. Useful for a one-line board."""
    rows = self._conn.execute(
        "SELECT status, COUNT(*) AS n FROM task WHERE project_id = ? GROUP BY status",
        (self.pid,),
    ).fetchall()
    return {r["status"]: int(r["n"]) for r in rows}


def task_type_counts(self) -> dict[str, int]:
    """How many rows exist for each task type, including Iterations."""
    rows = self._conn.execute(
        "SELECT task_type,COUNT(*) AS n FROM task WHERE project_id=? GROUP BY task_type",
        (self.pid,),
    ).fetchall()
    return {r["task_type"]: int(r["n"]) for r in rows}


def statuses(self, task_type: str = "story") -> list[str]:
    """The statuses this project's flow actually defines for a task type."""
    return list(workflow.get(self._conn, self.pid, normalize_type(task_type)).statuses)


def flow(self, task_type: str = "story"):
    """The Workflow object: `.allows(a, b)`, `.next_from(s)`, `.display(s)`."""
    return workflow.get(self._conn, self.pid, normalize_type(task_type))


def actions(self, key: str) -> list[Action]:
    """Semantic actions configured for the task's current state."""
    task = self.task(key)
    config_dir = hooks.project_backlog_dir(require_backlog_dir())
    return hooks.available_actions(config_dir, task.task_type, task.status)


def startable(
    self, actor: str | None = None, iteration: str | None = None
) -> list[Task]:
    """Open, unblocked deliverables, optionally selected by Iteration."""
    selected = None
    if iteration:
        selected = self.task(iteration)
        if selected.task_type != "iteration":
            raise BacklogError(f"{selected.key} is not an Iteration")
        if selected.status != "open":
            raise BacklogError(
                f"{selected.key} is {selected.status}; work may only be "
                "selected from an Open Iteration"
            )
    out = []
    for t in self.tasks(assignee=actor, open_only=True):
        if selected:
            if t.task_type not in {"story", "bug"} or t.status not in ACTIONABLE_BY_DEV:
                continue
            if all(i.key != selected.key for i in t.iterations):
                continue
        elif t.task_type == "iteration":
            continue
        if not t.blockers:
            out.append(t)
    return out


def blocked(self) -> list[tuple[Task, list[str]]]:
    """Every open task that something unfinished is standing in front of."""
    out = []
    for t in self.tasks(open_only=True):
        names = [b["other_key"] for b in t.blockers]
        if names:
            out.append((t, names))
    return out


def cycles(self) -> list[list[str]]:
    """Dependency cycles, as lists of keys. Empty when the graph is sane."""
    return deps.cycles(self._conn)


def dependencies(self, key: str, kind: str | None = None) -> list[dict]:
    """All dependency edges touching a task, including satisfied edges."""
    task = self.task(key)
    return deps.edges_for(self._conn, task.id, kind)


def artifacts(self, key: str) -> list[dict]:
    """Every durable artifact recorded against a task."""
    task = self.task(key)
    return [dict(row) for row in list_artifacts(self._conn, task.id)]

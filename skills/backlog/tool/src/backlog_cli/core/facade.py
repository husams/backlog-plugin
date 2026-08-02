"""Thin public passthroughs for core backlog operations."""

from __future__ import annotations

from .. import deps, execution, hooks, workflow
from ..db import BacklogError, require_backlog_dir
from ..hooks import Action
from ..types import Gate, Task
from . import ACTIONABLE_BY_DEV
from .artifacts import list_artifacts
from .gates import gate, normalize_gate, trigger_action
from .items import add_item, set_items
from .normalization import normalize_item_kind, normalize_key, normalize_status, normalize_type
from .pull_requests import set_pr
from .tasks import (
    add_iteration_member,
    add_task,
    assign,
    find_task,
    get_task,
    remove_iteration_member,
)


def _validated_spec(value: dict | None) -> dict | None:
    return None if value is None else execution.parse_spec(value).canonical()


def _prepare_items(items: list[str | dict]) -> list[tuple[str, dict | None]]:
    prepared: list[tuple[str, dict | None]] = []
    for item in items:
        if isinstance(item, str):
            content, spec = item, None
        elif isinstance(item, dict):
            unknown = set(item) - {"content", "execution"}
            if unknown:
                raise BacklogError("unknown item fields: " + ", ".join(sorted(unknown)))
            content = item.get("content")
            spec = _validated_spec(item.get("execution"))
        else:
            raise TypeError("items must be strings or {content, execution} mappings")
        if not isinstance(content, str) or not content.strip():
            raise BacklogError("item content must be a non-empty string")
        prepared.append((content.strip(), spec))
    return prepared


class CoreApi:
    __slots__ = ()

    def create_task(
        self, task_type: str, title: str, *, parent: str | None = None,
        description: str = "", priority: str = "P2", owner: str | None = None,
        assignee: str | None = None, reviewer: str | None = None,
        branch: str | None = None, acceptance_criteria: list[str | dict] | None = None,
    ) -> Task:
        """Create a task; criteria may be strings or ``{content, execution}`` mappings."""
        prepared = _prepare_items(acceptance_criteria or [])
        row = add_task(
            self._conn, self.pid, task_type, title, parent=parent,
            description=description, priority=priority, owner=owner,
            assignee=assignee, reviewer=reviewer, branch=branch, actor=self.actor,
        )
        for content, spec in prepared:
            item = add_item(
                self._conn, self.pid, row["key"], "acceptance_criteria",
                content, actor=self.actor,
            )
            if spec is not None:
                execution.set_executable(self._conn, item["id"], spec)
        return self._task(get_task(self._conn, self.pid, row["key"]))

    def create_feature(self, title: str, **kwargs) -> Task:
        """Create a feature with optional plain or executable criteria."""
        kwargs.pop("branch", None)
        return self.create_task("feature", title, **kwargs)

    def create_story(self, title: str, *, feature: str | None = None, **kwargs) -> Task:
        """Create a story with optional plain or executable criteria."""
        return self.create_task("story", title, parent=feature, **kwargs)

    def create_bug(self, title: str, **kwargs) -> Task:
        """Create a standalone bug with optional plain or executable criteria."""
        return self.create_task("bug", title, parent=None, **kwargs)

    def create_iteration(self, title: str, **kwargs) -> Task:
        """Create a standalone parallel unit of work."""
        kwargs.pop("branch", None)
        return self.create_task("iteration", title, parent=None, **kwargs)

    def add_iteration_member(self, iteration: str, member: str) -> None:
        """Associate deliverable work with an Iteration without changing its status."""
        add_iteration_member(self._conn, self.pid, iteration, member, actor=self.actor)

    def remove_iteration_member(self, iteration: str, member: str) -> None:
        """Remove work from an Open Iteration without changing the work itself."""
        remove_iteration_member(self._conn, self.pid, iteration, member, actor=self.actor)

    def add_item(
        self, key: str, kind: str, content: str, *, execution_spec: dict | None = None
    ) -> dict:
        """Add one plain, shell, or hook item and return its details."""
        spec = _validated_spec(execution_spec)
        normalized_kind = normalize_item_kind(kind)
        if spec is not None and normalized_kind not in ("acceptance_criteria", "checklist"):
            raise BacklogError(
                "only acceptance criteria and checklist items may declare execution"
            )
        row = add_item(
            self._conn, self.pid, key, normalized_kind, content, actor=self.actor
        )
        if spec is not None:
            execution.set_executable(self._conn, row["id"], spec)
        return execution._item_details(self._conn, row)

    def set_items(self, key: str, kind: str, items: list[str | dict]) -> list[dict]:
        """Replace one item kind using strings or ``{content, execution}`` mappings."""
        prepared = _prepare_items(items)
        normalized_kind = normalize_item_kind(kind)
        if (
            any(spec is not None for _, spec in prepared)
            and normalized_kind not in ("acceptance_criteria", "checklist")
        ):
            raise BacklogError(
                "only acceptance criteria and checklist items may declare execution"
            )
        rows = set_items(
            self._conn, self.pid, key, normalized_kind,
            [content for content, _ in prepared], actor=self.actor,
        )
        for row, (_, spec) in zip(rows, prepared):
            if spec is not None:
                execution.set_executable(self._conn, row["id"], spec)
        return [execution._item_details(self._conn, row) for row in rows]

    def task(self, key: str) -> Task:
        """One task by key. Raises BacklogError if it does not exist."""
        return self._task(get_task(self._conn, self.pid, key))

    def find(self, key: str) -> Task | None:
        row = find_task(self._conn, self.pid, key)
        return self._task(row) if row is not None else None

    def _task(self, row) -> Task:
        """Give every Task accessor the same parent projection."""
        data = dict(row)
        parent_id = data.get("parent_id")
        if "parent_key" not in data:
            parent = (
                self._conn.execute("SELECT key FROM task WHERE id = ?", (parent_id,)).fetchone()
                if parent_id is not None else None
            )
            data["parent_key"] = parent["key"] if parent is not None else None
        return Task(data, self)

    def tasks(self, *, status: str | None = None, task_type: str | None = None,
              assignee: str | None = None, reviewer: str | None = None,
              parent: str | None = None, open_only: bool = False) -> list[Task]:
        """Every task matching the filters, ordered by priority then key."""
        where, params = [], []
        if status:
            where.append("t.status = ?")
            params.append(normalize_status(status))
        if task_type:
            where.append("t.task_type = ?")
            params.append(normalize_type(task_type))
        if assignee:
            where.append("t.assignee = ?")
            params.append(assignee)
        if reviewer:
            where.append("t.reviewer = ?")
            params.append(reviewer)
        if parent:
            where.append("t.parent_id = (SELECT id FROM task WHERE project_id = ? AND key = ?)")
            params += [self.pid, normalize_key(parent)]
        if open_only:
            where.append("t.closed_at IS NULL")
        sql = ("SELECT t.*, p.key AS parent_key FROM task t "
               "LEFT JOIN task p ON p.id = t.parent_id WHERE t.project_id = ?")
        if where:
            sql += " AND " + " AND ".join(where)
        rows = self._conn.execute(sql + " ORDER BY t.priority, t.key",
                                  [self.pid, *params]).fetchall()
        return [self._task(r) for r in rows]

    def counts(self) -> dict[str, int]:
        """How many tasks sit in each status. Useful for a one-line board."""
        rows = self._conn.execute(
            "SELECT status, COUNT(*) AS n FROM task WHERE project_id = ? GROUP BY status",
            (self.pid,)).fetchall()
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
        return hooks.available_actions(
            config_dir, task.task_type, task.status
        )

    def startable(self, actor: str | None = None,
                  iteration: str | None = None) -> list[Task]:
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
                if (t.task_type not in {"story", "bug"}
                        or t.status not in ACTIONABLE_BY_DEV):
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

    def can(self, key: str, target: str = "merge", **waivers) -> Gate:
        """Evaluate a gate without moving anything.

        `target` is one of start / in_review / accepted / done / merge.
        Waivers mirror the CLI flags: allow_blocked, no_pr, allow_open_children.
        """
        ok, checks = gate(self._conn, self.pid, key, target, **waivers)
        return Gate(normalize_key(key), normalize_gate(target), ok,
                    [(c.name, c.ok, c.detail) for c in checks])

    def trigger(self, key: str, action: Action, *,
                actor: str | None = None, operation: str = "api.trigger",
                parameters: dict | None = None, **waivers) -> Task:
        """Submit an action; workflow configuration selects the destination."""
        if not isinstance(action, Action):
            raise TypeError("action must be an Action")
        if action in hooks.THREAD_MANAGED_ACTIONS:
            raise BacklogError(
                f"{action.value} is managed by review threads; use review_open, "
                "review_reply, or review_reopen"
            )
        row, _, _ = trigger_action(
            self._conn,
            self.pid,
            key,
            action,
            actor=actor or self.actor,
            operation=operation,
            parameters=parameters,
            **waivers,
        )
        return self._task(row)

    def set_pr(self, key: str, *, url: str | None = None,
               number: int | None = None, repo: str | None = None,
               state: str | None = None, review_state: str | None = None,
               actor: str | None = None) -> Task:
        """Record PR state and emit the corresponding standard PR action."""
        row = set_pr(
            self._conn,
            self.pid,
            key,
            url=url,
            number=number,
            repo=repo,
            state=state,
            review_state=review_state,
            actor=actor or self.actor,
        )
        return self._task(row)

    def assign(self, key: str, to: str | None = None, reviewer: str | None = None,
               actor: str | None = None) -> Task:
        row = assign(self._conn, self.pid, key, to=to, reviewer=reviewer,
                          actor=actor or self.actor)
        self._conn.commit()
        return self._task(row)

"""Task and item API methods."""

from .. import execution
from ..db import BacklogError
from ..types import Task
from .items import add_item as insert_item, set_items as replace_items
from .normalization import (
    normalize_item_kind,
    normalize_key,
    normalize_status,
    normalize_type,
)
from .iterations import (
    add_iteration_member as link_iteration_member,
    remove_iteration_member as unlink_iteration_member,
)
from .task_queries import find_task as find_task_row, get_task as get_task_row
from .tasks import (
    add_task as insert_task,
)
from .todos import (
    add_todos as insert_todos,
    list_todos as read_todos,
    move_todo as reorder_todo,
    set_state as set_todo_state,
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


def create_task(
    self,
    task_type: str,
    title: str,
    *,
    parent: str | None = None,
    description: str = "",
    priority: str = "P2",
    owner: str | None = None,
    assignee: str | None = None,
    reviewer: str | None = None,
    branch: str | None = None,
    acceptance_criteria: list[str | dict] | None = None,
) -> Task:
    """Create a task; criteria may be strings or ``{content, execution}`` mappings."""
    prepared = _prepare_items(acceptance_criteria or [])
    row = insert_task(
        self._conn,
        self.pid,
        task_type,
        title,
        parent=parent,
        description=description,
        priority=priority,
        owner=owner,
        assignee=assignee,
        reviewer=reviewer,
        branch=branch,
        actor=self.actor,
    )
    for content, spec in prepared:
        item = insert_item(
            self._conn,
            self.pid,
            row["key"],
            "acceptance_criteria",
            content,
            actor=self.actor,
        )
        if spec is not None:
            execution.set_executable(self._conn, item["id"], spec)
    return self._task(get_task_row(self._conn, self.pid, row["key"]))


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
    link_iteration_member(self._conn, self.pid, iteration, member, actor=self.actor)


def remove_iteration_member(self, iteration: str, member: str) -> None:
    """Remove work from an Open Iteration without changing the work itself."""
    unlink_iteration_member(self._conn, self.pid, iteration, member, actor=self.actor)


def add_item(
    self, key: str, kind: str, content: str, *, execution_spec: dict | None = None
) -> dict:
    """Add one plain, shell, or hook item and return its details."""
    spec = _validated_spec(execution_spec)
    normalized_kind = normalize_item_kind(kind)
    if normalized_kind == "todo":
        raise BacklogError("todos must be added through add_todo() or add_todos()")
    if spec is not None and normalized_kind not in (
        "acceptance_criteria",
        "checklist",
    ):
        raise BacklogError(
            "only acceptance criteria and checklist items may declare execution"
        )
    row = insert_item(
        self._conn, self.pid, key, normalized_kind, content, actor=self.actor
    )
    if spec is not None:
        execution.set_executable(self._conn, row["id"], spec)
    return execution._item_details(self._conn, row)


def set_items(self, key: str, kind: str, items: list[str | dict]) -> list[dict]:
    """Replace one item kind using strings or ``{content, execution}`` mappings."""
    prepared = _prepare_items(items)
    normalized_kind = normalize_item_kind(kind)
    if normalized_kind == "todo":
        raise BacklogError("todos cannot be replaced; use the dedicated todo operations")
    if any(spec is not None for _, spec in prepared) and normalized_kind not in (
        "acceptance_criteria",
        "checklist",
    ):
        raise BacklogError(
            "only acceptance criteria and checklist items may declare execution"
        )
    rows = replace_items(
        self._conn,
        self.pid,
        key,
        normalized_kind,
        [content for content, _ in prepared],
        actor=self.actor,
    )
    for row, (_, spec) in zip(rows, prepared):
        if spec is not None:
            execution.set_executable(self._conn, row["id"], spec)
    return [execution._item_details(self._conn, row) for row in rows]


def add_todo(self, key: str, content: str) -> dict:
    """Append one open todo and return its persisted public view."""
    return insert_todos(self._conn, self.pid, key, [content], actor=self.actor)[0]


def add_todos(self, key: str, contents: list[str]) -> list[dict]:
    """Append several open todos atomically in the supplied order."""
    if not isinstance(contents, list):
        raise TypeError("contents must be a list of strings")
    return insert_todos(self._conn, self.pid, key, contents, actor=self.actor)


def todos(self, key: str) -> list[dict]:
    """List a task's todos in stable contiguous order."""
    return read_todos(self._conn, self.pid, key)


def close_todo(self, todo_id: int) -> dict:
    """Close one open todo."""
    return set_todo_state(
        self._conn, self.pid, todo_id, done=True, actor=self.actor
    )


def reopen_todo(self, todo_id: int) -> dict:
    """Reopen one closed todo."""
    return set_todo_state(
        self._conn, self.pid, todo_id, done=False, actor=self.actor
    )


def move_todo(self, todo_id: int, position: int) -> dict:
    """Move a todo to a zero-based position without changing its state."""
    return reorder_todo(
        self._conn, self.pid, todo_id, position, actor=self.actor
    )


def task(self, key: str) -> Task:
    """One task by key. Raises BacklogError if it does not exist."""
    return self._task(get_task_row(self._conn, self.pid, key))


def find(self, key: str) -> Task | None:
    row = find_task_row(self._conn, self.pid, key)
    return self._task(row) if row is not None else None


def _task(self, row) -> Task:
    """Give every Task accessor the same parent projection."""
    data = dict(row)
    parent_id = data.get("parent_id")
    if "parent_key" not in data:
        parent = (
            self._conn.execute(
                "SELECT key FROM task WHERE id = ?", (parent_id,)
            ).fetchone()
            if parent_id is not None
            else None
        )
        data["parent_key"] = parent["key"] if parent is not None else None
    return Task(data, self)


def tasks(
    self,
    *,
    status: str | None = None,
    task_type: str | None = None,
    assignee: str | None = None,
    reviewer: str | None = None,
    parent: str | None = None,
    open_only: bool = False,
) -> list[Task]:
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
        where.append(
            "t.parent_id = (SELECT id FROM task WHERE project_id = ? AND key = ?)"
        )
        params += [self.pid, normalize_key(parent)]
    if open_only:
        where.append("t.closed_at IS NULL")
    sql = (
        "SELECT t.*, p.key AS parent_key FROM task t "
        "LEFT JOIN task p ON p.id = t.parent_id WHERE t.project_id = ?"
    )
    if where:
        sql += " AND " + " AND ".join(where)
    rows = self._conn.execute(
        sql + " ORDER BY t.priority, t.key", [self.pid, *params]
    ).fetchall()
    return [self._task(r) for r in rows]

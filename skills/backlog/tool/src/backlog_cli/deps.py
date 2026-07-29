"""Dependency edges between tasks.

An edge is stored once, in the canonical direction `from <kind> to`:

    blocks      the source must finish before the target may start (gate-enforced)
    relates     soft association, no ordering
    duplicates  the source is a duplicate of the target

Both ends are `task.id`, so a feature blocking a story is the same row shape as
a subtask blocking a subtask — no union across tables, and the database keeps
the endpoints honest.
"""

from __future__ import annotations

from .db import BacklogError, Conn, Row, log_event, utcnow
from .schema import (
    DEPENDENCY_KIND_ALIASES,
    DEPENDENCY_KINDS,
    HARD_DEPENDENCY_KINDS,
    SATISFIED_STATUSES,
    SYMMETRIC_DEPENDENCY_KINDS,
)

# Resolves the task on the far side of an edge in the same query.
_OTHER = (
    "t.key AS other_key, t.title AS other_title, t.status AS other_status, "
    "t.task_type AS other_type, t.priority AS other_priority, t.id AS other_id"
)


def normalize_kind(value: str) -> str:
    slug = value.strip().lower().replace("-", "_").replace(" ", "_")
    slug = DEPENDENCY_KIND_ALIASES.get(slug, slug)
    if slug not in DEPENDENCY_KINDS:
        raise BacklogError(
            f"unknown dependency kind {value!r}. Valid: {', '.join(DEPENDENCY_KINDS)}"
        )
    return slug


def is_satisfied(status: str | None, conn: "Conn | None" = None,
                 project_id: int | None = None, task_type: str | None = None) -> bool:
    """Has the task progressed far enough to stop blocking its dependents?

    The answer belongs to the project's workflow (`satisfies_dependency`), so a
    project that adds its own terminal status is understood without a code
    change; the built-in set is only the fallback.
    """
    if conn is not None and project_id is not None and task_type:
        from . import workflow

        return workflow.get(conn, project_id, task_type).satisfies(status or "")
    return status in SATISFIED_STATUSES


def _canonical(a: int, b: int, kind: str) -> tuple[int, int]:
    """`relates` has no direction, so store one row per pair."""
    if kind in SYMMETRIC_DEPENDENCY_KINDS and a > b:
        return b, a
    return a, b


# --------------------------------------------------------------------------- #
# mutation
# --------------------------------------------------------------------------- #

def add(conn: Conn, project_id: int, from_key: str, to_key: str, kind: str = "blocks",
        note: str = "", actor: str | None = None, external_id: str | None = None) -> dict:
    from .core import get_task

    kind = normalize_kind(kind)
    src = get_task(conn, project_id, from_key)
    dst = get_task(conn, project_id, to_key)
    if src["id"] == dst["id"]:
        raise BacklogError(f"{src['key']} cannot depend on itself")
    a, b = _canonical(src["id"], dst["id"], kind)

    # Check for the edge first: it is an indexed lookup, whereas the cycle check
    # walks every `blocks` edge. An edge that is already recorded cannot
    # introduce a cycle, and re-syncing an unchanged graph is the common case.
    existing = conn.execute(
        "SELECT * FROM dependency WHERE from_task_id=? AND to_task_id=? AND kind=?",
        (a, b, kind),
    ).fetchone()
    if existing is not None:
        if (note and note != existing["note"]) or (
            external_id and external_id != existing["external_id"]
        ):
            conn.execute(
                "UPDATE dependency SET note = COALESCE(NULLIF(?,''), note), "
                "external_id = COALESCE(?, external_id) WHERE id = ?",
                (note, external_id, existing["id"]),
            )
            conn.commit()
        return {**dict(existing), "created": False,
                "from_key": src["key"], "to_key": dst["key"]}

    if kind in HARD_DEPENDENCY_KINDS:
        path = cycle_path(conn, a, b)
        if path:
            keys = _keys_for(conn, path)
            raise BacklogError(
                "that edge would create a dependency cycle: " + " -> ".join(keys)
            )

    conn.execute(
        "INSERT INTO dependency(from_task_id, to_task_id, kind, note, external_id, "
        "created_at, created_by) VALUES(?,?,?,?,?,?,?)",
        (a, b, kind, note, external_id, utcnow(), actor),
    )
    detail = f"{src['key']} {kind} {dst['key']}"
    log_event(conn, "dependency", project_id, src["id"], src["key"], actor,
              to_value=dst["key"], detail=detail)
    log_event(conn, "dependency", project_id, dst["id"], dst["key"], actor,
              from_value=src["key"], detail=detail)
    conn.commit()
    row = conn.execute(
        "SELECT * FROM dependency WHERE from_task_id=? AND to_task_id=? AND kind=?",
        (a, b, kind),
    ).fetchone()
    return {**dict(row), "created": True, "from_key": src["key"], "to_key": dst["key"]}


def remove(conn: Conn, project_id: int, from_key: str, to_key: str,
           kind: str = "blocks", actor: str | None = None) -> dict:
    from .core import get_task

    kind = normalize_kind(kind)
    src = get_task(conn, project_id, from_key)
    dst = get_task(conn, project_id, to_key)
    a, b = _canonical(src["id"], dst["id"], kind)
    row = conn.execute(
        "SELECT * FROM dependency WHERE from_task_id=? AND to_task_id=? AND kind=?",
        (a, b, kind),
    ).fetchone()
    if row is None:
        raise BacklogError(f"no {kind} edge {src['key']} -> {dst['key']}")
    conn.execute("DELETE FROM dependency WHERE id = ?", (row["id"],))
    detail = f"{src['key']} {kind} {dst['key']}"
    log_event(conn, "dependency_removed", project_id, src["id"], src["key"], actor,
              to_value=dst["key"], detail=detail)
    log_event(conn, "dependency_removed", project_id, dst["id"], dst["key"], actor,
              from_value=src["key"], detail=detail)
    conn.commit()
    return {**dict(row), "from_key": src["key"], "to_key": dst["key"]}


# --------------------------------------------------------------------------- #
# queries
# --------------------------------------------------------------------------- #

def outgoing(conn: Conn, task_id: int, kind: str | None = None) -> list[Row]:
    sql = (f"SELECT d.*, {_OTHER} FROM dependency d JOIN task t ON t.id = d.to_task_id "
           "WHERE d.from_task_id = ?")
    params: list = [task_id]
    if kind:
        sql += " AND d.kind = ?"
        params.append(normalize_kind(kind))
    return conn.execute(sql + " ORDER BY d.kind, t.key", params).fetchall()


def incoming(conn: Conn, task_id: int, kind: str | None = None) -> list[Row]:
    sql = (f"SELECT d.*, {_OTHER} FROM dependency d JOIN task t ON t.id = d.from_task_id "
           "WHERE d.to_task_id = ?")
    params: list = [task_id]
    if kind:
        sql += " AND d.kind = ?"
        params.append(normalize_kind(kind))
    return conn.execute(sql + " ORDER BY d.kind, t.key", params).fetchall()


def edges_for(conn: Conn, task_id: int, kind: str | None = None) -> list[dict]:
    out = [{**dict(r), "direction": "out"} for r in outgoing(conn, task_id, kind)]
    inc = [{**dict(r), "direction": "in"} for r in incoming(conn, task_id, kind)]
    edges = out + inc
    for e in edges:
        e["satisfied"] = is_satisfied(e["other_status"])
    return edges


def blockers(conn: Conn, task_id: int, open_only: bool = True) -> list[dict]:
    """Everything that must finish before this task may start."""
    out = []
    project_id = conn.execute(
        "SELECT project_id FROM task WHERE id = ?", (task_id,)
    ).fetchone()["project_id"]
    for r in incoming(conn, task_id, "blocks"):
        e = dict(r)
        e["satisfied"] = is_satisfied(e["other_status"], conn, project_id, e["other_type"])
        if open_only and e["satisfied"]:
            continue
        out.append(e)
    return out


def blocked_by_map(conn: Conn, project_id: int) -> dict[str, list[str]]:
    """key -> unsatisfied blocker keys, for the whole project. One query."""
    rows = conn.execute(
        "SELECT tgt.key AS target, src.key AS blocker, src.status AS blocker_status, "
        "       src.task_type AS blocker_type "
        "FROM dependency d "
        "JOIN task src ON src.id = d.from_task_id "
        "JOIN task tgt ON tgt.id = d.to_task_id "
        "WHERE d.kind = 'blocks' AND tgt.project_id = ? "
        "ORDER BY tgt.key, src.key",
        (project_id,),
    ).fetchall()
    out: dict[str, list[str]] = {}
    for r in rows:
        if is_satisfied(r["blocker_status"], conn, project_id, r["blocker_type"]):
            continue
        out.setdefault(r["target"], []).append(r["blocker"])
    return out


def all_edges(conn: Conn, project_id: int, kind: str | None = None) -> list[Row]:
    sql = (
        "SELECT d.*, src.key AS from_key, dst.key AS to_key, "
        "       src.status AS from_status, dst.status AS to_status "
        "FROM dependency d "
        "JOIN task src ON src.id = d.from_task_id "
        "JOIN task dst ON dst.id = d.to_task_id "
        "WHERE src.project_id = ?"
    )
    params: list = [project_id]
    if kind:
        sql += " AND d.kind = ?"
        params.append(normalize_kind(kind))
    return conn.execute(sql + " ORDER BY d.kind, src.key, dst.key", params).fetchall()


# --------------------------------------------------------------------------- #
# graph integrity
# --------------------------------------------------------------------------- #

def _blocks_adjacency(conn: Conn) -> dict[int, list[int]]:
    adj: dict[int, list[int]] = {}
    for r in conn.execute(
        "SELECT from_task_id, to_task_id FROM dependency WHERE kind='blocks' "
        "ORDER BY from_task_id, to_task_id"
    ):
        adj.setdefault(r["from_task_id"], []).append(r["to_task_id"])
    return adj


def _keys_for(conn: Conn, ids: list[int]) -> list[str]:
    if not ids:
        return []
    rows = conn.execute(
        "SELECT id, key FROM task WHERE id IN (" + ",".join("?" * len(ids)) + ")", ids
    ).fetchall()
    by_id = {r["id"]: r["key"] for r in rows}
    return [by_id.get(i, str(i)) for i in ids]


def cycle_path(conn: Conn, from_id: int, to_id: int) -> list[int] | None:
    """Would adding `from blocks to` close a loop? Return the loop if so."""
    if from_id == to_id:
        return [from_id, to_id]
    adj = _blocks_adjacency(conn)
    stack = [(to_id, [from_id, to_id])]
    seen = {to_id}
    while stack:
        node, path = stack.pop()
        for nxt in adj.get(node, []):
            if nxt == from_id:
                return path + [nxt]
            if nxt not in seen:
                seen.add(nxt)
                stack.append((nxt, path + [nxt]))
    return None


def cycles(conn: Conn) -> list[list[str]]:
    """Every `blocks` cycle already recorded, as task keys."""
    adj = _blocks_adjacency(conn)
    found: list[list[int]] = []
    seen_sets: set[frozenset[int]] = set()
    colour: dict[int, int] = {}

    def walk(node: int, path: list[int]) -> None:
        colour[node] = 1
        for nxt in adj.get(node, []):
            if colour.get(nxt) == 1:
                loop = path[path.index(nxt):] + [nxt]
                sig = frozenset(loop)
                if sig not in seen_sets:
                    seen_sets.add(sig)
                    found.append(loop)
            elif colour.get(nxt, 0) == 0:
                walk(nxt, path + [nxt])
        colour[node] = 2

    for node in sorted(adj):
        if colour.get(node, 0) == 0:
            walk(node, [node])
    return [_keys_for(conn, loop) for loop in found]


def dot(conn: Conn, project_id: int) -> str:
    """Graphviz rendering of one project's dependency graph."""
    style = {
        "blocks": 'color="#cc3333"',
        "relates": 'color="#888888" style=dashed arrowhead=none',
        "duplicates": 'color="#3366cc" style=dotted',
    }
    edges = all_edges(conn, project_id)
    lines = ["digraph backlog {", "  rankdir=LR;", '  node [shape=box fontname="monospace"];']
    seen: set[str] = set()
    for r in edges:
        for key, status in ((r["from_key"], r["from_status"]), (r["to_key"], r["to_status"])):
            if key in seen:
                continue
            seen.add(key)
            row = conn.execute(
                "SELECT title FROM task WHERE project_id = ? AND key = ?", (project_id, key)
            ).fetchone()
            title = (row["title"] if row else "?").replace('"', "'")[:44]
            lines.append(f'  "{key}" [label="{key}\\n{title}\\n[{status}]"];')
    for r in edges:
        lines.append(
            f'  "{r["from_key"]}" -> "{r["to_key"]}" '
            f'[{style[r["kind"]]} label="{r["kind"]}"];'
        )
    lines.append("}")
    return "\n".join(lines)


def dangling(conn: Conn) -> list[dict]:
    """Foreign keys make a dangling edge impossible; kept for `doctor`."""
    return []

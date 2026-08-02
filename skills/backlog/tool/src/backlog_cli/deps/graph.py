"""Dependency graph integrity and rendering."""

from __future__ import annotations

from ..db import Conn
from .queries import all_edges

# --------------------------------------------------------------------------- #


def blocks_adjacency(conn: Conn) -> dict[int, list[int]]:
    adj: dict[int, list[int]] = {}
    for r in conn.execute(
        "SELECT from_task_id, to_task_id FROM dependency WHERE kind='blocks' "
        "ORDER BY from_task_id, to_task_id"
    ):
        adj.setdefault(r["from_task_id"], []).append(r["to_task_id"])
    return adj


def keys_for(conn: Conn, ids: list[int]) -> list[str]:
    rows = conn.execute(
        "SELECT id, key FROM task WHERE id IN (" + ",".join("?" * len(ids)) + ")", ids
    ).fetchall()
    by_id = {r["id"]: r["key"] for r in rows}
    return [by_id[i] for i in ids]


def cycle_path(conn: Conn, from_id: int, to_id: int) -> list[int] | None:
    """Would adding `from blocks to` close a loop? Return the loop if so."""
    adj = blocks_adjacency(conn)
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
    adj = blocks_adjacency(conn)
    found: list[list[int]] = []
    seen_sets: set[frozenset[int]] = set()
    colour: dict[int, int] = {}

    def walk(node: int, path: list[int]) -> None:
        colour[node] = 1
        for nxt in adj.get(node, []):
            if colour.get(nxt) == 1:
                loop = path[path.index(nxt) :] + [nxt]
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
    return [keys_for(conn, loop) for loop in found]


def dot(conn: Conn, project_id: int) -> str:
    """Graphviz rendering of one project's dependency graph."""
    style = {
        "blocks": 'color="#cc3333"',
        "relates": 'color="#888888" style=dashed arrowhead=none',
        "duplicates": 'color="#3366cc" style=dotted',
    }
    edges = all_edges(conn, project_id)
    lines = [
        "digraph backlog {",
        "  rankdir=LR;",
        '  node [shape=box fontname="monospace"];',
    ]
    seen: set[str] = set()
    for r in edges:
        for key, status in (
            (r["from_key"], r["from_status"]),
            (r["to_key"], r["to_status"]),
        ):
            if key in seen:
                continue
            seen.add(key)
            row = conn.execute(
                "SELECT title FROM task WHERE project_id = ? AND key = ?",
                (project_id, key),
            ).fetchone()
            assert row is not None
            title = row["title"].replace('"', "'")[:44]
            lines.append(f'  "{key}" [label="{key}\\n{title}\\n[{status}]"];')
    for r in edges:
        lines.append(
            f'  "{r["from_key"]}" -> "{r["to_key"]}" '
            f'[{style[r["kind"]]} label="{r["kind"]}"];'
        )
    lines.append("}")
    return "\n".join(lines)

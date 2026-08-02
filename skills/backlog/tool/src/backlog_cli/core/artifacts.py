"""Durable task artifact recording and lookup."""

from __future__ import annotations

import shutil
from pathlib import Path

from ..db import BacklogError, Conn, Row, log_event, utcnow
from .task_queries import get_task


def add_artifact(
    conn: Conn,
    backlog_dir: Path,
    project_id: int,
    key: str,
    source: Path,
    title: str = "",
    kind: str = "doc",
    actor: str | None = None,
) -> dict:
    task = get_task(conn, project_id, key)
    src = Path(source).expanduser()
    if not src.exists():
        raise BacklogError(f"artifact source not found: {src}")
    dest_dir = backlog_dir / "artifacts" / task["key"]
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / src.name
    if src.resolve() != dest.resolve():
        if src.is_dir():
            shutil.copytree(src, dest, dirs_exist_ok=True)
        else:
            shutil.copy2(src, dest)
    rel = f"artifacts/{task['key']}/{src.name}"
    conn.execute(
        "INSERT INTO artifact(task_id, rel_path, title, kind, created_at, created_by) "
        "VALUES(?,?,?,?,?,?) ON CONFLICT(task_id, rel_path) DO UPDATE SET "
        "title = excluded.title, kind = excluded.kind",
        (task["id"], rel, title or src.name, kind, utcnow(), actor),
    )
    log_event(
        conn,
        "artifact",
        project_id,
        task["id"],
        task["key"],
        actor,
        to_value=rel,
        detail=title,
    )
    conn.commit()
    return {
        "key": task["key"],
        "task_type": task["task_type"],
        "rel_path": rel,
        "abs_path": str(dest),
        "title": title or src.name,
        "kind": kind,
    }


def list_artifacts(conn: Conn, task_id: int) -> list[Row]:
    return conn.execute(
        "SELECT * FROM artifact WHERE task_id = ? ORDER BY rel_path", (task_id,)
    ).fetchall()

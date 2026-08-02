"""backlog — backlog tracker for coding agents (SQLite or shared PostgreSQL)."""

from __future__ import annotations

import json
from pathlib import Path

from .. import (
    core,
)
from ..db import (
    BacklogError,
    get_or_create_project,
    resync_sequences,
)
from ..schema import (
    SCHEMA_VERSION,
    TASK_PARENT_TYPES,
)


from .context import Ctx

_EXPORT_TABLES: list[tuple[str, str]] = [
    ("meta", "key"),
    ("template", "id"),
    ("template_workflow", "id"),
    ("template_status", "id"),
    ("template_transition", "id"),
    ("project", "id"),
    ("workflow", "id"),
    ("workflow_status", "id"),
    ("workflow_transition", "id"),
    ("key_counter", "project_id"),
    ("task", "id"),
    ("iteration_member", "iteration_id, member_id"),
    ("retrospective_action", "id"),
    ("task_item", "id"),
    ("executable_item", "item_id"),
    ("execution_result", "id"),
    ("validation_waiver", "id"),
    ("dependency", "id"),
    ("artifact", "id"),
    ("review_thread", "id"),
    ("review_comment", "id"),
    ("event", "id"),
]


def cmd_export(ctx: Ctx, args) -> int:
    conn = ctx.conn
    dump = {"format": "backlog-export", "schema_version": SCHEMA_VERSION, "tables": {}}
    for t, order in _EXPORT_TABLES:
        dump["tables"][t] = [
            {k: r[k] for k in r.keys()}
            for r in conn.execute(f"SELECT * FROM {t} ORDER BY {order}")
        ]
    blob = json.dumps(dump, indent=2, sort_keys=True, default=str)
    if args.out:
        Path(args.out).expanduser().write_text(blob + "\n")
        ctx.emit({"written": args.out}, f"wrote {args.out}")
    else:
        print(blob)
    return 0


def cmd_import(ctx: Ctx, args) -> int:
    data = json.loads(Path(args.file).expanduser().read_text())
    if data.get("format") != "backlog-export":
        raise BacklogError(f"{args.file} is not a backlog export")
    version = int(data.get("schema_version", 0))
    conn = ctx.conn

    if version > SCHEMA_VERSION:
        raise BacklogError(
            f"export schema v{version} is newer than this tool (v{SCHEMA_VERSION})"
        )

    if version < 3:
        # An older dump carries the feature/item shape; convert as we load it.
        from ..db import load_v2_export

        slug = args.as_project or ctx.spec.project
        proj = get_or_create_project(conn, slug, ctx.spec)
        notes = load_v2_export(conn, int(proj["id"]), data["tables"])
        ctx.emit(
            {
                "imported": args.file,
                "project": proj["slug"],
                "converted_from_schema": version,
                "notes": notes,
            },
            f"imported {args.file} into project '{proj['slug']}' "
            f"(converted from schema v{version})\n"
            + "\n".join(f"  {n}" for n in notes),
        )
        return 0

    if not args.replace:
        raise BacklogError("import rewrites the whole store; pass --replace to confirm")
    _validate_import_tasks(data["tables"].get("task", []))
    _validate_import_retrospectives(data["tables"])
    _validate_import_workflows(data["tables"])
    if not conn.is_postgres:
        conn.execute("PRAGMA foreign_keys = OFF")
    for t, _ in reversed(_EXPORT_TABLES):
        conn.execute(f"DELETE FROM {t}")
    for t, _ in _EXPORT_TABLES:
        rows = data["tables"].get(t, [])
        if not rows:
            continue
        cols = list(rows[0].keys())
        conn.executemany(
            f"INSERT INTO {t}({','.join(cols)}) VALUES({','.join('?' for _ in cols)})",
            [tuple(r[c] for c in cols) for r in rows],
        )
    conn.commit()
    if not conn.is_postgres:
        conn.execute("PRAGMA foreign_keys = ON")
    moved = resync_sequences(conn)
    ctx.emit(
        {"imported": args.file, "sequences_resynced": moved},
        f"replaced backlog from {args.file}"
        + (f"\n  sequences resynced: {', '.join(moved)}" if moved else ""),
    )
    return 0


def _validate_import_tasks(rows: list[dict]) -> None:
    """Reject invalid task hierarchies before an import deletes existing data."""
    by_id = {row.get("id"): row for row in rows}
    for row in rows:
        key = row.get("key") or "<unknown>"
        task_type = core.normalize_type(row.get("task_type") or "")
        parent_id = row.get("parent_id")
        if parent_id is None:
            if task_type == "subtask":
                raise BacklogError(
                    f"import rejected: subtask {key} requires a parent story or bug"
                )
            continue
        parent = by_id.get(parent_id)
        if parent is None:
            raise BacklogError(
                f"import rejected: {key} refers to missing parent id {parent_id}"
            )
        parent_type = core.normalize_type(parent.get("task_type") or "")
        if parent_type not in TASK_PARENT_TYPES[task_type]:
            raise BacklogError(
                f"import rejected: a {task_type} cannot sit under a {parent_type} "
                f"({parent.get('key') or parent_id})"
            )


def _validate_import_workflows(tables: dict[str, list[dict]]) -> None:
    """Reject task types whose project workflow is absent before replacing data."""
    available = {
        (row.get("project_id"), row.get("task_type"))
        for row in tables.get("workflow", [])
    }
    projects = {row.get("id"): row.get("slug") for row in tables.get("project", [])}
    missing = sorted(
        {
            (row.get("project_id"), core.normalize_type(row.get("task_type") or ""))
            for row in tables.get("task", [])
            if core.normalize_type(row.get("task_type") or "") in ("bug", "iteration")
            if (row.get("project_id"), core.normalize_type(row.get("task_type") or ""))
            not in available
        }
    )
    if missing:
        project_id, task_type = missing[0]
        raise BacklogError(
            f"import rejected: {task_type} tasks require a {task_type} workflow "
            f"in project {projects.get(project_id) or project_id}; run "
            "`backlog workflow upgrade` in the source project and export again"
        )


def _validate_import_retrospectives(tables: dict[str, list[dict]]) -> None:
    """Reject cross-project or incorrectly typed retrospective references."""
    tasks = {row.get("id"): row for row in tables.get("task", [])}
    projects = {row.get("id"): row for row in tables.get("project", [])}
    for row in tables.get("retrospective_action", []):
        key = row.get("key") or "<unknown>"
        project_id = row.get("project_id")
        if project_id not in projects:
            raise BacklogError(
                f"import rejected: retrospective action {key} has no owning project"
            )
        iteration = tasks.get(row.get("iteration_id"))
        if (
            iteration is None
            or iteration.get("task_type") != "iteration"
            or iteration.get("project_id") != project_id
        ):
            raise BacklogError(
                f"import rejected: retrospective action {key} requires an Iteration "
                "in its owning project"
            )
        resolution_project_id = row.get("resolution_project_id")
        resolution_task_id = row.get("resolution_task_id")
        if resolution_project_id is None and resolution_task_id is None:
            continue
        target = tasks.get(resolution_task_id)
        if (
            resolution_project_id not in projects
            or target is None
            or target.get("project_id") != resolution_project_id
            or target.get("task_type") not in {"feature", "bug"}
        ):
            raise BacklogError(
                f"import rejected: retrospective action {key} requires a Feature or Bug "
                "in its resolution project"
            )

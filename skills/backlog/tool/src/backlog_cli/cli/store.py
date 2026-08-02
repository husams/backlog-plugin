"""backlog — backlog tracker for coding agents (SQLite or shared PostgreSQL)."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .. import (
    __version__, core, deps, execution, hooks, retrospective, review, templates, workflow,
)
from ..db import (
    BacklogError,
    Conn,
    connect,
    database_errors,
    get_or_create_project,
    init_store,
    list_projects,
    require_backlog_dir,
    require_project,
    resolve_spec,
    resync_sequences,
    slugify,
)
from ..render import (
    deps_block,
    items_block,
    projects_table,
    render_task,
    render_thread,
    row_to_dict,
    table,
    tasks_table,
)
from ..schema import (
    ARTIFACT_KINDS,
    GATE_CHECKS,
    GATE_DESCRIPTIONS,
    STATUS_CATEGORIES,
    TASK_KEY_PREFIX,
    DEPENDENCY_KINDS,
    ITEM_KINDS,
    PR_REVIEW_STATES,
    PR_STATES,
    SCHEMA_VERSION,
    STATUS_DISPLAY,
    STATUSES,
    TASK_PARENT_TYPES,
    TASK_TYPES,
    transitions_for,
)



from .context import Ctx, _task_rows

def _spec_payload(spec, project=None) -> dict:
    out = {
        "backend": spec.dialect,
        "scope": spec.scope,
        "project": spec.project,
        "location": spec.location,
        "artifacts_dir": str(spec.artifacts_dir),
        "backlog_dir": str(spec.backlog_dir) if spec.backlog_dir else None,
    }
    if project is not None:
        out["project_id"] = project["id"]
        out["project_name"] = project["name"]
    return out


def cmd_init(ctx: Ctx, args) -> int:
    root = Path(args.path).expanduser().resolve()
    if not root.is_dir():
        raise BacklogError(f"{root} is not a directory")
    spec = resolve_spec(root, for_init=True)
    spec = init_store(root, force=args.force, spec=spec)
    text = (
        f"initialised backlog for project '{spec.project}'\n"
        f"  backend : {spec.dialect} ({spec.scope})\n"
        f"  store   : {spec.location}\n"
        f"  schema  : v{SCHEMA_VERSION}"
    )
    if spec.scope == "repo":
        text += "\n  commit it:  git add .backlog && git commit -m 'chore: init backlog'"
    ctx.emit({"schema_version": SCHEMA_VERSION, **_spec_payload(spec)}, text)
    return 0


def cmd_where(ctx: Ctx, args) -> int:
    spec = ctx.spec
    env = {k: os.environ[k] for k in
           ("BACKLOG_DB", "BACK_LOG_URL", "BACKLOG_PROJECT", "BACKLOG_SCHEMA",
            "BACKLOG_DIR", "BACKLOG_ARTIFACTS") if k in os.environ}
    if "BACK_LOG_URL" in env:
        env["BACK_LOG_URL"] = "(set; hidden)"
    if "BACKLOG_DB" in env and "://" in env["BACKLOG_DB"]:
        env["BACKLOG_DB"] = "(legacy URL set; hidden)"
    rows = [["backend", spec.dialect], ["scope", spec.scope], ["project", spec.project],
            ["store", spec.location], ["artifacts", str(spec.artifacts_dir)]]
    rows += [[f"env {k}", v] for k, v in sorted(env.items())]
    ctx.emit({**_spec_payload(spec), "env": env}, table(["FIELD", "VALUE"], rows))
    return 0


def cmd_projects(ctx: Ctx, args) -> int:
    rows = list_projects(ctx.conn)
    ctx.emit([row_to_dict(r) for r in rows],
             projects_table(rows, active=ctx.project_override or ctx.spec.project))
    return 0


def cmd_project_add(ctx: Ctx, args) -> int:
    slug = slugify(args.slug or args.name)
    row = get_or_create_project(ctx.conn, slug, ctx.spec, name=args.name,
                                description=args.description or "",
                                template=args.template)
    tpl = ctx.conn.execute("SELECT slug FROM template WHERE id = ?",
                           (row["template_id"],)).fetchone()
    ctx.emit(row_to_dict(row),
             f"{row['slug']}  {row['name']}"
             + (f"  [template: {tpl['slug']}]" if tpl else ""))
    return 0


def cmd_project_set(ctx: Ctx, args) -> int:
    proj = require_project(ctx.conn, args.slug)
    sets, values = [], []
    for field in ("name", "description", "status"):
        value = getattr(args, field, None)
        if value is not None:
            sets.append(f"{field} = ?")
            values.append(value)
    if not sets:
        raise BacklogError("nothing to set: pass --name / --description / --status")
    from ..db import utcnow

    sets.append("updated_at = ?")
    values += [utcnow(), proj["id"]]
    ctx.conn.execute(f"UPDATE project SET {', '.join(sets)} WHERE id = ?", values)
    ctx.conn.commit()
    row = require_project(ctx.conn, args.slug)
    ctx.emit(row_to_dict(row), f"{row['slug']}  {row['status']}  {row['name']}")
    return 0


def cmd_doctor(ctx: Ctx, args) -> int:
    problems: list[str] = []
    diagnostics: list[str] = []
    try:
        spec = ctx.spec
    except BacklogError as exc:
        ctx.emit({"ok": False, "problems": [str(exc)]}, f"FAIL: {exc}")
        return 1
    conn = ctx.conn
    d = spec.backlog_dir or spec.artifacts_dir.parent
    info = {**_spec_payload(spec), "schema_version": None, "counts": {}}
    info["schema_version"] = int(
        conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()["value"]
    )
    for tbl in ("template", "template_workflow", "template_status", "template_transition",
                "project", "workflow", "workflow_status", "workflow_transition",
                "task", "retrospective_action", "task_item", "executable_item", "execution_result",
                "validation_waiver",
                "dependency", "review_comment",
                "review_thread", "artifact", "event"):
        info["counts"][tbl] = conn.execute(f"SELECT COUNT(*) AS c FROM {tbl}").fetchone()["c"]

    if not conn.integrity_ok():
        problems.append(f"{spec.dialect} integrity check failed")

    for loop in deps.cycles(conn):
        problems.append("dependency cycle: " + " -> ".join(loop))

    bad_parent = conn.execute(
        "SELECT c.key, c.task_type, p.task_type AS parent_type FROM task c "
        "JOIN task p ON p.id = c.parent_id "
        "WHERE (c.task_type = 'subtask' AND p.task_type NOT IN ('story','bug')) "
        "   OR (c.task_type = 'story'   AND p.task_type != 'feature') "
        "   OR (c.task_type IN ('feature','bug','iteration'))"
    ).fetchall()
    problems += [f"{r['key']} is a {r['task_type']} under a {r['parent_type']}"
                 for r in bad_parent]

    orphan_sub = conn.execute(
        "SELECT key FROM task WHERE task_type = 'subtask' AND parent_id IS NULL"
    ).fetchall()
    problems += [f"subtask {r['key']} has no parent story" for r in orphan_sub]

    bad_retrospective_iterations = conn.execute(
        "SELECT a.key,i.key AS iteration_key,i.task_type,i.project_id "
        "FROM retrospective_action a JOIN task i ON i.id=a.iteration_id "
        "WHERE i.task_type!='iteration' OR i.project_id!=a.project_id"
    ).fetchall()
    problems += [
        f"retrospective action {r['key']} points at invalid Iteration {r['iteration_key']}"
        for r in bad_retrospective_iterations
    ]
    bad_retrospective_resolutions = conn.execute(
        "SELECT a.key,t.key AS task_key,t.task_type,t.project_id "
        "FROM retrospective_action a JOIN task t ON t.id=a.resolution_task_id "
        "WHERE t.task_type NOT IN ('feature','bug') "
        "OR t.project_id!=a.resolution_project_id"
    ).fetchall()
    problems += [
        f"retrospective action {r['key']} has invalid resolution {r['task_key']}"
        for r in bad_retrospective_resolutions
    ]

    # A status is legal if the task's own workflow declares it — the compiled-in
    # vocabulary is not the authority any more.
    no_flow = conn.execute(
        "SELECT DISTINCT t.project_id, t.task_type FROM task t "
        "LEFT JOIN workflow w ON w.project_id = t.project_id AND w.task_type = t.task_type "
        "WHERE w.id IS NULL"
    ).fetchall()
    problems += [f"no workflow for {r['task_type']} in project {r['project_id']}; "
                 "run `backlog workflow upgrade` to add missing shipped flows"
                 for r in no_flow]
    off_flow = conn.execute(
        "SELECT t.key, t.status, t.task_type FROM task t "
        "JOIN workflow w ON w.project_id = t.project_id AND w.task_type = t.task_type "
        "LEFT JOIN workflow_status s ON s.workflow_id = w.id AND s.slug = t.status "
        "WHERE s.id IS NULL ORDER BY t.key"
    ).fetchall()
    problems += [
        f"{r['key']} is in status {r['status']!r}, which its {r['task_type']} flow "
        "does not define"
        for r in off_flow
    ]
    orphan_tpl = conn.execute(
        "SELECT slug FROM project WHERE template_id IS NULL ORDER BY slug"
    ).fetchall()
    problems += [f"project {r['slug']} is not bound to a template" for r in orphan_tpl]

    started_blocked = []
    for proj in list_projects(conn):
        blocked = deps.blocked_by_map(conn, proj["id"])
        rows = conn.execute(
            "SELECT key, status FROM task WHERE project_id = ? "
            "AND status IN ('in_progress','in_review')", (proj["id"],)
        ).fetchall()
        started_blocked += [
            f"{r['key']} is {STATUS_DISPLAY.get(r['status'], r['status'])} but still blocked by "
            + ", ".join(blocked[r["key"]])
            for r in rows if r["key"] in blocked
        ]
    problems += started_blocked

    bad_threads = conn.execute(
        "SELECT t.root_key FROM review_thread t "
        "LEFT JOIN review_comment c ON c.key = t.last_comment_key WHERE c.key IS NULL"
    ).fetchall()
    problems += [f"thread {r['root_key']} points at a missing last comment" for r in bad_threads]

    closed_with_open = conn.execute(
        "SELECT t.key, COUNT(r.id) AS n FROM task t JOIN review_thread r ON r.task_id = t.id "
        "WHERE t.status IN ('accepted','done') AND r.state != 'closed' "
        "AND r.severity = 'blocker' GROUP BY t.key"
    ).fetchall()
    problems += [f"{r['key']} is accepted/done but has {r['n']} open blocker thread(s)"
                 for r in closed_with_open]

    missing_art = [a["rel_path"] for a in conn.execute("SELECT rel_path FROM artifact").fetchall()
                   if not (d / a["rel_path"]).exists()]
    problems += [f"artifact file missing on disk: .backlog/{p}" for p in missing_art]

    from ..execution import source_revision_unavailable_items
    unavailable = source_revision_unavailable_items(conn)
    diagnostics += [
        "source_revision_unavailable: latest fresh result for item "
        f"#{item_id} has no VCS source identity"
        for item_id in unavailable
    ]
    for detail in execution.validation_diagnostics(conn):
        diagnostics.append(
            f"validation_{detail['kind']}: {detail['task']} item "
            f"#{detail['item_id']} ({detail['executor']}) actor={detail['actor']} "
            f"reason={detail['reason']} prior={detail['prior_result']} "
            f"at={detail['timestamp']} content={detail['item']}"
        )
    info["diagnostics"] = diagnostics

    ok = not problems
    text = ("OK  " if ok else "FAIL ") + (
        f"{spec.location}  ({spec.dialect}/{spec.scope})  schema v{info['schema_version']}\n"
    ) + table(["TABLE", "ROWS"], [[k, str(v)] for k, v in sorted(info["counts"].items())])
    if problems:
        text += "\n\nproblems:\n" + "\n".join(f"  - {p}" for p in problems)
    if diagnostics:
        text += "\n\ndiagnostics:\n" + "\n".join(f"  - {d}" for d in diagnostics)
    ctx.emit({"ok": ok, "problems": problems, **info}, text)
    return 0 if ok else 1

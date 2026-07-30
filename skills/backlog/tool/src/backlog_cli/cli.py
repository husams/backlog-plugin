"""backlog — backlog tracker for coding agents (SQLite or shared PostgreSQL)."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from . import __version__, core, deps, execution, hooks, review, templates, workflow
from .db import (
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
from .render import (
    deps_block,
    items_block,
    projects_table,
    render_task,
    render_thread,
    row_to_dict,
    table,
    tasks_table,
)
from .schema import (
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
    TASK_TYPES,
    transitions_for,
)


class Ctx:
    def __init__(self, args: argparse.Namespace):
        self.json = bool(getattr(args, "json", False))
        self.project_override = getattr(args, "project", None)
        self._dir: Path | None = None
        self._conn: Conn | None = None
        self._spec = None
        self._project = None

    @property
    def spec(self):
        if self._spec is None:
            self._spec = resolve_spec()
        return self._spec

    @property
    def dir(self) -> Path:
        if self._dir is None:
            self._dir = require_backlog_dir()
        return self._dir

    @property
    def conn(self) -> Conn:
        if self._conn is None:
            self._conn = connect(spec=self.spec)
        return self._conn

    @property
    def project(self):
        """The project row every task command is scoped to."""
        if self._project is None:
            slug = self.project_override or self.spec.project
            self._project = require_project(self.conn, slug)
            config_dir = hooks.project_backlog_dir(self.dir)
            hooks.apply_workflow(self.conn, int(self._project["id"]), config_dir)
        return self._project

    @property
    def pid(self) -> int:
        return int(self.project["id"])

    def emit(self, payload, text: str) -> None:
        if self.json:
            print(json.dumps(payload, indent=2, sort_keys=True, default=str))
        else:
            print(text)


def _task_rows(conn: Conn, project_id: int, where: str = "", params=()) -> list:
    """Tasks with their parent key resolved, ready for `tasks_table`."""
    sql = ("SELECT t.*, p.key AS parent_key FROM task t "
           "LEFT JOIN task p ON p.id = t.parent_id WHERE t.project_id = ?")
    return conn.execute(sql + where + " ORDER BY t.priority, t.key",
                        [project_id, *params]).fetchall()


# --------------------------------------------------------------------------- #
# store
# --------------------------------------------------------------------------- #

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
    from .db import utcnow

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
                "task", "task_item", "executable_item", "execution_result",
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
        "WHERE (c.task_type = 'subtask' AND p.task_type != 'story') "
        "   OR (c.task_type = 'story'   AND p.task_type != 'feature') "
        "   OR (c.task_type = 'feature')"
    ).fetchall()
    problems += [f"{r['key']} is a {r['task_type']} under a {r['parent_type']}"
                 for r in bad_parent]

    orphan_sub = conn.execute(
        "SELECT key FROM task WHERE task_type = 'subtask' AND parent_id IS NULL"
    ).fetchall()
    problems += [f"subtask {r['key']} has no parent story" for r in orphan_sub]

    # A status is legal if the task's own workflow declares it — the compiled-in
    # vocabulary is not the authority any more.
    no_flow = conn.execute(
        "SELECT DISTINCT t.project_id, t.task_type FROM task t "
        "LEFT JOIN workflow w ON w.project_id = t.project_id AND w.task_type = t.task_type "
        "WHERE w.id IS NULL"
    ).fetchall()
    problems += [f"no workflow for {r['task_type']} in project {r['project_id']}"
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

    from .execution import source_revision_unavailable_items
    unavailable = source_revision_unavailable_items(conn)
    diagnostics += [
        "source_revision_unavailable: latest fresh result for item "
        f"#{item_id} has no VCS source identity"
        for item_id in unavailable
    ]
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


# --------------------------------------------------------------------------- #
# tasks
# --------------------------------------------------------------------------- #

def _json_argument(value: str | None, label: str, default):
    if value is None:
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError as exc:
        raise BacklogError(f"{label} must be valid JSON: {exc.msg}") from exc


def _matcher_argument(args, stream: str) -> dict | None:
    values = [
        (name, getattr(args, f"{stream}_{name}", None))
        for name in ("equals", "contains", "regex")
    ]
    selected = [(name, value) for name, value in values if value is not None]
    if len(selected) > 1:
        raise BacklogError(
            f"--{stream}-equals, --{stream}-contains, and --{stream}-regex "
            "are mutually exclusive"
        )
    return dict(selected) if selected else None


def _execution_spec(args) -> dict | None:
    shell = getattr(args, "shell", None)
    hook = getattr(args, "hook", None)
    if shell is None and hook is None:
        extras = [
            "requirement", "timeout", "working_directory", "expected_exit_code",
            "stdout_equals", "stdout_contains", "stdout_regex",
            "stderr_equals", "stderr_contains", "stderr_regex",
            "environment", "arguments", "expected_result",
        ]
        if any(getattr(args, name, None) is not None for name in extras):
            raise BacklogError("execution options require exactly one of --shell or --hook")
        return None
    if shell is not None and hook is not None:
        raise BacklogError("--shell and --hook are mutually exclusive")
    requirement = getattr(args, "requirement", None) or "required"
    timeout = getattr(args, "timeout", None) or 60
    if shell is not None:
        environment = {}
        for pair in getattr(args, "environment", None) or []:
            if "=" not in pair:
                raise BacklogError(f"--env must be NAME=VALUE, got {pair!r}")
            name, value = pair.split("=", 1)
            if not name:
                raise BacklogError("--env variable name cannot be empty")
            environment[name] = value
        shell_spec = {
            "command": shell,
            "timeout_seconds": timeout,
            "working_directory": getattr(args, "working_directory", None) or ".",
            "expected_exit_code": (
                getattr(args, "expected_exit_code", None)
                if getattr(args, "expected_exit_code", None) is not None else 0
            ),
            "environment": environment,
        }
        for stream in ("stdout", "stderr"):
            matcher = _matcher_argument(args, stream)
            if matcher:
                shell_spec[stream] = matcher
        spec = {"executor": "shell", "requirement": requirement, "shell": shell_spec}
    else:
        spec = {
            "executor": "hook",
            "requirement": requirement,
            "hook": {
                "name": hook,
                "arguments": _json_argument(
                    getattr(args, "arguments", None), "--arguments", {}
                ),
                "timeout_seconds": timeout,
                "expected_result": _json_argument(
                    getattr(args, "expected_result", None), "--expected-result", None
                ),
            },
        }
    return execution.parse_spec(spec).canonical()


def _add_task(ctx: Ctx, args, task_type: str, parent: str | None) -> int:
    item_spec = _execution_spec(args)
    criteria = [line for line in (args.ac or "").splitlines() if line.strip()]
    if item_spec and len(criteria) != 1:
        raise BacklogError(
            "executable --ac requires exactly one non-empty acceptance criterion"
        )
    row = core.add_task(
        ctx.conn, ctx.pid, task_type, args.title, parent=parent,
        description=args.description or "", priority=args.priority, owner=args.owner,
        assignee=args.assignee, reviewer=args.reviewer, branch=getattr(args, "branch", None),
        actor=args.actor,
    )
    if criteria:
        items = core.set_items(
            ctx.conn, ctx.pid, row["key"], "acceptance_criteria", criteria, actor=args.actor
        )
        if item_spec:
            execution.set_executable(ctx.conn, items[0]["id"], item_spec)
    wf = workflow.get(ctx.conn, ctx.pid, row["task_type"])
    ctx.emit(row_to_dict(row),
             f"{row['key']}  {row['title']}  [{wf.display(row['status'])}]"
             + (f"  parent={parent}" if parent else ""))
    return 0


def cmd_task_add(ctx: Ctx, args) -> int:
    return _add_task(ctx, args, core.normalize_type(args.type), args.parent)


def cmd_feature_add(ctx: Ctx, args) -> int:
    return _add_task(ctx, args, "feature", None)


def cmd_story_add(ctx: Ctx, args) -> int:
    return _add_task(ctx, args, "story", args.feature)


def cmd_subtask_add(ctx: Ctx, args) -> int:
    return _add_task(ctx, args, "subtask", args.story)


def _list(ctx: Ctx, args, task_type: str | None) -> int:
    where, params = "", []
    if task_type:
        where += " AND t.task_type = ?"
        params.append(task_type)
    elif getattr(args, "type", None):
        where += " AND t.task_type = ?"
        params.append(core.normalize_type(args.type))
    if getattr(args, "status", None):
        where += " AND t.status = ?"
        params.append(core.normalize_status(args.status))
    if getattr(args, "open", False):
        where += " AND t.status NOT IN ('accepted','done')"
    if getattr(args, "assignee", None):
        where += " AND t.assignee = ?"
        params.append(args.assignee)
    if getattr(args, "reviewer", None):
        where += " AND t.reviewer = ?"
        params.append(args.reviewer)
    if getattr(args, "parent", None):
        where += " AND p.key = ?"
        params.append(core.normalize_key(args.parent))
    rows = _task_rows(ctx.conn, ctx.pid, where, params)
    ctx.emit([row_to_dict(r) for r in rows], tasks_table(rows))
    return 0


def cmd_list(ctx: Ctx, args) -> int:
    return _list(ctx, args, None)


def cmd_feature_list(ctx: Ctx, args) -> int:
    return _list(ctx, args, "feature")


def cmd_story_list(ctx: Ctx, args) -> int:
    return _list(ctx, args, "story")


def cmd_subtask_list(ctx: Ctx, args) -> int:
    return _list(ctx, args, "subtask")


def cmd_set(ctx: Ctx, args) -> int:
    item_spec = _execution_spec(args)
    criteria = None
    if args.ac is not None:
        criteria = [line for line in args.ac.splitlines() if line.strip()]
        if item_spec and len(criteria) != 1:
            raise BacklogError(
                "executable --ac requires exactly one non-empty acceptance criterion"
            )
    row = core.update_task(ctx.conn, ctx.pid, args.key, actor=args.actor,
                           title=args.title, description=args.description,
                           priority=args.priority, branch=args.branch,
                           owner=args.owner, parent=args.parent)
    if criteria is not None:
        items = core.set_items(
            ctx.conn, ctx.pid, row["key"], "acceptance_criteria", criteria, actor=args.actor
        )
        if item_spec:
            execution.set_executable(ctx.conn, items[0]["id"], item_spec)
    ctx.emit(row_to_dict(row), render_task(ctx.conn, row))
    return 0


def cmd_assign(ctx: Ctx, args) -> int:
    row = core.assign(ctx.conn, ctx.pid, args.key, to=args.to, reviewer=args.reviewer,
                      actor=args.actor, to_kind=args.to_kind, reviewer_kind=args.reviewer_kind)
    ctx.emit(row_to_dict(row),
             f"{row['key']}  assignee={row['assignee'] or '-'} ({row['assignee_kind']})  "
             f"reviewer={row['reviewer'] or '-'} ({row['reviewer_kind']})")
    return 0


def cmd_show(ctx: Ctx, args) -> int:
    task = core.get_task(ctx.conn, ctx.pid, args.key)
    payload = row_to_dict(task)
    payload["items"] = [
        execution.public_item(ctx.conn, i)
        for i in core.task_items(ctx.conn, task["id"])
    ]
    payload["dependencies"] = deps.edges_for(ctx.conn, task["id"])
    payload["blocked_by"] = [b["other_key"] for b in deps.blockers(ctx.conn, task["id"])]
    payload["open_threads"] = [review.thread_summary(ctx.conn, t["root_key"])
                               for t in core.open_threads(ctx.conn, task["id"])]
    payload["children"] = [row_to_dict(c) for c in core.children_of(ctx.conn, task["id"])]
    payload["artifacts"] = [row_to_dict(a) for a in core.list_artifacts(ctx.conn, task["id"])]
    ctx.emit(payload, render_task(ctx.conn, task))
    return 0


def cmd_action(ctx: Ctx, args) -> int:
    parameters = {}
    for item in args.parameter or []:
        if "=" not in item:
            raise BacklogError(f"parameter must be NAME=VALUE, got {item!r}")
        name, value = item.split("=", 1)
        if not name:
            raise BacklogError("parameter name cannot be empty")
        parameters[name] = value
    before = core.get_task(ctx.conn, ctx.pid, args.key)
    row, checks, transitioned = core.trigger_action(
        ctx.conn,
        ctx.pid,
        args.key,
        args.action,
        actor=args.actor,
        operation=args.operation,
        parameters=parameters,
        no_pr=args.no_pr,
        allow_open_children=args.allow_open_subtasks,
        allow_blocked=args.allow_blocked,
    )
    wf = workflow.get(ctx.conn, ctx.pid, row["task_type"])
    ctx.emit(
        {
            "action": hooks.normalize_action(args.action).value,
            "transitioned": transitioned,
            "from": before["status"],
            "task": row_to_dict(row),
            "checks": [check.as_dict() for check in checks],
        },
        (
            f"{row['key']}  action={hooks.normalize_action(args.action).value}  "
            + (
                f"{wf.display(before['status'])} -> {wf.display(row['status'])}"
                if transitioned
                else f"recorded; status remains {wf.display(row['status'])}"
            )
        ),
    )
    return 0


def cmd_actions(ctx: Ctx, args) -> int:
    task = core.get_task(ctx.conn, ctx.pid, args.key)
    config_dir = hooks.project_backlog_dir(ctx.dir)
    actions = hooks.available_actions(
        config_dir, task["task_type"], task["status"]
    )
    ctx.emit(
        [action.value for action in actions],
        "\n".join(action.value for action in actions) if actions
        else "(no configured actions)",
    )
    return 0


def cmd_gate(ctx: Ctx, args) -> int:
    ok, checks = core.gate(ctx.conn, ctx.pid, args.key, args.__dict__["for"],
                           allow_open_children=args.allow_open_subtasks, no_pr=args.no_pr,
                           allow_blocked=args.allow_blocked)
    key = core.normalize_key(args.key)
    text = f"{key}  gate={args.__dict__['for']}  " + ("PASS" if ok else "BLOCKED")
    text += "\n" + "\n".join(f"  {'OK  ' if c.ok else 'FAIL'} {c.name}: {c.detail}" for c in checks)
    ctx.emit({"key": key, "gate": args.__dict__["for"], "ok": ok,
              "checks": [c.as_dict() for c in checks]}, text)
    return 0 if ok else 2


def cmd_statuses(ctx: Ctx, args) -> int:
    """The flow this project actually runs — read it before moving anything."""
    payload, blocks = {}, []
    for ttype in ([core.normalize_type(args.type)] if args.type else TASK_TYPES):
        wf = workflow.get(ctx.conn, ctx.pid, ttype)
        payload[ttype] = {
            "name": wf.name,
            "statuses": [row_to_dict(x) for x in wf.ordered],
            "transitions": {f: dict(t) for f, t in wf.transitions.items()},
        }
        blocks.append(f"== {ttype}  ({wf.name})\n" + workflow.render(wf))
    ctx.emit(payload, "\n\n".join(blocks))
    return 0


def cmd_templates(ctx: Ctx, args) -> int:
    templates.install_builtins(ctx.conn)
    rows = templates.list_all(ctx.conn)
    ctx.emit([row_to_dict(r) for r in rows],
             table(["", "TEMPLATE", "WORKFLOWS", "PROJECTS", "NAME", "DESCRIPTION"],
                   [["*" if r["is_default"] else "", r["slug"], str(r["workflows"]),
                     str(r["projects"]), r["name"], r["description"][:60]] for r in rows]))
    return 0


def cmd_template_show(ctx: Ctx, args) -> int:
    templates.install_builtins(ctx.conn)
    tpl = templates.require(ctx.conn, args.slug)
    types = [core.normalize_type(args.type)] if args.type else TASK_TYPES
    blocks = [f"{tpl['slug']}  {tpl['name']}"
              + ("  [default]" if tpl["is_default"] else "")
              + (f"\n{tpl['description']}" if tpl["description"] else "")]
    payload = {"template": row_to_dict(tpl), "workflows": {}}
    for ttype in types:
        blocks.append(f"\n== {ttype}\n" + templates.render(ctx.conn, int(tpl["id"]), ttype))
        wf = templates.workflows_of(ctx.conn, int(tpl["id"])).get(ttype)
        if wf:
            payload["workflows"][ttype] = {
                "statuses": [row_to_dict(x) for x in templates.statuses_of(ctx.conn, int(wf["id"]))],
                "transitions": [row_to_dict(x)
                                for x in templates.transitions_of(ctx.conn, int(wf["id"]))],
            }
    ctx.emit(payload, "\n".join(blocks))
    return 0


def cmd_template_add(ctx: Ctx, args) -> int:
    templates.install_builtins(ctx.conn)
    from_project = None
    if args.from_project:
        from_project = int(require_project(ctx.conn, args.from_project)["id"])
    row = templates.create(ctx.conn, args.slug, args.name or args.slug,
                           description=args.description or "", copy_of=args.copy_of,
                           from_project=from_project)
    ctx.emit(row_to_dict(row), f"{row['slug']}  {row['name']}")
    return 0


def cmd_template_rm(ctx: Ctx, args) -> int:
    templates.remove(ctx.conn, args.slug)
    ctx.emit({"removed": args.slug}, f"removed template {args.slug}")
    return 0


def cmd_template_default(ctx: Ctx, args) -> int:
    row = templates.set_default(ctx.conn, args.slug)
    ctx.emit(row_to_dict(row), f"{row['slug']} is now the default template")
    return 0


def cmd_template_status_add(ctx: Ctx, args) -> int:
    row = templates.add_status(ctx.conn, args.slug, core.normalize_type(args.type),
                               args.status, args.display or "", category=args.category,
                               after=args.after, satisfies=args.satisfies,
                               terminal=args.terminal)
    ctx.emit(row_to_dict(row),
             f"{args.slug}/{args.type}: added {row['display']} ({row['slug']})")
    return 0


def cmd_template_move_add(ctx: Ctx, args) -> int:
    templates.set_transition(ctx.conn, args.slug, core.normalize_type(args.type),
                             args.__dict__["from"], args.to, gates=args.gate or "")
    ctx.emit({"template": args.slug, "type": args.type,
              "from": args.__dict__["from"], "to": args.to, "gates": args.gate or ""},
             f"{args.slug}/{args.type}: {args.__dict__['from']} -> {args.to}"
             + (f"  (gates: {args.gate})" if args.gate else ""))
    return 0


def cmd_workflow_apply(ctx: Ctx, args) -> int:
    """Re-instantiate this project's flow from a template."""
    tpl = templates.require(ctx.conn, args.template) if args.template \
        else workflow.template_of(ctx.conn, ctx.pid)
    done = templates.instantiate(ctx.conn, int(tpl["id"]), ctx.pid,
                                 core.normalize_type(args.type) if args.type else None,
                                 replace=True)
    ctx.conn.execute("UPDATE project SET template_id = ? WHERE id = ?", (tpl["id"], ctx.pid))
    ctx.conn.commit()
    ctx.emit({"template": tpl["slug"], "types": done},
             f"applied template '{tpl['slug']}' to {ctx.project['slug']}: " + ", ".join(done))
    return 0


def cmd_workflow_show(ctx: Ctx, args) -> int:
    return cmd_statuses(ctx, args)


def cmd_workflow_gates(ctx: Ctx, args) -> int:
    ctx.emit({"gates": {g: GATE_DESCRIPTIONS[g] for g in GATE_CHECKS}},
             table(["GATE", "MEANS"], [[g, GATE_DESCRIPTIONS[g]] for g in GATE_CHECKS]))
    return 0


def cmd_workflow_status_add(ctx: Ctx, args) -> int:
    row = workflow.add_status(ctx.conn, ctx.pid, core.normalize_type(args.type),
                              args.slug, args.display or "", category=args.category,
                              after=args.after, satisfies=args.satisfies,
                              terminal=args.terminal, description=args.description or "")
    ctx.emit(row_to_dict(row),
             f"added {row['display']} ({row['slug']}) to the {args.type} flow  "
             f"[{row['category']}]")
    return 0


def cmd_workflow_status_rm(ctx: Ctx, args) -> int:
    workflow.remove_status(ctx.conn, ctx.pid, core.normalize_type(args.type), args.slug)
    ctx.emit({"removed": args.slug, "type": args.type},
             f"removed {args.slug} from the {args.type} flow")
    return 0


def cmd_workflow_move_add(ctx: Ctx, args) -> int:
    workflow.set_transition(ctx.conn, ctx.pid, core.normalize_type(args.type),
                            args.__dict__["from"], args.to, gates=args.gate or "",
                            note=args.note or "")
    ctx.emit({"type": args.type, "from": args.__dict__["from"], "to": args.to,
              "gates": args.gate or ""},
             f"{args.type}: {args.__dict__['from']} -> {args.to}"
             + (f"  (gates: {args.gate})" if args.gate else ""))
    return 0


def cmd_workflow_move_rm(ctx: Ctx, args) -> int:
    workflow.remove_transition(ctx.conn, ctx.pid, core.normalize_type(args.type),
                               args.__dict__["from"], args.to)
    ctx.emit({"removed": [args.__dict__["from"], args.to]},
             f"{args.type}: removed {args.__dict__['from']} -> {args.to}")
    return 0


def cmd_workflow_reset(ctx: Ctx, args) -> int:
    types = [core.normalize_type(args.type)] if args.type else TASK_TYPES
    for t in types:
        workflow.reset(ctx.conn, ctx.pid, t)
    ctx.emit({"reset": types}, "reset to the built-in flow: " + ", ".join(types))
    return 0


def cmd_workflow_copy(ctx: Ctx, args) -> int:
    src = require_project(ctx.conn, args.__dict__["from"])
    done = workflow.copy_from(ctx.conn, int(src["id"]), ctx.pid,
                              core.normalize_type(args.type) if args.type else None)
    ctx.emit({"copied_from": src["slug"], "types": done},
             f"adopted {src['slug']}'s flow for: " + ", ".join(done))
    return 0


# --------------------------------------------------------------------------- #
# task items
# --------------------------------------------------------------------------- #

def cmd_item_add(ctx: Ctx, args) -> int:
    spec = _execution_spec(args)
    if spec and core.normalize_item_kind(args.kind) not in (
        "acceptance_criteria", "checklist"
    ):
        raise BacklogError(
            "only acceptance criteria and checklist items may declare execution"
        )
    lines = [line for line in args.content.splitlines() if line.strip()]
    if spec and len(lines) != 1:
        raise BacklogError("an executable item requires exactly one non-empty content line")
    rows = [
        core.add_item(ctx.conn, ctx.pid, args.key, args.kind, line, actor=args.actor)
        for line in lines
    ]
    if spec:
        execution.set_executable(ctx.conn, rows[0]["id"], spec)
    public = [execution.public_item(ctx.conn, row) for row in rows]
    ctx.emit(public, "\n".join(items_block(rows, conn=ctx.conn)) or "(nothing added)")
    return 0


def cmd_item_set(ctx: Ctx, args) -> int:
    spec = _execution_spec(args)
    if spec and core.normalize_item_kind(args.kind) not in (
        "acceptance_criteria", "checklist"
    ):
        raise BacklogError(
            "only acceptance criteria and checklist items may declare execution"
        )
    lines = [line for line in args.content.splitlines() if line.strip()]
    if spec and len(lines) != 1:
        raise BacklogError("an executable item requires exactly one non-empty content line")
    rows = core.set_items(ctx.conn, ctx.pid, args.key, args.kind,
                          lines, actor=args.actor)
    if spec:
        execution.set_executable(ctx.conn, rows[0]["id"], spec)
    public = [execution.public_item(ctx.conn, row) for row in rows]
    ctx.emit(public, "\n".join(items_block(rows, conn=ctx.conn)) or "(cleared)")
    return 0


def cmd_item_list(ctx: Ctx, args) -> int:
    task = core.get_task(ctx.conn, ctx.pid, args.key)
    rows = core.task_items(ctx.conn, task["id"], args.kind)
    public = [execution.public_item(ctx.conn, row) for row in rows]
    ctx.emit(public, "\n".join(items_block(rows, conn=ctx.conn)) or "(none)")
    return 0


def cmd_item_check(ctx: Ctx, args) -> int:
    row = core.tick_item(ctx.conn, ctx.pid, args.id, done=not args.undo, actor=args.actor)
    ctx.emit(row_to_dict(row),
             f"#{row['id']}  {'[x]' if row['done'] else '[ ]'} {row['content']}")
    return 0


def cmd_item_rm(ctx: Ctx, args) -> int:
    row = core.remove_item(ctx.conn, ctx.pid, args.id, actor=args.actor)
    ctx.emit(row_to_dict(row), f"removed #{row['id']}  {row['content']}")
    return 0


# --------------------------------------------------------------------------- #
# dependencies
# --------------------------------------------------------------------------- #

def _dep_pair(args) -> tuple[str, str, str]:
    if args.blocks:
        return args.key, args.blocks, "blocks"
    if args.blocked_by:
        return args.blocked_by, args.key, "blocks"
    if args.relates:
        return args.key, args.relates, "relates"
    if args.duplicates:
        return args.key, args.duplicates, "duplicates"
    raise BacklogError(
        "say which way it points: --blocks / --blocked-by / --relates / --duplicates"
    )


def cmd_dep_add(ctx: Ctx, args) -> int:
    a, b, kind = _dep_pair(args)
    row = deps.add(ctx.conn, ctx.pid, a, b, kind, note=args.note or "", actor=args.actor)
    verb = "added" if row.get("created") else "already present"
    ctx.emit(row, f"{verb}: {row['from_key']} {row['kind']} {row['to_key']}")
    return 0


def cmd_dep_rm(ctx: Ctx, args) -> int:
    a, b, kind = _dep_pair(args)
    row = deps.remove(ctx.conn, ctx.pid, a, b, kind, actor=args.actor)
    ctx.emit(row, f"removed: {row['from_key']} {row['kind']} {row['to_key']}")
    return 0


_DEP_LABEL = {
    ("in", "blocks"): "blocked by",
    ("out", "blocks"): "blocks",
    ("in", "relates"): "relates to",
    ("out", "relates"): "relates to",
    ("in", "duplicates"): "duplicated by",
    ("out", "duplicates"): "duplicates",
}


def cmd_dep_list(ctx: Ctx, args) -> int:
    if args.key:
        task = core.get_task(ctx.conn, ctx.pid, args.key)
        edges = deps.edges_for(ctx.conn, task["id"], kind=args.kind)
        rows = [["OK" if e["satisfied"] else ("WAIT" if e["kind"] == "blocks" else ""),
                 _DEP_LABEL[(e["direction"], e["kind"])], e["other_key"],
                 e["other_status"], e["other_title"], e["note"]]
                for e in sorted(edges, key=lambda e: (e["kind"] != "blocks",
                                                      e["direction"], e["other_key"]))]
        ctx.emit(edges, table(["", "RELATION", "KEY", "STATUS", "TITLE", "NOTE"], rows))
        return 0
    rows = [row_to_dict(r) for r in deps.all_edges(ctx.conn, ctx.pid, kind=args.kind)]
    ctx.emit(rows, table(["FROM", "KIND", "TO", "NOTE"],
                         [[r["from_key"], r["kind"], r["to_key"], r["note"]] for r in rows]))
    return 0


def cmd_dep_check(ctx: Ctx, args) -> int:
    task = core.get_task(ctx.conn, ctx.pid, args.key)
    blockers = deps.blockers(ctx.conn, task["id"])
    ok = not blockers
    text = f"{task['key']}  " + (
        "READY — nothing is blocking it" if ok
        else "BLOCKED\n" + table(["KEY", "STATUS", "TITLE"],
                                 [[b["other_key"], b["other_status"], b["other_title"]]
                                  for b in blockers])
    )
    ctx.emit({"key": task["key"], "ok": ok, "blocked_by": blockers}, text)
    return 0 if ok else 2


def cmd_dep_graph(ctx: Ctx, args) -> int:
    conn = ctx.conn
    if args.format == "dot":
        print(deps.dot(conn, ctx.pid))
        return 0
    edges = [row_to_dict(r) for r in deps.all_edges(conn, ctx.pid)]
    loops = deps.cycles(conn)
    blocked = deps.blocked_by_map(conn, ctx.pid)
    if args.format == "json" or ctx.json:
        ctx.json = True
        ctx.emit({"edges": edges, "cycles": loops, "blocked_by": blocked}, "")
        return 0
    text = table(["FROM", "KIND", "TO"], [[e["from_key"], e["kind"], e["to_key"]] for e in edges])
    if blocked:
        text += "\n\ncurrently blocked:\n" + "\n".join(
            f"  {k} waits on {', '.join(v)}" for k, v in sorted(blocked.items()))
    if loops:
        text += "\n\nCYCLES:\n" + "\n".join("  " + " -> ".join(c) for c in loops)
    print(text)
    return 0


# --------------------------------------------------------------------------- #
# pull requests / review / artifacts
# --------------------------------------------------------------------------- #

def cmd_pr_set(ctx: Ctx, args) -> int:
    row = core.set_pr(ctx.conn, ctx.pid, args.key, url=args.url, number=args.number,
                      repo=args.repo, state=args.state, review_state=args.review_state,
                      actor=args.actor)
    ctx.emit(row_to_dict(row),
             f"{row['key']}  pr={row['pr_url'] or '#' + str(row['pr_number'])}  "
             f"state={row['pr_state']}  review={row['pr_review_state']}")
    return 0


def cmd_pr_sync(ctx: Ctx, args) -> int:
    row = core.sync_pr(ctx.conn, ctx.pid, args.key, actor=args.actor)
    ctx.emit(row_to_dict(row),
             f"{row['key']}  pr={row['pr_url']}  state={row['pr_state']}  "
             f"review={row['pr_review_state']}")
    return 0


def cmd_review_open(ctx: Ctx, args) -> int:
    t = review.open_thread(ctx.conn, ctx.pid, args.key, args.author, args.body,
                           role=args.role, title=args.title or "", file_path=args.file,
                           line=args.line, severity=args.severity)
    ctx.emit(t, render_thread(t))
    return 0


def cmd_review_reply(ctx: Ctx, args) -> int:
    t = review.reply(ctx.conn, ctx.pid, args.comment, args.author, args.action,
                     args.body, role=args.role)
    ctx.emit(t, render_thread(t))
    return 0


def cmd_review_reopen(ctx: Ctx, args) -> int:
    t = review.reopen(ctx.conn, ctx.pid, args.root, args.author, args.body, role=args.role)
    ctx.emit(t, render_thread(t))
    return 0


def cmd_review_inbox(ctx: Ctx, args) -> int:
    threads = review.inbox(
        ctx.conn, ctx.pid, actor=args.actor, role=args.role, key=args.item,
        severity=args.severity,
    )
    text = ("\n\n".join(render_thread(t) for t in threads) if threads
            else "(no review threads waiting on you)")
    ctx.emit(threads, text)
    return 0


def cmd_review_thread(ctx: Ctx, args) -> int:
    t = (review.full_thread(ctx.conn, args.root) if args.full
         else review.thread_summary(ctx.conn, args.root))
    ctx.emit(t, render_thread(t, full=args.full))
    return 0


def cmd_review_list(ctx: Ctx, args) -> int:
    threads = review.list_threads(
        ctx.conn, ctx.pid, args.key, state=args.state, severity=args.severity
    )
    ctx.emit(threads, "\n\n".join(render_thread(t) for t in threads) if threads else "(no threads)")
    return 0


def cmd_review_severity(ctx: Ctx, args) -> int:
    t = review.set_severity(
        ctx.conn, ctx.pid, args.root, args.severity, author=args.author
    )
    ctx.emit(t, render_thread(t))
    return 0


def cmd_artifact_add(ctx: Ctx, args) -> int:
    info = core.add_artifact(ctx.conn, ctx.dir, ctx.pid, args.key, Path(args.path),
                             title=args.title or "", kind=args.kind, actor=args.actor)
    ctx.emit(info, f"{info['key']}  <- .backlog/{info['rel_path']}")
    return 0


def cmd_artifact_list(ctx: Ctx, args) -> int:
    task = core.get_task(ctx.conn, ctx.pid, args.key)
    rows = core.list_artifacts(ctx.conn, task["id"])
    ctx.emit([row_to_dict(r) for r in rows],
             table(["PATH", "KIND", "TITLE"],
                   [[f".backlog/{r['rel_path']}", r["kind"], r["title"]] for r in rows]))
    return 0


# --------------------------------------------------------------------------- #
# board / next / history
# --------------------------------------------------------------------------- #

def cmd_board(ctx: Ctx, args) -> int:
    conn = ctx.conn
    flows = workflow.all_for(conn, ctx.pid)
    # Column order comes from the project's workflow, so a custom status lands
    # where its author put it rather than at the end.
    order, seen = [], set()
    for ttype in TASK_TYPES:
        for st in flows[ttype].ordered:
            if st["slug"] not in seen:
                seen.add(st["slug"])
                order.append((st["slug"], st["display"], st["category"]))

    rows = _task_rows(conn, ctx.pid, "")
    if not args.all:
        rows = [r for r in rows if flows[r["task_type"]].is_open(r["status"])]
    by_status: dict[str, list] = {}
    for r in rows:
        by_status.setdefault(r["status"], []).append(r)

    open_by_task = {
        r["key"]: r["n"] for r in conn.execute(
            "SELECT t.key, COUNT(*) AS n FROM review_thread r JOIN task t ON t.id = r.task_id "
            "WHERE r.state != 'closed' AND t.project_id = ? GROUP BY t.key", (ctx.pid,)
        ).fetchall()
    }
    blocked = deps.blocked_by_map(conn, ctx.pid)
    lines = []
    for slug, display, _cat in order:
        group = by_status.pop(slug, [])
        if not group:
            continue
        lines.append(f"== {display} ({len(group)})")
        for r in group:
            flags = []
            if blocked.get(r["key"]):
                flags.append("blocked by " + ", ".join(blocked[r["key"]]))
            if open_by_task.get(r["key"]):
                flags.append(f"{open_by_task[r['key']]} open review")
            if r["pr_number"]:
                flags.append(f"PR #{r['pr_number']} {r['pr_state']}/{r['pr_review_state']}")
            suffix = ("  [" + "; ".join(flags) + "]") if flags else ""
            lines.append(f"  {r['key']:<7} {TASK_KEY_PREFIX[r['task_type']]}  {r['priority']}  "
                         f"{r['assignee'] or '-':<12} {r['title']}{suffix}")
    # anything in a status no workflow declares is still shown, never hidden
    for slug, group in sorted(by_status.items()):
        lines.append(f"== {slug} ({len(group)})  [not in any workflow]")
        for r in group:
            lines.append(f"  {r['key']:<7} {r['title']}")
    ctx.emit(
        {"project": row_to_dict(ctx.project),
         "tasks_by_status": {slug: [row_to_dict(r) for r in group]
                             for slug, group in by_status.items()},
         "open_review_threads": open_by_task, "blocked_by": blocked},
        f"project: {ctx.project['slug']}  ({ctx.project['name']})\n\n"
        + ("\n".join(lines) if lines else "(no open tasks)"),
    )
    return 0


def cmd_next(ctx: Ctx, args) -> int:
    conn, actor, pid = ctx.conn, args.actor, ctx.pid
    threads = review.inbox(conn, pid, actor=actor)
    where = " AND t.status IN ('ready','in_progress','needs_work')"
    params: list = []
    if actor:
        where += " AND t.assignee = ?"
        params.append(actor)
    all_dev = _task_rows(conn, pid, where, params)
    blocked = deps.blocked_by_map(conn, pid)
    dev_items = [r for r in all_dev if r["key"] not in blocked]
    blocked_items = [r for r in all_dev if r["key"] in blocked]

    rev_where = " AND t.status = 'in_review'"
    rev_params: list = []
    if actor:
        rev_where += " AND t.reviewer = ?"
        rev_params.append(actor)
    rev_items = _task_rows(conn, pid, rev_where, rev_params)

    ready_to_accept, ready_to_done = [], []
    for r in _task_rows(conn, pid, " AND t.status = 'in_review'"):
        ok, _ = core.gate(conn, pid, r["key"], "accepted")
        if ok and (not actor or r["reviewer"] == actor):
            ready_to_accept.append(r)
    for r in _task_rows(conn, pid, " AND t.status = 'accepted'"):
        ok, _ = core.gate(conn, pid, r["key"], "done")
        if ok and (not actor or r["assignee"] == actor):
            ready_to_done.append(r)

    parts = []
    if threads:
        parts.append(f"REVIEW THREADS WAITING ON YOU ({len(threads)})\n"
                     + "\n\n".join(render_thread(t) for t in threads))
    if dev_items:
        parts.append("WORK TO DO\n" + tasks_table(dev_items))
    if blocked_items:
        parts.append("BLOCKED — do not start\n" + tasks_table(blocked_items) + "\n"
                     + "\n".join(f"  {r['key']} waits on " + ", ".join(blocked[r["key"]])
                                 for r in blocked_items))
    if rev_items:
        parts.append("AWAITING YOUR REVIEW\n" + tasks_table(rev_items))
    if ready_to_accept:
        parts.append(
            "GATES PASS — submit the review or delivery approval action\n"
            + tasks_table(ready_to_accept)
        )
    if ready_to_done:
        parts.append(
            "DELIVERY READY — submit the delivery or PR completion action\n"
            + tasks_table(ready_to_done)
        )
    ctx.emit(
        {"actor": actor, "project": ctx.project["slug"], "review_threads": threads,
         "work_to_do": [row_to_dict(r) for r in dev_items],
         "blocked": [{**row_to_dict(r), "blocked_by": blocked[r["key"]]} for r in blocked_items],
         "awaiting_your_review": [row_to_dict(r) for r in rev_items],
         "ready_to_accept": [row_to_dict(r) for r in ready_to_accept],
         "ready_to_done": [row_to_dict(r) for r in ready_to_done]},
        "\n\n".join(parts) if parts else "(nothing actionable)",
    )
    return 0


def cmd_history(ctx: Ctx, args) -> int:
    task = core.get_task(ctx.conn, ctx.pid, args.key)
    rows = ctx.conn.execute(
        "SELECT * FROM event WHERE task_id = ? ORDER BY id", (task["id"],)
    ).fetchall()
    ctx.emit([row_to_dict(r) for r in rows],
             table(["TS", "KIND", "ACTOR", "FROM", "TO", "DETAIL"],
                   [[r["ts"], r["kind"], r["actor"] or "-", r["from_value"] or "",
                     r["to_value"] or "", r["detail"]] for r in rows]))
    return 0


# --------------------------------------------------------------------------- #
# export / import
# --------------------------------------------------------------------------- #

_EXPORT_TABLES: list[tuple[str, str]] = [
    ("meta", "key"),
    ("project", "id"),
    ("key_counter", "project_id"),
    ("task", "id"),
    ("task_item", "id"),
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
        dump["tables"][t] = [{k: r[k] for k in r.keys()}
                             for r in conn.execute(f"SELECT * FROM {t} ORDER BY {order}")]
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

    if version < SCHEMA_VERSION:
        # An older dump carries the feature/item shape; convert as we load it.
        from .db import load_v2_export

        slug = args.as_project or ctx.spec.project
        proj = get_or_create_project(conn, slug, ctx.spec)
        notes = load_v2_export(conn, int(proj["id"]), data["tables"])
        ctx.emit({"imported": args.file, "project": proj["slug"],
                  "converted_from_schema": version, "notes": notes},
                 f"imported {args.file} into project '{proj['slug']}' "
                 f"(converted from schema v{version})\n"
                 + "\n".join(f"  {n}" for n in notes))
        return 0

    if not args.replace:
        raise BacklogError("import rewrites the whole store; pass --replace to confirm")
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
    ctx.emit({"imported": args.file, "sequences_resynced": moved},
             f"replaced backlog from {args.file}"
             + (f"\n  sequences resynced: {', '.join(moved)}" if moved else ""))
    return 0


# --------------------------------------------------------------------------- #
# parser
# --------------------------------------------------------------------------- #

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="backlog", description=__doc__)
    p.add_argument("--json", action="store_true", help="machine-readable output")
    p.add_argument("--actor", help="who is performing this action (recorded in history)")
    p.add_argument("--project", help="project slug to act on (default: $BACKLOG_PROJECT)")
    p.add_argument("--version", action="version", version=f"backlog {__version__}")

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
    common.add_argument("--actor", default=argparse.SUPPRESS)
    common.add_argument("--project", default=argparse.SUPPRESS)

    class Sub:
        def __init__(self, action):
            self._action = action

        def add_parser(self, name, **kw):
            kw.setdefault("parents", [common])
            return self._action.add_parser(name, **kw)

        def group(self, name, **kw):
            grp = self._action.add_parser(name, **kw)
            return Sub(grp.add_subparsers(dest="sub", required=True))

    sub = Sub(p.add_subparsers(dest="cmd", required=True))

    sp = sub.add_parser("init", help="create the store and this project")
    sp.add_argument("path", nargs="?", default=".")
    sp.add_argument("--force", action="store_true")
    sp.set_defaults(func=cmd_init)

    sp = sub.add_parser("doctor", help="verify store integrity and invariants")
    sp.set_defaults(func=cmd_doctor)

    sp = sub.add_parser(
        "where", help="which store and project this invocation talks to",
        description=(
            "Resolved from the environment:\n"
            "  BACKLOG_DB         sqlite | postgres; unset => repo SQLite\n"
            "  BACK_LOG_URL       SQLite path/URL or PostgreSQL DSN\n"
            "                     BACKLOG_DB=sqlite with no URL => repo mode\n"
            "  BACKLOG_DB may still hold a legacy path/URL for compatibility\n"
            "  BACKLOG_PROJECT    project slug (default: git repo name)\n"
            "  BACKLOG_SCHEMA     PostgreSQL schema (default: backlog)\n"
            "  BACKLOG_DIR        explicit .backlog directory (repo mode)\n"
            "  BACKLOG_ARTIFACTS  where artifact files live"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sp.set_defaults(func=cmd_where)

    sp = sub.add_parser("projects", help="every project in this store")
    sp.set_defaults(func=cmd_projects)

    pp = sub.group("project", help="projects in this store")
    sp = pp.add_parser("add")
    sp.add_argument("--name", required=True)
    sp.add_argument("--slug")
    sp.add_argument("--description")
    sp.add_argument("--template", help="template to build the project's flow from")
    sp.set_defaults(func=cmd_project_add)
    sp = pp.add_parser("set")
    sp.add_argument("slug")
    sp.add_argument("--name")
    sp.add_argument("--description")
    sp.add_argument("--status", choices=["active", "archived"])
    sp.set_defaults(func=cmd_project_set)
    sp = pp.add_parser("list")
    sp.set_defaults(func=cmd_projects)

    sp = sub.add_parser("statuses", help="this project's status flow, per task type")
    sp.add_argument("--type", choices=TASK_TYPES)
    sp.set_defaults(func=cmd_statuses)

    sp = sub.add_parser("templates", help="the project templates available")
    sp.set_defaults(func=cmd_templates)

    tp2 = sub.group("template", help="pre-defined project shapes new projects are built from")
    sp = tp2.add_parser("show")
    sp.add_argument("slug")
    sp.add_argument("--type", choices=TASK_TYPES)
    sp.set_defaults(func=cmd_template_show)
    sp = tp2.add_parser("list")
    sp.set_defaults(func=cmd_templates)
    sp = tp2.add_parser("add", help="author a template")
    sp.add_argument("--slug", required=True)
    sp.add_argument("--name")
    sp.add_argument("--description")
    sp.add_argument("--copy-of", dest="copy_of", metavar="TEMPLATE")
    sp.add_argument("--from-project", dest="from_project", metavar="PROJECT",
                    help="capture a project's current flow as a template")
    sp.set_defaults(func=cmd_template_add)
    sp = tp2.add_parser("rm")
    sp.add_argument("slug")
    sp.set_defaults(func=cmd_template_rm)
    sp = tp2.add_parser("default", help="make a template the one new projects use")
    sp.add_argument("slug")
    sp.set_defaults(func=cmd_template_default)
    sp = tp2.add_parser("status-add")
    sp.add_argument("slug")
    sp.add_argument("--type", required=True, choices=TASK_TYPES)
    sp.add_argument("--status", required=True)
    sp.add_argument("--display")
    sp.add_argument("--category", default="active", choices=STATUS_CATEGORIES)
    sp.add_argument("--after")
    sp.add_argument("--satisfies", action="store_true")
    sp.add_argument("--terminal", action="store_true")
    sp.set_defaults(func=cmd_template_status_add)
    sp = tp2.add_parser("move-add")
    sp.add_argument("slug")
    sp.add_argument("--type", required=True, choices=TASK_TYPES)
    sp.add_argument("--from", dest="from", required=True)
    sp.add_argument("--to", required=True)
    sp.add_argument("--gate")
    sp.set_defaults(func=cmd_template_move_add)

    wp = sub.group("workflow", help="define this project's status flow")
    sp = wp.add_parser("apply", help="re-instantiate this project's flow from a template")
    sp.add_argument("--template", help="default: the template the project was created from")
    sp.add_argument("--type", choices=TASK_TYPES)
    sp.set_defaults(func=cmd_workflow_apply)
    sp = wp.add_parser("show", help="the flow this project runs")
    sp.add_argument("--type", choices=TASK_TYPES)
    sp.set_defaults(func=cmd_workflow_show)
    sp = wp.add_parser("gates", help="the gate checks a transition may demand")
    sp.set_defaults(func=cmd_workflow_gates)
    sp = wp.add_parser("status-add", help="add a status to a flow")
    sp.add_argument("--type", required=True, choices=TASK_TYPES)
    sp.add_argument("--slug", required=True)
    sp.add_argument("--display")
    sp.add_argument("--category", default="active", choices=STATUS_CATEGORIES)
    sp.add_argument("--after", help="place it after this status")
    sp.add_argument("--satisfies", action="store_true",
                    help="work in this status counts as finished for dependents")
    sp.add_argument("--terminal", action="store_true")
    sp.add_argument("--description")
    sp.set_defaults(func=cmd_workflow_status_add)
    sp = wp.add_parser("status-rm")
    sp.add_argument("--type", required=True, choices=TASK_TYPES)
    sp.add_argument("--slug", required=True)
    sp.set_defaults(func=cmd_workflow_status_rm)
    sp = wp.add_parser("move-add", help="allow a transition, with optional gates")
    sp.add_argument("--type", required=True, choices=TASK_TYPES)
    sp.add_argument("--from", dest="from", required=True)
    sp.add_argument("--to", required=True)
    sp.add_argument("--gate", help="comma-separated gate checks (see `workflow gates`)")
    sp.add_argument("--note")
    sp.set_defaults(func=cmd_workflow_move_add)
    sp = wp.add_parser("move-rm")
    sp.add_argument("--type", required=True, choices=TASK_TYPES)
    sp.add_argument("--from", dest="from", required=True)
    sp.add_argument("--to", required=True)
    sp.set_defaults(func=cmd_workflow_move_rm)
    sp = wp.add_parser("reset", help="back to this project's template")
    sp.add_argument("--type", choices=TASK_TYPES)
    sp.set_defaults(func=cmd_workflow_reset)
    sp = wp.add_parser("copy", help="adopt another project's flow")
    sp.add_argument("--from", dest="from", required=True, metavar="PROJECT")
    sp.add_argument("--type", choices=TASK_TYPES)
    sp.set_defaults(func=cmd_workflow_copy)

    sp = sub.add_parser("board", help="this project's open work grouped by status")
    sp.add_argument("--all", action="store_true", help="include Accepted and Done")
    sp.set_defaults(func=cmd_board)

    sp = sub.add_parser("next", help="what should be worked on now")
    sp.set_defaults(func=cmd_next)

    sp = sub.add_parser("show", help="one task in full")
    sp.add_argument("key")
    sp.set_defaults(func=cmd_show)

    sp = sub.add_parser("history", help="audit trail for a task")
    sp.add_argument("key")
    sp.set_defaults(func=cmd_history)

    def add_create_flags(sp, with_branch=True):
        sp.add_argument("--title", required=True)
        sp.add_argument("--description")
        sp.add_argument("--ac", help="acceptance criteria, one per line")
        sp.add_argument("--priority", default="P2")
        sp.add_argument("--owner")
        sp.add_argument("--assignee")
        sp.add_argument("--reviewer")
        if with_branch:
            sp.add_argument("--branch")
        add_execution_flags(sp)

    def add_execution_flags(sp):
        executor = sp.add_mutually_exclusive_group()
        executor.add_argument("--shell", metavar="COMMAND")
        executor.add_argument("--hook", metavar="NAME")
        sp.add_argument("--requirement", choices=["required", "advisory"])
        sp.add_argument("--timeout", type=int, metavar="SECONDS")
        sp.add_argument("--working-directory", metavar="PATH")
        sp.add_argument("--expected-exit-code", type=int)
        for stream in ("stdout", "stderr"):
            sp.add_argument(f"--{stream}-equals")
            sp.add_argument(f"--{stream}-contains")
            sp.add_argument(f"--{stream}-regex")
        sp.add_argument("--env", dest="environment", action="append", metavar="NAME=VALUE")
        sp.add_argument("--arguments", metavar="JSON")
        sp.add_argument("--expected-result", metavar="JSON")

    tp = sub.group("task", help="tasks of any type")
    sp = tp.add_parser("add")
    sp.add_argument("--type", required=True, choices=TASK_TYPES)
    sp.add_argument("--parent")
    add_create_flags(sp)
    sp.set_defaults(func=cmd_task_add)
    sp = tp.add_parser("list")
    _add_list_filters(sp)
    sp.add_argument("--type", choices=TASK_TYPES)
    sp.add_argument("--parent")
    sp.set_defaults(func=cmd_list)

    fp = sub.group("feature", help="features (containers of stories)")
    sp = fp.add_parser("add")
    add_create_flags(sp, with_branch=False)
    sp.set_defaults(func=cmd_feature_add)
    sp = fp.add_parser("list")
    _add_list_filters(sp)
    sp.set_defaults(func=cmd_feature_list)

    stp = sub.group("story", help="user stories")
    sp = stp.add_parser("add")
    sp.add_argument("--feature", help="parent feature key")
    add_create_flags(sp)
    sp.set_defaults(func=cmd_story_add)
    sp = stp.add_parser("list")
    _add_list_filters(sp)
    sp.add_argument("--parent", help="filter by feature key")
    sp.set_defaults(func=cmd_story_list)

    sbp = sub.group("subtask", help="subtasks of a story")
    sp = sbp.add_parser("add")
    sp.add_argument("--story", required=True)
    add_create_flags(sp)
    sp.set_defaults(func=cmd_subtask_add)
    sp = sbp.add_parser("list")
    _add_list_filters(sp)
    sp.add_argument("--parent", help="filter by story key")
    sp.set_defaults(func=cmd_subtask_list)

    sp = sub.add_parser("list", help="every task in this project")
    _add_list_filters(sp)
    sp.add_argument("--type", choices=TASK_TYPES)
    sp.add_argument("--parent")
    sp.set_defaults(func=cmd_list)

    sp = sub.add_parser("set", help="edit fields of a task")
    sp.add_argument("key")
    sp.add_argument("--title")
    sp.add_argument("--description")
    sp.add_argument("--ac", help="replace the acceptance criteria, one per line")
    sp.add_argument("--priority")
    sp.add_argument("--owner")
    sp.add_argument("--branch")
    sp.add_argument("--parent")
    add_execution_flags(sp)
    sp.set_defaults(func=cmd_set)

    sp = sub.add_parser("assign", help="set assignee and/or reviewer")
    sp.add_argument("key")
    sp.add_argument("--to")
    sp.add_argument("--reviewer")
    sp.add_argument("--to-kind", dest="to_kind", choices=["human", "agent"],
                    help="override the human/agent guess for --to")
    sp.add_argument("--reviewer-kind", dest="reviewer_kind", choices=["human", "agent"])
    sp.set_defaults(func=cmd_assign)

    sp = sub.add_parser(
        "action", help="submit a semantic action; the workflow selects the destination"
    )
    sp.add_argument("key")
    sp.add_argument("action", choices=[action.value for action in hooks.public_actions()])
    sp.add_argument("--operation", default="cli.action")
    sp.add_argument("--parameter", action="append", metavar="NAME=VALUE")
    sp.add_argument("--no-pr", action="store_true")
    sp.add_argument("--allow-open-subtasks", action="store_true")
    sp.add_argument("--allow-blocked", action="store_true")
    sp.set_defaults(func=cmd_action)

    sp = sub.add_parser(
        "actions", help="list semantic actions configured for a task's current state"
    )
    sp.add_argument("key")
    sp.set_defaults(func=cmd_actions)

    sp = sub.add_parser("gate", help="check a gate without transitioning (exit 2 = blocked)")
    sp.add_argument("key")
    sp.add_argument("--for", dest="for", required=True,
                    choices=["start", "in_progress", "accepted", "done", "merge", "in_review"])
    sp.add_argument("--no-pr", action="store_true")
    sp.add_argument("--allow-open-subtasks", action="store_true")
    sp.add_argument("--allow-blocked", action="store_true")
    sp.set_defaults(func=cmd_gate)

    ip = sub.group("item", help="sections of a task: criteria, checklist, notes")
    sp = ip.add_parser("add")
    sp.add_argument("key")
    sp.add_argument("--kind", default="acceptance_criteria", choices=ITEM_KINDS)
    sp.add_argument("--content", required=True, help="one entry per line")
    add_execution_flags(sp)
    sp.set_defaults(func=cmd_item_add)
    sp = ip.add_parser("set", help="replace every entry of one kind")
    sp.add_argument("key")
    sp.add_argument("--kind", default="acceptance_criteria", choices=ITEM_KINDS)
    sp.add_argument("--content", required=True)
    add_execution_flags(sp)
    sp.set_defaults(func=cmd_item_set)
    sp = ip.add_parser("list")
    sp.add_argument("key")
    sp.add_argument("--kind", choices=ITEM_KINDS)
    sp.set_defaults(func=cmd_item_list)
    sp = ip.add_parser("check", help="tick a checklist entry")
    sp.add_argument("id", type=int)
    sp.add_argument("--undo", action="store_true")
    sp.set_defaults(func=cmd_item_check)
    sp = ip.add_parser("rm")
    sp.add_argument("id", type=int)
    sp.set_defaults(func=cmd_item_rm)

    dp = sub.group("dep", help="dependencies between tasks")
    for name, helptext in (("add", "record a dependency"), ("rm", "drop a dependency")):
        sp = dp.add_parser(name, help=helptext)
        sp.add_argument("key")
        grp = sp.add_mutually_exclusive_group(required=True)
        grp.add_argument("--blocks", metavar="KEY")
        grp.add_argument("--blocked-by", dest="blocked_by", metavar="KEY")
        grp.add_argument("--relates", metavar="KEY")
        grp.add_argument("--duplicates", metavar="KEY")
        if name == "add":
            sp.add_argument("--note")
        sp.set_defaults(func=cmd_dep_add if name == "add" else cmd_dep_rm)
    sp = dp.add_parser("list")
    sp.add_argument("key", nargs="?")
    sp.add_argument("--kind", choices=DEPENDENCY_KINDS)
    sp.set_defaults(func=cmd_dep_list)
    sp = dp.add_parser("check", help="is this task startable? (exit 2 = blocked)")
    sp.add_argument("key")
    sp.set_defaults(func=cmd_dep_check)
    sp = dp.add_parser("graph")
    sp.add_argument("--format", choices=["text", "dot", "json"], default="text")
    sp.set_defaults(func=cmd_dep_graph)

    prp = sub.group("pr", help="pull-request reference")
    sp = prp.add_parser("set")
    sp.add_argument("key")
    sp.add_argument("--url")
    sp.add_argument("--number", type=int)
    sp.add_argument("--repo")
    sp.add_argument("--state", choices=PR_STATES)
    sp.add_argument("--review-state", dest="review_state", choices=PR_REVIEW_STATES)
    sp.set_defaults(func=cmd_pr_set)
    sp = prp.add_parser("sync", help="refresh state from `gh`")
    sp.add_argument("key")
    sp.set_defaults(func=cmd_pr_sync)

    rp = sub.group("review", help="threaded review comments")
    sp = rp.add_parser("open")
    sp.add_argument("key")
    sp.add_argument("--author", required=True)
    sp.add_argument("--body", required=True)
    sp.add_argument("--role", choices=["reviewer", "developer", "auto"], default="auto")
    sp.add_argument("--title")
    sp.add_argument("--file")
    sp.add_argument("--line", type=int)
    sp.add_argument(
        "--severity",
        choices=["blocker", "nice_to_have", "info"],
        default="blocker",
    )
    sp.set_defaults(func=cmd_review_open)
    sp = rp.add_parser("reply")
    sp.add_argument("comment")
    sp.add_argument("--author", required=True)
    sp.add_argument("--action", required=True, choices=["comment", "fix", "reject", "accept"])
    sp.add_argument("--body", required=True)
    sp.add_argument("--role", choices=["reviewer", "developer", "auto"], default="auto")
    sp.set_defaults(func=cmd_review_reply)
    sp = rp.add_parser("reopen")
    sp.add_argument("root")
    sp.add_argument("--author", required=True)
    sp.add_argument("--body", required=True)
    sp.add_argument("--role", choices=["reviewer", "developer", "auto"], default="auto")
    sp.set_defaults(func=cmd_review_reopen)
    sp = rp.add_parser("inbox")
    sp.add_argument("--role", choices=["reviewer", "developer"])
    sp.add_argument("--item")
    sp.add_argument("--severity", choices=["blocker", "nice_to_have", "info"])
    sp.set_defaults(func=cmd_review_inbox)
    sp = rp.add_parser("thread")
    sp.add_argument("root")
    sp.add_argument("--full", action="store_true")
    sp.set_defaults(func=cmd_review_thread)
    sp = rp.add_parser("list")
    sp.add_argument("key")
    sp.add_argument("--state", choices=["open", "closed", "all"], default="open")
    sp.add_argument("--severity", choices=["blocker", "nice_to_have", "info"])
    sp.set_defaults(func=cmd_review_list)
    sp = rp.add_parser("severity")
    sp.add_argument("root")
    sp.add_argument("--severity", required=True,
                    choices=["blocker", "nice_to_have", "info"])
    sp.add_argument("--author", required=True)
    sp.set_defaults(func=cmd_review_severity)

    ap = sub.group("artifact", help="files attached to a task")
    sp = ap.add_parser("add")
    sp.add_argument("key")
    sp.add_argument("path")
    sp.add_argument("--title")
    sp.add_argument("--kind", default="doc", choices=ARTIFACT_KINDS)
    sp.set_defaults(func=cmd_artifact_add)
    sp = ap.add_parser("list")
    sp.add_argument("key")
    sp.set_defaults(func=cmd_artifact_list)

    sp = sub.add_parser("export", help="JSON dump of the whole store")
    sp.add_argument("--out")
    sp.set_defaults(func=cmd_export)
    sp = sub.add_parser("import", help="restore from a dump (older dumps are converted)")
    sp.add_argument("file")
    sp.add_argument("--replace", action="store_true")
    sp.add_argument("--as-project", dest="as_project",
                    help="project slug to load an older dump into")
    sp.set_defaults(func=cmd_import)

    return p


def _add_list_filters(sp: argparse.ArgumentParser) -> None:
    sp.add_argument("--status")
    sp.add_argument("--open", action="store_true", help="exclude Accepted and Done")
    sp.add_argument("--assignee")
    sp.add_argument("--reviewer")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    ctx = Ctx(args)
    if not hasattr(args, "actor"):
        args.actor = None
    try:
        return args.func(ctx, args)
    except BacklogError as e:
        if getattr(args, "json", False):
            print(json.dumps({"error": str(e)}, indent=2))
        else:
            print(f"error: {e}", file=sys.stderr)
        return 1
    except database_errors() as e:
        print(f"error: database: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

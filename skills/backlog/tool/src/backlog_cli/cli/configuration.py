"""backlog — backlog tracker for coding agents (SQLite or shared PostgreSQL)."""

from __future__ import annotations


from .. import (
    core,
    templates,
    workflow,
)
from ..db import (
    require_project,
)
from ..render import (
    row_to_dict,
    table,
)
from ..schema import (
    GATE_CHECKS,
    GATE_DESCRIPTIONS,
    TASK_TYPES,
)


from .context import Ctx
from .validation import cmd_statuses


def cmd_templates(ctx: Ctx, args) -> int:
    templates.install_builtins(ctx.conn)
    rows = templates.list_all(ctx.conn)
    ctx.emit(
        [row_to_dict(r) for r in rows],
        table(
            ["", "TEMPLATE", "WORKFLOWS", "PROJECTS", "NAME", "DESCRIPTION"],
            [
                [
                    "*" if r["is_default"] else "",
                    r["slug"],
                    str(r["workflows"]),
                    str(r["projects"]),
                    r["name"],
                    r["description"][:60],
                ]
                for r in rows
            ],
        ),
    )
    return 0


def cmd_template_show(ctx: Ctx, args) -> int:
    templates.install_builtins(ctx.conn)
    tpl = templates.require(ctx.conn, args.slug)
    types = [core.normalize_type(args.type)] if args.type else TASK_TYPES
    blocks = [
        f"{tpl['slug']}  {tpl['name']}"
        + ("  [default]" if tpl["is_default"] else "")
        + (f"\n{tpl['description']}" if tpl["description"] else "")
    ]
    payload = {"template": row_to_dict(tpl), "workflows": {}}
    workflows = templates.workflows_of(ctx.conn, int(tpl["id"]))
    for ttype in types:
        blocks.append(
            f"\n== {ttype}\n" + templates.render(ctx.conn, int(tpl["id"]), ttype)
        )
        wf = workflows[ttype]
        payload["workflows"][ttype] = {
            "statuses": [
                row_to_dict(x) for x in templates.statuses_of(ctx.conn, int(wf["id"]))
            ],
            "transitions": [
                row_to_dict(x)
                for x in templates.transitions_of(ctx.conn, int(wf["id"]))
            ],
        }
    ctx.emit(payload, "\n".join(blocks))
    return 0


def cmd_template_add(ctx: Ctx, args) -> int:
    templates.install_builtins(ctx.conn)
    from_project = None
    if args.from_project:
        from_project = int(require_project(ctx.conn, args.from_project)["id"])
    row = templates.create(
        ctx.conn,
        args.slug,
        args.name or args.slug,
        description=args.description or "",
        copy_of=args.copy_of,
        from_project=from_project,
    )
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
    row = templates.add_status(
        ctx.conn,
        args.slug,
        core.normalize_type(args.type),
        args.status,
        args.display or "",
        category=args.category,
        after=args.after,
        satisfies=args.satisfies,
        terminal=args.terminal,
    )
    ctx.emit(
        row_to_dict(row),
        f"{args.slug}/{args.type}: added {row['display']} ({row['slug']})",
    )
    return 0


def cmd_template_move_add(ctx: Ctx, args) -> int:
    templates.set_transition(
        ctx.conn,
        args.slug,
        core.normalize_type(args.type),
        args.__dict__["from"],
        args.to,
        gates=args.gate or "",
    )
    ctx.emit(
        {
            "template": args.slug,
            "type": args.type,
            "from": args.__dict__["from"],
            "to": args.to,
            "gates": args.gate or "",
        },
        f"{args.slug}/{args.type}: {args.__dict__['from']} -> {args.to}"
        + (f"  (gates: {args.gate})" if args.gate else ""),
    )
    return 0


def cmd_workflow_apply(ctx: Ctx, args) -> int:
    """Re-instantiate this project's flow from a template."""
    tpl = (
        templates.require(ctx.conn, args.template)
        if args.template
        else workflow.template_of(ctx.conn, ctx.pid)
    )
    done = templates.instantiate(
        ctx.conn,
        int(tpl["id"]),
        ctx.pid,
        core.normalize_type(args.type) if args.type else None,
        replace=True,
    )
    ctx.conn.execute(
        "UPDATE project SET template_id = ? WHERE id = ?", (tpl["id"], ctx.pid)
    )
    ctx.conn.commit()
    ctx.emit(
        {"template": tpl["slug"], "types": done},
        f"applied template '{tpl['slug']}' to {ctx.project['slug']}: "
        + ", ".join(done),
    )
    return 0


def cmd_workflow_upgrade(ctx: Ctx, args) -> int:
    """Add only missing task-type flows from the project's template."""
    slug = ctx.project_override or ctx.spec.project
    project = require_project(ctx.conn, slug)
    added = workflow.upgrade(ctx.conn, int(project["id"]))
    ctx.emit(
        {"project": project["slug"], "added": added},
        ("added missing workflows: " + ", ".join(added))
        if added
        else "workflow already up to date; no project-specific flow was changed",
    )
    return 0


def cmd_workflow_show(ctx: Ctx, args) -> int:
    return cmd_statuses(ctx, args)


def cmd_workflow_gates(ctx: Ctx, args) -> int:
    ctx.emit(
        {"gates": {g: GATE_DESCRIPTIONS[g] for g in GATE_CHECKS}},
        table(["GATE", "MEANS"], [[g, GATE_DESCRIPTIONS[g]] for g in GATE_CHECKS]),
    )
    return 0


def cmd_workflow_status_add(ctx: Ctx, args) -> int:
    row = workflow.add_status(
        ctx.conn,
        ctx.pid,
        core.normalize_type(args.type),
        args.slug,
        args.display or "",
        category=args.category,
        after=args.after,
        satisfies=args.satisfies,
        terminal=args.terminal,
        description=args.description or "",
    )
    ctx.emit(
        row_to_dict(row),
        f"added {row['display']} ({row['slug']}) to the {args.type} flow  "
        f"[{row['category']}]",
    )
    return 0


def cmd_workflow_status_rm(ctx: Ctx, args) -> int:
    workflow.remove_status(ctx.conn, ctx.pid, core.normalize_type(args.type), args.slug)
    ctx.emit(
        {"removed": args.slug, "type": args.type},
        f"removed {args.slug} from the {args.type} flow",
    )
    return 0


def cmd_workflow_move_add(ctx: Ctx, args) -> int:
    workflow.set_transition(
        ctx.conn,
        ctx.pid,
        core.normalize_type(args.type),
        args.__dict__["from"],
        args.to,
        gates=args.gate or "",
        note=args.note or "",
    )
    ctx.emit(
        {
            "type": args.type,
            "from": args.__dict__["from"],
            "to": args.to,
            "gates": args.gate or "",
        },
        f"{args.type}: {args.__dict__['from']} -> {args.to}"
        + (f"  (gates: {args.gate})" if args.gate else ""),
    )
    return 0


def cmd_workflow_move_rm(ctx: Ctx, args) -> int:
    workflow.remove_transition(
        ctx.conn,
        ctx.pid,
        core.normalize_type(args.type),
        args.__dict__["from"],
        args.to,
    )
    ctx.emit(
        {"removed": [args.__dict__["from"], args.to]},
        f"{args.type}: removed {args.__dict__['from']} -> {args.to}",
    )
    return 0


def cmd_workflow_reset(ctx: Ctx, args) -> int:
    types = [core.normalize_type(args.type)] if args.type else TASK_TYPES
    for t in types:
        workflow.reset(ctx.conn, ctx.pid, t)
    ctx.emit({"reset": types}, "reset to the built-in flow: " + ", ".join(types))
    return 0


def cmd_workflow_copy(ctx: Ctx, args) -> int:
    src = require_project(ctx.conn, args.__dict__["from"])
    done = workflow.copy_from(
        ctx.conn,
        int(src["id"]),
        ctx.pid,
        core.normalize_type(args.type) if args.type else None,
    )
    ctx.emit(
        {"copied_from": src["slug"], "types": done},
        f"adopted {src['slug']}'s flow for: " + ", ".join(done),
    )
    return 0

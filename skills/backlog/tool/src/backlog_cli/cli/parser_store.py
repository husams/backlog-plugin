"""backlog — backlog tracker for coding agents (SQLite or shared PostgreSQL)."""

from __future__ import annotations

import argparse

from ..schema import (
    STATUS_CATEGORIES,
    TASK_TYPES,
)


from .store import (
    cmd_init,
    cmd_where,
    cmd_projects,
    cmd_project_add,
    cmd_project_set,
    cmd_doctor,
)
from .validation import (
    cmd_statuses,
)
from .configuration import (
    cmd_templates,
    cmd_template_show,
    cmd_template_add,
    cmd_template_rm,
    cmd_template_default,
    cmd_template_status_add,
    cmd_template_move_add,
    cmd_workflow_apply,
    cmd_workflow_upgrade,
    cmd_workflow_show,
    cmd_workflow_gates,
    cmd_workflow_status_add,
    cmd_workflow_status_rm,
    cmd_workflow_move_add,
    cmd_workflow_move_rm,
    cmd_workflow_reset,
    cmd_workflow_copy,
)


def register_store(sub):
    sp = sub.add_parser("init", help="create the store and this project")
    sp.add_argument("path", nargs="?", default=".")
    sp.add_argument("--force", action="store_true")
    sp.set_defaults(func=cmd_init)

    sp = sub.add_parser("doctor", help="verify store integrity and invariants")
    sp.set_defaults(func=cmd_doctor)

    sp = sub.add_parser(
        "where",
        help="which store and project this invocation talks to",
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

    tp2 = sub.group(
        "template", help="pre-defined project shapes new projects are built from"
    )
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
    sp.add_argument(
        "--from-project",
        dest="from_project",
        metavar="PROJECT",
        help="capture a project's current flow as a template",
    )
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
    sp = wp.add_parser(
        "apply", help="re-instantiate this project's flow from a template"
    )
    sp.add_argument(
        "--template", help="default: the template the project was created from"
    )
    sp.add_argument("--type", choices=TASK_TYPES)
    sp.set_defaults(func=cmd_workflow_apply)
    sp = wp.add_parser(
        "upgrade",
        help="add missing shipped task-type flows without changing existing flows",
    )
    sp.set_defaults(func=cmd_workflow_upgrade)
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
    sp.add_argument(
        "--satisfies",
        action="store_true",
        help="work in this status counts as finished for dependents",
    )
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

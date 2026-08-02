"""backlog — backlog tracker for coding agents (SQLite or shared PostgreSQL)."""

from __future__ import annotations


from .. import (
    hooks,
    retrospective,
)
from ..schema import (
    TASK_TYPES,
)


from .tasks import (
    cmd_task_add,
    cmd_feature_add,
    cmd_story_add,
    cmd_bug_add,
    cmd_iteration_add,
    cmd_iteration_member_add,
    cmd_iteration_member_remove,
    cmd_retrospective_add,
    cmd_retrospective_list,
    cmd_retrospective_show,
    cmd_retrospective_accept,
    cmd_retrospective_reject,
    cmd_retrospective_close,
    cmd_retrospective_history,
    cmd_subtask_add,
    _list,
    cmd_list,
    cmd_feature_list,
    cmd_story_list,
    cmd_bug_list,
    cmd_subtask_list,
    cmd_set,
    cmd_assign,
    cmd_action,
)
from .validation import (
    cmd_actions,
    cmd_gate,
)

from .parser_helpers import _add_list_filters, add_create_flags, add_execution_flags


def register_tasks(sub):
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

    bp = sub.group("bug", help="standalone defects")
    sp = bp.add_parser("add")
    add_create_flags(sp)
    sp.set_defaults(func=cmd_bug_add)
    sp = bp.add_parser("list")
    _add_list_filters(sp)
    sp.set_defaults(func=cmd_bug_list)

    ip = sub.group("iteration", help="parallel units of work")
    sp = ip.add_parser("add")
    add_create_flags(sp, with_branch=False)
    sp.set_defaults(func=cmd_iteration_add)
    sp = ip.add_parser("list")
    _add_list_filters(sp)
    sp.set_defaults(func=lambda ctx, args: _list(ctx, args, "iteration"))
    sp = ip.add_parser(
        "member-add", help="add a Ready Story or Bug to an Open Iteration"
    )
    sp.add_argument("iteration")
    sp.add_argument("member")
    sp.set_defaults(func=cmd_iteration_member_add)
    sp = ip.add_parser("member-remove", help="remove work from an Open Iteration")
    sp.add_argument("iteration")
    sp.add_argument("member")
    sp.set_defaults(func=cmd_iteration_member_remove)

    rap = sub.group(
        "retrospective",
        help="workflow-improvement actions discovered during Iterations",
    )
    sp = rap.add_parser("add", help="record a repeated issue and proposed solution")
    sp.add_argument("--iteration", required=True)
    sp.add_argument("--issue", required=True, help="the repeated workflow issue")
    sp.add_argument("--solution", required=True, help="the proposed improvement")
    sp.add_argument("--title", help="short label; defaults to the issue's first line")
    sp.set_defaults(func=cmd_retrospective_add)
    sp = rap.add_parser("list", help="list retrospective actions in this project")
    sp.add_argument("--status", choices=retrospective.STATUSES)
    sp.add_argument("--iteration")
    sp.set_defaults(func=cmd_retrospective_list)
    sp = rap.add_parser("show", help="show one retrospective action")
    sp.add_argument("key")
    sp.set_defaults(func=cmd_retrospective_show)
    sp = rap.add_parser("accept", help="accept a Created action and make it Ready")
    sp.add_argument("key")
    sp.set_defaults(func=cmd_retrospective_accept)
    sp = rap.add_parser("reject", help="reject a Created or Ready action with a reason")
    sp.add_argument("key")
    sp.add_argument("--reason", required=True)
    sp.set_defaults(func=cmd_retrospective_reject)
    sp = rap.add_parser("close", help="close a Ready action against a Feature or Bug")
    sp.add_argument("key")
    sp.add_argument(
        "--resolution-project",
        required=True,
        help="project containing the Feature or Bug (may differ from the owning project)",
    )
    resolution = sp.add_mutually_exclusive_group(required=True)
    resolution.add_argument("--feature")
    resolution.add_argument("--bug")
    sp.set_defaults(func=cmd_retrospective_close)
    sp = rap.add_parser("history", help="audit trail for one retrospective action")
    sp.add_argument("key")
    sp.set_defaults(func=cmd_retrospective_history)

    sbp = sub.group("subtask", help="subtasks of a story or bug")
    sp = sbp.add_parser("add")
    parent = sp.add_mutually_exclusive_group(required=True)
    parent.add_argument("--story")
    parent.add_argument("--bug")
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
    sp.add_argument(
        "--to-kind",
        dest="to_kind",
        choices=["human", "agent"],
        help="override the human/agent guess for --to",
    )
    sp.add_argument("--reviewer-kind", dest="reviewer_kind", choices=["human", "agent"])
    sp.set_defaults(func=cmd_assign)

    sp = sub.add_parser(
        "action", help="submit a semantic action; the workflow selects the destination"
    )
    sp.add_argument("key")
    sp.add_argument(
        "action", choices=[action.value for action in hooks.public_actions()]
    )
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

    sp = sub.add_parser(
        "gate", help="check a gate without transitioning (exit 2 = blocked)"
    )
    sp.add_argument("key")
    sp.add_argument(
        "--for",
        dest="for",
        required=True,
        choices=["start", "in_progress", "accepted", "done", "merge", "in_review"],
    )
    sp.add_argument("--no-pr", action="store_true")
    sp.add_argument("--allow-open-subtasks", action="store_true")
    sp.add_argument("--allow-blocked", action="store_true")
    sp.set_defaults(func=cmd_gate)

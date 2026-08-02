"""backlog — backlog tracker for coding agents (SQLite or shared PostgreSQL)."""

from __future__ import annotations


from ..schema import (
    ARTIFACT_KINDS,
    DEPENDENCY_KINDS,
    ITEM_KINDS,
    PR_REVIEW_STATES,
    PR_STATES,
)


from .items import (
    cmd_item_add,
    cmd_item_set,
    cmd_item_list,
    cmd_item_check,
    cmd_item_rm,
)
from .dependencies import (
    cmd_dep_add,
    cmd_dep_rm,
    cmd_dep_list,
    cmd_dep_check,
    cmd_dep_graph,
)
from .collaboration import (
    cmd_pr_set,
    cmd_pr_sync,
    cmd_review_open,
    cmd_review_reply,
    cmd_review_reopen,
    cmd_review_inbox,
    cmd_review_thread,
    cmd_review_audit,
    cmd_review_list,
    cmd_review_severity,
    cmd_artifact_add,
    cmd_artifact_list,
)
from .transfer import (
    cmd_export,
    cmd_import,
)

from .parser_helpers import add_execution_flags


def register_collaboration(sub):
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
    sp.add_argument(
        "--waive-validation",
        action="store_true",
        help="auditably complete without a current pass",
    )
    sp.add_argument("--reason", help="required non-empty waiver reason")
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
    sp.add_argument(
        "--action", required=True, choices=["comment", "fix", "reject", "accept"]
    )
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
    sp = rp.add_parser("audit")
    sp.add_argument("root")
    sp.set_defaults(func=cmd_review_audit)
    sp = rp.add_parser("list")
    sp.add_argument("key")
    sp.add_argument("--state", choices=["open", "closed", "all"], default="open")
    sp.add_argument("--severity", choices=["blocker", "nice_to_have", "info"])
    sp.set_defaults(func=cmd_review_list)
    sp = rp.add_parser("severity")
    sp.add_argument("root")
    sp.add_argument(
        "--severity", required=True, choices=["blocker", "nice_to_have", "info"]
    )
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
    sp = sub.add_parser(
        "import", help="restore from a dump (older dumps are converted)"
    )
    sp.add_argument("file")
    sp.add_argument("--replace", action="store_true")
    sp.add_argument(
        "--as-project",
        dest="as_project",
        help="project slug to load an older dump into",
    )
    sp.set_defaults(func=cmd_import)

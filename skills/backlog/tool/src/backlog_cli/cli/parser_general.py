"""backlog — backlog tracker for coding agents (SQLite or shared PostgreSQL)."""

from __future__ import annotations


from .tasks import (
    cmd_show,
)
from .validation import (
    cmd_validation_run,
    cmd_validation_run_all,
    cmd_validation_history,
    cmd_validation_waive,
)
from .board import (
    cmd_board,
    cmd_next,
    cmd_history,
)


def register_general(sub):
    sp = sub.add_parser("board", help="this project's open work grouped by status")
    sp.add_argument("--all", action="store_true", help="include Accepted and Done")
    sp.add_argument(
        "--iteration", help="only eligible member work from this Open Iteration"
    )
    sp.set_defaults(func=cmd_board)

    sp = sub.add_parser("next", help="what should be worked on now")
    sp.add_argument("--iteration", help="only member work from this Iteration")
    sp.set_defaults(func=cmd_next)

    sp = sub.add_parser("show", help="one task in full")
    sp.add_argument("key")
    sp.set_defaults(func=cmd_show)

    sp = sub.add_parser("history", help="audit trail for a task")
    sp.add_argument("key")
    sp.set_defaults(func=cmd_history)

    vp = sub.group("validation", help="execute declared item validations")
    sp = vp.add_parser("run", help="run one shell or hook executable item")
    sp.add_argument("item_id", type=int)
    sp.add_argument(
        "--project-root",
        default=".",
        help="trusted local project checkout (default: current directory)",
    )
    sp.set_defaults(func=cmd_validation_run)
    sp = vp.add_parser("run-all", help="run all executable items for a task")
    sp.add_argument("key")
    sp.add_argument(
        "--project-root",
        default=".",
        help="trusted local project checkout (default: current directory)",
    )
    sp.add_argument("--fail-fast", action="store_true")
    sp.set_defaults(func=cmd_validation_run_all)
    sp = vp.add_parser("history", help="inspect bounded validation result history")
    sp.add_argument("item_id", type=int)
    sp.add_argument("--limit", type=int, default=20)
    sp.add_argument("--project-root", default=".")
    sp.set_defaults(func=cmd_validation_history)
    sp = vp.add_parser("waive", help="audit an explicit validation waiver")
    sp.add_argument("item_id", type=int)
    sp.add_argument("--reason", required=True)
    sp.set_defaults(func=cmd_validation_waive)

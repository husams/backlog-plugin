"""Change named features, stories or subtasks through their configured flow.

    backlog-py scripts/change_status.py S-004 in_review --actor claude
    backlog-py scripts/change_status.py F-002 T-009 --done --actor claude

Only the named tasks are read. Each transition uses the public API and is
refused when it is illegal or a configured gate fails.
"""

import argparse

from backlog_cli import api


def _done_status(bl: api.Backlog, key: str) -> str:
    task = bl.task(key)
    flow = bl.flow(task.task_type)
    if flow.terminal is None:
        raise api.BacklogError(
            f"{key}: --done needs exactly one terminal status; "
            f"this {task.task_type} flow does not define one"
        )
    return flow.terminal


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("values", nargs="+", metavar="KEY_OR_STATUS")
    ap.add_argument("--done", action="store_true",
                    help="move each named task to its flow's terminal status")
    ap.add_argument("--actor", required=True)
    ap.add_argument("--reason", default="")
    ap.add_argument("--project")
    args = ap.parse_args()

    if args.done:
        keys, explicit_status = args.values, None
    else:
        if len(args.values) < 2:
            ap.error("pass one or more task keys followed by STATUS, or use --done")
        keys, explicit_status = args.values[:-1], args.values[-1]

    refused = []
    with api.open(project=args.project, actor=args.actor) as bl:
        for key in keys:
            try:
                target = explicit_status or _done_status(bl, key)
                task = bl.move(key, target, reason=args.reason)
                print(f"{task.key}  {task.status}  {task.title}")
            except api.BacklogError as exc:
                refused.append(f"{key}: {exc}")

    for message in refused:
        print(f"refused: {message}")
    return 2 if refused else 0


if __name__ == "__main__":
    raise SystemExit(main())

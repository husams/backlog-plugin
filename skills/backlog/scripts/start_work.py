"""Pick a task up safely: check it is startable, move it, print what it needs.

    backlog-py scripts/start_work.py S-004 --actor claude

Refuses with the reason rather than forcing the move. Exit 0 started, 2 refused.
"""

import argparse

from backlog_cli import api


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("key")
    ap.add_argument("--actor", required=True)
    ap.add_argument("--status", default="in_progress",
                    help="target status (default: in_progress)")
    ap.add_argument("--project")
    args = ap.parse_args()

    with api.open(project=args.project, actor=args.actor) as bl:
        task = bl.task(args.key)

        gate = bl.can(task.key, "start")
        if not gate.ok:
            print(f"refused: {'; '.join(gate.failures)}")
            return 2

        try:
            task = bl.move(task.key, args.status, reason="picked up")
        except api.BacklogError as exc:
            print(f"refused: {exc}")
            return 2

        print(f"started {task.key} ({task.status})  {task.title}")

        criteria = task.items("criteria")
        if criteria:
            print("\nacceptance")
            for i, line in enumerate(criteria, 1):
                print(f"  {i}. {line}")

        checklist = task.items("checklist")
        if checklist:
            print("\nchecklist")
            for line in checklist:
                print(f"  [ ] {line}")

        kids = [c for c in task.children if c.is_open]
        if kids:
            print("\nopen subtasks")
            for c in kids:
                print(f"  {c.key}  {c.status}  {c.title}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

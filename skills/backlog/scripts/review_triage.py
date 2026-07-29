"""What review comments are waiting on me, oldest first.

    backlog-py scripts/review_triage.py --actor claude

Shows the root comment and the latest reply for each thread -- the same three
comments `review inbox` shows, never the whole thread.
"""

import argparse

from backlog_cli import api


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--actor", help="whose queue; omit for every open thread")
    ap.add_argument("--role", choices=("developer", "reviewer"))
    ap.add_argument("--project")
    args = ap.parse_args()

    with api.open(project=args.project, actor=args.actor) as bl:
        threads = bl.inbox(actor=args.actor, role=args.role)
        if not threads:
            print("no review threads waiting" + (f" on {args.actor}" if args.actor else ""))
            return 0

        who = f" on {args.actor}" if args.actor else ""
        print(f"{len(threads)} thread{'s' if len(threads) != 1 else ''} waiting{who}\n")
        for th in threads:
            head = f"{th.task_key}  {th.root_key}  {th.opened_by}, {th.age_days:.0f}d"
            if th.file:
                head += f"  {th.where}"
            print(head)
            print(f'  "{th.body}"')
            if th.latest and th.latest != th.body:
                print(f"  latest: {th.latest_author} \"{th.latest}\"")
            if th.hidden_comments:
                print(f"  ({th.hidden_comments} more — `backlog review show {th.root_key} --full`)")
            print(f"  reply to: {th.reply_to}")
            print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

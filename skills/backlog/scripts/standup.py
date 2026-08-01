"""Where am I, and what should I do next -- in one process.

    backlog-py scripts/standup.py --actor claude

Replaces the four-command opening sequence (`where`, `board`, `next`,
`statuses`). Prints a few lines; never JSON.
"""

import argparse

from backlog_cli import api


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--actor", help="whose queue to report; omit for the whole board")
    ap.add_argument("--project")
    args = ap.parse_args()

    with api.open(project=args.project, actor=args.actor) as bl:
        counts = bl.counts()
        open_n = sum(n for s, n in counts.items() if s not in ("done", "dropped"))
        done_n = counts.get("done", 0)
        blocked = bl.blocked()
        retrospective_actions = sorted(
            bl.retrospective_actions(status=api.RetrospectiveStatus.CREATED)
            + bl.retrospective_actions(status=api.RetrospectiveStatus.READY),
            key=lambda action: action.key,
        )

        print(f"store   {bl.store}")
        print(f"board   {open_n} open / {len(blocked)} blocked / {done_n} done"
              f"   [{', '.join(f'{s}:{n}' for s, n in sorted(counts.items()))}]")

        if args.actor:
            ready = bl.startable(args.actor)
            print()
            if ready:
                print("you can start now")
                for t in ready:
                    print(f"  {t.key}  {t.title}")
            else:
                print("nothing assigned to you is startable")

            waiting = bl.inbox(args.actor)
            if waiting:
                print()
                print("waiting on your reply")
                for th in waiting:
                    print(f"  {th.root_key} on {th.task_key}  "
                          f"{th.opened_by}, {th.age_days:.0f}d  {th.body}")

        if retrospective_actions:
            print()
            print("open retrospective actions")
            for action in retrospective_actions:
                print(
                    f"  {action.key}  {action.status}  {action.iteration_key}  "
                    f"{action.title}  [next: {action.required_decision}]"
                )

        if blocked:
            print()
            print("blocked")
            for t, on in blocked:
                print(f"  {t.key} <- {', '.join(on)}")

        cycles = bl.cycles()
        if cycles:
            print()
            print(f"WARNING dependency cycle: {' -> '.join(cycles[0])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Is it safe to merge? Runs the real merge gate, one line per task.

    backlog-py scripts/merge_check.py S-004
    backlog-py scripts/merge_check.py --all

Exit 0 when everything asked about is ready, 2 when anything is blocked --
the same contract as `backlog gate --for merge`.
"""

import argparse

from backlog_cli import api


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("keys", nargs="*", help="task keys; omit with --all")
    ap.add_argument("--all", action="store_true",
                    help="every task currently in review")
    ap.add_argument("--project")
    args = ap.parse_args()

    if not args.keys and not args.all:
        ap.error("pass one or more keys, or --all")

    with api.open(project=args.project) as bl:
        keys = args.keys or [t.key for t in bl.tasks(status="in_review")]
        if not keys:
            print("nothing is in review")
            return 0

        gates = [bl.can(k, "merge") for k in keys]
        for g in gates:
            print(g)

        ready = sum(1 for g in gates if g.ok)
        if len(gates) > 1:
            print(f"\n{ready} of {len(gates)} ready to merge")
        return 0 if ready == len(gates) else 2


if __name__ == "__main__":
    raise SystemExit(main())

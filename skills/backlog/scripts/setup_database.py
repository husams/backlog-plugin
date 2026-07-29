"""Create or upgrade the configured backlog database and project.

    BACKLOG_DB=sqlite backlog-py scripts/setup_database.py
    BACKLOG_DB=sqlite BACK_LOG_URL=sqlite:///abs/backlog.db \
      backlog-py scripts/setup_database.py
    BACKLOG_DB=postgres BACK_LOG_URL=postgresql://host/backlog \
      backlog-py scripts/setup_database.py

This script is idempotent. It uses the backlog bootstrap and migration API;
it never reads or writes schema tables directly.
"""

import argparse
from pathlib import Path

from backlog_cli import db


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--path", default=".",
                    help="project root used for repo SQLite and project naming")
    args = ap.parse_args()

    root = Path(args.path).expanduser().resolve()
    if not root.is_dir():
        raise db.BacklogError(f"{root} is not a directory")

    try:
        spec = db.resolve_spec(root, for_init=True)
        db.init_store(root, spec=spec)
    except db.BacklogError as exc:
        print(f"error: {exc}")
        return 1
    print(f"database ready: {spec.dialect} ({spec.scope}) {spec.location}")
    print(f"project ready: {spec.project}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

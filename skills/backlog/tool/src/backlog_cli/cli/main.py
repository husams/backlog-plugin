"""CLI process entry point."""

from __future__ import annotations

import json
import sys

from ..db import BacklogError, database_errors
from .context import Ctx
from .parser import build_parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    ctx = Ctx(args)
    try:
        return args.func(ctx, args)
    except BacklogError as e:
        if getattr(args, "json", False):
            print(json.dumps({"error": str(e)}, indent=2))
        else:
            print(f"error: {e}", file=sys.stderr)
        return 1
    except database_errors() as e:
        print(f"error: database: {e}", file=sys.stderr)
        return 1

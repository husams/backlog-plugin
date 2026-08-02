"""Top-level CLI parser composition."""

import argparse

from .. import __version__
from .parser_collaboration import register_collaboration
from .parser_general import register_general
from .parser_helpers import Sub
from .parser_store import register_store
from .parser_tasks import register_tasks


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="backlog", description=__doc__)
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument(
        "--actor", help="who is performing this action (recorded in history)"
    )
    parser.add_argument(
        "--project", help="project slug to act on (default: $BACKLOG_PROJECT)"
    )
    parser.add_argument("--version", action="version", version=f"backlog {__version__}")

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
    common.add_argument("--actor", default=argparse.SUPPRESS)
    common.add_argument("--project", default=argparse.SUPPRESS)

    sub = Sub(parser.add_subparsers(dest="cmd", required=True), common)
    register_store(sub)
    register_general(sub)
    register_tasks(sub)
    register_collaboration(sub)
    return parser

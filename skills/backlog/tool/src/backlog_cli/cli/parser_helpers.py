"""Shared CLI parser construction helpers."""

import argparse


class Sub:
    """Add subcommands that inherit the common top-level flags."""

    def __init__(self, action, common):
        self._action = action
        self._common = common

    def add_parser(self, name, **kwargs):
        kwargs.setdefault("parents", [self._common])
        return self._action.add_parser(name, **kwargs)

    def group(self, name, **kwargs):
        group = self._action.add_parser(name, **kwargs)
        return Sub(group.add_subparsers(dest="sub", required=True), self._common)


def add_create_flags(parser: argparse.ArgumentParser, with_branch: bool = True) -> None:
    parser.add_argument("--title", required=True)
    parser.add_argument("--description")
    parser.add_argument("--ac", help="acceptance criteria, one per line")
    parser.add_argument("--priority", default="P2")
    parser.add_argument("--owner")
    parser.add_argument("--assignee")
    parser.add_argument("--reviewer")
    if with_branch:
        parser.add_argument("--branch")
    add_execution_flags(parser)


def add_execution_flags(parser: argparse.ArgumentParser) -> None:
    executor = parser.add_mutually_exclusive_group()
    executor.add_argument("--shell", metavar="COMMAND")
    executor.add_argument("--hook", metavar="NAME")
    parser.add_argument("--requirement", choices=["required", "advisory"])
    parser.add_argument("--timeout", type=int, metavar="SECONDS")
    parser.add_argument("--working-directory", metavar="PATH")
    parser.add_argument("--expected-exit-code", type=int)
    for stream in ("stdout", "stderr"):
        parser.add_argument(f"--{stream}-equals")
        parser.add_argument(f"--{stream}-contains")
        parser.add_argument(f"--{stream}-regex")
    parser.add_argument("--env", dest="environment", action="append", metavar="NAME")
    parser.add_argument("--arguments", metavar="JSON")
    parser.add_argument("--expected-result", metavar="JSON")


def _add_list_filters(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--status")
    parser.add_argument("--open", action="store_true", help="exclude Accepted and Done")
    parser.add_argument("--assignee")
    parser.add_argument("--reviewer")

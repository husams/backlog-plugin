from __future__ import annotations


_TASK_CREATE_COMMANDS = {
    ("task", "add"),
    ("feature", "add"),
    ("story", "add"),
    ("bug", "add"),
    ("subtask", "add"),
    ("iteration", "add"),
}


def attributed_cli_args(args) -> list[str]:
    """Give ordinary fixtures explicit creator/reviewer identities."""
    command = list(args)
    if "--actor" in command:
        return command
    pairs = set(zip(command, command[1:]))
    if pairs & _TASK_CREATE_COMMANDS:
        return [*command, "--actor", "fixture-creator"]
    if "action" in command and "refinement.accepted" in command:
        return [*command, "--actor", "fixture-reviewer"]
    return command

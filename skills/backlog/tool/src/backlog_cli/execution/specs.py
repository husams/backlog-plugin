"""Parsing and decorators for executable-item specifications."""

from typing import Any, Callable, Mapping

from ..db import BacklogError
from .contracts import (
    ExecutionSpec,
    Executor,
    HookSpec,
    Requirement,
    ShellSpec,
    TextMatcher,
)


def validation_hook(*, version: str | None = None):
    """Optionally attach an explicit fallback version to a hook callable."""
    if version is not None and (not isinstance(version, str) or not version.strip()):
        raise BacklogError("validation hook version must be a non-empty string")

    def decorate(callback: Callable):
        if not callable(callback):
            raise BacklogError("validation hook registration must be callable")
        if version is not None:
            setattr(callback, "__backlog_validation_version__", version.strip())
        return callback

    return decorate


def parse_spec(value: Mapping[str, Any]) -> ExecutionSpec:
    """Validate a JSON-like stored specification into the stable typed form."""
    if not isinstance(value, Mapping):
        raise BacklogError("execution specification must be an object")
    unknown = set(value) - {"executor", "requirement", "shell", "hook"}
    if unknown:
        raise BacklogError(
            "unknown execution specification fields: " + ", ".join(sorted(unknown))
        )
    try:
        executor = Executor(value.get("executor"))
        requirement = Requirement(value.get("requirement", "required"))
    except ValueError as exc:
        raise BacklogError(str(exc)) from exc
    shell_data, hook_data = value.get("shell"), value.get("hook")
    shell = _parse_shell(shell_data) if shell_data is not None else None
    hook = _parse_hook(hook_data) if hook_data is not None else None
    return ExecutionSpec(executor, requirement, shell, hook)


def _parse_shell(value: Any) -> ShellSpec:
    if not isinstance(value, Mapping):
        raise BacklogError("shell specification must be an object")
    data = dict(value)
    environment = data.get("environment", [])
    if not isinstance(environment, (list, tuple)):
        raise BacklogError(
            "shell environment must be a list of trusted-local variable names"
        )
    data["environment"] = tuple(environment)
    stdout = _matcher(data.pop("stdout", None))
    stderr = _matcher(data.pop("stderr", None))
    try:
        return ShellSpec(stdout=stdout, stderr=stderr, **data)
    except TypeError as exc:
        raise BacklogError(f"invalid shell specification: {exc}") from exc


def _parse_hook(value: Any) -> HookSpec:
    if not isinstance(value, Mapping):
        raise BacklogError("hook specification must be an object")
    try:
        return HookSpec(**dict(value))
    except TypeError as exc:
        raise BacklogError(f"invalid hook specification: {exc}") from exc


def _matcher(value: Any) -> TextMatcher | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise BacklogError("text matcher must be an object")
    try:
        return TextMatcher(**dict(value))
    except TypeError as exc:
        raise BacklogError(f"invalid text matcher: {exc}") from exc

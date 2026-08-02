"""Typed executable-item contracts and input validation."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import PurePosixPath
from typing import Any, Callable, Mapping

from ..db import BacklogError


class Executor(str, Enum):
    SHELL = "shell"
    HOOK = "hook"


class Requirement(str, Enum):
    REQUIRED = "required"
    ADVISORY = "advisory"


class TerminalStatus(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    ERROR = "error"
    SKIPPED = "skipped"


@dataclass(frozen=True)
class TextMatcher:
    equals: str | None = None
    contains: str | None = None
    regex: str | None = None

    def __post_init__(self) -> None:
        selected = [v is not None for v in (self.equals, self.contains, self.regex)]
        if sum(selected) != 1:
            raise BacklogError("a text matcher requires exactly one of equals, contains, or regex")
        if self.regex is not None:
            try:
                re.compile(self.regex)
            except re.error as exc:
                raise BacklogError(f"invalid matcher regex: {exc}") from exc


@dataclass(frozen=True)
class ShellSpec:
    command: str
    timeout_seconds: int = 60
    working_directory: str = "."
    expected_exit_code: int = 0
    output_limit_bytes: int | None = None
    stdout: TextMatcher | None = None
    stderr: TextMatcher | None = None
    environment: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.command, str) or not self.command.strip():
            raise BacklogError("shell command must be a non-empty string")
        _positive_timeout(self.timeout_seconds)
        if self.output_limit_bytes is not None:
            _positive_limit(self.output_limit_bytes, "output_limit_bytes")
        _relative_project_path(self.working_directory)
        if (
            not isinstance(self.environment, tuple)
            or not all(isinstance(name, str) and name for name in self.environment)
            or len(set(self.environment)) != len(self.environment)
        ):
            raise BacklogError(
                "shell environment must contain unique non-empty variable names"
            )


@dataclass(frozen=True)
class HookSpec:
    name: str
    arguments: Any = field(default_factory=dict)
    timeout_seconds: int = 60
    expected_result: Any = None

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]*", self.name or ""):
            raise BacklogError("hook name must be stable and contain only letters, digits, ., _, or -")
        _positive_timeout(self.timeout_seconds)
        _json_value(self.arguments, "hook arguments")
        _json_value(self.expected_result, "hook expected result")


@dataclass(frozen=True)
class ExecutionSpec:
    executor: Executor
    requirement: Requirement = Requirement.REQUIRED
    shell: ShellSpec | None = None
    hook: HookSpec | None = None

    def __post_init__(self) -> None:
        if (self.shell is not None) + (self.hook is not None) != 1:
            raise BacklogError("an executable item requires exactly one of shell or hook")
        if self.executor == Executor.SHELL and self.shell is None:
            raise BacklogError("executor=shell requires a shell specification")
        if self.executor == Executor.HOOK and self.hook is None:
            raise BacklogError("executor=hook requires a hook specification")

    def canonical(self) -> dict[str, Any]:
        value = asdict(self)
        value["executor"] = self.executor.value
        value["requirement"] = self.requirement.value
        return value

    @property
    def fingerprint(self) -> str:
        payload = json.dumps(
            self.canonical(), sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode()
        return "sha256:" + hashlib.sha256(payload).hexdigest()


@dataclass(frozen=True)
class ExecutionPolicy:
    shell_enabled: bool = False
    allowed_working_directories: tuple[str, ...] = (".",)
    allowed_environment_variables: tuple[str, ...] = ()
    allowed_commands: tuple[str, ...] = ()
    max_timeout_seconds: int = 300
    max_output_bytes: int = 1_000_000
    max_batch_seconds: int = 900
    allowed_hooks: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _positive_timeout(self.max_timeout_seconds)
        _positive_timeout(self.max_batch_seconds)
        if self.max_output_bytes <= 0:
            raise BacklogError("max_output_bytes must be positive")
        for path in self.allowed_working_directories:
            _relative_project_path(path)

    def denial_reason(self, spec: ExecutionSpec) -> str | None:
        if spec.shell:
            if not self.shell_enabled:
                return "shell_disabled"
            if spec.shell.timeout_seconds > self.max_timeout_seconds:
                return "timeout_exceeds_policy"
            if (
                spec.shell.output_limit_bytes is not None
                and spec.shell.output_limit_bytes > self.max_output_bytes
            ):
                return "output_limit_exceeds_policy"
            if self.allowed_commands:
                try:
                    command = shlex.split(
                        spec.shell.command, posix=os.name != "nt"
                    )[0]
                except (ValueError, IndexError):
                    return "command_denied"
                if command not in self.allowed_commands:
                    return "command_denied"
            if not _path_allowed(spec.shell.working_directory, self.allowed_working_directories):
                return "working_directory_denied"
            denied = sorted(set(spec.shell.environment) - set(self.allowed_environment_variables))
            if denied:
                return "environment_variable_denied:" + ",".join(denied)
        if spec.hook:
            if spec.hook.name not in self.allowed_hooks:
                return "hook_not_allowed"
            if spec.hook.timeout_seconds > self.max_timeout_seconds:
                return "timeout_exceeds_policy"
        return None


@dataclass(frozen=True)
class SourceIdentity:
    revision: str | None = None
    dirty_fingerprint: str | None = None
    unavailable: bool = False


@dataclass(frozen=True)
class ValidationContext:
    """Immutable identity supplied to a trusted local validation hook."""

    task_key: str
    task_id: int
    item_id: int
    item_kind: str
    item_content: str
    actor: str
    source: SourceIdentity


@dataclass(frozen=True)
class ValidationHookResult:
    """Typed value returned by a validation hook."""

    value: Any
    detail: str = ""

    def __post_init__(self) -> None:
        _json_value(self.value, "validation hook result")
        if not isinstance(self.detail, str):
            raise BacklogError("validation hook result detail must be a string")


@dataclass(frozen=True)
class ValidationExecutionResult:
    """Normalized terminal result returned by the hook runner."""

    status: TerminalStatus
    reason: str
    detail: str
    expected: Any
    actual: Any
    hook_name: str
    implementation_identity: str | None
    record: Mapping[str, Any]


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
        raise BacklogError("unknown execution specification fields: " + ", ".join(sorted(unknown)))
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


def _json_value(value: Any, label: str) -> None:
    try:
        json.dumps(value, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise BacklogError(f"{label} must be JSON-like: {exc}") from exc


def _positive_timeout(value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise BacklogError("timeout_seconds must be a positive integer")


def _positive_limit(value: int, label: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise BacklogError(f"{label} must be a positive integer")


def _relative_project_path(value: str) -> None:
    if not isinstance(value, str) or not value:
        raise BacklogError("working directory must be a project-relative path")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts:
        raise BacklogError("working directory must stay within the project")


def _path_allowed(value: str, allowed: tuple[str, ...]) -> bool:
    path = PurePosixPath(value)
    for base in allowed:
        base_path = PurePosixPath(base)
        if path == base_path or base_path in path.parents:
            return True
    return False


def _strings(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise BacklogError("policy allowlists must be lists of strings")
    return tuple(value)


def _bool(value: Any, label: str) -> bool:
    if not isinstance(value, bool):
        raise BacklogError(f"{label} must be true or false")
    return value

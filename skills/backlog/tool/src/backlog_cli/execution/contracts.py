"""Typed executable-item contracts and input validation."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import PurePosixPath
from typing import Any, Mapping

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
            raise BacklogError(
                "a text matcher requires exactly one of equals, contains, or regex"
            )
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
            raise BacklogError(
                "hook name must be stable and contain only letters, digits, ., _, or -"
            )
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
            raise BacklogError(
                "an executable item requires exactly one of shell or hook"
            )
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

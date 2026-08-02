"""Trusted local execution policy and source identity."""

from __future__ import annotations

import hashlib
import os
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path
from pathlib import PurePosixPath
from typing import Any

import yaml

from ..db import BacklogError
from .contracts import (
    ExecutionSpec,
    SourceIdentity,
    _positive_timeout,
    _relative_project_path,
)


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
                    command = shlex.split(spec.shell.command, posix=os.name != "nt")[0]
                except (ValueError, IndexError):
                    return "command_denied"
                if command not in self.allowed_commands:
                    return "command_denied"
            if not _path_allowed(
                spec.shell.working_directory, self.allowed_working_directories
            ):
                return "working_directory_denied"
            denied = sorted(
                set(spec.shell.environment) - set(self.allowed_environment_variables)
            )
            if denied:
                return "environment_variable_denied:" + ",".join(denied)
        if spec.hook:
            if spec.hook.name not in self.allowed_hooks:
                return "hook_not_allowed"
            if spec.hook.timeout_seconds > self.max_timeout_seconds:
                return "timeout_exceeds_policy"
        return None


def load_policy(project_root: Path) -> ExecutionPolicy:
    """Load policy only from the executing checkout; never from the store."""
    root = project_root.resolve()
    path = root / ".backlog" / "execution.yaml"
    legacy_path = root / ".backlog" / "execution-policy.yaml"
    if not path.is_file() and legacy_path.is_file():
        path = legacy_path
    if not path.is_file():
        return ExecutionPolicy()
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise BacklogError(f"{path} must contain a mapping")
    unknown = set(raw) - {
        "shell_enabled",
        "allowed_working_directories",
        "allowed_environment_variables",
        "allowed_commands",
        "max_timeout_seconds",
        "max_output_bytes",
        "allowed_hooks",
        "max_batch_seconds",
    }
    if unknown:
        raise BacklogError(
            "unknown execution policy fields: " + ", ".join(sorted(unknown))
        )
    return ExecutionPolicy(
        shell_enabled=_bool(raw.get("shell_enabled", False), "shell_enabled"),
        allowed_working_directories=_strings(
            raw.get("allowed_working_directories", ["."])
        ),
        allowed_environment_variables=_strings(
            raw.get("allowed_environment_variables", [])
        ),
        allowed_commands=_strings(raw.get("allowed_commands", [])),
        max_timeout_seconds=int(raw.get("max_timeout_seconds", 300)),
        max_output_bytes=int(raw.get("max_output_bytes", 1_000_000)),
        max_batch_seconds=int(raw.get("max_batch_seconds", 900)),
        allowed_hooks=_strings(raw.get("allowed_hooks", [])),
    )


def source_identity(project_root: Path) -> SourceIdentity:
    """Identify a checkout without making non-VCS projects ineligible."""
    root = project_root.resolve()
    revision = _git(root, "rev-parse", "--verify", "HEAD")
    if revision is None:
        return SourceIdentity(unavailable=True)
    dirty = _git(root, "status", "--porcelain=v1", "-z", "--untracked-files=all")
    if not dirty:
        return SourceIdentity(revision=revision)
    names = _git_bytes(root, "ls-files", "-co", "--exclude-standard", "-z")
    if names is None:
        return SourceIdentity(revision=revision, unavailable=True)
    digest = hashlib.sha256()
    for raw_name in sorted(filter(None, names.split(b"\0"))):
        rel = raw_name.decode("utf-8", "surrogateescape")
        path = root / rel
        if not path.is_file():
            continue
        digest.update(rel.encode("utf-8", "surrogateescape"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return SourceIdentity(
        revision=revision, dirty_fingerprint="sha256:" + digest.hexdigest()
    )


def _git(root: Path, *args: str) -> str | None:
    raw = _git_bytes(root, *args)
    return raw.decode().strip() if raw is not None else None


def _git_bytes(root: Path, *args: str) -> bytes | None:
    try:
        result = subprocess.run(
            ["git", "-C", os.fspath(root), *args], capture_output=True, check=False
        )
    except OSError:
        return None
    return result.stdout if result.returncode == 0 else None


def _path_allowed(value: str, allowed: tuple[str, ...]) -> bool:
    path = PurePosixPath(value)
    return any(
        path == PurePosixPath(base) or PurePosixPath(base) in path.parents
        for base in allowed
    )


def _strings(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise BacklogError("policy allowlists must be lists of strings")
    return tuple(value)


def _bool(value: Any, label: str) -> bool:
    if not isinstance(value, bool):
        raise BacklogError(f"{label} must be true or false")
    return value

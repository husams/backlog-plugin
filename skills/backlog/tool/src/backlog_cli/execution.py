"""Typed executable-item contracts and trusted local execution policy.

This module deliberately does not execute anything. Runners use these stable
types to validate stored specifications, load machine-local policy, fingerprint
freshness, and record comparable terminal outcomes.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

import yaml

from .db import BacklogError, Conn, utcnow


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
    stdout: TextMatcher | None = None
    stderr: TextMatcher | None = None
    environment: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.command, str) or not self.command.strip():
            raise BacklogError("shell command must be a non-empty string")
        _positive_timeout(self.timeout_seconds)
        _relative_project_path(self.working_directory)
        _json_value(dict(self.environment), "shell environment")
        for key, value in self.environment.items():
            if not isinstance(key, str) or not isinstance(value, str):
                raise BacklogError("shell environment names and values must be strings")


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
    max_timeout_seconds: int = 300
    max_output_bytes: int = 1_000_000
    allowed_hooks: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _positive_timeout(self.max_timeout_seconds)
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


def load_policy(project_root: Path) -> ExecutionPolicy:
    """Load policy only from the executing checkout; never from the store."""
    root = project_root.resolve()
    path = root / ".backlog" / "execution-policy.yaml"
    if not path.is_file():
        return ExecutionPolicy()
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise BacklogError(f"{path} must contain a mapping")
    unknown = set(raw) - {
        "shell_enabled", "allowed_working_directories",
        "allowed_environment_variables", "max_timeout_seconds",
        "max_output_bytes", "allowed_hooks",
    }
    if unknown:
        raise BacklogError("unknown execution policy fields: " + ", ".join(sorted(unknown)))
    return ExecutionPolicy(
        shell_enabled=_bool(raw.get("shell_enabled", False), "shell_enabled"),
        allowed_working_directories=_strings(raw.get("allowed_working_directories", ["."])),
        allowed_environment_variables=_strings(raw.get("allowed_environment_variables", [])),
        max_timeout_seconds=int(raw.get("max_timeout_seconds", 300)),
        max_output_bytes=int(raw.get("max_output_bytes", 1_000_000)),
        allowed_hooks=_strings(raw.get("allowed_hooks", [])),
    )


def set_executable(conn: Conn, item_id: int, value: Mapping[str, Any]) -> dict[str, Any]:
    spec = parse_spec(value)
    item = conn.execute("SELECT id, kind FROM task_item WHERE id = ?", (item_id,)).fetchone()
    if item is None:
        raise BacklogError(f"no task item with id {item_id}")
    if item["kind"] not in ("acceptance_criteria", "checklist"):
        raise BacklogError(
            "only acceptance criteria and checklist items may declare execution; "
            f"item {item_id} is {item['kind']}"
        )
    now = utcnow()
    encoded = json.dumps(spec.canonical(), sort_keys=True, separators=(",", ":"))
    conn.execute(
        "INSERT INTO executable_item(item_id, executor, requirement, execution_spec, "
        "spec_fingerprint, created_at, updated_at) VALUES(?,?,?,?,?,?,?) "
        "ON CONFLICT(item_id) DO UPDATE SET executor=excluded.executor, "
        "requirement=excluded.requirement, execution_spec=excluded.execution_spec, "
        "spec_fingerprint=excluded.spec_fingerprint, updated_at=excluded.updated_at",
        (item_id, spec.executor.value, spec.requirement.value, encoded,
         spec.fingerprint, now, now),
    )
    conn.commit()
    return executable_item(conn, item_id)


def executable_item(conn: Conn, item_id: int) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM executable_item WHERE item_id = ?", (item_id,)).fetchone()
    if row is None:
        raise BacklogError(f"task item {item_id} has no execution specification")
    result = {key: row[key] for key in row.keys()}
    result["execution_spec"] = json.loads(result["execution_spec"])
    return result


def public_item(conn: Conn, item: Mapping[str, Any]) -> dict[str, Any]:
    """Return an item suitable for CLI/API inspection without secret values."""
    result = {key: item[key] for key in item.keys()}
    row = conn.execute(
        "SELECT executor, requirement, execution_spec, spec_fingerprint "
        "FROM executable_item WHERE item_id = ?",
        (item["id"],),
    ).fetchone()
    if row is None:
        result.update({"executor": "plain", "requirement": None, "state": None})
        return result
    spec = _public_spec(json.loads(row["execution_spec"]))
    result.update(
        {
            "executor": row["executor"],
            "requirement": row["requirement"],
            "state": item_state(conn, int(item["id"])),
            "execution_spec": spec,
            "spec_fingerprint": row["spec_fingerprint"],
        }
    )
    return result


def public_executable(conn: Conn, item_id: int) -> dict[str, Any]:
    """Backward-compatible executable row with secret-bearing values redacted."""
    result = executable_item(conn, item_id)
    result["execution_spec"] = _public_spec(result["execution_spec"])
    result["state"] = item_state(conn, item_id)
    return result


def _public_spec(spec: dict[str, Any]) -> dict[str, Any]:
    spec = dict(spec)
    shell = spec.get("shell")
    if shell:
        shell = dict(shell)
        shell["command"] = "<hidden>"
        if shell.get("environment"):
            shell["environment"] = sorted(shell["environment"])
        for stream in ("stdout", "stderr"):
            matcher = shell.get(stream)
            if matcher:
                shell[stream] = {next(iter(matcher)): "<hidden>"}
        spec = {**spec, "shell": shell}
    hook = spec.get("hook")
    if hook:
        hook = dict(hook)
        hook["arguments"] = "<hidden>"
        hook["expected_result"] = "<hidden>"
        spec = {**spec, "hook": hook}
    return spec


def record_result(
    conn: Conn, item_id: int, spec_fingerprint: str, status: TerminalStatus | str,
    *, reason: str = "", detail: str = "", source: SourceIdentity | None = None,
    started_at: str | None = None, finished_at: str | None = None,
) -> dict[str, Any]:
    try:
        terminal = TerminalStatus(status)
    except ValueError as exc:
        raise BacklogError("execution result must be pass, fail, error, or skipped") from exc
    if terminal == TerminalStatus.SKIPPED and reason != "policy_denied":
        raise BacklogError("skipped execution results require reason=policy_denied")
    source = source or SourceIdentity(unavailable=True)
    now = utcnow()
    rid = conn.insert_returning_id(
        "INSERT INTO execution_result(item_id,spec_fingerprint,status,reason,detail,"
        "source_revision,source_dirty_fingerprint,source_revision_unavailable,"
        "started_at,finished_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
        (item_id, spec_fingerprint, terminal.value, reason, detail, source.revision,
         source.dirty_fingerprint, 1 if source.unavailable else 0,
         started_at or now, finished_at or now),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM execution_result WHERE id = ?", (rid,)).fetchone()
    return {key: row[key] for key in row.keys()}


def item_state(conn: Conn, item_id: int) -> str:
    """Return pending or the latest fresh terminal status."""
    executable = executable_item(conn, item_id)
    row = conn.execute(
        "SELECT status FROM execution_result WHERE item_id = ? AND spec_fingerprint = ? "
        "ORDER BY id DESC LIMIT 1", (item_id, executable["spec_fingerprint"]),
    ).fetchone()
    return row["status"] if row else "pending"


def required_validations_pass(conn: Conn, task_id: int) -> tuple[bool, list[int]]:
    rows = conn.execute(
        "SELECT e.item_id, e.spec_fingerprint FROM executable_item e "
        "JOIN task_item i ON i.id=e.item_id "
        "WHERE i.task_id=? AND e.requirement='required' ORDER BY e.item_id", (task_id,),
    ).fetchall()
    failed: list[int] = []
    for row in rows:
        latest = conn.execute(
            "SELECT status FROM execution_result WHERE item_id=? AND spec_fingerprint=? "
            "ORDER BY id DESC LIMIT 1", (row["item_id"], row["spec_fingerprint"]),
        ).fetchone()
        if latest is None or latest["status"] != TerminalStatus.PASS.value:
            failed.append(int(row["item_id"]))
    return not failed, failed


def source_revision_unavailable_items(conn: Conn) -> list[int]:
    """Items whose latest fresh attempt still lacks source identity."""
    rows = conn.execute(
        "SELECT e.item_id FROM executable_item e "
        "JOIN execution_result r ON r.id = ("
        "  SELECT r2.id FROM execution_result r2 "
        "  WHERE r2.item_id=e.item_id AND r2.spec_fingerprint=e.spec_fingerprint "
        "  ORDER BY r2.id DESC LIMIT 1"
        ") WHERE r.source_revision_unavailable=1 ORDER BY e.item_id"
    ).fetchall()
    return [int(row["item_id"]) for row in rows]


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
    return SourceIdentity(revision=revision, dirty_fingerprint="sha256:" + digest.hexdigest())


def _parse_shell(value: Any) -> ShellSpec:
    if not isinstance(value, Mapping):
        raise BacklogError("shell specification must be an object")
    data = dict(value)
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

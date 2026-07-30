"""Typed executable-item contracts and trusted local execution policy.

This module deliberately does not execute anything. Runners use these stable
types to validate stored specifications, load machine-local policy, fingerprint
freshness, and record comparable terminal outcomes.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import os
import re
import signal
import subprocess
import threading
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Mapping

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
    expected: Any = None, actual: Any = None, hook_name: str | None = None,
    implementation_identity: str | None = None,
) -> dict[str, Any]:
    try:
        terminal = TerminalStatus(status)
    except ValueError as exc:
        raise BacklogError("execution result must be pass, fail, error, or skipped") from exc
    if terminal == TerminalStatus.SKIPPED and reason != "policy_denied":
        raise BacklogError("skipped execution results require reason=policy_denied")
    source = source or SourceIdentity(unavailable=True)
    if hook_name is not None and not isinstance(hook_name, str):
        raise BacklogError("hook_name must be a string")
    if implementation_identity is not None and not isinstance(implementation_identity, str):
        raise BacklogError("implementation_identity must be a string")
    _json_value(expected, "expected result")
    _json_value(actual, "actual result")
    now = utcnow()
    rid = conn.insert_returning_id(
        "INSERT INTO execution_result(item_id,spec_fingerprint,status,reason,detail,"
        "expected_result,actual_result,hook_name,implementation_identity,"
        "source_revision,source_dirty_fingerprint,source_revision_unavailable,"
        "started_at,finished_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (item_id, spec_fingerprint, terminal.value, reason, detail,
         json.dumps(expected, sort_keys=True, separators=(",", ":"), ensure_ascii=False),
         json.dumps(actual, sort_keys=True, separators=(",", ":"), ensure_ascii=False),
         hook_name, implementation_identity, source.revision,
         source.dirty_fingerprint, 1 if source.unavailable else 0,
         started_at or now, finished_at or now),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM execution_result WHERE id = ?", (rid,)).fetchone()
    return {key: row[key] for key in row.keys()}


def run_hook_validation(
    backlog, item_id: int, *, actor: str, project_root: Path,
) -> ValidationExecutionResult:
    """Resolve and execute one allowlisted project validation hook."""
    from . import hooks

    root = project_root.resolve()
    executable = executable_item(backlog._conn, item_id)
    spec = parse_spec(executable["execution_spec"])
    if spec.executor != Executor.HOOK or spec.hook is None:
        raise BacklogError(f"task item {item_id} is not a hook executable")
    hook_spec = spec.hook
    policy = load_policy(root)
    denial = policy.denial_reason(spec)
    if denial is not None:
        return _hook_terminal(
            backlog, executable, hook_spec, actor, root, TerminalStatus.SKIPPED,
            "policy_denied", denial, None, None, emit_failed=False,
        )

    task_item = backlog._conn.execute(
        "SELECT i.id, i.kind, i.content, t.id AS task_id, t.key AS task_key "
        "FROM task_item i JOIN task t ON t.id=i.task_id WHERE i.id=? AND t.project_id=?",
        (item_id, backlog.pid),
    ).fetchone()
    if task_item is None:
        raise BacklogError(f"no task item with id {item_id} in this project")

    backlog_dir = root / ".backlog"
    try:
        module = hooks.load_project_hooks(backlog_dir)
    except BacklogError:
        return _hook_terminal(
            backlog, executable, hook_spec, actor, root, TerminalStatus.ERROR,
            "hooks_package_invalid", "trusted hooks package could not be loaded",
            None, None,
        )
    if module is None:
        return _hook_terminal(
            backlog, executable, hook_spec, actor, root, TerminalStatus.ERROR,
            "hooks_package_missing", "trusted hooks package is absent", None, None,
        )
    registrations = getattr(module, "validation_hooks", None)
    if not isinstance(registrations, Mapping):
        return _hook_terminal(
            backlog, executable, hook_spec, actor, root, TerminalStatus.ERROR,
            "validation_hooks_missing", "validation_hooks must be a mapping", None, None,
        )
    if hook_spec.name not in registrations:
        return _hook_terminal(
            backlog, executable, hook_spec, actor, root, TerminalStatus.ERROR,
            "hook_unknown", "registered hook name was not found", None, None,
        )
    callback = registrations[hook_spec.name]
    if not callable(callback):
        return _hook_terminal(
            backlog, executable, hook_spec, actor, root, TerminalStatus.ERROR,
            "hook_not_callable", "registered hook entry is not callable", None, None,
        )
    try:
        identity = hook_implementation_identity(callback)
    except BacklogError:
        return _hook_terminal(
            backlog, executable, hook_spec, actor, root, TerminalStatus.ERROR,
            "hook_identity_unavailable", "deterministic hook identity is unavailable",
            None, None,
        )
    timeout_constraint = _timeout_constraint()
    if timeout_constraint is not None:
        return _hook_terminal(
            backlog, executable, hook_spec, actor, root, TerminalStatus.ERROR,
            "hook_timeout_unavailable", timeout_constraint, None, identity,
        )

    source = source_identity(root)
    context = ValidationContext(
        task_key=task_item["task_key"],
        task_id=int(task_item["task_id"]),
        item_id=int(task_item["id"]),
        item_kind=task_item["kind"],
        item_content=task_item["content"],
        actor=actor,
        source=source,
    )
    backlog.trigger(
        task_item["task_key"], hooks.Action.CHECK_STARTED, actor=actor,
        operation="validation.hook",
        parameters={"item_id": item_id, "hook": hook_spec.name, "identity": identity},
    )
    try:
        with _deadline(hook_spec.timeout_seconds):
            returned = callback(backlog, context, hook_spec.arguments)
        if not isinstance(returned, ValidationHookResult):
            raise BacklogError("hook must return ValidationHookResult")
        actual = returned.value
        status = (TerminalStatus.PASS if actual == hook_spec.expected_result
                  else TerminalStatus.FAIL)
        reason = "" if status == TerminalStatus.PASS else "result_mismatch"
        action = (hooks.Action.CHECK_PASSED if status == TerminalStatus.PASS
                  else hooks.Action.CHECK_FAILED)
        backlog.trigger(
            task_item["task_key"], action, actor=actor, operation="validation.hook",
            parameters={"item_id": item_id, "hook": hook_spec.name, "identity": identity},
        )
        return _hook_terminal(
            backlog, executable, hook_spec, actor, root, status, reason,
            returned.detail, actual, identity, emit_failed=False, source=source,
        )
    except _HookTimeout:
        backlog.trigger(
            task_item["task_key"], hooks.Action.CHECK_TIMED_OUT, actor=actor,
            operation="validation.hook",
            parameters={"item_id": item_id, "hook": hook_spec.name, "reason": "hook_timeout"},
        )
        return _hook_terminal(
            backlog, executable, hook_spec, actor, root, TerminalStatus.ERROR,
            "hook_timeout", "validation hook exceeded its timeout", None, identity,
            emit_failed=False, source=source,
        )
    except Exception:
        backlog.trigger(
            task_item["task_key"], hooks.Action.CHECK_FAILED, actor=actor,
            operation="validation.hook",
            parameters={"item_id": item_id, "hook": hook_spec.name,
                        "reason": "hook_exception"},
        )
        return _hook_terminal(
            backlog, executable, hook_spec, actor, root, TerminalStatus.ERROR,
            "hook_exception", "validation hook raised an exception", None, identity,
            emit_failed=False, source=source,
        )


def hook_implementation_identity(callback: Callable) -> str:
    """Return the canonical source digest, or the explicit version fallback."""
    unwrapped = inspect.unwrap(callback)
    try:
        source = inspect.getsource(unwrapped)
    except (OSError, TypeError):
        version = getattr(callback, "__backlog_validation_version__", None)
        if isinstance(version, str) and version.strip():
            return "version:" + version.strip()
        raise BacklogError("hook_identity_unavailable") from None
    normalized = source.replace("\r\n", "\n").replace("\r", "\n")
    normalized = "\n".join(line.rstrip(" \t") for line in normalized.split("\n"))
    normalized = normalized.rstrip("\n") + "\n"
    return "source_sha256:" + hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _hook_terminal(
    backlog, executable: Mapping[str, Any], hook_spec: HookSpec, actor: str,
    root: Path, status: TerminalStatus, reason: str, detail: str, actual: Any,
    identity: str | None, *, emit_failed: bool = True,
    source: SourceIdentity | None = None,
) -> ValidationExecutionResult:
    from .hooks import Action

    item_id = int(executable["item_id"])
    if emit_failed:
        row = backlog._conn.execute(
            "SELECT t.key FROM task_item i JOIN task t ON t.id=i.task_id "
            "WHERE i.id=? AND t.project_id=?", (item_id, backlog.pid),
        ).fetchone()
        if row is not None:
            backlog.trigger(
                row["key"], Action.CHECK_FAILED, actor=actor,
                operation="validation.hook",
                parameters={"item_id": item_id, "hook": hook_spec.name, "reason": reason},
            )
    record = record_result(
        backlog._conn, item_id, executable["spec_fingerprint"], status,
        reason=reason, detail=detail, source=source or source_identity(root),
        expected=hook_spec.expected_result, actual=actual, hook_name=hook_spec.name,
        implementation_identity=identity,
    )
    return ValidationExecutionResult(
        status, reason, detail, hook_spec.expected_result, actual,
        hook_spec.name, identity, record,
    )


class _HookTimeout(Exception):
    pass


@contextmanager
def _deadline(seconds: int):
    constraint = _timeout_constraint()
    if constraint is not None:
        raise BacklogError(constraint)
    previous = signal.getsignal(signal.SIGALRM)

    def expired(_signum, _frame):
        raise _HookTimeout()

    signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


def _timeout_constraint() -> str | None:
    """Return a stable pre-invocation reason when in-process timeout is unsafe."""
    if not all(hasattr(signal, name) for name in ("SIGALRM", "ITIMER_REAL", "setitimer")):
        return "sigalrm_unavailable"
    if threading.current_thread() is not threading.main_thread():
        return "main_thread_required"
    return None


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

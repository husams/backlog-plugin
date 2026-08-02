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
import shlex
import shutil
import time
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
        "allowed_environment_variables", "allowed_commands", "max_timeout_seconds",
        "max_output_bytes", "allowed_hooks",
        "max_batch_seconds",
    }
    if unknown:
        raise BacklogError("unknown execution policy fields: " + ", ".join(sorted(unknown)))
    return ExecutionPolicy(
        shell_enabled=_bool(raw.get("shell_enabled", False), "shell_enabled"),
        allowed_working_directories=_strings(raw.get("allowed_working_directories", ["."])),
        allowed_environment_variables=_strings(raw.get("allowed_environment_variables", [])),
        allowed_commands=_strings(raw.get("allowed_commands", [])),
        max_timeout_seconds=int(raw.get("max_timeout_seconds", 300)),
        max_output_bytes=int(raw.get("max_output_bytes", 1_000_000)),
        max_batch_seconds=int(raw.get("max_batch_seconds", 900)),
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


def _item_details(conn: Conn, item: Mapping[str, Any]) -> dict[str, Any]:
    """Return a task item with its execution declaration and current state."""
    result = {key: item[key] for key in item.keys()}
    row = conn.execute(
        "SELECT executor, requirement, execution_spec, spec_fingerprint "
        "FROM executable_item WHERE item_id = ?",
        (item["id"],),
    ).fetchone()
    if row is None:
        result.update({"executor": "plain", "requirement": None, "state": None})
        return result
    spec = json.loads(row["execution_spec"])
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


def record_result(
    conn: Conn, item_id: int, spec_fingerprint: str, status: TerminalStatus | str,
    *, reason: str = "", detail: str = "", source: SourceIdentity | None = None,
    actual_exit_code: int | None = None, stdout: str = "", stderr: str = "",
    duration_ms: int = 0,
    started_at: str | None = None, finished_at: str | None = None,
    expected: Any = None, actual: Any = None, hook_name: str | None = None,
    implementation_identity: str | None = None, actor: str | None = None,
) -> dict[str, Any]:
    try:
        terminal = TerminalStatus(status)
    except ValueError as exc:
        raise BacklogError("execution result must be pass, fail, error, or skipped") from exc
    if terminal == TerminalStatus.SKIPPED and reason not in {
        "policy_denied", "batch_budget_exhausted",
    }:
        raise BacklogError(
            "skipped execution results require reason=policy_denied "
            "or batch_budget_exhausted"
        )
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
        "actual_exit_code,stdout,stderr,duration_ms,"
        "source_revision,source_dirty_fingerprint,source_revision_unavailable,"
        "actor,started_at,finished_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (item_id, spec_fingerprint, terminal.value, reason, detail,
         json.dumps(expected, sort_keys=True, separators=(",", ":"), ensure_ascii=False),
         json.dumps(actual, sort_keys=True, separators=(",", ":"), ensure_ascii=False),
         hook_name, implementation_identity, actual_exit_code, stdout, stderr,
         duration_ms, source.revision,
         source.dirty_fingerprint, 1 if source.unavailable else 0,
         (actor or "unknown").strip() or "unknown", started_at or now, finished_at or now),
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
        actor=actor,
    )
    if status == TerminalStatus.PASS:
        _after_pass(backlog, item_id, actor)
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


@dataclass(frozen=True)
class ExecutionResult:
    item_id: int
    status: str
    executor: str
    expected: dict[str, Any]
    actual_exit_code: int | None
    stdout: str
    stderr: str
    duration_ms: int
    diagnostic: str
    output_truncated: bool = False

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def run_shell(
    backlog, item_id: int, project_root: Path, *,
    policy: ExecutionPolicy | None = None, actor: str | None = None,
) -> ExecutionResult:
    """Execute one shell item under trusted local policy and audit the attempt."""
    executable = executable_item(backlog._conn, item_id)
    spec = parse_spec(executable["execution_spec"])
    if spec.executor != Executor.SHELL or spec.shell is None:
        raise BacklogError(f"executable item {item_id} is not a shell executor")
    task = _task_for_item(backlog._conn, backlog.pid, item_id)
    root = Path(project_root).resolve()
    policy = policy or load_policy(root)
    denied = policy.denial_reason(spec)
    if denied:
        result = _record_shell_result(
            backlog, task["key"], item_id, spec, "skipped",
            reason="policy_denied", diagnostic=f"policy_denied:{denied}",
            source=SourceIdentity(unavailable=True),
            actor=actor,
        )
        return result
    return _invoke_shell(backlog, task["key"], item_id, spec, root, policy, actor)


def run_task_shells(
    backlog, key: str, project_root: Path, *, fail_fast: bool = False,
    policy: ExecutionPolicy | None = None, actor: str | None = None,
) -> list[ExecutionResult]:
    """Run shell items in declaration order with a separate local batch budget."""
    task = backlog.task(key)
    root = Path(project_root).resolve()
    policy = policy or load_policy(root)
    rows = backlog._conn.execute(
        "SELECT e.* FROM executable_item e JOIN task_item i ON i.id=e.item_id "
        "WHERE i.task_id=? AND e.executor='shell' "
        "ORDER BY i.kind,i.position,i.id", (task.id,),
    ).fetchall()
    started = time.monotonic()
    results: list[ExecutionResult] = []
    budget_exhausted = False
    for row in rows:
        spec = parse_spec(json.loads(row["execution_spec"]))
        assert spec.shell is not None
        remaining = policy.max_batch_seconds - (time.monotonic() - started)
        if budget_exhausted or remaining < spec.shell.timeout_seconds:
            budget_exhausted = True
            results.append(_record_shell_result(
                backlog, task.key, int(row["item_id"]), spec, "skipped",
                reason="batch_budget_exhausted",
                diagnostic="batch_budget_exhausted",
                source=SourceIdentity(unavailable=True),
            ))
            continue
        result = run_shell(
            backlog, int(row["item_id"]), root, policy=policy, actor=actor
        )
        results.append(result)
        if fail_fast and result.status in {"fail", "error"}:
            break
    return results


def run_validation(
    backlog, item_id: int, project_root: Path, *,
    policy: ExecutionPolicy | None = None, actor: str | None = None,
) -> ExecutionResult | ValidationExecutionResult:
    """Run either executor through one stable public operation."""
    executable = executable_item(backlog._conn, item_id)
    executor = Executor(executable["executor"])
    effective_actor = actor or backlog.actor or "unknown"
    if executor == Executor.HOOK:
        return run_hook_validation(
            backlog, item_id, actor=effective_actor, project_root=project_root
        )
    return run_shell(
        backlog, item_id, project_root, policy=policy, actor=effective_actor
    )


def run_task_validations(
    backlog, key: str, project_root: Path, *, fail_fast: bool = False,
    policy: ExecutionPolicy | None = None, actor: str | None = None,
) -> list[ExecutionResult | ValidationExecutionResult]:
    """Run all executable items in item declaration order."""
    task = backlog.task(key)
    rows = backlog._conn.execute(
        "SELECT e.* FROM executable_item e "
        "JOIN task_item i ON i.id=e.item_id WHERE i.task_id=? "
        "ORDER BY i.kind,i.position,i.id",
        (task.id,),
    ).fetchall()
    root = Path(project_root).resolve()
    policy = policy or load_policy(root)
    started = time.monotonic()
    results: list[ExecutionResult | ValidationExecutionResult] = []
    budget_exhausted = False
    for row in rows:
        spec = parse_spec(json.loads(row["execution_spec"]))
        timeout = (
            spec.shell.timeout_seconds if spec.shell is not None
            else spec.hook.timeout_seconds
        )
        remaining = policy.max_batch_seconds - (time.monotonic() - started)
        if budget_exhausted or remaining < timeout:
            budget_exhausted = True
            if spec.shell is not None:
                result = _record_shell_result(
                    backlog, task.key, int(row["item_id"]), spec, "skipped",
                    reason="batch_budget_exhausted",
                    diagnostic="batch_budget_exhausted",
                    source=SourceIdentity(unavailable=True),
                    actor=actor or backlog.actor,
                )
            else:
                assert spec.hook is not None
                result = _hook_terminal(
                    backlog, row, spec.hook, actor or backlog.actor or "unknown",
                    root, TerminalStatus.SKIPPED, "batch_budget_exhausted",
                    "batch_budget_exhausted", None, None, emit_failed=False,
                )
            results.append(result)
            continue
        result = run_validation(
            backlog, int(row["item_id"]), root,
            policy=policy, actor=actor,
        )
        results.append(result)
        if fail_fast and result.status in {
            TerminalStatus.FAIL, TerminalStatus.ERROR, "fail", "error"
        }:
            break
    return results


def _invoke_shell(backlog, task_key: str, item_id: int, spec: ExecutionSpec,
                  root: Path, policy: ExecutionPolicy,
                  actor: str | None) -> ExecutionResult:
    assert spec.shell is not None
    shell = spec.shell
    try:
        argv = shlex.split(shell.command, posix=os.name != "nt")
    except ValueError as exc:
        return _pre_invocation_error(
            backlog, task_key, item_id, spec, f"invalid_command:{exc}", root, actor
        )
    if not argv:
        return _pre_invocation_error(
            backlog, task_key, item_id, spec, "invalid_command:empty", root, actor
        )
    cwd = (root / shell.working_directory).resolve()
    try:
        cwd.relative_to(root)
    except ValueError:
        return _pre_invocation_error(
            backlog, task_key, item_id, spec, "working_directory_outside_project",
            root, actor,
        )
    if not cwd.is_dir():
        return _pre_invocation_error(
            backlog, task_key, item_id, spec, "working_directory_unavailable",
            root, actor,
        )
    missing = sorted(name for name in shell.environment if name not in os.environ)
    if missing:
        return _pre_invocation_error(
            backlog, task_key, item_id, spec,
            "environment_variable_unavailable:" + ",".join(missing), root, actor,
        )
    requested_environment = {
        name: os.environ[name] for name in shell.environment
    }
    env = {"PATH": os.defpath, **requested_environment}
    executable_path = shutil.which(argv[0], path=env["PATH"])
    if executable_path is None:
        return _pre_invocation_error(
            backlog, task_key, item_id, spec,
            f"command_unavailable:{argv[0]}", root, actor,
        )
    argv[0] = executable_path
    started_wall = _utc_timestamp()
    started = time.monotonic()
    try:
        process = subprocess.Popen(
            argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            start_new_session=(os.name == "posix"),
        )
    except OSError as exc:
        return _pre_invocation_error(
            backlog, task_key, item_id, spec,
            f"process_start_failed:{exc.__class__.__name__}", root, actor,
        )
    backlog.trigger(
        task_key, _action("check.started"), actor=actor,
        operation="validation.shell",
        parameters={"item_id": item_id},
    )
    stdout, stderr, truncated, timed_out, read_error = _communicate_bounded(
        process, shell.timeout_seconds,
        shell.output_limit_bytes or policy.max_output_bytes,
    )
    duration_ms = max(0, int((time.monotonic() - started) * 1000))
    if read_error:
        status, diagnostic = "error", f"runtime_infrastructure_failure:{read_error}"
    elif timed_out:
        status, diagnostic = "error", "timed_out"
    else:
        mismatches = _mismatches(shell, process.returncode, stdout, stderr)
        status = "fail" if mismatches else "pass"
        diagnostic = ";".join(mismatches)
    stdout = _redact(stdout, requested_environment.values())
    stderr = _redact(stderr, requested_environment.values())
    if truncated:
        diagnostic = ";".join(filter(None, (diagnostic, "output_truncated")))
    result = _record_shell_result(
        backlog, task_key, item_id, spec, status,
        diagnostic=diagnostic, actual_exit_code=process.returncode,
        stdout=stdout, stderr=stderr, duration_ms=duration_ms,
        source=source_identity(root), started_at=started_wall,
        actor=actor,
    )
    terminal = "check.timed_out" if timed_out else (
        "check.passed" if status == "pass" else "check.failed"
    )
    backlog.trigger(
        task_key, _action(terminal), actor=actor, operation="validation.shell",
        parameters={"item_id": item_id, "diagnostic": diagnostic},
    )
    return result


def _pre_invocation_error(backlog, task_key: str, item_id: int,
                          spec: ExecutionSpec, diagnostic: str,
                          root: Path, actor: str | None) -> ExecutionResult:
    result = _record_shell_result(
        backlog, task_key, item_id, spec, "error", diagnostic=diagnostic,
        source=source_identity(root), actor=actor,
    )
    backlog.trigger(
        task_key, _action("check.failed"), actor=actor,
        operation="validation.shell",
        parameters={"item_id": item_id, "diagnostic": diagnostic},
    )
    return result


def _record_shell_result(backlog, task_key: str, item_id: int,
                         spec: ExecutionSpec, status: str, *,
                         reason: str = "", diagnostic: str = "",
                         actual_exit_code: int | None = None,
                         stdout: str = "", stderr: str = "",
                         duration_ms: int = 0,
                         source: SourceIdentity | None = None,
                         started_at: str | None = None,
                         actor: str | None = None) -> ExecutionResult:
    executable = executable_item(backlog._conn, item_id)
    assert spec.shell is not None
    expected = {
        "exit_code": spec.shell.expected_exit_code,
        "stdout": asdict(spec.shell.stdout) if spec.shell.stdout else None,
        "stderr": asdict(spec.shell.stderr) if spec.shell.stderr else None,
    }
    actual = {
        "exit_code": actual_exit_code,
        "stdout": stdout,
        "stderr": stderr,
    }
    record_result(
        backlog._conn, item_id, executable["spec_fingerprint"], status,
        reason=reason, detail=diagnostic, source=source,
        actual_exit_code=actual_exit_code, stdout=stdout, stderr=stderr,
        duration_ms=duration_ms, started_at=started_at,
        actor=actor or backlog.actor,
        expected=expected, actual=actual,
    )
    if status == TerminalStatus.PASS.value:
        _after_pass(backlog, item_id, actor or backlog.actor or "unknown")
    return ExecutionResult(
        item_id, status, spec.executor.value, expected, actual_exit_code,
        stdout, stderr, duration_ms, diagnostic,
        output_truncated=diagnostic.endswith("output_truncated"),
    )


def _communicate_bounded(process: subprocess.Popen, timeout: int,
                         limit: int) -> tuple[str, str, bool, bool, str]:
    captured = [bytearray(), bytearray()]
    truncated = [False]
    read_errors: list[str] = []
    lock = threading.Lock()

    def drain(stream, index: int) -> None:
        try:
            while True:
                chunk = stream.read(65536)
                if not chunk:
                    break
                with lock:
                    remaining = max(0, limit - sum(len(value) for value in captured))
                    captured[index].extend(chunk[:remaining])
                    if len(chunk) > remaining:
                        truncated[0] = True
        except OSError as exc:
            read_errors.append(exc.__class__.__name__)

    threads = [
        threading.Thread(target=drain, args=(process.stdout, 0), daemon=True),
        threading.Thread(target=drain, args=(process.stderr, 1), daemon=True),
    ]
    for thread in threads:
        thread.start()
    timed_out = False
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGKILL)
        else:
            process.kill()
        process.wait()
    for thread in threads:
        thread.join()
    for stream in (process.stdout, process.stderr):
        if stream is not None:
            stream.close()
    stdout = captured[0].decode("utf-8", "replace")
    stderr = captured[1].decode("utf-8", "replace")
    diagnostic = ",".join(read_errors)
    return stdout, stderr, truncated[0], timed_out, diagnostic


def _mismatches(shell: ShellSpec, exit_code: int | None,
                stdout: str, stderr: str) -> list[str]:
    mismatches = []
    if exit_code != shell.expected_exit_code:
        mismatches.append(
            f"exit_code_mismatch:expected={shell.expected_exit_code},actual={exit_code}"
        )
    for label, matcher, actual in (
        ("stdout", shell.stdout, stdout), ("stderr", shell.stderr, stderr)
    ):
        if matcher and not _matches(matcher, actual):
            mismatches.append(f"{label}_mismatch")
    return mismatches


def _matches(matcher: TextMatcher, actual: str) -> bool:
    if matcher.equals is not None:
        return actual == matcher.equals
    if matcher.contains is not None:
        return matcher.contains in actual
    assert matcher.regex is not None
    return re.search(matcher.regex, actual) is not None


def _redact(value: str, secrets) -> str:
    for secret in sorted(
        {secret for secret in secrets if secret}, key=len, reverse=True
    ):
        value = value.replace(secret, "[REDACTED]")
    return value


def _task_for_item(conn: Conn, project_id: int, item_id: int):
    row = conn.execute(
        "SELECT t.* FROM task t JOIN task_item i ON i.task_id=t.id "
        "WHERE t.project_id=? AND i.id=?", (project_id, item_id),
    ).fetchone()
    if row is None:
        raise BacklogError(f"no task item with id {item_id} in this project")
    return row


def _action(value: str):
    from .hooks import Action
    return Action(value)


def _utc_timestamp() -> str:
    return utcnow()


def item_state(conn: Conn, item_id: int, project_root: Path | None = None) -> str:
    """Return pending or the latest fresh terminal status."""
    executable = executable_item(conn, item_id)
    row = conn.execute(
        "SELECT * FROM execution_result WHERE item_id = ? AND spec_fingerprint = ? "
        "ORDER BY id DESC LIMIT 1", (item_id, executable["spec_fingerprint"]),
    ).fetchone()
    if row is None:
        return "pending"
    if project_root is not None and not _source_matches(row, source_identity(project_root)):
        return "pending"
    return row["status"]


def required_validations_pass(
    conn: Conn, task_id: int, project_root: Path | None = None,
) -> tuple[bool, list[int]]:
    rows = conn.execute(
        "SELECT e.item_id, e.spec_fingerprint FROM executable_item e "
        "JOIN task_item i ON i.id=e.item_id "
        "WHERE i.task_id=? AND e.requirement='required' ORDER BY e.item_id", (task_id,),
    ).fetchall()
    failed: list[int] = []
    for row in rows:
        latest = conn.execute(
            "SELECT * FROM execution_result WHERE item_id=? AND spec_fingerprint=? "
            "ORDER BY id DESC LIMIT 1", (row["item_id"], row["spec_fingerprint"]),
        ).fetchone()
        passed = (
            latest is not None
            and latest["status"] == TerminalStatus.PASS.value
            and (
                project_root is None
                or _source_matches(latest, source_identity(project_root))
            )
        )
        if not passed and current_waiver(conn, int(row["item_id"])) is None:
            failed.append(int(row["item_id"]))
    return not failed, failed


def required_results_pass(
    conn: Conn, task_id: int, project_root: Path | None = None,
) -> tuple[bool, list[int]]:
    """Aggregate execution verdict; unlike workflow gates, waivers are not passes."""
    rows = conn.execute(
        "SELECT e.item_id FROM executable_item e JOIN task_item i ON i.id=e.item_id "
        "WHERE i.task_id=? AND e.requirement='required' ORDER BY e.item_id",
        (task_id,),
    ).fetchall()
    failed = [
        int(row["item_id"]) for row in rows
        if item_state(conn, int(row["item_id"]), project_root) != "pass"
    ]
    return not failed, failed


def execution_history(
    conn: Conn, item_id: int, *, limit: int = 20,
    project_root: Path | None = None,
) -> list[dict[str, Any]]:
    """Return newest-first bounded result history."""
    executable = executable_item(conn, item_id)
    if isinstance(limit, bool) or limit < 1 or limit > 100:
        raise BacklogError("history limit must be between 1 and 100")
    current_source = source_identity(project_root) if project_root is not None else None
    rows = conn.execute(
        "SELECT * FROM execution_result WHERE item_id=? ORDER BY id DESC LIMIT ?",
        (item_id, limit),
    ).fetchall()
    history: list[dict[str, Any]] = []
    for row in rows:
        value = {key: row[key] for key in row.keys()}
        expected = _decode_json(value.pop("expected_result"))
        actual = _decode_json(value.pop("actual_result"))
        value["expected"] = expected
        value["actual"] = actual
        value["diagnostic"] = value.pop("detail")
        value["stale"] = (
            value["spec_fingerprint"] != executable["spec_fingerprint"]
            or (
                current_source is not None
                and not _source_matches(row, current_source)
            )
        )
        history.append(value)
    return history


def waive_validation(
    conn: Conn, project_id: int, item_id: int, *, actor: str, reason: str,
) -> dict[str, Any]:
    actor = (actor or "").strip()
    reason = (reason or "").strip()
    if not actor:
        raise BacklogError("validation waiver requires a non-empty actor")
    if not reason:
        raise BacklogError("validation waiver requires a non-empty reason")
    task = _task_for_item(conn, project_id, item_id)
    executable = executable_item(conn, item_id)
    now = utcnow()
    waiver_id = conn.insert_returning_id(
        "INSERT INTO validation_waiver(item_id,spec_fingerprint,actor,reason,created_at) "
        "VALUES(?,?,?,?,?)",
        (item_id, executable["spec_fingerprint"], actor, reason, now),
    )
    from .core import log_event
    log_event(
        conn, "validation.waived", project_id, task["id"], task["key"], actor,
        to_value=str(item_id), detail=reason,
    )
    conn.commit()
    row = conn.execute(
        "SELECT * FROM validation_waiver WHERE id=?", (waiver_id,)
    ).fetchone()
    return {key: row[key] for key in row.keys()}


def current_waiver(conn: Conn, item_id: int) -> dict[str, Any] | None:
    executable = executable_item(conn, item_id)
    row = conn.execute(
        "SELECT * FROM validation_waiver WHERE item_id=? AND spec_fingerprint=? "
        "AND superseded_at IS NULL ORDER BY id DESC LIMIT 1",
        (item_id, executable["spec_fingerprint"]),
    ).fetchone()
    return {key: row[key] for key in row.keys()} if row else None


def validation_diagnostics(conn: Conn) -> list[dict[str, Any]]:
    """Skipped and active-waiver details for doctor."""
    rows = conn.execute(
        "SELECT e.item_id,e.executor,e.spec_fingerprint,i.content,t.key AS task_key "
        "FROM executable_item e JOIN task_item i ON i.id=e.item_id "
        "JOIN task t ON t.id=i.task_id ORDER BY t.key,e.item_id"
    ).fetchall()
    diagnostics: list[dict[str, Any]] = []
    for row in rows:
        latest = conn.execute(
            "SELECT * FROM execution_result WHERE item_id=? AND spec_fingerprint=? "
            "ORDER BY id DESC LIMIT 1",
            (row["item_id"], row["spec_fingerprint"]),
        ).fetchone()
        skipped = conn.execute(
            "SELECT s.* FROM execution_result s WHERE s.item_id=? "
            "AND s.spec_fingerprint=? AND s.status='skipped' "
            "AND NOT EXISTS (SELECT 1 FROM execution_result p "
            "  WHERE p.item_id=s.item_id AND p.spec_fingerprint=s.spec_fingerprint "
            "  AND p.status='pass' AND p.id>s.id) "
            "ORDER BY s.id DESC LIMIT 1",
            (row["item_id"], row["spec_fingerprint"]),
        ).fetchone()
        waiver = current_waiver(conn, int(row["item_id"]))
        if skipped is not None:
            prior = conn.execute(
                "SELECT status FROM execution_result WHERE item_id=? "
                "AND spec_fingerprint=? AND id<? ORDER BY id DESC LIMIT 1",
                (row["item_id"], row["spec_fingerprint"], skipped["id"]),
            ).fetchone()
            diagnostics.append({
                "kind": "skipped", "task": row["task_key"],
                "item_id": int(row["item_id"]), "item": row["content"],
                "executor": row["executor"], "actor": skipped["actor"],
                "reason": skipped["reason"],
                "prior_result": prior["status"] if prior else "pending",
                "timestamp": skipped["finished_at"],
            })
        if waiver is not None:
            diagnostics.append({
                "kind": "waived", "task": row["task_key"],
                "item_id": int(row["item_id"]), "item": row["content"],
                "executor": row["executor"], "actor": waiver["actor"],
                "reason": waiver["reason"],
                "prior_result": latest["status"] if latest else "pending",
                "timestamp": waiver["created_at"],
            })
    return diagnostics


def _after_pass(backlog, item_id: int, actor: str) -> None:
    now = utcnow()
    backlog._conn.execute(
        "UPDATE validation_waiver SET superseded_at=? "
        "WHERE item_id=? AND superseded_at IS NULL",
        (now, item_id),
    )
    row = backlog._conn.execute(
        "SELECT i.kind,i.done,e.requirement FROM task_item i "
        "JOIN executable_item e ON e.item_id=i.id WHERE i.id=?",
        (item_id,),
    ).fetchone()
    if (
        row is not None and row["kind"] == "checklist"
        and row["requirement"] == Requirement.REQUIRED.value and not row["done"]
    ):
        task = _task_for_item(backlog._conn, backlog.pid, item_id)
        backlog._conn.execute(
            "UPDATE task_item SET done=1,updated_at=? WHERE id=?", (now, item_id)
        )
        from .core import log_event
        log_event(
            backlog._conn, "item", backlog.pid, task["id"], task["key"], actor,
            to_value="done", detail=f"validation pass automatically checked item #{item_id}",
        )
    backlog._conn.commit()


def _source_matches(result: Mapping[str, Any], current: SourceIdentity) -> bool:
    if current.unavailable or result["source_revision_unavailable"]:
        return True
    return (
        result["source_revision"] == current.revision
        and result["source_dirty_fingerprint"] == current.dirty_fingerprint
    )


def _decode_json(value: str | None) -> Any:
    return json.loads(value) if value is not None else None


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

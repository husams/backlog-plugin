"""Named validation-hook loading, invocation, identity, and timeout handling."""

from __future__ import annotations

import hashlib
import inspect
import signal
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Mapping

from .. import audit
from ..db import BacklogError
from .contracts import (
    Executor,
    HookSpec,
    SourceIdentity,
    TerminalStatus,
    ValidationContext,
    ValidationExecutionResult,
    ValidationHookResult,
)
from .policy import load_policy, source_identity
from .specs import parse_spec
from .store import executable_item


def run_hook_validation(
    backlog,
    item_id: int,
    *,
    actor: str,
    project_root: Path,
) -> ValidationExecutionResult:
    """Resolve and execute one allowlisted project validation hook."""
    from .. import hooks

    root = project_root.resolve()
    executable = executable_item(backlog._conn, item_id)
    spec = parse_spec(executable["execution_spec"])
    if spec.executor != Executor.HOOK or spec.hook is None:
        raise BacklogError(f"task item {item_id} is not a hook executable")
    hook_spec = spec.hook
    task_item = backlog._conn.execute(
        "SELECT i.id, i.kind, i.content, t.id AS task_id, t.key AS task_key "
        "FROM task_item i JOIN task t ON t.id=i.task_id WHERE i.id=? AND t.project_id=?",
        (item_id, backlog.pid),
    ).fetchone()
    if task_item is None:
        raise BacklogError(f"no task item with id {item_id} in this project")

    policy = load_policy(root)
    denial = policy.denial_reason(spec)
    if denial is not None:
        return _hook_terminal(
            backlog,
            executable,
            hook_spec,
            actor,
            root,
            TerminalStatus.SKIPPED,
            "policy_denied",
            denial,
            None,
            None,
            emit_failed=False,
        )

    backlog_dir = root / ".backlog"
    try:
        module = hooks.load_project_hooks(backlog_dir)
    except BacklogError:
        return _hook_terminal(
            backlog,
            executable,
            hook_spec,
            actor,
            root,
            TerminalStatus.ERROR,
            "hooks_package_invalid",
            "trusted hooks package could not be loaded",
            None,
            None,
        )
    if module is None:
        return _hook_terminal(
            backlog,
            executable,
            hook_spec,
            actor,
            root,
            TerminalStatus.ERROR,
            "hooks_package_missing",
            "trusted hooks package is absent",
            None,
            None,
        )
    registrations = getattr(module, "validation_hooks", None)
    if not isinstance(registrations, Mapping):
        return _hook_terminal(
            backlog,
            executable,
            hook_spec,
            actor,
            root,
            TerminalStatus.ERROR,
            "validation_hooks_missing",
            "validation_hooks must be a mapping",
            None,
            None,
        )
    if hook_spec.name not in registrations:
        return _hook_terminal(
            backlog,
            executable,
            hook_spec,
            actor,
            root,
            TerminalStatus.ERROR,
            "hook_unknown",
            "registered hook name was not found",
            None,
            None,
        )
    callback = registrations[hook_spec.name]
    if not callable(callback):
        return _hook_terminal(
            backlog,
            executable,
            hook_spec,
            actor,
            root,
            TerminalStatus.ERROR,
            "hook_not_callable",
            "registered hook entry is not callable",
            None,
            None,
        )
    try:
        identity = hook_implementation_identity(callback)
    except BacklogError:
        return _hook_terminal(
            backlog,
            executable,
            hook_spec,
            actor,
            root,
            TerminalStatus.ERROR,
            "hook_identity_unavailable",
            "deterministic hook identity is unavailable",
            None,
            None,
        )
    timeout_constraint = _timeout_constraint()
    if timeout_constraint is not None:
        return _hook_terminal(
            backlog,
            executable,
            hook_spec,
            actor,
            root,
            TerminalStatus.ERROR,
            "hook_timeout_unavailable",
            timeout_constraint,
            None,
            identity,
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
        task_item["task_key"],
        hooks.Action.CHECK_STARTED,
        actor=actor,
        operation="validation.hook",
        parameters={"item_id": item_id, "hook": hook_spec.name, "identity": identity},
    )
    try:
        with _deadline(hook_spec.timeout_seconds):
            returned = callback(backlog, context, hook_spec.arguments)
        if not isinstance(returned, ValidationHookResult):
            raise BacklogError("hook must return ValidationHookResult")
        actual = returned.value
        status = (
            TerminalStatus.PASS
            if actual == hook_spec.expected_result
            else TerminalStatus.FAIL
        )
        reason = "" if status == TerminalStatus.PASS else "result_mismatch"
        action = (
            hooks.Action.CHECK_PASSED
            if status == TerminalStatus.PASS
            else hooks.Action.CHECK_FAILED
        )
        backlog.trigger(
            task_item["task_key"],
            action,
            actor=actor,
            operation="validation.hook",
            parameters={
                "item_id": item_id,
                "hook": hook_spec.name,
                "identity": identity,
            },
        )
        return _hook_terminal(
            backlog,
            executable,
            hook_spec,
            actor,
            root,
            status,
            reason,
            returned.detail,
            actual,
            identity,
            emit_failed=False,
            source=source,
        )
    except _HookTimeout:
        backlog.trigger(
            task_item["task_key"],
            hooks.Action.CHECK_TIMED_OUT,
            actor=actor,
            operation="validation.hook",
            parameters={
                "item_id": item_id,
                "hook": hook_spec.name,
                "reason": "hook_timeout",
            },
        )
        return _hook_terminal(
            backlog,
            executable,
            hook_spec,
            actor,
            root,
            TerminalStatus.ERROR,
            "hook_timeout",
            "validation hook exceeded its timeout",
            None,
            identity,
            emit_failed=False,
            source=source,
        )
    except Exception:
        backlog.trigger(
            task_item["task_key"],
            hooks.Action.CHECK_FAILED,
            actor=actor,
            operation="validation.hook",
            parameters={
                "item_id": item_id,
                "hook": hook_spec.name,
                "reason": "hook_exception",
            },
        )
        return _hook_terminal(
            backlog,
            executable,
            hook_spec,
            actor,
            root,
            TerminalStatus.ERROR,
            "hook_exception",
            "validation hook raised an exception",
            None,
            identity,
            emit_failed=False,
            source=source,
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
    backlog,
    executable: Mapping[str, Any],
    hook_spec: HookSpec,
    actor: str,
    root: Path,
    status: TerminalStatus,
    reason: str,
    detail: str,
    actual: Any,
    identity: str | None,
    *,
    emit_failed: bool = True,
    source: SourceIdentity | None = None,
) -> ValidationExecutionResult:
    from ..hooks import Action

    item_id = int(executable["item_id"])
    if emit_failed:
        row = backlog._conn.execute(
            "SELECT t.key FROM task_item i JOIN task t ON t.id=i.task_id "
            "WHERE i.id=? AND t.project_id=?",
            (item_id, backlog.pid),
        ).fetchone()
        assert row is not None
        backlog.trigger(
            row["key"],
            Action.CHECK_FAILED,
            actor=actor,
            operation="validation.hook",
            parameters={"item_id": item_id, "hook": hook_spec.name, "reason": reason},
        )
    record = audit.record_result(
        backlog._conn,
        item_id,
        executable["spec_fingerprint"],
        status,
        reason=reason,
        detail=detail,
        source=source or source_identity(root),
        expected=hook_spec.expected_result,
        actual=actual,
        hook_name=hook_spec.name,
        implementation_identity=identity,
        actor=actor,
    )
    if status == TerminalStatus.PASS:
        audit._after_pass(backlog, item_id, actor)
    return ValidationExecutionResult(
        status,
        reason,
        detail,
        hook_spec.expected_result,
        actual,
        hook_spec.name,
        identity,
        record,
    )


class _HookTimeout(Exception):
    pass


@contextmanager
def _deadline(seconds: int):
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
    if not all(
        hasattr(signal, name) for name in ("SIGALRM", "ITIMER_REAL", "setitimer")
    ):
        return "sigalrm_unavailable"
    if threading.current_thread() is not threading.main_thread():
        return "main_thread_required"
    return None

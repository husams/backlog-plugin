"""Shell and batch validation runners."""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .. import audit
from ..db import BacklogError, Conn, utcnow
from .contracts import (
    ExecutionPolicy,
    ExecutionSpec,
    Executor,
    ShellSpec,
    SourceIdentity,
    TerminalStatus,
    TextMatcher,
    ValidationExecutionResult,
    parse_spec,
)
from .hook_runner import _hook_terminal, run_hook_validation
from .policy import load_policy, source_identity
from .store import executable_item


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
    audit.record_result(
        backlog._conn, item_id, executable["spec_fingerprint"], status,
        reason=reason, detail=diagnostic, source=source,
        actual_exit_code=actual_exit_code, stdout=stdout, stderr=stderr,
        duration_ms=duration_ms, started_at=started_at,
        actor=actor or backlog.actor,
        expected=expected, actual=actual,
    )
    if status == TerminalStatus.PASS.value:
        audit._after_pass(backlog, item_id, actor or backlog.actor or "unknown")
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
    from ..hooks import Action
    return Action(value)


def _utc_timestamp() -> str:
    return utcnow()

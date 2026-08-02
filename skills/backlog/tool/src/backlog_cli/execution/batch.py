"""Batch and executor-neutral validation dispatch."""

from __future__ import annotations

import json
import time
from pathlib import Path

from .contracts import (
    Executor,
    SourceIdentity,
    TerminalStatus,
    ValidationExecutionResult,
)
from .hook_runner import _hook_terminal, run_hook_validation
from .policy import ExecutionPolicy, load_policy
from .shell import ExecutionResult, _record_shell_result, run_shell
from .specs import parse_spec
from .store import executable_item


def run_validation(
    backlog,
    item_id: int,
    project_root: Path,
    *,
    policy: ExecutionPolicy | None = None,
    actor: str | None = None,
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
    backlog,
    key: str,
    project_root: Path,
    *,
    fail_fast: bool = False,
    policy: ExecutionPolicy | None = None,
    actor: str | None = None,
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
            spec.shell.timeout_seconds
            if spec.shell is not None
            else spec.hook.timeout_seconds
        )
        remaining = policy.max_batch_seconds - (time.monotonic() - started)
        if budget_exhausted or remaining < timeout:
            budget_exhausted = True
            if spec.shell is not None:
                result = _record_shell_result(
                    backlog,
                    task.key,
                    int(row["item_id"]),
                    spec,
                    "skipped",
                    reason="batch_budget_exhausted",
                    diagnostic="batch_budget_exhausted",
                    source=SourceIdentity(unavailable=True),
                    actor=actor or backlog.actor,
                )
            else:
                assert spec.hook is not None
                result = _hook_terminal(
                    backlog,
                    row,
                    spec.hook,
                    actor or backlog.actor or "unknown",
                    root,
                    TerminalStatus.SKIPPED,
                    "batch_budget_exhausted",
                    "batch_budget_exhausted",
                    None,
                    None,
                    emit_failed=False,
                )
            results.append(result)
            continue
        result = run_validation(
            backlog,
            int(row["item_id"]),
            root,
            policy=policy,
            actor=actor,
        )
        results.append(result)
        if fail_fast and result.status in {
            TerminalStatus.FAIL,
            TerminalStatus.ERROR,
            "fail",
            "error",
        }:
            break
    return results

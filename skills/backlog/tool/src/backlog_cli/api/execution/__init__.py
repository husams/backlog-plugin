"""Execution APIs grouped by responsibility below this package."""

from .contracts import (
    ExecutionPolicy,
    ExecutionSpec,
    Executor,
    HookSpec,
    Requirement,
    ShellSpec,
    SourceIdentity,
    TerminalStatus,
    TextMatcher,
    ValidationContext,
    ValidationExecutionResult,
    ValidationHookResult,
    parse_spec,
    validation_hook,
)
from .hooks import hook_implementation_identity, run_hook_validation, _timeout_constraint
from .policy import load_policy, source_identity
from .runner import (
    ExecutionResult,
    run_shell,
    run_task_shells,
    run_task_validations,
    run_validation,
)
from .store import (
    _item_details,
    current_waiver,
    executable_item,
    execution_history,
    item_state,
    record_result,
    required_results_pass,
    required_validations_pass,
    set_executable,
    source_revision_unavailable_items,
    validation_diagnostics,
    waive_validation,
)

__all__ = [
    "ExecutionPolicy", "ExecutionSpec", "ExecutionResult", "Executor", "HookSpec",
    "Requirement", "ShellSpec", "SourceIdentity", "TerminalStatus", "TextMatcher",
    "ValidationContext", "ValidationExecutionResult", "ValidationHookResult",
    "current_waiver", "executable_item", "execution_history",
    "hook_implementation_identity", "item_state", "load_policy", "parse_spec",
    "record_result", "required_results_pass", "required_validations_pass",
    "run_hook_validation", "run_shell", "run_task_shells", "run_task_validations",
    "run_validation", "set_executable", "source_identity",
    "source_revision_unavailable_items", "validation_diagnostics", "validation_hook",
    "waive_validation",
]

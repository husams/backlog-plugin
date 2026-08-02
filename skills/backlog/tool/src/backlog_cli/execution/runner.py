"""Compatibility exports for validation runners."""

from .batch import run_task_shells, run_task_validations, run_validation
from .shell import ExecutionResult, run_shell

__all__ = [
    "ExecutionResult",
    "run_shell",
    "run_task_shells",
    "run_task_validations",
    "run_validation",
]

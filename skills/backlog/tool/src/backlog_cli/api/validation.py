"""Executable-item authoring, result, history, policy, and runner APIs."""

from __future__ import annotations

from pathlib import Path

from .. import execution
from ..execution import (
    ExecutionPolicy,
    ExecutionResult,
    SourceIdentity,
    TerminalStatus,
    ValidationExecutionResult,
)


class ValidationApi:
    __slots__ = ()

    def set_item_execution(self, item_id: int, spec: dict) -> dict:
        """Attach or replace the typed execution declaration for one item."""
        return execution.set_executable(self._conn, item_id, spec)

    def record_execution_result(
        self, item_id: int, spec_fingerprint: str,
        status: TerminalStatus | str, **kwargs,
    ) -> dict:
        """Record one terminal attempt; pending is represented by no row."""
        return execution.record_result(
            self._conn, item_id, spec_fingerprint, status, **kwargs
        )

    def execution_history(self, item_id: int, *, limit: int = 20,
                          project_root=None) -> list[dict]:
        """Newest-first bounded validation history with freshness metadata."""
        from pathlib import Path
        root = Path(project_root) if project_root is not None else None
        return execution.execution_history(
            self._conn, item_id, limit=limit, project_root=root
        )

    def waive_validation(self, item_id: int, *, reason: str,
                         actor: str | None = None) -> dict:
        """Audit an explicit waiver for the item's current execution spec."""
        return execution.waive_validation(
            self._conn, self.pid, item_id,
            actor=actor or self.actor or "", reason=reason,
        )

    def execution_policy(self, project_root) -> ExecutionPolicy:
        """Load trusted local policy from the executing project checkout."""
        from pathlib import Path
        return execution.load_policy(Path(project_root))

    def source_identity(self, project_root) -> SourceIdentity:
        """Return optional clean/dirty VCS identity for a validation run."""
        from pathlib import Path
        return execution.source_identity(Path(project_root))

    def run_hook_validation(
        self, item_id: int, *, actor: str | None = None, project_root=".",
    ) -> ValidationExecutionResult:
        """Resolve and run one trusted, allowlisted local validation hook."""
        from pathlib import Path
        return execution.run_hook_validation(
            self, item_id, actor=actor or self.actor or "unknown",
            project_root=Path(project_root),
        )

    def run_item(self, item_id: int, project_root, *,
                 policy: ExecutionPolicy | None = None,
                 actor: str | None = None) -> ExecutionResult:
        """Run one shell or hook executable item under trusted local policy."""
        from pathlib import Path
        return execution.run_validation(
            self, item_id, Path(project_root), policy=policy,
            actor=actor or self.actor,
        )

    def run_task(self, key: str, project_root, *, fail_fast: bool = False,
                 policy: ExecutionPolicy | None = None,
                 actor: str | None = None) -> list[ExecutionResult]:
        """Run all executable items in declaration order."""
        from pathlib import Path
        return execution.run_task_validations(
            self, key, Path(project_root), fail_fast=fail_fast, policy=policy,
            actor=actor or self.actor,
        )

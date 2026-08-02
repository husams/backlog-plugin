from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class World:
    root: Path
    env: dict[str, str] = field(repr=False)
    current_key: str | None = None
    current_type: str | None = None
    keys: dict[str, str] = field(default_factory=dict)
    last_result: subprocess.CompletedProcess[str] | None = None
    last_json: Any = None
    item_id: int | None = None
    review_root: str | None = None
    review_comment: str | None = None
    retrospective_key: str | None = None
    iteration_key: str | None = None

    def run(
        self,
        *args: str,
        actor: str | None = None,
        json_output: bool = True,
        expected: int | None = 0,
    ) -> Any:
        command = [sys.executable, "-m", "backlog_cli.cli"]
        if json_output:
            command.append("--json")
        if actor:
            command.extend(["--actor", actor])
        command.extend(args)
        result = subprocess.run(
            command,
            cwd=self.root,
            env=self.env,
            text=True,
            capture_output=True,
        )
        self.last_result = result
        self.last_json = (
            json.loads(result.stdout) if json_output and result.stdout.strip() else None
        )
        if expected is not None:
            assert result.returncode == expected, result.stderr or result.stdout
        return self.last_json if json_output else result.stdout

    def task(self, key: str | None = None) -> dict[str, Any]:
        previous_result, previous_json = self.last_result, self.last_json
        payload = self.run("show", key or self.require_key())
        self.last_result, self.last_json = previous_result, previous_json
        return payload

    def require_key(self) -> str:
        assert self.current_key is not None
        return self.current_key

    def output(self) -> str:
        assert self.last_result is not None
        return f"{self.last_result.stdout}\n{self.last_result.stderr}"

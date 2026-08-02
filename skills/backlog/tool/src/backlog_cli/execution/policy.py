"""Trusted local execution policy and source identity."""

from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path

import yaml

from ..db import BacklogError
from .contracts import ExecutionPolicy, SourceIdentity, _bool, _strings


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

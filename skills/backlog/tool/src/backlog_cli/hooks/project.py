"""Trusted project hook loading and transition callbacks."""

from __future__ import annotations

import hashlib
import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING, Any

from ..db import BacklogError
from .actions import Action

if TYPE_CHECKING:
    from ..api import Backlog

Trigger = dict[str, Any]


def _load_project_hooks(backlog_dir: Path) -> ModuleType | None:
    package_dir = backlog_dir / "hooks"
    path = package_dir / "__init__.py"
    if not path.is_file():
        return None
    digest = hashlib.sha256(str(package_dir.resolve()).encode()).hexdigest()[:16]
    name = f"_backlog_project_hooks_{digest}"
    spec = importlib.util.spec_from_file_location(
        name,
        path,
        submodule_search_locations=[str(package_dir)],
    )
    assert spec is not None and spec.loader is not None
    for loaded_name in [
        loaded_name
        for loaded_name in sys.modules
        if loaded_name == name or loaded_name.startswith(f"{name}.")
    ]:
        del sys.modules[loaded_name]
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    project_root = str(backlog_dir.parent)
    sys.path.insert(0, project_root)
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        sys.modules.pop(name, None)
        raise BacklogError(f"cannot load project hooks from {path}: {exc}") from None
    finally:
        sys.path.remove(project_root)
    return module


def load_project_hooks(backlog_dir: Path) -> ModuleType | None:
    """Load the trusted local hooks package for validation or transitions."""
    return _load_project_hooks(backlog_dir)


def pre_transition(
    backlog_dir: Path,
    action: Action,
    trigger: Trigger,
    current_state: str,
    new_state: str,
    backlog: "Backlog",
) -> str:
    module = _load_project_hooks(backlog_dir)
    callback = getattr(module, "pre_transition", None) if module else None
    if callback is None:
        return new_state
    if not callable(callback):
        raise BacklogError(
            f"{backlog_dir / 'hooks' / '__init__.py'}: pre_transition is not callable"
        )
    try:
        result = callback(action, trigger, current_state, new_state, backlog)
    except Exception as exc:
        raise BacklogError(f"pre_transition blocked the transition: {exc}") from None
    if not isinstance(result, str) or not result.strip():
        raise BacklogError("pre_transition must return a non-empty state string")
    return result


def post_transition(
    backlog_dir: Path,
    action: Action,
    trigger: Trigger,
    previous_state: str,
    current_state: str,
    backlog: "Backlog",
) -> None:
    module = _load_project_hooks(backlog_dir)
    callback = getattr(module, "post_transition", None) if module else None
    if callback is None:
        return
    if not callable(callback):
        raise BacklogError(
            f"{backlog_dir / 'hooks' / '__init__.py'}: post_transition is not callable"
        )
    try:
        callback(action, trigger, previous_state, current_state, backlog)
    except Exception as exc:
        raise BacklogError(
            f"transition committed, but post_transition failed: {exc}"
        ) from None

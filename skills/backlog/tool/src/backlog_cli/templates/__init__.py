"""Project template storage, instantiation, and authoring."""

from .authoring import add_status, create, remove, render, set_default, set_transition
from .store import (
    default,
    get,
    install_builtins,
    instantiate,
    list_all,
    require,
    statuses_of,
    transitions_of,
    workflows_of,
)

__all__ = [name for name in globals() if not name.startswith("_")]

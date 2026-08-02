"""Normalization and actor-attribution rules."""

from __future__ import annotations

from ..db import BacklogError
from ..schema import (
    ITEM_KIND_ALIASES,
    ITEM_KINDS,
    PRIORITIES,
    STATUS_ALIASES,
    STATUS_DISPLAY,
    STATUSES,
    TASK_TYPE_ALIASES,
    TASK_TYPES,
)


def normalize_status(value: str) -> str:
    """Normalize spelling and apply the built-in status aliases."""
    slug = value.strip().lower().replace("-", "_").replace(" ", "_")
    while "__" in slug:
        slug = slug.replace("__", "_")
    slug = STATUS_ALIASES.get(slug, slug)
    if slug not in STATUSES:
        raise BacklogError(
            f"unknown status {value!r}. Valid: "
            + ", ".join(STATUS_DISPLAY.get(s, s) for s in STATUSES)
        )
    return slug


def normalize_key(value: str) -> str:
    return value.strip().upper()


def normalize_priority(value: str) -> str:
    p = value.strip().upper()
    if p not in PRIORITIES:
        raise BacklogError(
            f"unknown priority {value!r}. Valid: {', '.join(PRIORITIES)}"
        )
    return p


def normalize_type(value: str) -> str:
    t = value.strip().lower().replace("-", "_").replace(" ", "_")
    t = TASK_TYPE_ALIASES.get(t, t)
    if t not in TASK_TYPES:
        raise BacklogError(
            f"unknown task type {value!r}. Valid: {', '.join(TASK_TYPES)}"
        )
    return t


def normalize_item_kind(value: str) -> str:
    k = value.strip().lower().replace("-", "_").replace(" ", "_")
    k = ITEM_KIND_ALIASES.get(k, k)
    if k not in ITEM_KINDS:
        raise BacklogError(
            f"unknown item kind {value!r}. Valid: {', '.join(ITEM_KINDS)}"
        )
    return k


def require_actor(actor: str | None, operation: str) -> str:
    """Require an attributable identity before creating accountable work."""
    if not isinstance(actor, str) or not actor.strip():
        raise BacklogError(
            f"{operation} requires an actor; pass --actor NAME or "
            "open the Python API with actor=NAME"
        )
    return actor.strip()


def require_independent_actor(
    key: str,
    creator: str | None,
    actor: str | None,
    operation: str,
) -> str | None:
    """Prevent attributed work from being promoted by its own creator.

    A NULL creator is tolerated only for historical rows whose original
    creation event had no actor and therefore cannot be backfilled.
    """
    if not creator:
        return actor.strip() if isinstance(actor, str) and actor.strip() else None
    if not isinstance(actor, str) or not actor.strip():
        raise BacklogError(
            f"{key} was created by {creator}; {operation} requires an independent actor"
        )
    identity = actor.strip()
    if identity.casefold() == creator.strip().casefold():
        raise BacklogError(
            f"{identity} created {key} and cannot perform {operation} on work it created; "
            "use an independent reviewer"
        )
    return identity

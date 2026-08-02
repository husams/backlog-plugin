"""Small shared value objects used by the public API."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone


def _age_days(stamp: str | None) -> float:
    if not stamp:
        return 0.0
    try:
        when = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - when).total_seconds() / 86400.0


@dataclass(frozen=True)
class Store:
    """Which store and project this session talks to."""

    backend: str
    scope: str
    project: str
    location: str

    def __str__(self) -> str:
        return f"{self.location} ({self.backend}/{self.scope}) project={self.project}"

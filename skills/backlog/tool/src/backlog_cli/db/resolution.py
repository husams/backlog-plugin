"""Backlog store and project location resolution."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from .common import (
    ARTIFACTS_DIR_NAME,
    BACKLOG_DIR_NAME,
    DB_NAME,
    DEFAULT_PG_SCHEMA,
    POSTGRES,
    SQLITE,
    BacklogError,
)

# --------------------------------------------------------------------------- #


@dataclass
class StoreSpec:
    dialect: str
    scope: str  # repo | central | shared
    project: str
    artifacts_dir: Path
    db_path: Path | None = None
    dsn: str | None = None
    schema: str | None = None
    backlog_dir: Path | None = None

    @property
    def location(self) -> str:
        if self.dialect == POSTGRES:
            u = urlparse(self.dsn or "")
            return (
                f"postgresql://{u.hostname or '?'}{u.path or ''} schema={self.schema}"
            )
        return str(self.db_path)


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    return slug or "default"


def _repo_root(start: Path | None = None) -> Path | None:
    cur = (start or Path.cwd()).resolve()
    for candidate in [cur, *cur.parents]:
        if (candidate / ".git").exists():
            return candidate
    return None


def default_project(start: Path | None = None) -> str:
    env = os.environ.get("BACKLOG_PROJECT")
    if env:
        return slugify(env)
    root = _repo_root(start) or (start or Path.cwd()).resolve()
    return slugify(root.name)


def find_backlog_dir(start: Path | None = None) -> Path | None:
    env = os.environ.get("BACKLOG_DIR")
    if env:
        p = Path(env).expanduser()
        return p.resolve() if p.is_dir() else None
    cur = (start or Path.cwd()).resolve()
    for candidate in [cur, *cur.parents]:
        d = candidate / BACKLOG_DIR_NAME
        if d.is_dir():
            return d
    return None


def resolve_spec(start: Path | None = None, for_init: bool = False) -> StoreSpec:
    """Work out which store this invocation talks to. Never connects."""
    selector = (os.environ.get("BACKLOG_DB") or "").strip()
    configured_url = (os.environ.get("BACK_LOG_URL") or "").strip()
    project = default_project(start)
    art = os.environ.get("BACKLOG_ARTIFACTS")

    if selector.lower() in (SQLITE, POSTGRES):
        backend = selector.lower()
        url = configured_url
        if backend == POSTGRES and not url:
            raise BacklogError(
                "BACKLOG_DB=postgres requires BACK_LOG_URL with a PostgreSQL DSN."
            )
        if backend == POSTGRES and urlparse(url).scheme.lower() not in (
            "postgres",
            "postgresql",
        ):
            raise BacklogError(
                "BACK_LOG_URL must start with postgres:// or postgresql:// "
                "when BACKLOG_DB=postgres."
            )
        if backend == SQLITE and urlparse(url).scheme.lower() in (
            "postgres",
            "postgresql",
        ):
            raise BacklogError(
                "BACK_LOG_URL cannot be a PostgreSQL DSN when BACKLOG_DB=sqlite."
            )
    else:
        # Compatibility with releases where BACKLOG_DB itself held a path/URL.
        url = selector

    if url:
        scheme = urlparse(url).scheme.lower()
        if scheme in ("postgres", "postgresql"):
            home = Path(art).expanduser() if art else Path.home() / BACKLOG_DIR_NAME
            return StoreSpec(
                dialect=POSTGRES,
                scope="shared",
                project=project,
                dsn=url,
                schema=os.environ.get("BACKLOG_SCHEMA", DEFAULT_PG_SCHEMA),
                artifacts_dir=home / ARTIFACTS_DIR_NAME,
            )
        raw = url[len("sqlite://") :] if scheme == "sqlite" else url
        path = Path(raw if raw.startswith("/") else raw.lstrip("/")).expanduser()
        # A path ending in .db names the file; anything else is a directory
        # holding one store for every project on this machine.
        home = path.resolve() if path.suffix == ".db" else (path / DB_NAME).resolve()
        return StoreSpec(
            dialect=SQLITE,
            scope="central",
            project=project,
            db_path=home,
            backlog_dir=home.parent,
            artifacts_dir=(
                Path(art).expanduser() if art else home.parent / ARTIFACTS_DIR_NAME
            ),
        )

    d = find_backlog_dir(start)
    if d is None:
        if not for_init:
            raise BacklogError(
                "no backlog store found.\n"
                "  repo mode     : run `backlog init` in the project root\n"
                "  central/shared: set BACKLOG_DB (see `backlog where --help`)"
            )
        d = (start or Path.cwd()).resolve() / BACKLOG_DIR_NAME
    return StoreSpec(
        dialect=SQLITE,
        scope="repo",
        project=project,
        db_path=d / DB_NAME,
        backlog_dir=d,
        artifacts_dir=(Path(art).expanduser() if art else d / ARTIFACTS_DIR_NAME),
    )


def require_backlog_dir(start: Path | None = None) -> Path:
    """Where artifact files belong for this invocation."""
    spec = resolve_spec(start)
    spec.artifacts_dir.parent.mkdir(parents=True, exist_ok=True)
    return spec.backlog_dir or spec.artifacts_dir.parent


# --------------------------------------------------------------------------- #

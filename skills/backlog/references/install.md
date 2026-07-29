# Installing and updating

The skill directory is self-contained: `skills/backlog/` carries its own
launchers, scripts, Python module and lockfile. Copy or link that one directory
and the skill works — that is what makes the same build install under both
Claude Code and Codex.

## Layout

```
backlog-plugin/
  .claude-plugin/plugin.json      Claude Code manifest
  .claude-plugin/marketplace.json single-plugin marketplace for this repo
  skills/backlog/                 the skill — self-contained, portable
    SKILL.md                      rules + routing (the only file loaded by default)
    references/*.md               everything else, loaded on demand
    scripts/*.py                  ready-made answers        <- run, never read
    bin/                          launchers + optional install helper
    tool/                         uv project implementing it <- never read
```

## Install

**Codex** (and Claude Code's skills directory):

```bash
./skills/backlog/bin/install.sh
```

Symlinks `skills/backlog` into `~/.codex/skills/backlog` and
`~/.claude/skills/backlog`, so an edit in the repo is live in both immediately.
Codex discovers it on the next session; the launchers are called by path:

```bash
~/.codex/skills/backlog/bin/backlog where
```

**Claude Code as a plugin** — test without installing:

```bash
claude --plugin-dir ~/workspace/backlog-plugin
```

The skill references `bin/backlog` and `bin/backlog-py` relative to its own
directory. Claude Code resolves the installed skill before following those
instructions, including from its versioned plugin cache.

or install through its own marketplace:

```
/plugin marketplace add ~/workspace/backlog-plugin
/plugin install backlog-plugin@backlog-marketplace
/reload-plugins
```

The skill is namespaced `/backlog:backlog`. Its instructions resolve
`bin/backlog` and `bin/backlog-py` relative to the installed skill directory;
the plugin does not add a nonstandard root launcher directory.

Installing both ways at once is fine — they resolve to the same directory.

## Requirements

The only external requirement is [uv](https://docs.astral.sh/uv/). The first
launcher call runs `uv sync` to provision `tool/.venv`; every call after that
execs the venv directly (~60ms, no resolver). The environment is re-synced only
when `uv.lock` changes or a new extra is needed.

The CLI itself uses nothing outside the standard library. One optional extra
exists: `psycopg`, installed automatically the first time
`BACKLOG_DB=postgres`. To
pre-install it:

```bash
uv sync --project skills/backlog/tool --extra postgres
```

`gh` is optional and only powers `pr sync`.

## Adding a backlog to a project

From the repository root — this is the per-repository default; see
[store.md](store.md) to put it in a central directory or a shared PostgreSQL
server instead:

```bash
backlog init .
git add .backlog && git commit -m "chore: track backlog in .backlog"
```

`init` creates `.backlog/backlog.db`, `.backlog/artifacts/`, a `README.md`, a
`.gitattributes` that marks the database binary and unmergeable, and a
`.gitignore` for the SQLite sidecar files. The database **is** committed; the
`-wal`/`-shm` files are not.

## Setting up the database explicitly

Plugin installation does not need database credentials and therefore does not
provision a database. After choosing the backend, run the idempotent setup
script. It creates missing tables, applies migrations, installs workflow
templates, and creates the selected project:

```bash
# Repository-local SQLite
BACKLOG_DB=sqlite backlog-py scripts/setup_database.py

# SQLite at an explicit location
BACKLOG_DB=sqlite BACK_LOG_URL=sqlite:///absolute/path/backlog.db \
  backlog-py scripts/setup_database.py

# Shared PostgreSQL
BACKLOG_DB=postgres BACK_LOG_URL=postgresql://backlog@db.internal/backlog \
  backlog-py scripts/setup_database.py
```

Use PostgreSQL credential files or `PG*` variables rather than placing passwords
in shell history. Running the script again is safe and upgrades an older schema.

Optionally point the project's `CLAUDE.md` / `AGENTS.md` at it so future
sessions pick it up without being told:

```markdown
## Backlog

Active development backlog lives in `.backlog/`. Use the `backlog` skill for all
reads and writes — never edit `.backlog/backlog.db` by hand.
```

## Verifying

```bash
backlog --version
backlog where
backlog doctor
backlog-py scripts/standup.py
```

## Updating

Edit the markdown, scripts or runtime tool, bump `version` in
`.claude-plugin/plugin.json` and `.claude-plugin/marketplace.json`, then validate
the skill and plugin. Symlinked installs pick changes up immediately. In a session
started with `--plugin-dir`, `/reload-plugins` picks the change up; an installed
plugin copy needs `/plugin marketplace update backlog-marketplace`.

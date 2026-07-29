# Where the backlog lives

Three shapes. Which one is in use is decided entirely by environment, so no
project has to change how it calls the CLI.

```bash
$BL where       # backend, scope, project, exact location, env in effect
$BL projects    # every project sharing this store
```

| Scope | Environment | Store |
| --- | --- | --- |
| **repo** (default) | `BACKLOG_DB=sqlite`, URL unset | `<repo>/.backlog/backlog.db`, committed to git |
| **central** | `BACKLOG_DB=sqlite`, `BACK_LOG_URL=~/.backlog` | `<dir>/backlog.db` |
| **central** | `BACKLOG_DB=sqlite`, `BACK_LOG_URL=sqlite:///abs/path.db` | that one file |
| **shared** | `BACKLOG_DB=postgres`, `BACK_LOG_URL=postgresql://host/db` | the selected PostgreSQL schema |

## Environment

| Variable | Purpose |
| --- | --- |
| `BACKLOG_DB` | backend selector: `sqlite` or `postgres`; unset means repo SQLite |
| `BACK_LOG_URL` | SQLite path/URL or PostgreSQL DSN; required for `postgres`, optional for `sqlite` |
| `BACKLOG_PROJECT` | project slug in a central/shared store (default: the git repository's directory name, slugified) |
| `BACKLOG_DIR` | an explicit `.backlog` directory (repo mode only) |
| `BACKLOG_ARTIFACTS` | where artifact files live; defaults next to the store |

## Repo mode

The default and the right choice for a single repository: the backlog is a file
in the tree, reviewed and versioned with the code.

```bash
cd ~/work/widgets && $BL init .
git add .backlog && git commit -m 'chore: init backlog'
```

## Central: every project on this machine, one place

Useful when you work across many repositories and want one home for all of
them — and for repositories where a committed database is unwelcome.

```bash
export BACKLOG_DB=sqlite
export BACK_LOG_URL=~/.backlog
cd ~/work/widgets  && $BL init .    # -> ~/.backlog/widgets/backlog.db
cd ~/work/gadgets  && $BL init .    # -> ~/.backlog/gadgets/backlog.db
$BL projects
```

Each project keeps its own key sequence, so both start at `F-001` / `S-001`.
The slug comes from the repository directory name; override it when two
checkouts are the same project:

```bash
BACKLOG_PROJECT=widgets $BL board
```

## Shared: a PostgreSQL server the team can reach

Same commands, same data model. The configured schema is created on setup or
first use.

```bash
export BACKLOG_DB=postgres
export BACK_LOG_URL='postgresql://backlog@db.internal/backlog'
cd ~/work/widgets && $BL board
$BL projects
```

The PostgreSQL driver is an optional extra; the launcher installs it with `uv`
the first time `BACKLOG_DB` names a server. Nothing else in the skill has a
third-party dependency.

**Credentials.** Prefer a `~/.pgpass` entry or a `PG*` environment variable over
putting a password in `BACK_LOG_URL`, so it does not end up in shell history or a
process list.

**Connection slots.** A shared server hands out a finite number, and a busy
neighbour can hold them all for a moment. The CLI retries a transient
"remaining connection slots are reserved" failure five times with backoff; tune
with `BACKLOG_PG_RETRIES` and `BACKLOG_PG_RETRY_DELAY`. If it still fails, look
at who is holding them:

```sql
SELECT datname, state, count(*) FROM pg_stat_activity GROUP BY 1, 2 ORDER BY 3 DESC;
```

**Latency matters more than you expect.** Every command is a series of small
queries, so round-trip time multiplies. On a LAN server this is invisible; over
a proxy or a loaded host at ~100 ms per statement a full `linear pull` takes
minutes where SQLite takes seconds. Measure before committing a team to it, and
set `BACKLOG_DB=sqlite` and unset `BACK_LOG_URL` for a fast local fallback.

## Moving between backends

`export` / `import` is the transport, and it carries everything: features,
stories, subtasks, dependencies, review threads, artifacts metadata, Linear
links and history.

```bash
$BL export --out /tmp/widgets.json                      # from the old store
BACKLOG_DB=postgres BACK_LOG_URL=postgresql://db.internal/backlog \
  $BL import /tmp/widgets.json --replace                # into the new one
```

`import --replace` overwrites the whole target store — check `$BL where` first.
Artifact *files* are not carried by the dump; copy the `artifacts/` directory
alongside it.

## Which to pick

- One repo, backlog belongs with the code → **repo**.
- Many repos, one person or machine → **central**.
- Several people or agents on different machines needing the same board →
  **shared**. This is the only shape where two agents see each other's writes
  without a git pull.

Older installations that put a path or URL directly in `BACKLOG_DB` remain
supported, but new configuration should use the selector plus `BACK_LOG_URL`.

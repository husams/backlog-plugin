# Troubleshooting

Diagnose with the CLI. Never open the database in `sqlite3` or `psql` and never
patch it by hand — you will bypass the gates and desynchronise thread state.

Start with `$BL where`: most "the backlog is empty / wrong / missing" reports are
really "I am talking to a different store than I thought".

## `no backlog store found`

You are outside the project, or the environment points somewhere else. Check
first:

```bash
$BL where
```

In repo mode the store is found by walking up from the current directory, so
either `cd` into the repository or point at it:

```bash
BACKLOG_DIR=/path/to/repo/.backlog $BL board
```

If the project has no backlog yet, create one at the repository root:

```bash
$BL init /path/to/repo && (cd /path/to/repo && git add .backlog)
```

## The wrong project's backlog came up

In a central or shared store the project slug comes from the repository
directory name. Two checkouts of the same project, or a differently-named
directory, will resolve differently:

```bash
$BL where                                 # what slug am I using?
$BL projects                              # what else is in this store?
BACKLOG_PROJECT=widgets $BL board         # pin it
```

## `psycopg driver is not installed`

`BACKLOG_DB` names a PostgreSQL server but the optional extra is missing. The
launcher normally installs it; force it with:

```bash
uv sync --project ${CLAUDE_PLUGIN_ROOT}/tool --extra postgres
```

## `illegal transition`

This project's flow does not allow that move. The error lists what it does
allow. This is not a bug and there is no force flag — the CLI is reading
`workflow_transition`, so the fix is either to take the legal path or to change
the flow:

```bash
$BL statuses --type story                # what is allowed here
$BL workflow move-add --type story --from A --to B   # if the flow is wrong
```

See [templates.md](templates.md).

## `unknown status 'X' for a story in this project`

Statuses are per project. Another project's status names do not apply here.
`$BL statuses` lists the ones that do.

## A gate is blocking me

```bash
$BL gate <KEY> --for accepted     # or done / merge
```

Each failing check is printed with the reason. Common ones:

| Check | Fix |
| --- | --- |
| `pr_recorded` | `$BL pr set <KEY> --url <PR-URL>`, or `--no-pr` if it truly ships without one |
| `pr_approved` | `$BL pr set <KEY> --review-state approved` after the reviewer approves |
| `pr_merged` | `$BL pr set <KEY> --state merged` after the merge |
| `review_threads_closed` | `$BL review list <KEY> --state open`, then resolve each |
| `subtasks_complete` | finish the named subtasks, or `--allow-open-subtasks` deliberately |
| `status_accepted` | the item is not Accepted yet — do not merge |
| `dependencies_clear` | finish the blockers it names, drop the edge if it is wrong (`$BL dep rm`), or `--allow-blocked` deliberately |
| `children_complete` | finish the child tasks it names, or `--allow-open-subtasks` deliberately |

Waiving a gate (`--no-pr`, `--allow-open-subtasks`, `--allow-blocked`) is a
decision, not a workaround. Record why with `--reason` and say so in your reply
to the user.

## `that edge would create a dependency cycle`

The error prints the loop. One of those edges is wrong — decide which and drop
it with `$BL dep rm`. Do not invert an edge to dodge the message; that just
moves the false claim somewhere else.

## A Linear sync reported a conflict

Both sides changed the same item since they last agreed, so nothing was written.
Look at what differs, then pick a side explicitly:

```bash
$BL linear status ... --project cidx      # which items, which direction
$BL linear pull   ... --prefer remote     # Linear wins
$BL linear push   ... --prefer local --apply
```

`skipped: local ahead` and `skipped: remote ahead` are not errors — they mean the
other direction is the one to run. See [linear.md](linear.md).

## `cannot infer role`

The comment author matches neither the item's `assignee` nor its `reviewer`.
Either fix the assignment or state the role:

```bash
$BL assign <KEY> --to developer --reviewer senior-developer
$BL review reply C-003 --author qa-engineer --role reviewer --action reject --body "..."
```

## `thread ... is closed`

Closed threads reject replies. If it must genuinely be re-opened:

```bash
$BL review reopen <ROOT-KEY> --author <you> --body "why this is back"
```

## `uv is required but was not found`

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Or set `UV_BIN=/path/to/uv`. The first run resolves the environment and is
slower; later runs are fast.

## Git conflict on `.backlog/backlog.db`

`backlog.db` is binary — git cannot merge it. Resolve by replaying one side:

```bash
git checkout --ours   .backlog/backlog.db      # keep your branch's store
$BL export --out /tmp/mine.json

git checkout --theirs .backlog/backlog.db      # inspect the other side
$BL export --out /tmp/theirs.json
```

Compare the two JSON dumps, decide which store is authoritative, restore it with
`$BL import /tmp/<chosen>.json --replace`, then re-apply the handful of commands
from the other side (they are visible in its `event` table / `$BL history`).
Finish with `$BL doctor`.

Prevention: keep one branch's backlog edits scoped to that branch's stories, and
commit `.backlog/` in its own commit so a conflict is easy to isolate.

## The store looks wrong

```bash
$BL doctor
```

Checks database integrity, tasks nested under the wrong type, subtasks with no
parent, **tasks sitting in a status their flow does not define**, task types
with no workflow, projects not bound to a template, dependency cycles,
items that are In Progress or In Review while still blocked, threads with a
missing last comment, items marked Accepted/Done while review threads are still
open, Linear links pointing at deleted items, and artifact rows whose file
vanished. Exit 1 means it found problems, which it lists. If `doctor` reports
damage the CLI cannot explain, restore from the last good commit of
`.backlog/backlog.db` (or a `$BL export` dump) rather than editing the
database.

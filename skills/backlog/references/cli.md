# Command reference

```bash
BL=backlog            # on PATH under Claude Code; otherwise <skill>/bin/backlog
```

One command answers one question. When the answer has to be computed across
many tasks, use `backlog-py` instead — see [api.md](api.md).

`--json`, `--actor <name>` and `--project <slug>` are accepted on every command,
before or after the subcommand.

Keys are per project and case-insensitive: `F-001` features, `S-001` stories,
`T-001` subtasks, `C-001` review comments. A thread is named by its root
comment key.

## Store and projects

| Command | Purpose |
| --- | --- |
| `$BL init [path]` | create the store and this project |
| `$BL where` | which store and project this invocation talks to |
| `$BL projects` | every project in the store |
| `$BL project add --name N [--slug S] [--template T] [--description D]` | new project |
| `$BL project set SLUG [--name\|--description\|--status active\|archived]` | edit |
| `$BL doctor` | integrity + invariant check; exit 1 if problems |
| `$BL export [--out FILE]` | JSON dump of the whole store |
| `$BL import FILE --replace` | restore (older dumps are converted; `--as-project`) |

`BACKLOG_DB` picks the backend, `BACKLOG_PROJECT` the project, `BACKLOG_SCHEMA`
the PostgreSQL schema. See [store.md](store.md).

## Templates and flows

| Command | Purpose |
| --- | --- |
| `$BL templates` | templates available |
| `$BL template show SLUG [--type T]` | one template's flow |
| `$BL template add --slug S [--copy-of T\|--from-project P]` | author one |
| `$BL template status-add SLUG --type T --status X [...]` | edit a template |
| `$BL template move-add SLUG --type T --from A --to B [--gate G]` | |
| `$BL template default SLUG` / `$BL template rm SLUG` | |
| `$BL statuses [--type T]` | **this project's** flow |
| `$BL workflow show [--type T]` | the same |
| `$BL workflow gates` | what each gate check means |
| `$BL workflow status-add --type T --slug X --display D [--category C] [--after S] [--satisfies] [--terminal]` | |
| `$BL workflow status-rm --type T --slug X` | |
| `$BL workflow move-add --type T --from A --to B [--gate G,H]` | |
| `$BL workflow move-rm --type T --from A --to B` | |
| `$BL workflow apply [--template T] [--type T]` | re-instantiate from a template |
| `$BL workflow reset [--type T]` | back to this project's template |
| `$BL workflow copy --from PROJECT [--type T]` | adopt another project's flow |

See [templates.md](templates.md).

## Reading

| Command | Purpose |
| --- | --- |
| `$BL board [--all]` | open work grouped by status, in the workflow's order |
| `$BL next [--actor X]` | everything actionable for X, with blocked work separated |
| `$BL show KEY` | one task in full |
| `$BL list [filters]` | every task |
| `$BL feature list` / `story list` / `subtask list` | one type |
| `$BL history KEY` | audit trail |

Filters: `--status S`, `--open`, `--assignee X`, `--reviewer Y`, `--parent KEY`,
`--type feature|story|subtask`.

## Creating and editing

```bash
$BL feature  add --title T [--description D] [--ac "..."] [--priority P0..P3] [--owner X]
$BL story    add --title T [--feature F-001] [--branch B] [...]
$BL subtask  add --story S-001 --title T [...]
$BL task     add --type feature|story|subtask [--parent KEY] --title T [...]

$BL set KEY [--title|--description|--ac|--priority|--owner|--branch|--parent]
$BL assign KEY [--to X] [--reviewer Y] [--to-kind human|agent] [--reviewer-kind ...]
```

A subtask requires a story; a story may hang off a feature or stand alone; a
feature is a root. `--ac` replaces the acceptance criteria, one per line.
Assignee and reviewer names are free text — the human/agent kind is guessed
from the name and shown with a `*` on agents.

There is **no** `--status` flag anywhere. `move` is the only way status changes.

## Task items — criteria, checklists, notes

```bash
$BL item add KEY [--kind acceptance_criteria|checklist|note] --content "one per line"
$BL item set KEY --kind checklist --content "..."     # replace every entry of that kind
$BL item list KEY [--kind K]
$BL item check ID [--undo]                            # checklist entries only
$BL item rm ID
```

## Status and gates

```bash
$BL action KEY ACTION [--operation NAME] [--parameter NAME=VALUE]
$BL move KEY STATUS [--reason "..."] [--no-pr] [--allow-open-subtasks] [--allow-blocked]
$BL gate KEY --for start|in_review|accepted|done|merge [same waivers]
```

`action` resolves the destination from `.backlog/workflow.yaml`, or from the
bundled default when the project has no custom file. It runs project
`pre_transition` and `post_transition` hooks and uses the same gates as `move`.

`move` exits `1` on an illegal transition or a failed gate. `gate` exits `0`
pass, `2` blocked, `1` command error. Both read this project's flow — see
[workflow.md](workflow.md).

## Dependencies

```bash
$BL dep add KEY --blocks OTHER | --blocked-by OTHER | --relates OTHER | --duplicates OTHER [--note "why"]
$BL dep rm  KEY --blocks OTHER | ...
$BL dep list [KEY] [--kind blocks|relates|duplicates]
$BL dep check KEY                       # 0 startable, 2 blocked
$BL dep graph [--format text|dot|json]
```

See [dependencies.md](dependencies.md).

## Pull requests

```bash
$BL pr set KEY [--url URL] [--number N] [--repo owner/name]
               [--state none|draft|open|merged|closed]
               [--review-state none|pending|changes_requested|approved]
$BL pr sync KEY      # pull state from the `gh` CLI
```

`--url` auto-fills repo and number for GitHub `/pull/N` and GitLab
`/-/merge_requests/N`. A feature rejects `pr set`.

## Review

```bash
$BL review open KEY --author A --body B [--role reviewer|developer] [--severity blocker|nice_to_have|info] [--title T] [--file PATH] [--line N]
$BL review reply COMMENT_KEY --author A --action comment|fix|reject|accept --body B
$BL review reopen ROOT_KEY --author A --body B
$BL review severity ROOT_KEY --severity blocker|nice_to_have|info --author A
$BL review inbox [--actor X] [--role R] [--item KEY] [--severity blocker|nice_to_have|info]
$BL review thread ROOT_KEY [--full]
$BL review list KEY [--state open|closed|all] [--severity blocker|nice_to_have|info]
```

See [review.md](review.md).

## Artifacts

```bash
$BL artifact add KEY PATH [--title T] [--kind doc|spec|design|log|report|patch|data]
$BL artifact list KEY
```

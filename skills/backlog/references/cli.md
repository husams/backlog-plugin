# Command reference

```bash
BL=backlog            # on PATH under Claude Code; otherwise <skill>/bin/backlog
```

One command answers one question. When the answer has to be computed across
many tasks, use `backlog-py` instead — see [api.md](api.md).

`--json`, `--actor <name>` and `--project <slug>` are accepted on every command,
before or after the subcommand.

Keys are per project and case-insensitive: `F-001` features, `S-001` stories,
`B-001` standalone Bugs, `I-001` Iterations, `T-001` subtasks, and `C-001`
review comments. Retrospective improvement actions use `R-001`. A thread is
named by its root comment key.

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
| `$BL workflow upgrade` | add missing shipped task-type flows without replacing existing project-specific flows |

See [templates.md](templates.md).

## Reading

| Command | Purpose |
| --- | --- |
| `$BL board [--all] [--iteration I-001]` | open work grouped by status, or eligible member work from one Open Iteration |
| `$BL next [--actor X] [--iteration I-001]` | everything actionable for X, optionally limited to one Open Iteration |
| `$BL show KEY` | one task in full |
| `$BL list [filters]` | every task |
| `$BL feature list` / `story list` / `bug list` / `iteration list` / `subtask list` | one type |
| `$BL history KEY` | audit trail |

Filters: `--status S`, `--open`, `--assignee X`, `--reviewer Y`, `--parent KEY`,
`--type feature|story|bug|subtask|iteration`. An Iteration is listed in its
own board section, but it is not generic deliverable work for `next`.

## Creating and editing

```bash
$BL feature  add --title T --actor NAME [--description D] [--ac "..."] [--priority P0..P3] [--owner X]
$BL story    add --title T --actor NAME [--feature F-001] [--branch B] [...]
$BL bug      add --title T --actor NAME [--branch B] [...]
$BL iteration add --title T --actor NAME [--priority P0..P3] [...]
$BL iteration member-add I-001 S-001
$BL iteration member-remove I-001 S-001
$BL subtask  add (--story S-001 | --bug B-001) --title T --actor NAME [...]
$BL task     add --type feature|story|bug|subtask|iteration [--parent KEY] --title T --actor NAME [...]

$BL set KEY [--title|--description|--ac|--priority|--owner|--branch|--parent]
$BL assign KEY [--to X] [--reviewer Y] [--to-kind human|agent] [--reviewer-kind ...]
```

A subtask requires a story or Bug; a Story may hang off a Feature or stand
alone; Features, Bugs, and Iterations are roots. A Bug cannot have a Feature
parent. Only a Ready Story or standalone Ready Bug may be added to an Open
Iteration, and a member may belong to only one Open Iteration. Eligibility is
checked when adding; later member lifecycle changes do not remove it. A member
can be removed from an Open Iteration without deleting it or changing its
status. `next --iteration I-001` and `board --iteration I-001` select only
eligible member work from that Open Iteration; generic selection remains
unscoped. `--ac` replaces the acceptance criteria, one per line.
Assignee and reviewer names are free text — the human/agent kind is guessed
from the name and shown with a `*` on agents.

There is no agent-facing command that accepts a destination status. Status
changes only when a semantic action resolves through the configured workflow.

Iteration lifecycle actions are explicit facts:

```bash
$BL actions I-001
$BL action I-001 iteration.opened       # Planned -> Open
$BL action I-001 iteration.closed       # Open -> Closed, after both close gates
$BL action I-001 iteration.reopened     # Closed -> Open, unless membership conflicts
```

Closing requires every retained member to be in a finished status and every
Iteration review thread to be closed. Closing never changes member status.
Reopening is rejected if a retained member is already in another Open
Iteration, and the error names each conflict.

## Retrospective improvement actions

```bash
$BL retrospective add --iteration I-001 --issue "Repeated problem" \
  --solution "Proposed workflow improvement" [--title "Short label"] --actor facilitator
$BL retrospective list [--status created|ready|done|rejected] [--iteration I-001]
$BL retrospective show R-001
$BL retrospective accept R-001 --actor product-manager
$BL retrospective reject R-001 --reason "Why it will not be pursued"
$BL retrospective close R-001 --resolution-project PROJECT \
  (--feature F-001 | --bug B-001)
$BL retrospective history R-001
```

An action belongs to the selected project and must reference one of that
project's Iterations. Accept moves Created to Ready. Reject is available from
Created or Ready and retains the required reason. Close is available only from
Ready and retains the target project plus Feature or Bug; that target may be in
another project in the store. See [retrospectives.md](retrospectives.md).

All new task and retrospective-action creation commands require `--actor`.
The recorded creator cannot perform the corresponding acceptance, and an
attributed record cannot be accepted without an actor.

## Task items — criteria, checklists, notes

```bash
$BL item add KEY [--kind acceptance_criteria|checklist|note] --content "one per line"
$BL item set KEY --kind checklist --content "..."     # replace every entry of that kind
$BL item list KEY [--kind K]
$BL item check ID [--undo] [--waive-validation --reason TEXT]
$BL item rm ID
```

Add `--shell COMMAND` or `--hook NAME` to `feature add`, `story add`, `set`,
`item add`, or `item set` to attach an executor to one acceptance criterion or
checklist entry. Both accept `--requirement required|advisory` and
`--timeout SECONDS`.

Shell options are `--working-directory`, `--expected-exit-code`,
`--stdout-equals|--stdout-contains|--stdout-regex`, the corresponding
`--stderr-*` options, and repeatable `--env NAME`. Hook options are
`--arguments JSON` and `--expected-result JSON`. Executable input must contain
one non-empty line; existing multi-line plain syntax is unchanged.

`item list` and `show` display plain/shell/hook, required/advisory, and
`pending` before the first run. Commands, matcher values, hook arguments,
expected hook values, and environment values are hidden in text and JSON;
environment variable names remain visible.

## Status and gates

```bash
$BL action KEY ACTION [--operation NAME] [--parameter NAME=VALUE]
$BL actions KEY
$BL gate KEY --for start|in_review|accepted|done|merge [same waivers]
```

`action` resolves the destination from `.backlog/workflow.yaml`, or from the
bundled default when the project has no custom file. It runs project
`pre_transition` and `post_transition` hooks and enforces the configured gates.
`actions` lists only the semantic actions configured for that task type and
current state.

`action` exits `1` on an illegal transition or a failed gate. `gate` exits `0`
pass, `2` blocked, `1` command error. Both read this project's flow — see
[workflow.md](workflow.md).

For a Bug, inspect the dedicated Bug flow with `statuses --type bug`; it
mirrors the selected template's Story flow and gates. For example,
`software-delivery` applies PR/review gates, while `lightweight` has no PR or
review stage. Story/Bug delivery gates block on open `blocker` threads;
`nice_to_have` and `info` threads still require the normal response and
reviewer decision but do not block the Story/Bug delivery gate. For an
Iteration, `statuses --type iteration` shows the dedicated
`Planned -> Open -> Closed` flow and its `iteration_members_finished` and
`iteration_comments_closed` close gates. Iteration review feedback uses the
same `review open`, `review reply`, inbox, and audit commands as other tasks;
all comment severities block Iteration closure.

## Executable validation

```bash
$BL validation run ITEM_ID [--project-root DIR]
$BL validation run-all KEY [--project-root DIR] [--fail-fast]
$BL validation history ITEM_ID [--limit 20] [--project-root DIR]
$BL validation waive ITEM_ID --reason TEXT --actor NAME
```

Shell execution is disabled unless the trusted checkout contains
`.backlog/execution.yaml` with `shell_enabled: true`. One-item and batch
commands return exit `0` only when the item, or every required item, has a
current pass; they return `2` for pending, stale, fail, error, timeout, policy
denial, or batch-budget skip, and `1` for command errors. Batch execution runs
every shell or hook item in declaration order unless
`--fail-fast` is explicit. See
[the executable-item guide](../../../docs/executable-items.md).

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
$BL review audit ROOT
$BL review reopen ROOT_KEY --author A --body B
$BL review severity ROOT_KEY --severity blocker|nice_to_have|info --author A
$BL review inbox [--actor X] [--role R] [--item KEY] [--severity blocker|nice_to_have|info]
$BL review thread ROOT_KEY [--full]
$BL review list KEY [--state open|closed|all] [--severity blocker|nice_to_have|info]
```

See [review.md](review.md).

Review threads can be opened directly on an Iteration for retrospective
observations or unexpected behavior:

```bash
$BL review open I-001 --author reviewer --severity info \
  --body "The handoff exposed a missing release checklist item."
$BL review reply C-001 --author developer --action fix \
  --body "Added the checklist item and linked the follow-up."
```

Iteration feedback is lifecycle-neutral: `feedback.posted`,
`feedback.reopened`, and `feedback.resolved` do not open, close, or otherwise
change the Iteration. The `iteration_comments_closed` gate independently
requires every open blocker, nice-to-have, and info thread to be resolved
before `iteration.closed` can succeed. Closed Iterations keep their comments
visible for retrospective review.

## Artifacts

```bash
$BL artifact add KEY PATH [--title T] [--kind doc|spec|design|log|report|patch|data]
$BL artifact list KEY
```

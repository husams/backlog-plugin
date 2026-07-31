# Ready-made scripts

Four common requests, already written. Each runs in one process and prints a few
lines — no JSON, no files, nothing left behind.

```bash
backlog-py scripts/<name>.py [args]
```

Paths are relative to this skill's directory. Do not read the scripts; this page
is their documentation. If none of them fits, write a snippet against
[api.md](api.md) instead of editing these.

## standup.py — where am I, what is next

```bash
backlog-py scripts/standup.py --actor claude
```

Store, board counts, what the actor can start now, review threads waiting on
them, what is blocked and by what, and a warning if the dependency graph has a
cycle. Replaces the four-command opening sequence.

`--actor` optional (omit for the whole board), `--project` optional.

## start_work.py — pick a task up safely

```bash
backlog-py scripts/start_work.py S-004 --actor claude
```

Checks the start gate, submits `Action.WORK_STARTED`, then prints its acceptance
criteria, checklist and open subtasks. The configured action workflow chooses
the destination. Refuses with the reason instead of forcing:

```
refused: dependencies_clear: blocked by S-001=in_progress
```

Exit `0` started, `2` refused. There is no destination-status override.

## merge_check.py — is it safe to merge

```bash
backlog-py scripts/merge_check.py S-004
backlog-py scripts/merge_check.py --all
```

Runs the real merge gate over the named tasks, or every task in review, one line
each:

```
S-004  READY    all merge gates pass
S-007  BLOCKED  review_threads_closed: 1 open: C-009
```

Exit `0` when all are ready, `2` when any is blocked — same contract as
`backlog gate --for merge`.

## review_triage.py — what needs my reply

```bash
backlog-py scripts/review_triage.py --actor claude
```

Open threads waiting on the actor, oldest first, each with the root comment, the
latest reply, where it points, and the comment key to reply to. `--role
developer|reviewer` narrows it further.

Use this only for the session's initial, narrowly actor-scoped review read;
also pass `--role` when it narrows the request. Retain each thread's root and
returned `reply_to` key. For subsequent checks of known roots, do not run triage
or reload the inbox; call `bl.review_updates(ROOT, after=LAST_SEEN)` from a
short Python snippet, process every returned comment, and print only newly added
comments. Before handoff, a single final semantically scoped inbox discovery is
allowed only to identify previously unseen roots. See
[review.md](review.md#reading-reviews-strict-incremental-procedure) and
[api.md](api.md#incremental-review-reads).

Reply with the CLI: `backlog review reply C-003 --author claude --action fix
--body "..."`.

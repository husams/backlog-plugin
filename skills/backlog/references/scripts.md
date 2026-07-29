# Ready-made scripts

Five common requests, already written. Each runs in one process and prints a few
lines — no JSON, no files, nothing left behind.

```bash
backlog-py scripts/<name>.py [args]
```

Paths are relative to this skill's directory. Do not read the scripts; this page
is their documentation. If none of them fits, write a snippet against
[api.md](api.md) instead of editing these.

## change_status.py — move named work or mark it done

```bash
backlog-py scripts/change_status.py S-004 in_review --actor claude
backlog-py scripts/change_status.py F-002 T-009 --done --actor claude
```

Changes only the named features, stories, or subtasks. Every change goes through
the task type's configured flow and gates. `--done` resolves that flow's single
terminal status; it does not skip intermediate states. Exit `0` when every move
succeeds, `2` when any is refused. Add `--reason "..."` to the audit entry.

This is for a small, explicit set of task keys. For a computed batch, filter and
move through the public API documented in [api.md](api.md).

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

Checks the start gate, moves the task, then prints its acceptance criteria,
checklist and open subtasks. Refuses with the reason instead of forcing:

```
refused: dependencies_clear: blocked by S-001=in_progress
```

Exit `0` started, `2` refused. `--status` overrides the target (default
`in_progress`).

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

Reply with the CLI: `backlog review reply C-003 --author claude --action fix
--body "..."`.

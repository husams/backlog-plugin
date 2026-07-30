# Planning and grooming

## Shape

```
Feature  F-001   a planned capability, a container
  Story  S-001   a user-visible slice, carries the PR
   Task  T-001   a subtask of one story, inside that PR
```

All three are rows in `task`, told apart by `task_type`. A story may stand
alone without a feature; a subtask always belongs to a story.

## Filing a feature and its stories

```bash
$BL feature add --title "Incremental reindex" --priority P1 --owner architect \
  --description "Reindex only translation units whose inputs changed."

$BL story add --feature F-001 --title "Detect stale translation units" --priority P1 \
  --description "Compare recorded input hashes against the working tree." \
  --ac "Given an unchanged TU, when reindex runs, it is skipped.
Given a changed header, when reindex runs, every dependent TU is rebuilt.
cidx index --incremental exits 0 and reports the skipped count."

$BL subtask add --story S-001 --title "Record per-TU input hashes at index time"
$BL subtask add --story S-001 --title "Add --incremental to cidx index"
```

Every new task starts in **its flow's initial status** — `Created` on the
shipped flow, but `Proposed` on the `research` template. Check with
`$BL statuses --type story` rather than assuming.

## Acceptance criteria, checklists and notes

`--ac` on create is shorthand; each line becomes its own row, so criteria can
be listed, replaced and reasoned about individually.

```bash
$BL item list S-001
$BL item add S-001 --kind checklist --content "wire the route
add the regression test"
$BL item check 7                      # tick a checklist entry
$BL item add S-001 --kind note --content "Upstream changed the header layout in 18.1"
$BL item set S-001 --kind acceptance_criteria --content "..."   # replace them all
```

Only checklist entries are tickable. Acceptance criteria are proven by review,
not by a tick — that is what the `review_threads_closed` gate is for.
Passing a required executable checklist checks it automatically. A failed,
stale, or pending executable checklist cannot be checked manually without
`--waive-validation --reason TEXT --actor NAME`; the waiver is audited.

One criterion or checklist entry may declare a shell or hook executor:

```bash
$BL item add S-001 --kind acceptance_criteria --content "unit tests pass" \
  --shell "python -m unittest" --stdout-contains "OK"
$BL item add S-001 --kind checklist --content "release policy passes" \
  --hook checks.release --arguments '{"channel":"stable"}' \
  --expected-result true --requirement advisory
```

The same execution flags work with feature/story creation and `item set`.
Existing plain and multi-line forms remain unchanged.

## Grooming

Everything starts in the initial status. Grooming decides which way it goes:

```bash
$BL action S-001 refinement.accepted --actor product-manager

# Under-specified: park it and say what is missing
$BL action S-002 refinement.marked_incomplete --actor business-analyst \
    --parameter reason="No criteria for the failure path"
$BL item set S-002 --kind acceptance_criteria --content "..."
$BL action S-002 refinement.accepted --actor business-analyst
```

Write the criteria before a task leaves the backlog. If an action is refused,
the message names the failed transition or gate.

## Assignment, and who is an agent

```bash
$BL assign S-001 --to claude --reviewer husam
$BL assign S-004 --to codex --reviewer senior-developer
$BL assign S-005 --to sam --to-kind human
```

Names stay free text; the store also records whether each name is a **human**
or an **agent**, guessed from the name and overridable with `--to-kind` /
`--reviewer-kind`. Listings mark agents with a `*`, so a board shows at a
glance what is being done by whom.

Assign both sides early: review-role inference and `review inbox --actor X`
depend on `assignee` and `reviewer` being set.

## Picking up work

```bash
$BL next --actor claude
```

Returns, in order: review threads waiting on you, work assigned to you that is
**startable**, work that is **blocked** and by what, items awaiting your
review, items whose Accepted gate now passes, and items whose PR is merged and
can move to Done.

## Priorities and ordering

`P0` (drop everything) … `P3` (whenever). Listings and `next` sort by priority
then key.

Priority says *how much it matters*; a dependency says *what has to come
first*. Do not encode sequencing in priorities — record it:

```bash
$BL dep add S-001 --blocks S-002 --note "S-002 reads the hashes S-001 records"
```

See [dependencies.md](dependencies.md).

## Splitting a task

A story is too big when its subtasks would need their own PRs. Prefer several
stories under one feature over one story with many subtasks: a story carries
its own PR, review threads and gates; subtasks track work inside a single PR.

A parent cannot be Accepted while a child is unfinished (`children_complete`).
If a subtask turns out to be unnecessary, close it honestly rather than waiving
the gate:

```bash
$BL action T-004 item.cancelled --parameter reason="Superseded by T-006"
```

## More than one project

```bash
$BL projects
$BL project add --name "Trading engine" --slug trading --template lightweight
$BL --project trading board
```

Each project keeps its own key sequence, its own flow, and its own tasks.

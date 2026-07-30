# Dependencies

A dependency is an edge between two tasks. Both ends are `task.id`, so a
feature blocking a story is the same row shape as a subtask blocking a subtask
— the database keeps the endpoints honest and "this story is blocked by that
feature" is expressible directly.

| Kind | Meaning | Gate? |
| --- | --- | --- |
| `blocks` | the source must finish before the target may **start** | yes |
| `relates` | soft association, no ordering (stored once, either direction) | no |
| `duplicates` | the source duplicates the target | no |

## Recording

```bash
$BL dep add S-004 --blocked-by S-002 --note "needs the session table"
$BL dep add S-002 --blocks S-004        # the same edge, said the other way
$BL dep add S-004 --blocked-by F-001    # a story waiting on a whole feature
$BL dep add S-004 --relates S-009
$BL dep add S-011 --duplicates S-004
$BL dep rm  S-002 --blocks S-004
```

An edge is stored once. `--blocks` and `--blocked-by` are two ways of naming the
same edge, so adding both is a no-op, not a duplicate. Give `--note` a reason —
future agents cannot re-derive it.

## What a `blocks` edge does

A blocker stops blocking once it reaches a status its flow marks *counts as
finished* — `Accepted` and `Done` on the shipped flow, whatever a custom flow
declares elsewhere. Until then:

- `action KEY work.started` fails with `FAIL dependencies_clear`
- `next` moves the item out of *WORK TO DO* into a **BLOCKED** section
- `board` tags it `[blocked by S-002]`
- `show KEY` lists both directions under `dependencies:`

```bash
$BL dep check S-004        # exit 0 = startable, exit 2 = blocked
$BL gate S-004 --for start # the same check in gate form
```

Nothing blocks grooming: `refinement.accepted` can still select **Ready** while blocked.
Only starting the work is gated, because that is the decision the dependency
exists to prevent.

To start anyway — because the blocker turned out to be irrelevant, or the work
genuinely overlaps — waive it explicitly rather than deleting the edge:

```bash
$BL action S-004 work.started --allow-blocked
```

`doctor` reports anything that is started while still blocked,
so a waiver stays visible instead of quietly becoming the norm.

## Cycles

A `blocks` edge that would close a loop is refused at insert time, naming the
loop:

```
error: that edge would create a dependency cycle: S-001 -> S-002 -> S-001
```

`dep graph` re-checks the whole graph, and `doctor` fails if a cycle ever
appears (an imported one, for instance).

## Reading the graph

```bash
$BL dep list S-004                 # both directions for one key
$BL dep list --kind blocks         # every blocking edge in the project
$BL dep graph                      # edges, who is currently blocked, cycles
$BL dep graph --format dot | dot -Tsvg -o deps.svg
$BL dep graph --format json
```

## Ordering a feature's stories

Prefer a chain of `blocks` edges over cramming sequencing into priorities:
priority says *how much it matters*, a dependency says *what has to come
first*. `next` then only ever offers work that is genuinely startable.

```bash
$BL dep add S-002 --blocks S-003     # contract before extraction
$BL dep add S-003 --blocks S-004     # extraction before the query surface
$BL next --actor developer           # offers S-002 only
```

Dependencies survive `export` / `import`.

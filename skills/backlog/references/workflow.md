# Status flow and gates

**The flow is data, not something you remember.** Each project holds one
workflow per task type — its statuses, the legal moves between them, and which
gate checks each move demands. Two projects can run completely different flows.
Read the one in front of you before moving anything:

```bash
$BL statuses                       # all three task types
$BL statuses --type story          # one
$BL workflow show --type story     # the same table
```

Example output — the shipped `software-delivery` story flow:

```
STATUS       SLUG         CATEGORY  FLAGS               LEGAL NEXT (gates)
Created      created      backlog   initial             In-complete, Ready
In-complete  incomplete   backlog                       Accepted, Ready
Ready        ready        ready                         In Progress (dependencies_clear)
In Progress  in_progress  active                        In Review (pr_recorded)
In Review    in_review    review                        Accepted (review_threads_closed +
                                                          pr_approved + children_complete),
                                                        Need work
Need work    needs_work   active                        In Review (pr_recorded)
Accepted     accepted     done      counts as finished  Done (pr_merged)
Done         done         done      counts as finished, terminal
```

## Moving

```bash
$BL move <KEY> <status> --actor <you> [--reason "why"]
```

Status names are forgiving — slug, display name, any casing. A move that the
flow does not allow **exits 1** and names what is legal instead:

```
error: illegal transition for S-001 (a story): In Review -> Done.
Legal next states from In Review: Accepted, Need work
(`backlog workflow show --type story` prints this project's flow)
```

That refusal is the CLI reading `workflow_transition`, not a convention. Do not
route around it — change the flow if the flow is wrong (see
[templates.md](templates.md)).

## Gates

A transition row may name gate checks. They run when you make that move, and a
failure **exits 1** listing which check failed and why.

| Gate | Passes when |
| --- | --- |
| `dependencies_clear` | nothing that `blocks` this task is still open |
| `children_complete` | every child task has reached a status marked *counts as finished* |
| `review_threads_closed` | no `blocker` review thread on the task is open |
| `pr_recorded` | a pull request is referenced |
| `pr_approved` | `pr_review_state` is `approved` |
| `pr_merged` | `pr_state` is `merged` |

`$BL workflow gates` prints this list live.

A feature carries no pull request of its own, so the three PR gates report
*"not applicable to a feature"* rather than failing — a container is finished
when its children are.

### Checking without moving

```bash
$BL gate <KEY> --for merge      # 0 allowed, 2 blocked, 1 command error
$BL gate <KEY> --for start|in_review|accepted|done
$BL dep check <KEY>             # 0 startable, 2 blocked
```

`--for merge` is the one to run before merging anything: exit `2` means **do
not merge**, and the output names each failing check.

### Waiving one

Three overrides exist, and each is a decision you should be able to defend:

```bash
$BL move S-004 in_review --no-pr                 # genuinely ships without a PR
$BL move S-004 in_progress --allow-blocked       # the blocker turned out irrelevant
$BL move S-004 accepted --allow-open-subtasks    # a child is not needed
```

`--no-pr` records a waiver on the task and relaxes the later PR gates too;
recording a real PR afterwards cancels it. `doctor` reports a task that is In
Progress while still blocked, so an override stays visible instead of quietly
becoming the norm.

## Categories

Every status carries a category, which is how the tool reasons about a status
it has never seen:

| Category | Meaning |
| --- | --- |
| `backlog` | filed, not started |
| `ready` | groomed, safe to start |
| `active` | someone is working on it |
| `review` | with a reviewer |
| `done` | finished |
| `dropped` | closed without delivering |

`board` groups by the workflow's own order, and anything in `done`/`dropped` is
hidden unless you pass `--all`. Whether a task stops blocking its dependents is
a per-status flag (*counts as finished*), not a hardcoded list — so a project
that adds its own terminal status is understood without a code change.

## Keeping PR state current

```bash
$BL pr set <KEY> --url <URL> --state open --review-state pending
$BL pr set <KEY> --review-state approved
$BL pr set <KEY> --state merged
$BL pr sync <KEY>                # or pull it all from `gh`
```

- `--state`: `none | draft | open | merged | closed`
- `--review-state`: `none | pending | changes_requested | approved`

A feature rejects `pr set` outright: a pull request belongs to a story or a
subtask.

## Closing the loop

A task is closed only when its flow says so, and the CLI enforces every step.
On the shipped flow that means: every review thread closed and accepted, the PR
approved, the PR merged, and every child finished. Then and only then does
`move ... done` succeed, and `closed_at` is stamped.

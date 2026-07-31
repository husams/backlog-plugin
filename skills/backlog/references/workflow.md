# Status flow and gates

**The flow is data, not something you remember.** Each project holds one
workflow per task type — its statuses, action-driven transitions, and the gate
checks each transition demands. Two projects can run completely different
flows. Inspect the task's available actions before changing workflow state:

```bash
$BL statuses                       # all five task types
$BL statuses --type story          # one
$BL workflow show --type story     # the same table
$BL actions S-001                  # actions valid from this task's current state
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

## Submitting actions

```bash
$BL actions <KEY>
$BL action <KEY> <ACTION> --actor <you> \
    [--operation <source>] [--parameter name=value]
```

Agents cannot supply a destination status. The action configuration resolves
`(task type, current state, action)` to a destination. The transition engine
then runs the project hooks, validates the configured gates, and applies the
result. An illegal or blocked transition exits `1` and explains why.

Do not infer a destination and do not call private transition internals. If the
required action is absent from `$BL actions KEY`, either the event does not
change state from here or the project's workflow configuration must be changed
(see [templates.md](templates.md)).

## Gates

A transition row may name gate checks. They run when an action resolves to that transition, and a
failure **exits 1** listing which check failed and why.

| Gate | Passes when |
| --- | --- |
| `dependencies_clear` | nothing that `blocks` this task is still open |
| `children_complete` | every child task has reached a status marked *counts as finished* |
| `review_threads_closed` | no `blocker` review thread on the task is open |
| `pr_recorded` | a pull request is referenced |
| `pr_approved` | `pr_review_state` is `approved` |
| `pr_merged` | `pr_state` is `merged` |
| `required_validations_pass` | every required executable item has a current pass or audited waiver |
| `iteration_members_finished` | every Iteration member has reached a finished status |
| `iteration_comments_closed` | every Iteration review thread is closed |

The shipped Iteration flow is `Planned -> Open -> Closed`, driven by
`iteration.opened`, `iteration.closed`, and `iteration.reopened`. Closing never
changes member status; reopening is rejected when membership conflicts with
another Open Iteration.

## Bug and Iteration flows

Bugs use the dedicated Bug flow, which mirrors the Story delivery lifecycle:

```text
Created -> Ready -> In Progress -> In Review -> Accepted -> Done
    \-> Incomplete -> Ready       \-> Needs Work -> In Progress
```

`statuses --type bug` and `bl.flow(task_type="bug")` expose the Bug statuses.
The Bug transitions use the same semantic refinement, work, review, PR, child,
validation, dependency, and delivery actions/gates as a Story. A Bug is still
a standalone root and cannot have a Feature parent.

Iterations have a separate lifecycle:

| Current | Action | Next | Gates |
| --- | --- | --- | --- |
| `Planned` | `iteration.opened` | `Open` | — |
| `Open` | `iteration.closed` | `Closed` | `iteration_members_finished` + `iteration_comments_closed` |
| `Closed` | `iteration.reopened` | `Open` | `iteration_members_finished` |

The Iteration row is not deliverable work: it appears in its own board section,
is included in task-type counts, and is excluded from generic `startable()` and
`next` results. An explicit Iteration selector returns only eligible member
work. Multiple Iterations may be Open concurrently.

Iteration review events are intentionally lifecycle-neutral. The review system
records `feedback.posted`, `feedback.reopened`, and `feedback.resolved` as
self-transitions in `Planned`, `Open`, and `Closed`; they do not invoke a
Story-style start gate or change Iteration state. Closure still has its own
`iteration_comments_closed` gate, which treats every open comment severity as
blocking and reports the blocking thread keys.

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
$BL action S-004 review.submitted --no-pr
$BL action S-004 work.started --allow-blocked
$BL action S-004 review.approved --allow-open-subtasks
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

A feature rejects `pr set` outright: a pull request belongs to a story, bug,
or subtask.

## Closing the loop

A task is closed only when its action flow says so, and the CLI enforces every step.
For a Story or Bug on the shipped delivery flow that means: every review thread
closed and accepted, the PR approved, the PR merged, and every child finished.
For an Iteration, the configured close action additionally requires every
retained member to be finished and every Iteration review thread of any severity
to be closed; the member statuses are not changed by closing. Then and only
then does the completion action resolve to the terminal state and stamp
`closed_at`.

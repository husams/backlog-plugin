# Templates and custom flows

A **template** is the pre-defined shape a project is built from: one workflow
per task type, with its statuses and transitions. Creating a project *copies*
the template into the project's own rows — so the project can then adapt its
flow without disturbing the template, and revising a template never rewrites a
project already running on it.

Nobody hand-builds a flow. A project always has one.

## What ships

```bash
$BL templates
```

| Template | Flow |
| --- | --- |
| `software-delivery` *(default)* | Features, stories, Bugs, and subtasks follow Created → Ready → In Progress → In Review → Accepted → Done, with an Incomplete refinement path and a Needs Work implementation-review loop. Stories, Bugs, and subtasks enforce PR gates. Iterations follow Planned → Open → Closed. |
| `lightweight` | Features, stories, Bugs, and subtasks follow Created → Ready → In Progress → Done (+ Dropped), with no review stage or PR gates. Iterations follow Planned → Open → Closed. |
| `research` | Features, stories, Bugs, and subtasks follow Proposed → Investigating → Drafted → Reviewed → Published (+ Parked). Iterations follow Planned → Open → Closed. |

They install themselves on first use and are editable and copyable like any
template you write.

Every shipped template has a dedicated flow for both new task types. Inspect
the active project or a template directly instead of assuming the Story flow:

```bash
$BL statuses --type bug
$BL statuses --type iteration
$BL template show software-delivery --type bug
$BL template show software-delivery --type iteration
```

Bugs follow that template's Story-shaped deliverable flow and inherit its
gates: `software-delivery` includes PR/review gates, while `lightweight` has
no PR or review stage. Iterations follow `Planned -> Open -> Closed` with
`iteration.opened`, `iteration.closed`, and `iteration.reopened`; closing uses
`iteration_members_finished` and `iteration_comments_closed`, while reopening
uses `iteration_members_finished`. The Iteration feedback actions
`feedback.posted`, `feedback.reopened`, and `feedback.resolved` are explicit
self-transitions in every Iteration state, so retrospective comments do not
change lifecycle state.

The action-driven workflow also ships a file-based default at
`assets/default-workflow.yaml`. When a project has no
`.backlog/workflow.yaml`, the action workflow loader uses that bundled file.
A project workflow replaces the default as a complete definition; the two are
not merged.

```bash
$BL template show research --type story
```

## Creating a project from one

```bash
$BL project add --name "Paper study" --slug paper --template research
$BL --project paper statuses --type story
```

Tasks created in that project then start in the template's initial status —
`Proposed`, not `Created` — and follow its transitions. Nothing else changes.

Without `--template` a project uses the default; `$BL template default <slug>`
changes which that is.

## Upgrading an existing project

Projects created by an older release can add newly shipped task-type flows with:

```bash
$BL workflow upgrade
```

The operation is idempotent and non-destructive: it copies only flows missing
from the project and never replaces project-specific statuses, transitions, or
gates. Run it again safely after upgrading the backlog skill; a project that is
already current reports that no flow changed. `backlog doctor` recommends this
command when tasks exist without their required flow.

## Adjusting one project's flow

Edits here affect only this project:

```bash
$BL workflow status-add --type story --slug qa --display "In QA" \
      --category review --after in_review
$BL workflow move-add --type story --from in_review --to qa --gate pr_approved
$BL workflow move-add --type story --from qa --to accepted \
      --gate review_threads_closed,children_complete
$BL workflow move-rm  --type story --from in_review --to accepted
```

The story flow now routes through QA. Actions must follow that configured path;
an action resolving directly from In Review to Accepted is refused. Removing a
status that tasks are currently in is refused too — transition them first.

Useful flags on `status-add`:

- `--category backlog|ready|active|review|done|dropped` — where it sits on the
  board and whether work in it counts as started
- `--satisfies` — a task in this status stops blocking its dependents
- `--terminal` — nothing follows it
- `--after <status>` — where to place it in the order

Back out with:

```bash
$BL workflow reset [--type story]        # back to this project's template
$BL workflow apply --template lightweight   # switch template entirely
$BL workflow copy --from <other-project>    # adopt another project's flow
```

## Authoring a template

Tune one project until the flow is right, then capture it:

```bash
$BL template add --slug academic --name "Academic" --from-project paper
```

Or build from an existing one:

```bash
$BL template add --slug hardened --copy-of software-delivery
$BL template status-add hardened --type story --status security_review \
      --display "Security review" --category review --after in_review
$BL template move-add hardened --type story --from in_review --to security_review
$BL template move-add hardened --type story --from security_review --to accepted \
      --gate review_threads_closed,pr_approved
$BL template default hardened      # new projects use it from now on
```

A template with projects attached cannot be removed — that would erase the
record of where their flow came from.

## Which gates you can attach

`dependencies_clear`, `children_complete`, `review_threads_closed`,
`pr_recorded`, `pr_approved`, `pr_merged`. `$BL workflow gates` explains each.

The names are fixed because each one is a piece of code; *which* of them guard
*which* move is a row you control. That is what keeps the rules enforced by the
tool rather than by whoever is reading the docs.

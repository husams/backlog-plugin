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
| `software-delivery` *(default)* | Created → Ready → In Progress → In Review → Accepted → Done, with the PR gates. Features are containers and carry no PR. |
| `lightweight` | Created → Ready → In Progress → Done (+ Dropped). No review stage, no PR gates. |
| `research` | Proposed → Investigating → Drafted → Reviewed → Published (+ Parked). |

They install themselves on first use and are editable and copyable like any
template you write.

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

The story flow now routes through QA, and `move S-001 accepted` from In Review
is refused. Removing a status that tasks are currently in is refused too —
move them first.

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

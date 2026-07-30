# GitHub Actions integration

Backlog can follow the normal GitHub pull-request lifecycle. The example
workflow records pull-request information and requests status transitions when:

- a pull request is opened or becomes ready for review;
- a reviewer approves it or requests changes;
- the `CI` workflow fails, times out, or is cancelled;
- the pull request is merged or closed.

Every requested status change goes through `backlog move`. The active project
workflow and its gates remain authoritative. If GitHub requests an illegal
transition, the command refuses it and the workflow run fails visibly.

## Install the example

Copy [examples/github-actions.yml](examples/github-actions.yml) into the
project repository:

```text
.github/workflows/backlog.yml
```

The workflow downloads Backlog from its GitHub repository at runtime. It does
not check out or execute code from the pull request.

## Configure the repository

Add these GitHub repository settings:

| Type | Name | Value |
| --- | --- | --- |
| Actions secret | `BACK_LOG_URL` | PostgreSQL connection URL for the shared backlog |
| Actions variable | `BACKLOG_PROJECT` | Backlog project slug |

The example uses PostgreSQL because independent GitHub runners need a shared
store. Repository-local SQLite would require safely committing a changed
database back to the repository and coordinating concurrent workflow runs.

Edit the four status variables in the workflow to match the project:

```yaml
BACKLOG_REVIEW_STATUS: in_review
BACKLOG_NEEDS_WORK_STATUS: needs_work
BACKLOG_ACCEPTED_STATUS: accepted
BACKLOG_DONE_STATUS: done
```

If the project's CI workflow is not named `CI`, change:

```yaml
workflow_run:
  workflows:
    - CI
```

## Name branches with a backlog key

The example extracts the feature, story, or subtask key from the pull-request
branch:

```text
S-004-add-cache
feature/S-004-add-cache
T-012/write-tests
```

A workflow run fails with a clear message when the branch has no backlog key.

## Event mapping

| GitHub event | Backlog operation |
| --- | --- |
| PR opened, reopened, synchronized, or ready | record PR as open; request `in_review` |
| PR converted to draft | record PR as draft |
| Review approved | record approval; request `accepted` |
| Review requests changes | record changes requested; request `needs_work` |
| Review dismissed | reset review state to pending |
| CI failed, timed out, or cancelled | request `needs_work` |
| CI passed | no transition by default |
| PR merged | record merged; request `done` |
| PR closed without merge | record closed |

The same event-to-status mapping can be changed without bypassing project
policy. Backlog still validates each destination against the task's current
state, configured transition, and gates.

## Security

The workflow uses `pull_request_target` for pull-request lifecycle events so it
can update a shared backlog for pull requests from forks. Never add a step that
checks out, downloads, or executes the pull request's code in this workflow.

GitHub does not pass Actions secrets to ordinary workflows triggered from
forked pull requests. Review and CI events from forks may therefore need an
organization-specific trusted workflow or GitHub App if they must update the
shared backlog.

Give the PostgreSQL account only the access required for its Backlog database.
Do not print `BACK_LOG_URL` or pass it as a command-line argument.

## Action-based API

The current example uses the existing `pr set` and `move` interfaces. After the
proposed action API is implemented, the mapping can emit semantic actions such
as:

```python
bl.trigger(task_key, Action.PR_CREATED, trigger=github_event)
bl.trigger(task_key, Action.REVIEW_APPROVED, trigger=github_event)
bl.trigger(task_key, Action.CHECK_FAILED, trigger=github_event)
bl.trigger(task_key, Action.PR_MERGED, trigger=github_event)
```

The workflow would no longer select destination statuses. The project workflow
would map each action and current state to the destination, then run
`pre_transition` and `post_transition`.

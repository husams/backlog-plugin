# Backlog

Backlog is a self-hosted backlog system for Codex and Claude Code. It gives
agents a shared, structured place to plan work, track activity, and move work
through project-defined delivery flows.

The plugin manages:

- features, stories, standalone bugs, subtasks, and parallel Iterations;
- retrospective actions for repeated workflow issues and proposed improvements;
- acceptance criteria, checklists, notes, flat ordered todos, priorities, and assignments;
- dependencies and blocked work;
- review threads with fixed blocker, nice-to-have, or informational severity,
  pull request state, and attached artifacts;
- custom statuses, transitions, gates, and reusable workflow templates;
- audit history for changes made by humans and agents;
- optional typed shell/hook validation declarations governed by trusted local
  project policy.

Workflow rules are enforced by the tool rather than left to the agent. Every
status change is checked against the selected project and task type. Illegal
transitions and failed gates are refused, so a custom flow remains consistent
regardless of which agent is operating it.

Implementation todos are lighter than subtasks: they have no assignment,
workflow, review, or pull request of their own. They stay in stable order on
their task, preserve attributed open/closed changes, and the shipped workflow
refuses review submission until every todo is closed.

Only unresolved review threads with `ReviewSeverity.BLOCKER` stop acceptance
or merge gates. Advisory and informational threads do not invalidate Ready,
but they must still receive a response and reviewer decision before acceptance.

Review threads have their own enforced lifecycle. Developers submit fixes;
reviewers accept them. Individual replies never transition the task, and all
review feedback must receive a developer response and an opening-reviewer
decision before acceptance. After every blocker is reviewer-accepted, the
review subsystem emits the
aggregate `feedback.resolved` workflow action.

If a reviewer opens a new blocker or reopens an accepted blocker with a reply,
the shipped workflow invalidates readiness and transitions `Ready` back to
`Incomplete`. The Python API exposes this as `review_reopen(...)`.

The generic skill is self-contained under `skills/backlog/`. Independent review
work is handled by the companion `skills/backlog-reviewer/` skill, which owns
incremental review decisions and merge-readiness handoff while the generic
skill remains responsible for ordinary backlog operations.

## Documentation

- [Example agent prompts](docs/prompts.md)
- [GitHub Actions integration example](docs/github-actions.md)
- [Python transition hooks and action workflow](docs/python-extensions.md)
- [Executable item schema and local policy](docs/executable-items.md)
- [Retrospective improvement actions](skills/backlog/references/retrospectives.md)

## Requirements

- Codex or Claude Code
- [uv](https://docs.astral.sh/uv/)

The first launcher invocation creates the skill's Python environment. The
PostgreSQL driver is installed automatically when PostgreSQL is selected.

## Install for Codex

### Install from the command line

Register the GitHub repository as a marketplace, then install the plugin:

```bash
codex plugin marketplace add husams/backlog-plugin
codex plugin add backlog-plugin@backlog-plugin
```

Start a new Codex session after installation. The plugin is installed and
enabled automatically.

To inspect or manage it interactively instead, start Codex and open the plugin
browser:

```text
/plugins
```

Select the **Backlog Plugin** marketplace and install or enable
`backlog-plugin`.

### Install from Codex desktop

Codex Desktop cannot currently register an arbitrary GitHub repository from
the Plugins screen. A UI-only installation is available only when the plugin
already appears in the public plugin directory, a workspace marketplace, or
**Personal → Shared with me**.

This plugin is currently distributed from GitHub and is not listed in the
public plugin directory. Therefore, first register its GitHub marketplace using
the command-line instructions above. After registration:

1. Restart Codex Desktop.
2. Select **Codex** and open **Plugins**.
3. Open **Personal**, then select the **Backlog Plugin** marketplace.
4. Open **Backlog** and select the **+** button to install it.
5. Start a new Codex task so the bundled skill is loaded.

If another user shares the plugin with you through Codex, open
**Plugins → Personal → Shared with me**, select **Backlog**, and use the **+**
button. No terminal command is needed for a shared or publicly listed plugin.

A GitHub repository link by itself is not an install link in Codex Desktop.

### Install from a local checkout

For plugin development without GitHub:

```bash
./skills/backlog/bin/install.sh
```

Restart Codex and start a new task after running the helper.

## Install for Claude Code

To try the plugin directly from its checkout:

```bash
claude --plugin-dir /absolute/path/to/backlog-plugin
```

To install it from GitHub, run these commands inside Claude Code:

```text
/plugin marketplace add husams/backlog-plugin
/plugin install backlog-plugin@backlog-marketplace
/reload-plugins
```

The skill is namespaced as `/backlog:backlog`. Its launcher paths are relative
to the installed skill directory, so the plugin does not require a root
executable directory or host-specific paths.

Alternatively, `./skills/backlog/bin/install.sh` installs it as a regular Claude
Code skill at:

```text
~/.claude/skills/backlog
```

## Configure the database

Backlog supports SQLite and PostgreSQL. Select the backend with `BACKLOG_DB` and
provide an optional location with `BACK_LOG_URL`.

### Repository-local SQLite

This is the simplest setup. The database is stored at
`<repository>/.backlog/backlog.db`.

```bash
export BACKLOG_DB=sqlite
unset BACK_LOG_URL
backlog-py scripts/setup_database.py
```

Under Codex, use the full launcher path:

```bash
BACKLOG_DB=sqlite \
  ~/.codex/skills/backlog/bin/backlog-py \
  ~/.codex/skills/backlog/scripts/setup_database.py
```

The generated `.backlog/` directory can be committed with the project.

### SQLite at an explicit location

```bash
export BACKLOG_DB=sqlite
export BACK_LOG_URL=sqlite:///absolute/path/backlog.db
backlog-py scripts/setup_database.py
```

### Shared PostgreSQL

```bash
export BACKLOG_DB=postgres
export BACK_LOG_URL=postgresql://backlog@db.internal/backlog
export BACKLOG_SCHEMA=backlog
backlog-py scripts/setup_database.py
```

Prefer PostgreSQL credential files or `PG*` environment variables instead of
putting passwords in command history. The setup script is idempotent: it
creates missing tables, applies migrations, installs workflow templates, and
creates the selected project.

Useful optional variables:

| Variable | Purpose |
| --- | --- |
| `BACKLOG_PROJECT` | Select a project; defaults to the repository directory name |
| `BACKLOG_SCHEMA` | Select the PostgreSQL schema; defaults to `backlog` |
| `BACKLOG_DIR` | Override the repository-local `.backlog` directory |
| `BACKLOG_ARTIFACTS` | Override where attached artifact files are stored |

Verify the configured store:

```bash
backlog where
backlog doctor
```

## Use the backlog

Agents load the skill automatically when asked to plan work, inspect the
backlog, find blocked tasks, change status, manage reviews, or check workflow
gates.

Common commands:

```bash
backlog board
backlog next --actor codex
backlog show S-001
backlog statuses --type story
```

Create a feature, story, and subtask:

```bash
backlog feature add --title "Account recovery" --actor product-manager
backlog story add --feature F-001 --title "Request a recovery link" \
  --ac "A valid account can request a time-limited recovery link." \
  --actor business-analyst
backlog subtask add --story S-001 --title "Add the recovery endpoint" --actor codex
backlog bug add --title "Recovery link expires too early" --actor business-analyst
backlog iteration add --title "July delivery slice" --priority P1 --actor product-manager
backlog action B-001 refinement.accepted --actor product-manager
backlog action I-001 iteration.opened --actor product-manager
backlog iteration member-add I-001 B-001
backlog next --actor codex --iteration I-001
# After the member finishes and Iteration comments are resolved:
backlog action I-001 iteration.closed --actor product-manager
```

Every new task requires `--actor NAME` and records that identity as
`created_by`; the same actor cannot submit `refinement.accepted`, and actor-less
acceptance is refused. Ready therefore requires an independent product manager,
business analyst, or reviewer. Only migrated legacy rows whose creation event
had no actor remain unattributed.

Record and resolve a workflow improvement discovered during the Iteration:

```bash
backlog retrospective add --iteration I-001 --actor facilitator \
  --issue "Release checks were skipped repeatedly" \
  --solution "Add a release-check skill and CI gate"
backlog retrospective accept R-001 --actor product-manager
backlog retrospective close R-001 --resolution-project agent-tooling \
  --feature F-003 --actor product-manager
```

New retrospective actions also require a creator identity. Their creator
cannot accept them, and acceptance without an actor is refused.

Task keys identify their type: `F-` Feature, `S-` Story, `B-` standalone Bug,
`T-` subtask, and `I-` Iteration. Bugs follow the Story-shaped delivery flow
without a Feature parent. Iterations group Ready Stories and standalone Ready
Bugs, expose their member work through `--iteration`, and close only after
members are finished and every Iteration review comment is resolved. See the
[planning](skills/backlog/references/planning.md),
[CLI](skills/backlog/references/cli.md), and
[Python API](skills/backlog/references/api.md) references for the full agent
interface. Retrospective actions use project-local `R-` keys and follow the
fixed Created → Ready → Done/Rejected lifecycle documented in the
[retrospective guide](skills/backlog/references/retrospectives.md).

Inspect and submit semantic workflow actions:

```bash
backlog actions S-001
backlog action S-001 refinement.accepted --actor product-manager
backlog actions S-001
backlog action S-001 work.started --actor codex
```

Agents cannot supply a destination status. The action workflow selects the
destination and enforces gates and hooks. The predefined start helper follows
the same contract:

```bash
backlog-py scripts/start_work.py S-001 --actor codex
```

## Custom workflows

Each project can define separate flows for features, stories, bugs, subtasks, and Iterations. A
flow controls:

- available statuses and their display order;
- legal transitions between statuses;
- initial and terminal states;
- conditions that must pass before a transition;
- whether a completed state stops blocking dependent work.

Inspect and manage flows with:

```bash
backlog statuses
backlog workflow show --type story
backlog workflow status-add --type story --slug qa --display "QA" \
  --category review --after in_review
backlog workflow move-add --type story --from in_review --to qa
backlog workflow move-add --type story --from qa --to accepted \
  --gate review_threads_closed,pr_approved
```

Reusable templates can seed the same workflow into multiple projects. The CLI
validates every transition against the active project's stored flow.

## Python API

Use `backlog-py` when an answer requires filtering, counting, comparing, or
updating a computed set of tasks. Computation happens inside one Python process
so the agent does not need to retrieve and reason over a large structured
dataset.

```bash
backlog-py <<'PY'
from backlog_cli import api

with api.open() as backlog:
    active = backlog.tasks(status="in_progress")
    print(f"{len(active)} tasks in progress")
PY
```

Todo mutations require an attributed API session. For example,
`backlog.add_todos("S-004", ["implement", "test"])` appends two open steps;
`close_todo`, `reopen_todo`, and `move_todo` update them without creating child
tasks. `backlog.todos("S-004")` and `backlog.task("S-004").todos` return the
same ordered public view.

The same API exposes `create_retrospective_action`,
`accept_retrospective_action`, `reject_retrospective_action`, and
`close_retrospective_action`. Closing requires a target project and exactly one
Feature or Bug, which may live in another project in the shared store.

Use only the documented public API. Do not query database tables, execute SQL,
or access internal connection attributes.

## Project layout

| Path | Purpose |
| --- | --- |
| `.codex-plugin/plugin.json` | Codex plugin manifest |
| `.claude-plugin/plugin.json` | Claude Code plugin manifest |
| `.claude-plugin/marketplace.json` | Local Claude Code marketplace |
| `docs/` | User prompts, integrations, and transition-hook documentation |
| `skills/backlog/SKILL.md` | Core agent rules and documentation routing |
| `skills/backlog-reviewer/SKILL.md` | Independent reviewer workflow and decision guardrails |
| `skills/backlog/bin/install.sh` | Optional direct-install helper for Codex and Claude Code |
| `skills/backlog/references/` | Task-specific documentation |
| `skills/backlog/scripts/` | Predefined operational scripts |
| `skills/backlog/bin/` | `backlog` and `backlog-py` launchers |
| `skills/backlog/tool/` | Runtime CLI and Python API package |

## Validate

```bash
python3 ~/.codex/skills/.system/skill-creator/scripts/quick_validate.py \
  skills/backlog
python3 ~/.codex/skills/.system/plugin-creator/scripts/validate_plugin.py .
```

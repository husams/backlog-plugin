---
name: backlog
description: "Track the active development backlog of a project in a `.backlog/` SQLite store, a central file, or a shared PostgreSQL server: projects, tasks (features, stories, bugs and subtasks in one table), their acceptance criteria and checklists, dependencies between them, per-project workflows built from templates, assignment to humans or agents, threaded review comments, PR links and artifacts. Use whenever the user asks about the backlog, what to work on next, task status, what is blocking what, review comments or feedback, whether something is ready to merge, or asks to plan, groom, assign, review, accept or close work, or to change a project's status flow."
---

# Backlog

All paths in this document are relative to this skill directory. The two
runtime entry points are:

```bash
BL=bin/backlog
PY=bin/backlog-py
```

`bin/install.sh` is an optional human-run direct installer. Agents do not run
it during normal skill use or marketplace installation; see
[references/install.md](references/install.md).

Add `--actor <name>` so the audit trail records who acted, and `--project
<slug>` to act on a project other than the current one. `--json` exists but you
rarely want it: prefer computing the answer over reading a dump.

**Use the documented Python API by default for every multi-step, computed, or
structured task.** `$BL` is only for one simple, documented command. You MUST
NOT generate shell scripts, command pipelines, loops, or chains of CLI calls
when the public Python API can do the work more directly. Feed a short Python
snippet on stdin and print only the conclusion:

```bash
$PY <<'PY'
from backlog_cli import api
with api.open() as bl:
    n = len(bl.startable("claude"))
    print(f"{n} startable")
PY
```

Common named-task activities are already written: see
[references/scripts.md](references/scripts.md). Use them only for small,
explicit inputs; use `$PY` to reduce computed or large task sets in-process.

## The shape

```
template ──copied at project creation──> project ──> task ──┬── task_item   criteria / checklist / notes
   │                                        │               ├── dependency  blocks / relates / duplicates
   └── the flow a new project starts with   │               ├── review_thread
                                            └── workflow    └── artifact
                                               (per task
                                                type)
```

`task` is **one table** for features, stories, bugs, subtasks and Iterations, told apart by
`task_type` and nested through `parent_id`. A feature holds stories; a story
holds subtasks.

## The rules are enforced by the tool, not by you

**Do not re-implement the rules from memory, and do not work around a refusal.**
The CLI reads this project's `workflow_transition` rows and refuses anything
they do not allow. Trust the exit code:

| Command | Exit | Means |
| --- | --- | --- |
| `$BL action KEY ACTION` | `0` | action recorded; configured transition applied when one matches |
| | `1` | illegal transition, or a gate failed — the message names which |
| `$BL gate KEY --for merge` | `0` | merging is allowed |
| | `2` | **blocked — do not merge** |
| `$BL dep check KEY` | `0` / `2` | startable / blocked |

There is no agent-facing operation that accepts a destination status. Submit
what happened with `action`; the project's action workflow selects the
destination, runs gates, and invokes transition hooks. Before submitting a
state-changing action, run `$BL actions KEY` to see the semantic actions
configured for that task's current state. A gate can be waived deliberately
(`--no-pr`, `--allow-blocked`, `--allow-open-subtasks`) and `doctor` reports
the waiver afterwards, so an override stays visible.

## Hard rules

Review work has exactly two accountable roles: the **implementer**, who must
disposition every finding, and the **opening reviewer**, who must decide every
implementer response and the final outcome of the story or feature. Neither
role may leave work pending for the other without an explicit reply.

Review authorship is attributed, not authenticated: `author=` and `--author`
are caller assertions. The tool enforces that only the thread's opening
reviewer can accept or reject a developer response, and an API session opened
with `api.open(actor=...)` rejects a different review author, but neither is
cryptographic proof of the human behind a process. Use `review audit ROOT` or
`bl.review_audit(ROOT)` to inspect decision authors and timestamps; do not
treat a recorded acceptance alone as evidence that a person verified the fix.

1. **Never read the code.** Everything under `bin/`, `tool/` and `scripts/` is
   off limits. The markdown here is their documentation: [references/cli.md](references/cli.md)
   for commands, [references/scripts.md](references/scripts.md) for the ready-made
   scripts, [references/api.md](references/api.md) for the Python API.
2. **Compute in Python, answer in prose.** When you use `$PY`, reduce inside the
   snippet and print the conclusion — a count, a list of keys, a verdict. Do not
   print a dataset back into your own context and summarise it there.
3. **Never build complex shell workflows.** The shell is limited to launching
   one documented command or one short `$PY` snippet. MUST NOT create shell
   scripts, pipelines, loops, temporary scripts, or repeated CLI sequences for
   work supported by the public API.
4. **Leave nothing behind.** Snippets go in on stdin. Never write a `.py` file, a
   temp file or a scratch artifact to answer a question.
5. **Never touch tables or the database directly.** No `sqlite3`, no `psql`, no
   SQL, schema imports, connection attributes, or internal API attributes. Use
   only the documented public API. Direct access bypasses the flow, gates and
   audit trail.
6. **Never request a destination status.** Before any state-changing event,
   run `$BL actions KEY`, then submit the matching semantic action with
   `$BL action KEY ACTION`. The workflow—not the agent—chooses the state.
7. **Never merge a PR unless `$BL gate <KEY> --for merge` exits 0.**
8. **Do not start blocked work.** If `action ... work.started` fails on
   `dependencies_clear`, pick something else.
9. **Never re-read context in the same session.** On the first review read, keep
   each thread's `reply_to` key in context. Later call
   `bl.review_updates(ROOT, after=LAST_SEEN)` and read only newly added
   comments. MUST NOT re-read task descriptions, inbox summaries, full threads,
   or other large text already present in context. A full read is allowed only
   when context was lost or a new comment is genuinely ambiguous, and the agent
   must state that reason.
10. **Review feedback is not an artifact.** Requests to post, add, leave,
   answer, accept, reject, or reply to a review or feedback must use
   `review open`, `review reply`, or another documented review command.
   Use `artifact add` only when the user explicitly asks to attach or record a
   file, document, report, patch, log, design, or other durable artifact.
11. **Implementers MUST answer every open review thread.** This includes
    `blocker`, `nice_to_have`, and `info` threads. Before handing the story
    back, the implementer MUST reply to every thread awaiting them with `fix`,
    `comment`, or `reject` and a non-empty body that explicitly accepts or
    rejects the feedback and explains the disposition. Implementers MUST NOT
    leave any advisory or blocker unanswered. When resolving a comment with
    changes, the implementer MUST briefly and concretely explain what changed;
    long narratives or a bare statement such as "fixed" are not sufficient.
12. **Thread resolution is reviewer-owned.** Only the reviewer who opened the
    thread may accept or reject a developer response. The API reuses that
    reviewer automatically and treats any other responding author as the
    developer/assignee for that reply. Callers MUST NOT repeat or alter role,
    reviewer, or assignee metadata. Never submit a
    `feedback.*` task action. The review subsystem emits `feedback.resolved`
    only after every blocker has reviewer acceptance; until then, leave the
    task status unchanged.
    Always open Python sessions with `api.open(actor=YOUR_IDENTITY)` before
    writing reviews so the session rejects mismatched `author=` assertions.
13. **Reviewers MUST decide every implementer response.** Before completing a
    review, the reviewer MUST reply to every thread awaiting them with
    `accept` or `reject` and a non-empty body explaining the decision. This
    includes advisory and informational feedback. A reviewer MUST NOT leave a
    response pending, silently abandon a thread, or substitute a neutral
    comment for a decision.
14. **Reviewers MUST leave the story or feature in a decisive state.** A review
    ends only when the reviewer either accepts the changes through the
    configured semantic action, or leaves explicit advisory/blocker threads
    and hands the item back through the configured changes-requested action.
    Reviewers MUST NOT finish with the item in an ambiguous or incomplete
    review state. Before either outcome, all thread replies awaiting the
    reviewer must already have an `accept` or `reject` decision.
15. **New blockers caused by a response require a causal explanation.** When
    changes made to resolve earlier feedback introduce a new blocker, the
    reviewer MUST open a new blocker that states: (a) which recent resolving
    changes introduced it, (b) how those changes caused the blocking behavior,
    and (c) what must be done differently to resolve the blocker without
    repeating the regression. A vague statement that the update is blocked is
    not sufficient.
16. **Reopen through the thread API.** To reactivate an accepted finding, use
    `review reopen ROOT --author REVIEWER --body REASON` or
    `bl.review_reopen(...)`. The reply is required. A new or reopened blocker
    on a Ready task emits a managed event that the shipped workflow resolves to
    Incomplete; never submit that `feedback.*` action yourself.

## Start here

```bash
$PY scripts/standup.py --actor <you>   # all four of the below, in one process
```

or individually:

```bash
$BL where                      # which store and project you are in
$BL board                      # what exists and where it stands
$BL next --actor <you>         # what YOU should do right now
$BL statuses                   # the flow this project actually runs
$BL show <KEY>                 # one task in full
```

`next` returns review threads waiting on you, work assigned to you that is
actually startable, work that is blocked and why, items awaiting your review,
and items whose gates now pass.

If no store exists: `$BL init .` from the repository root, then commit
`.backlog`.

## Load only what you need

| Task | Read this |
| --- | --- |
| A common request — workflow action, standup, start, merge check, review triage | [references/scripts.md](references/scripts.md) |
| Something the CLI has no flag for: count, filter or compare across tasks | [references/api.md](references/api.md) |
| Submit actions through a task's flow; understand a blocking gate | [references/workflow.md](references/workflow.md) |
| Change a project's statuses or transitions, or author a template | [references/templates.md](references/templates.md) |
| Record or inspect what blocks what; order a feature's stories | [references/dependencies.md](references/dependencies.md) |
| Open, answer, accept or reject review comments | [references/review.md](references/review.md) |
| Look up an exact command, flag or exit code | [references/cli.md](references/cli.md) |
| Plan work: create features/stories/subtasks, criteria, checklists, assign | [references/planning.md](references/planning.md) |
| Put the store somewhere else: central file, shared PostgreSQL | [references/store.md](references/store.md) |
| Attach a design doc, spec, log or report to a task | [references/artifacts.md](references/artifacts.md) |
| A command failed, or the store looks wrong | [references/troubleshooting.md](references/troubleshooting.md) |
| Install or update the skill | [references/install.md](references/install.md) |
| Select SQLite/PostgreSQL or provision the database | [references/store.md](references/store.md), [references/install.md](references/install.md) |

## The loop in one screen

```bash
$BL statuses --type story                         # what this project's flow allows
$BL story add --title "Cache resolved symbols" --feature F-001 \
    --ac "Given a cold cache, when resolve runs, entries are written."
$BL dep add S-004 --blocked-by S-002 --note "needs the session table"
$BL assign S-004 --to claude --reviewer husam     # agent vs human is recorded
$BL actions S-004
$BL action S-004 refinement.accepted --actor product-manager
$BL dep check S-004                               # exit 0 => safe to start
$BL actions S-004
$BL action S-004 work.started --actor developer
$BL item add S-004 --kind checklist --content "wire the route"
$BL pr set S-004 --url https://github.com/acme/repo/pull/91 --state open

$BL review open S-004 --author husam --severity blocker \
    --body "Lock is taken twice" --file src/cache.cpp --line 88
$BL review inbox --actor claude
$BL review reply C-003 --author claude --action fix --body "Fixed in a1b2c3d"
$BL review reply C-004 --author husam --action accept --body "Confirmed"

$BL pr set S-004 --review-state approved
$BL gate S-004 --for merge                        # exit 0 => merging is allowed
# ...merge the PR...
$BL pr set S-004 --state merged
```

`pr set` emits `pr.created`, `pr.approved`, or `pr.merged`; those are semantic
actions too. Every resulting status comes from this project's action workflow,
never from an agent-supplied destination.

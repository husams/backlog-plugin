---
name: backlog
description: "Query and update a project's Backlog through documented public APIs. Use for generic Backlog lookups and one-off commands such as status, what to work on next, blockers, allowed actions, workflow inspection, and task, Iteration, or retrospective records. For sustained role-specific delivery, use backlog-coordinator for Feature or Iteration decomposition and handoffs, backlog-implementer for implementing a refined Story or Bug, and backlog-reviewer for independent review and merge-readiness decisions. Do not trigger for those explicit role workflows."
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

Retrospective improvement actions are separate `R-` records. Each references
an Iteration and follows the fixed Created → Ready → Done/Rejected lifecycle;
see [references/retrospectives.md](references/retrospectives.md).

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

Two gates guard every transition into a finished status and neither has a
waiver: `todos_closed` and `acceptance_criteria_verified`. The second requires
the task to record at least one acceptance criterion and every one of them to
carry a current `met` verdict from an actor who is neither the task's assignee
nor its creator. Criteria are never ticked — `item check` refuses one. Record
the verdict with evidence instead:

```bash
$BL criteria list S-004
$BL --actor reviewer criteria verify 12 --met --evidence "how it was actually checked"
```

See [references/workflow.md](references/workflow.md) and
[references/review.md](references/review.md).

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
2. **Reason with code; return only the evidence needed.** For questions,
   reviews, and responses to feedback, use the documented Python API to filter,
   join, compare, count, group, and validate records in-process. Print only the
   smallest sufficient result: the relevant keys and fields, a count, a
   verdict, or the specific evidence needed to explain the conclusion. Never
   dump a large result set into model context and reason over the dump.
3. **Use the narrowest complete read, in this order.** First call a public API
   method with every applicable semantic filter, such as task key, actor, role,
   state, severity, status, task type, or `after=LAST_SEEN`. If that call returns
   a complete in-process collection, reduce it in Python and print only relevant
   records or an aggregate. If the documented API instead reports a bounded
   batch or continuation cursor, consume all matching batches in the same
   process while retaining only that reduced result. Pagination exists only to
   establish completeness after semantic filtering; it never replaces filtering
   or justifies printing each page. Never start with an unfiltered board, inbox,
   task collection, thread history, or JSON dump for convenience.
4. **Truncated data is unusable for conclusions.** Never answer a question,
   review work, or respond to feedback from a result marked or suspected as
   truncated. `budget.max_results`, output clipping, an API page size, or a
   display limit is not evidence that the remaining records are irrelevant. If
   a result is incomplete, use code to examine the complete matching record
   set. When a documented API returns records in batches or behind a
   continuation cursor, consume every matching batch until the API confirms
   there are no remaining matches, reducing in-process as the batches arrive.
   If completeness cannot be established, stop and state that the evidence is
   incomplete; do not provide a verdict or feedback.
5. **Never impose an arbitrary hard result limit.** Do not hard-code a maximum
   number of tasks, comments, threads, artifacts, or search results when
   correctness depends on examining the whole matching set. Prefer a selective
   query first; when the matching set is still large, examine it to exhaustion
   in code while retaining only the aggregate or relevant matches.
   A user-requested bound or a limit that is semantically part of the question
   is allowed, but it must not silently stand in for complete evidence.
6. **Never build complex shell workflows.** The shell is limited to launching
   one documented command or one short `$PY` snippet. MUST NOT create shell
   scripts, pipelines, loops, temporary scripts, or repeated CLI sequences for
   work supported by the public API.
7. **Leave nothing behind.** Snippets go in on stdin. Never write a `.py` file, a
   temp file or a scratch artifact to answer a question.
8. **Never touch tables or the database directly.** No `sqlite3`, no `psql`, no
   SQL, schema imports, connection attributes, or internal API attributes. Use
   only the documented public API. Direct access bypasses the flow, gates and
   audit trail.
9. **Never request a destination status.** Before any state-changing event,
   run `$BL actions KEY`, then submit the matching semantic action with
   `$BL action KEY ACTION`. The workflow—not the agent—chooses the state.
10. **Never merge a PR unless `$BL gate <KEY> --for merge` exits 0.**
11. **Do not start blocked work.** If `action ... work.started` fails on
   `dependencies_clear`, pick something else.
12. **Reviews are incremental, never historical by default.** Begin with one
   narrowly scoped inbox read using every applicable actor, role, item, and
   severity filter. Keep every returned thread's root key and `reply_to`; the
   latter is its `LAST_SEEN` key. For later comments on a known thread, call
   `bl.review_updates(ROOT, after=LAST_SEEN)`, process every returned comment in
   order, and advance `LAST_SEEN` only after all of them are processed. An empty
   result means there is nothing new on that root. MUST NOT poll known roots by
   re-reading task descriptions, inbox summaries, full threads, old comments,
   or other text already present in context. If new roots may have been opened
   and handoff requires a complete inbox, make one final semantically scoped
   discovery read in-process and print only previously unseen roots. A full
   thread read is allowed only after context loss or when a new comment cannot
   be understood from retained context; state the specific reason before doing
   it.
13. **Review feedback is not an artifact.** Requests to post, add, leave,
   answer, accept, reject, or reply to a review or feedback must use
   `review open`, `review reply`, or another documented review command.
   Use `artifact add` only when the user explicitly asks to attach or record a
   file, document, report, patch, log, design, or other durable artifact.
14. **Implementers MUST answer every open review thread.** This includes
    `blocker`, `nice_to_have`, and `info` threads. Before handing the story
    back, the implementer MUST reply to every thread awaiting them with `fix`,
    `comment`, or `reject` and a non-empty body that explicitly accepts or
    rejects the feedback and explains the disposition. Implementers MUST NOT
    leave any advisory or blocker unanswered. When resolving a comment with
    changes, the implementer MUST briefly and concretely explain what changed;
    long narratives or a bare statement such as "fixed" are not sufficient.
15. **Thread resolution is reviewer-owned.** Only the reviewer who opened the
    thread may accept or reject a developer response. The API reuses that
    reviewer automatically; only the task's assigned implementer may send the
    developer reply. A third actor cannot substitute for either side. Callers
    MUST NOT repeat or alter role, reviewer, or assignee metadata. Never submit a
    `feedback.*` task action. The review subsystem emits `feedback.resolved`
    only after every blocker has reviewer acceptance; until then, leave the
    task status unchanged.
    Always open Python sessions with `api.open(actor=YOUR_IDENTITY)` before
    writing reviews so the session rejects mismatched `author=` assertions.
16. **Reviewers MUST decide every implementer response.** Before completing a
    review, the reviewer MUST reply to every thread awaiting them with
    `accept` or `reject` and a non-empty body explaining the decision. This
    includes advisory and informational feedback. A reviewer MUST NOT leave a
    response pending, silently abandon a thread, or substitute a neutral
    comment for a decision.
    The thread opener must be the task's assigned reviewer, distinct from both
    creator and implementer; an explicit `role=` value never overrides this.
17. **Reviewers MUST leave the story or feature in a decisive state.** A review
    ends only when the reviewer either accepts the changes through the
    configured semantic action, or leaves explicit advisory/blocker threads
    and hands the item back through the configured changes-requested action.
    Reviewers MUST NOT finish with the item in an ambiguous or incomplete
    review state. Before either outcome, all thread replies awaiting the
    reviewer must already have an `accept` or `reject` decision.
18. **New blockers caused by a response require a causal explanation.** When
    changes made to resolve earlier feedback introduce a new blocker, the
    reviewer MUST open a new blocker that states: (a) which recent resolving
    changes introduced it, (b) how those changes caused the blocking behavior,
    and (c) what must be done differently to resolve the blocker without
    repeating the regression. A vague statement that the update is blocked is
    not sufficient.
19. **Reopen through the thread API.** To reactivate an accepted finding, use
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
| Record, accept, reject, close, or query retrospective workflow improvements | [references/retrospectives.md](references/retrospectives.md) |
| Put the store somewhere else: central file, shared PostgreSQL | [references/store.md](references/store.md) |
| Attach a design doc, spec, log or report to a task | [references/artifacts.md](references/artifacts.md) |
| A command failed, or the store looks wrong | [references/troubleshooting.md](references/troubleshooting.md) |
| Install or update the skill | [references/install.md](references/install.md) |
| Select SQLite/PostgreSQL or provision the database | [references/store.md](references/store.md), [references/install.md](references/install.md) |

## The loop in one screen

```bash
$BL statuses --type story                         # what this project's flow allows
$BL story add --title "Cache resolved symbols" --feature F-001 --actor product-manager \
    --ac "Given a cold cache, when resolve runs, entries are written."
$BL dep add S-004 --blocked-by S-002 --note "needs the session table"
$BL assign S-004 --to claude --reviewer husam     # agent vs human is recorded
$BL actions S-004
$BL action S-004 refinement.accepted --actor business-analyst
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

$BL criteria list S-004
$BL --actor husam criteria verify 12 --met --evidence "ran the suite and watched it pass"
$BL pr set S-004 --review-state approved
$BL gate S-004 --for merge                        # exit 0 => merging is allowed
# ...merge the PR...
$BL pr set S-004 --state merged
```

Task and retrospective-action creation must include `--actor NAME` so the
record stores `created_by`. The creator must never submit
`refinement.accepted` or accept its own retrospective action; use an independent
actor. The tool refuses creator self-acceptance and omitted acceptance actors.

`pr set` emits `pr.created`, `pr.approved`, or `pr.merged`; those are semantic
actions too. Every resulting status comes from this project's action workflow,
never from an agent-supplied destination.

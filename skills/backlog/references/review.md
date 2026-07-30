# Review comments

Review feedback lives in **threads**. A thread is one top-level comment plus its
chain of replies. Exactly one party holds the ball at any moment, so you never
have to scan the whole history to know whether it is your turn.

Every thread has one fixed severity from the `ReviewSeverity` enum:

| Severity | Meaning | Blocks workflow gates |
| --- | --- | --- |
| `blocker` | Must be resolved before the work can be accepted | yes |
| `nice_to_have` | Actionable improvement that is not required | no |
| `info` | Context or an observation with no required action | no |

Severity belongs to the root thread and applies to every reply. Existing
threads and new threads without an explicit severity default to `blocker`.
Only open `blocker` threads are evaluated by `review_threads_closed`.

Opening feedback records a review event but does not choose a task status.
When a blocker means a feature is not ready for implementation, submit the
project's refinement-incomplete action separately. This keeps review storage
distinct from the project's customizable transition policy.

## The five actions

| Action | Who typically | Effect |
| --- | --- | --- |
| `open` | reviewer | starts a thread; ball → the other party |
| `fix` | developer | "I addressed this"; ball → the other party to verify |
| `reject` | either | "I disagree, here is why"; ball → the other party |
| `comment` | either | question or note; ball → the other party |
| `accept` | **either** | **closes the thread** |

A thread closes as soon as **either** the reviewer **or** the developer accepts.
`fix` alone does not close it — the other party still has to accept.

## Reading: only three comments, never the whole thread

```bash
$BL review inbox --actor developer
```

For each thread waiting on you this returns:

- the **root** comment (the original ask),
- the **direct parent** of the latest reply,
- the **latest** reply,
- `reply_to` — the comment key to reply to,
- `hidden_comments` — how many middle comments were omitted.

That is all you need to act. Only reach for `$BL review thread <ROOT> --full`
when the summary is genuinely ambiguous, and say why.

Other reads:

```bash
$BL review inbox --actor senior-developer       # threads awaiting that actor
$BL review inbox --role reviewer                # any thread awaiting a reviewer
$BL review inbox --item S-004                   # scoped to one item
$BL review inbox --severity blocker             # only workflow blockers
$BL review list S-004 --state open|closed|all   # all threads on an item
$BL review list S-004 --severity nice_to_have   # advisory threads only
$BL review thread C-003                         # one thread, summary form
```

## Writing

```bash
# Reviewer opens a finding, optionally anchored to a location
$BL review open S-004 --author senior-developer \
    --severity blocker \
    --body "The mutex is taken twice on the error path." \
    --file src/cache.cpp --line 88

# Developer answers the comment named in `reply_to`
$BL review reply C-003 --author developer --action fix \
    --body "Released before the early return. Fixed in a1b2c3d."

# Reviewer confirms -> thread closes
$BL review reply C-004 --author senior-developer --action accept --body "Confirmed."
```

Always reply to the key in `reply_to` (the latest comment), not to the root —
that is what keeps the parent chain meaningful.

To correct a thread's severity, use the audited operation:

```bash
$BL review severity C-003 --severity nice_to_have --author senior-developer
```

## Roles

Every comment carries a role, `reviewer` or `developer`. It is inferred from the
item: an author matching the item's `reviewer` is a reviewer, an author matching
`assignee` is a developer. If the author is neither, pass `--role` explicitly:

```bash
$BL review reply C-003 --author qa-engineer --role reviewer --action reject --body "..."
```

Assign both sides up front so inference just works:

```bash
$BL assign S-004 --to developer --reviewer senior-developer
```

## Disagreeing

`reject` is a first-class move for both sides. A developer who believes the
finding is wrong should reject with the reasoning rather than silently comply:

```bash
$BL review reply C-003 --author developer --action reject \
    --body "The caller already holds the lock; taking it here would deadlock. See src/pool.cpp:40."
```

The ball goes back to the reviewer, who either accepts (thread closes, the
developer was right) or rejects again with counter-evidence.

## Reopening

A closed thread refuses new replies. If it must be re-litigated:

```bash
$BL review reopen C-003 --author senior-developer --body "This regressed in the rebase."
```

## Why an item is stuck

Open blocker threads block `Accepted`; `nice_to_have` and `info` threads remain
visible without stopping delivery. To see what is left:

```bash
$BL review list S-004 --state open --severity blocker
$BL gate S-004 --for accepted
```

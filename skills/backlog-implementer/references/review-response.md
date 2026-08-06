# Review response

Review threads have one opening reviewer and one implementer. The opening
reviewer owns `accept` or `reject`; the implementer owns `fix`, `comment`, or
`reject`. An implementer cannot accept a response or close a thread.

## Cursor protocol

At the start of a review turn, perform one semantically narrow inbox read for
the implementer actor, developer role, and task. Keep:

```text
root_key -> reply_to (LAST_SEEN)
```

For every known root, call `bl.review_updates(root_key, after=last_seen)`.
Process all returned comments oldest first. Only after every comment has been
answered or otherwise dispositioned may `LAST_SEEN` become the last returned
comment key. An empty result means there is nothing new on that root.

If a new comment is ambiguous without omitted history, read that one full
thread and record why. Truncation is not a reason to guess. Do not poll known
roots through `inbox` or reload old thread history.

Before handoff, make exactly one final semantically filtered inbox discovery
read. Retain only roots not already in the mapping, then process those roots
incrementally. This catches a new blocker, advisory, or informational comment
without reopening historical context.

## All-severity disposition

Every open root awaiting the implementer must receive a non-empty response,
regardless of severity:

- `fix`: name the concrete change and the validation evidence;
- `comment`: explain the implementation decision or ask a focused question;
- `reject`: state the counter-evidence and why no change is warranted.

“Fixed” alone is invalid. A blocker may require a new validation run and a
current required-item result. Nice-to-have and info roots do not block the
deliverable gate, but abandoning either violates the review contract.

After responding, return the ball to the opening reviewer. Do not accept it,
approve the PR, or merge. Only the opening reviewer may decide the response and
the configured workflow may decide the task transition.

## Rework resets acceptance verdicts

Work that returns for changes loses its acceptance evidence. The reviewer's
criterion verdicts are cleared when the task moves backwards out of review into
active work, when the criteria themselves are rewritten, and explicitly through
`bl.clear_criterion_verdicts`. A criterion whose text changed after its verdict
is `stale` and counts as unverified.

That reset is not a setback to work around. Before resubmitting:

- close every reopened and newly added todo, so `bl.todos(key)` is empty again;
- re-run the required executable items so each has a current `pass` for the
  current spec fingerprint;
- restate which evidence proves which acceptance criterion, including the
  criteria that were previously accepted — the reviewer must verify all of them
  again.

Never call `bl.verify_criterion` to restore a cleared verdict. Verification is
reviewer-owned and the API rejects this actor's identity.

## New findings and handoff

If a response introduces a regression, the reviewer—not the implementer—must
open a new blocker with the causal explanation. The implementer then answers
that new root with the same severity-independent rules. Before handoff, ensure
no known root is still awaiting the implementer, `bl.todos(key)` holds no open
todo, and submit only a currently allowed semantic review action.

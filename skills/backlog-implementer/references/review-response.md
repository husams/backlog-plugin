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

## New findings and handoff

If a response introduces a regression, the reviewer—not the implementer—must
open a new blocker with the causal explanation. The implementer then answers
that new root with the same severity-independent rules. Before handoff, ensure
no known root is still awaiting the implementer and submit only a currently
allowed semantic review action.

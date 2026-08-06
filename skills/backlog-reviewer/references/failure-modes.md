# Failure modes

| Failure | Required response |
| --- | --- |
| Reviewer equals creator or implementer | Stop and report an identity conflict; do not review or substitute an identity. |
| Actor is independent but not the assigned reviewer | Stop and report the assignment mismatch; a third actor cannot substitute for the named reviewer. |
| Task is not currently in review | Do not record criterion verdicts or final review actions; use the configured handoff flow first. |
| Inbox or API result is truncated | Do not decide; narrow the semantic query or consume the documented continuation to completeness. |
| Known root has new comments | Read `review_updates(root, after=LAST_SEEN)` and process all comments in order before advancing only that root. |
| Implementer response is unanswered | Reply with `fix`, `comment`, or `reject`; reviewers must later accept or reject it. |
| Reviewer response is insufficient | Reject the response with concrete remaining evidence and request changes through the workflow. |
| Accepted finding regressed | Reopen the original root with `review_reopen` and explain the causal regression. |
| Iteration roots are dispositioned but open | Do not submit `iteration.closed`; every root needs an opening-reviewer decision and closure. |
| Task has zero acceptance criteria | Refuse to approve; report the unspecified contract and return the work. `acceptance_criteria_verified` fails on an empty contract. |
| A criterion is `unverified`, `unmet`, or `stale` at verdict time | Do not approve; verify it now with inspected evidence, or record `met=False` and request changes. A stale verdict counts as unverified. |
| A criterion cannot be verified from available evidence | Record `bl.verify_criterion(..., met=False, evidence=<why>)` and request changes; never leave it unverified and approve. |
| Verdict evidence would be generic ("looks correct", "tests pass") | Do not record it; name the file, line, test, command, or output that was inspected. |
| Reviewer implemented the criterion under review | Record no verdict and report the conflict; never substitute another identity to pass the independence check. |
| An open todo remains at approval time | Do not approve; open a typed root for the outstanding work and return it. `todos_closed` also gates in-review to accepted and accepted to done. |
| Approval gate blocks and a waiver is tempting | Report `gate.failures` verbatim and request changes. `acceptance_criteria_verified` has no waiver; looking for one is itself a violation, as is waiving `todos_closed`. |
| A done-category status looks reachable by another route | Refuse; the configured approval action is the only route, and gate-skipping paths are forbidden. |
| Work is returned for rework | Call `bl.clear_criterion_verdicts(key, reason=...)` so no superseded verdict survives the next submission. |
| Merge gate is blocked | Report `gate.failures`, do not merge, and do not use a bypass to claim readiness. |
| Reviewer is asked to implement or merge | Refuse the out-of-role action and hand off to the implementer or designated merger. |
| Direct SQL/status mutation or forbidden implementation read requested | Refuse; use only documented public APIs and Markdown references. |

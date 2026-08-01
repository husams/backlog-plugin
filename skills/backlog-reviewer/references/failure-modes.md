# Failure modes

| Failure | Required response |
| --- | --- |
| Reviewer equals creator or implementer | Stop and report an identity conflict; do not review or substitute an identity. |
| Inbox or API result is truncated | Do not decide; narrow the semantic query or consume the documented continuation to completeness. |
| Known root has new comments | Read `review_updates(root, after=LAST_SEEN)` and process all comments in order before advancing only that root. |
| Implementer response is unanswered | Reply with `fix`, `comment`, or `reject`; reviewers must later accept or reject it. |
| Reviewer response is insufficient | Reject the response with concrete remaining evidence and request changes through the workflow. |
| Accepted finding regressed | Reopen the original root with `review_reopen` and explain the causal regression. |
| Iteration roots are dispositioned but open | Do not submit `iteration.closed`; every root needs an opening-reviewer decision and closure. |
| Merge gate is blocked | Report `gate.failures`, do not merge, and do not use a bypass to claim readiness. |
| Reviewer is asked to implement or merge | Refuse the out-of-role action and hand off to the implementer or designated merger. |
| Direct SQL/status mutation or forbidden implementation read requested | Refuse; use only documented public APIs and Markdown references. |

# Example prompts

These prompts show how to use the Backlog plugin through normal conversation.
Replace text in `<angle brackets>` with values for your project.

In Codex, select or mention `@backlog-plugin` when you want to require the
plugin. In Claude Code, invoke the bundled skill as `/backlog:backlog`.

The plugin enforces the selected workflow. If a requested status transition is
illegal or a gate fails, the agent should report the refusal instead of
bypassing it.

## 1. Set up a flow

Use a shipped template:

> @backlog-plugin Show me the available workflow templates. Create a project
> named `<project name>` with slug `<project-slug>` using the
> `software-delivery` template. Then show me the feature and story flows.

Customize the project after creating it:

> @backlog-plugin Add an `In QA` status to the story workflow after
> `In Review`. Allow stories to move from `In Review` to `In QA`, then from
> `In QA` to `Accepted`. Require closed review threads and completed child
> tasks before the second transition. Show me the resulting flow and do not
> change any other project.

## 2. Create a backlog for a project

For the repository currently open:

> @backlog-plugin Set up a backlog for this repository. Use SQLite with the
> repository-local database and create the project `<project name>` with slug
> `<project-slug>`. Use the `software-delivery` workflow. Show me where the
> backlog was created and summarize its initial board.

For PostgreSQL, configure `BACKLOG_DB=postgres` and `BACK_LOG_URL` in the
environment before using this prompt:

> @backlog-plugin Set up the backlog database using the configured PostgreSQL
> backend. Create project `<project name>` with slug `<project-slug>` and show
> me its workflows. Do not print the database URL or credentials.

## 3. Add the first feature

> @backlog-plugin Add our first feature to `<project-slug>`:
> **User authentication**. Priority is P1. The goal is to let registered users
> sign in securely and sign out from every active session. Add these acceptance
> criteria:
>
> - A registered user can sign in with valid credentials.
> - Invalid credentials do not reveal which field was incorrect.
> - A user can revoke all active sessions.
>
> Assign the feature to `<owner>` and show me the created feature key and
> current status.

Save the returned key, such as `F-001`, for later prompts.

## 4. Review a new feature

> @backlog-plugin Review feature `F-001` as `<product-reviewer>`. Check whether
> its goal, scope, priority, and acceptance criteria are clear and testable.
> Post each specific gap as a review thread, not as an artifact. Use severity
> `blocker` for gaps that prevent implementation readiness, `nice_to_have` for
> optional improvements, and `info` for context. If any blockers are found,
> use the configured refinement action to mark the feature Incomplete. Finish
> with either `ready for approval` or `needs revision`, and show the feature's
> resulting status.

## 5. Approve the feature and move it to Ready

Use this only after the review finds no unresolved gaps:

> @backlog-plugin Approve the scope of feature `F-001` and move it to the
> project’s Ready status as actor `<product-manager>`. Use the configured
> workflow rather than assuming the status name. If the move is refused,
> explain the failed rule or gate and leave the feature unchanged.

Here, “approve” means approving the feature for implementation. It does not
mean forcing it into a status named `Accepted`.

## 6. Create a story

> @backlog-plugin Create a story under feature `F-001` titled
> **Sign in with email and password** with priority P1. Add a concise user-story
> description and these acceptance criteria:
>
> - Valid credentials create a new authenticated session.
> - Invalid credentials return a generic authentication error.
> - Five consecutive failures trigger the configured rate limit.
>
> Assign it to `<developer>` and assign `<reviewer>` as reviewer. Show me the
> story key, parent feature, initial status, and allowed next statuses.

## 7. Change statuses

Move one item:

> @backlog-plugin Move story `S-001` to Ready as actor `<product-manager>`.
> First inspect the story workflow. Apply the transition only if it is legal,
> and report any failed gate without overriding it.

Start work safely:

> @backlog-plugin Start work on `S-001` as `<developer>`. Check dependencies
> first, move it through the configured transition, and then show its
> acceptance criteria, checklist, and open subtasks.

Ask for a status without changing it:

> @backlog-plugin Give me a one-line status summary for `S-001`, including its
> assignee and anything blocking its next transition.

## 8. Post a review

A posted review is stored as a review thread, not as an artifact. Attach an
artifact only when you explicitly want to associate a review document or file
with the task.

> @backlog-plugin Open a review finding on story `S-001` as `<reviewer>`.
> The finding is: “The error response reveals whether the email exists.”
> Mark it as severity `blocker`, anchor it to `<path/to/file>` line
> `<line-number>`, return the new review comment key, and identify who must
> reply next.

Review severity is a fixed enum: `blocker`, `nice_to_have`, or `info`. Only an
open blocker prevents acceptance or merge. Posting feedback records the review
event; use the project's workflow action separately when the finding requires
a status transition.

## 9. Post feedback

Use `fix` when the finding was addressed:

> @backlog-plugin Show the review inbox for `<developer>` on `S-001`. Reply to
> the requested comment as `<developer>` with action `fix` and body:
> “Changed the endpoint to return the same message and status for unknown
> users and incorrect passwords. Added regression coverage.” Return the reply
> key and identify who must respond next.

Use `reject` when you disagree:

> @backlog-plugin Reply to review comment `<comment-key>` as `<developer>` with
> action `reject`. Explain that `<reason and evidence>`. Keep the thread open
> for the reviewer.

## 10. Accept feedback

Accepting a reply closes its review thread:

> @backlog-plugin Review the latest reply in thread `<root-comment-key>` as
> `<reviewer>`. If the evidence resolves the original finding, reply to the
> current `reply_to` comment with action `accept` and body “Confirmed.” If it
> does not resolve the finding, do not accept it; explain what remains.

## 11. Reply to feedback

Use `comment` for a question or clarification that should keep the thread open:

> @backlog-plugin Show me the current summary of review thread
> `<root-comment-key>`. Reply to its current `reply_to` comment as
> `<developer-or-reviewer>` with action `comment` and body:
> “<your response>”. Return the new comment key and say who holds the next
> action.

Always reply to the comment identified by `reply_to`, not automatically to the
root comment. This preserves the thread’s parent chain.

## Complete example

> @backlog-plugin For project `accounts`, create feature **User
> authentication**, review its scope, and tell me whether it is ready. Do not
> move it until I approve.

After reviewing the result:

> @backlog-plugin I approve the scope. Move the feature to Ready as actor
> `product-manager`, create the first story **Sign in with email and password**,
> assign it to `codex` with `husam` as reviewer, and show the resulting feature
> tree. Stop and report the reason if any workflow operation is refused.

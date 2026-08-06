Feature: Task state machines
  The supported task types follow the configured action-driven lifecycle.

  Background:
    Given a new backlog project

  Scenario Outline: A task moves through refinement, implementation, review, and delivery
    Given a "<task_type>" task
    Then the task status is "created"
    When action "refinement.accepted" is submitted by "reviewer"
    Then the task status is "ready"
    When action "work.started" is submitted by "developer"
    Then the task status is "in_progress"
    When action "work.completed" is submitted by "developer"
    Then the task status is "in_review"
    When action "review.approved" is submitted and rejected
    Then the command reports the "acceptance_criteria_verified" gate
    When the acceptance criteria are verified by "reviewer"
    And action "review.approved" is submitted by "reviewer"
    Then the task status is "accepted"
    When the task delivery is finalized
    Then the task status is "done"

    Examples:
      | task_type |
      | feature   |
      | story     |
      | subtask   |

  Scenario: Incomplete refinement can be corrected and accepted
    Given a "story" task
    When action "refinement.marked_incomplete" is submitted by "reviewer"
    Then the task status is "incomplete"
    When action "refinement.accepted" is submitted by "reviewer"
    Then the task status is "ready"

  Scenario: Work cannot start before refinement is accepted
    Given a "story" task
    When action "work.started" is submitted and rejected
    Then the task status is "created"
    And the command reports an unavailable action

  Scenario: A feature cannot be accepted while a child is unfinished
    Given a feature with an unfinished story
    And the feature is in review
    When action "review.approved" is submitted and rejected
    Then the task status is "in_review"
    And the command reports the "children_complete" gate

  Scenario: Pull request events and every public gate outcome are exercised
    When pull request events and gate outcomes are exercised
    Then the gate exercise succeeds

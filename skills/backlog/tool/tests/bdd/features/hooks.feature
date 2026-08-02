Feature: Transition hooks
  Repository hooks can inspect, block, and observe state transitions.

  Background:
    Given a new backlog project
    And a "story" task

  Scenario: Pre and post hooks wrap a successful transition
    Given transition hooks that record every call
    When action "refinement.accepted" is submitted by "reviewer"
    Then the task status is "ready"
    And the hook log contains "pre:refinement.accepted:created:ready"
    And the hook log contains "post:refinement.accepted:created:ready"

  Scenario: A pre hook can keep the current state
    Given transition hooks that block a requested transition
    When refinement acceptance requests a block
    Then the task status is "created"
    And the transition is reported as skipped
    And no post hook was called

  Scenario: A pre hook exception prevents the transition
    Given a pre transition hook that raises an error
    When action "refinement.accepted" is submitted and rejected
    Then the task status is "created"
    And the command reports "pre_transition blocked the transition"

  Scenario: A post hook failure is reported after the commit
    Given a post transition hook that raises an error
    When action "refinement.accepted" is submitted and rejected
    Then the task status is "ready"
    And the command reports "transition committed, but post_transition failed"

  Scenario: Invalid transition hook contracts are rejected
    When invalid transition hook contracts are exercised
    Then hook configuration errors are reported

  Scenario: Custom action workflow configuration is applied and validated
    When custom action workflow configuration is exercised
    Then hook configuration errors are reported

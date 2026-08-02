Feature: Retrospective action state machine
  Improvement actions require independent decisions and durable resolution links.

  Background:
    Given a new backlog project
    And an iteration

  Scenario: An accepted action is closed against a feature
    When a facilitator creates a retrospective action
    Then the retrospective status is "created"
    When a product manager accepts the retrospective action
    Then the retrospective status is "ready"
    When the action is closed against a resolution feature
    Then the retrospective status is "done"
    And retrospective history records "retrospective.created,retrospective.accepted,retrospective.closed"

  Scenario: A created action can be rejected with a reason
    When a facilitator creates a retrospective action
    And a product manager rejects the retrospective action
    Then the retrospective status is "rejected"
    And the retrospective rejection reason is retained

  Scenario: A ready action can be rejected with a reason
    When a facilitator creates a retrospective action
    And a product manager accepts the retrospective action
    And a product manager rejects the retrospective action
    Then the retrospective status is "rejected"

  Scenario: An accepted action is closed against a bug
    When a facilitator creates a retrospective action
    And a product manager accepts the retrospective action
    And the action is closed against a resolution bug
    Then the retrospective status is "done"

  Scenario: The creator cannot accept their own action
    When a facilitator creates a retrospective action
    And the facilitator tries to accept the retrospective action
    Then the retrospective command is rejected
    And the retrospective status is "created"

  Scenario: A created retrospective action blocks iteration closure
    Given the iteration is open
    When a facilitator creates a retrospective action
    And iteration closure is attempted
    Then the iteration command is rejected by "iteration_retrospective_actions_clear"
    When a product manager accepts the retrospective action
    And iteration closure is attempted successfully
    Then the iteration status is "closed"

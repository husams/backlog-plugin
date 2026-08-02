Feature: Review state machine
  Review threads alternate between developer and reviewer ownership.

  Background:
    Given a new backlog project
    And a ready story assigned to a developer and reviewer

  Scenario: A developer fixes a blocker and the reviewer accepts it
    When the reviewer opens a blocker
    Then the task status is "incomplete"
    And the review state is "awaiting_developer"
    When the developer replies with "fix"
    Then the review state is "awaiting_reviewer"
    When the reviewer replies with "accept"
    Then the review state is "closed"
    And the review resolution is "accepted_by_reviewer"
    And the task status is "ready"

  Scenario: A reviewer rejects a proposed fix
    When the reviewer opens a blocker
    And the developer replies with "fix"
    And the reviewer replies with "reject"
    Then the review state is "awaiting_developer"
    And the task status is "incomplete"

  Scenario: An accepted thread can be reopened
    When the reviewer opens a blocker
    And the developer replies with "fix"
    And the reviewer replies with "accept"
    And the reviewer reopens the thread
    Then the review state is "awaiting_developer"
    And the task status is "incomplete"

  Scenario: The opening reviewer cannot impersonate the developer
    When the reviewer opens a blocker
    And the opening reviewer replies as the developer
    Then the review command is rejected
    And the review state is "awaiting_developer"

  Scenario: Review discovery, severity, audit, and non-blocking comments are available
    When review discovery and severity commands are exercised
    Then review discovery reports the complete thread

  Scenario: Invalid review roles, phases, reopening, and lookup are rejected
    When review validation and escalation commands are exercised
    Then review discovery reports the complete thread

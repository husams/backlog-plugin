Feature: Executable items
  Shell and hook validations run only under local policy and gate acceptance.

  Background:
    Given a new backlog project
    And a "story" task

  Scenario: An allowed shell validation passes
    Given an executable shell item expecting exit code 0
    And shell execution is enabled
    When the executable item is run
    Then the validation status is "pass"
    And validation history records "pass"

  Scenario: A shell exit-code mismatch fails
    Given an executable shell item expecting exit code 7
    And shell execution is enabled
    When the executable item is run
    Then the validation status is "fail"
    And validation history records "fail"

  Scenario: Disabled shell execution skips the item
    Given an executable shell item expecting exit code 0
    When the executable item is run
    Then the validation status is "skipped"
    And the validation diagnostic contains "shell_disabled"

  Scenario: An allowlisted hook returns a typed passing result
    Given an executable hook item expecting a matching result
    And the validation hook is installed and allowlisted
    When the executable item is run
    Then the validation status is "pass"
    And validation history records "pass"

  Scenario: A required validation blocks acceptance until it passes
    Given an executable shell item expecting exit code 0
    And the task is in review
    When action "review.approved" is submitted and rejected
    Then the command reports the "required_validations_pass" gate
    And the task status is "in_review"
    When shell execution is enabled
    And the executable item is run
    And action "review.approved" is submitted by "reviewer"
    Then the task status is "accepted"

  Scenario: Shell execution reports runtime, matcher, environment, and output limits
    When shell execution edge cases are exercised
    Then executable edge cases are reported

  Scenario: Hook execution reports installation, contract, failure, and timeout errors
    When hook execution edge cases are exercised
    Then executable edge cases are reported

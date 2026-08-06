Feature: Acceptance criteria are verified by review
  Acceptance is a reviewer's attributed verdict on every criterion, never an
  implementer's tick and never an empty list.

  Background:
    Given a new backlog project

  Scenario: A reviewer records verdicts through the CLI and the Python API
    When acceptance criteria are verified by an independent reviewer
    Then the acceptance behavior succeeds

  Scenario: Verdicts without independence, evidence, or a criterion are refused
    When invalid acceptance verdicts are attempted
    Then the acceptance behavior succeeds

  Scenario: An edited criterion makes its verdict stale
    When a verified criterion is rewritten
    Then the acceptance behavior succeeds

  Scenario: Verdicts can be cleared with an attributed reason
    When acceptance verdicts are cleared
    Then the acceptance behavior succeeds

  Scenario: A task with no acceptance criteria cannot be accepted
    When a task without acceptance criteria is gated
    Then the acceptance behavior succeeds

  Scenario: An Iteration carries no acceptance-criteria gate
    When an Iteration is gated for acceptance
    Then the acceptance behavior succeeds

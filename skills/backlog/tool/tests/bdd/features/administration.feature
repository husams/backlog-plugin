Feature: Administrative end-to-end workflows

  Background:
    Given a new backlog project

  Scenario: Projects, templates, and workflows are managed through the CLI
    When all project, template, and workflow operations are exercised
    Then the administrative commands succeed

  Scenario: Dependencies and artifacts are managed through the CLI
    When all dependency and artifact operations are exercised
    Then the administrative commands succeed

  Scenario: Store inspection and transfer operations are exercised
    When all store inspection and transfer operations are exercised
    Then the administrative commands succeed

  Scenario: The public Python API manages a complete session
    When the public Python API session is exercised
    Then the public API reports the active project

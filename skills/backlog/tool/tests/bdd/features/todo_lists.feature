Feature: Lightweight ordered todo lists
  Todos sequence implementation work without creating independently reviewed subtasks.

  Background:
    Given a new backlog project

  Scenario: Create an ordered todo list
    When ordered todos are created in one or more calls
    Then the todo behavior succeeds

  Scenario: Reorder implementation steps
    When mixed-state todos are reordered
    Then the todo behavior succeeds

  Scenario: Track completion and reopening
    When a todo is closed and reopened by attributed actors
    Then the todo behavior succeeds

  Scenario: Submit a task with no todos
    When a todo-free task is submitted for review
    Then the todo behavior succeeds

  Scenario: Block review while work remains
    When review is attempted with open todos
    Then the todo behavior succeeds

  Scenario: Submit after all todos close
    When review is attempted after every todo closes
    Then the todo behavior succeeds

  Scenario: Block resubmission after reopening work
    When returned work gains open todos before resubmission
    Then the todo behavior succeeds

  Scenario: Preserve existing task-item and subtask behavior
    When todos coexist with ordinary items and an unfinished subtask
    Then the todo behavior succeeds

  Scenario: Reject invalid todo operations atomically
    When invalid todo operations are attempted
    Then the todo behavior succeeds

  Scenario: Keep CLI and Python API behavior equivalent
    When todo operations are mixed across the CLI and Python API
    Then the todo behavior succeeds

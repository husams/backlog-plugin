Feature: Real database compatibility

  Background:
    Given a new backlog project

  Scenario Outline: Historical SQLite stores migrate to the current schema
    Given a real SQLite store shaped like schema version <version>
    When the backlog opens the historical store
    Then the database schema is current
    And current task types and gates are available

    Examples:
      | version |
      | 7       |
      | 9       |
      | 10      |
      | 12      |
      | 14      |
      | 15      |
      | 16      |

  Scenario: A legacy version two export imports into the real database
    Given a version two export containing linked work
    When the legacy export is imported
    Then the migrated legacy work is queryable

  Scenario: A real version two SQLite database migrates in place
    Given a real version two SQLite database
    When the backlog opens the historical store
    Then the database schema is current
    And the version two database work is queryable

  Scenario: A database from a newer tool is rejected safely
    Given a real SQLite store from a newer schema version
    When the newer store is opened
    Then the command reports "newer than this tool"

  Scenario: A damaged database is named as not implementing its schema version
    Given a real SQLite store with a damaged schema
    When the damaged store is opened
    Then the command reports "table task is missing"
    And the command reports "backlog doctor --repair"
    When the store is repaired
    Then the store is healthy

  Scenario: Repairing a healthy store changes nothing
    When the store is repaired
    Then the command reports "already matches"
    And the store is healthy

  Scenario: A store that lost a migrated column is reported and repaired
    Given a real SQLite store missing the column its schema version promises
    When the damaged store is opened
    Then the command reports "task_item.updated_by is missing"
    When the store is repaired
    Then the database schema is current
    And ordered todos work end to end

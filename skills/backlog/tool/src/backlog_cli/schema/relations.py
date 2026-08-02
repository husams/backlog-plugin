"""Pull request, dependency, review, and artifact vocabularies."""

from __future__ import annotations

from enum import Enum

# --------------------------------------------------------------------------- #

PR_STATES = ["none", "draft", "open", "merged", "closed"]
PR_REVIEW_STATES = ["none", "pending", "changes_requested", "approved"]

# A feature is a container: it has no branch and no PR of its own, so the PR
# gates simply do not apply to it.
PR_BEARING_TYPES = {"story", "bug", "subtask"}

DEPENDENCY_KINDS = ["blocks", "relates", "duplicates"]
HARD_DEPENDENCY_KINDS = {"blocks"}
SYMMETRIC_DEPENDENCY_KINDS = {"relates"}

DEPENDENCY_KIND_ALIASES = {
    "block": "blocks",
    "blocking": "blocks",
    "depends_on": "blocks",
    "dependson": "blocks",
    "relate": "relates",
    "related": "relates",
    "relates_to": "relates",
    "duplicate": "duplicates",
    "dupe": "duplicates",
}

REVIEW_ACTIONS = ["open", "comment", "fix", "reject", "accept"]
REVIEW_ROLES = ["reviewer", "developer"]
THREAD_STATES = ["awaiting_developer", "awaiting_reviewer", "closed"]


class ReviewSeverity(str, Enum):
    """Fixed impact levels for a review thread."""

    BLOCKER = "blocker"
    NICE_TO_HAVE = "nice_to_have"
    INFO = "info"


REVIEW_SEVERITIES = [severity.value for severity in ReviewSeverity]

ARTIFACT_KINDS = ["doc", "spec", "design", "log", "report", "patch", "data"]

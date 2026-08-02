"""Task item kinds and aliases."""

from __future__ import annotations

# --------------------------------------------------------------------------- #

ITEM_KINDS = ["acceptance_criteria", "checklist", "note", "todo"]
GENERIC_ITEM_KINDS = ["acceptance_criteria", "checklist", "note"]

ITEM_KIND_DISPLAY = {
    "acceptance_criteria": "acceptance criteria",
    "checklist": "checklist",
    "note": "note",
    "todo": "todos",
}

ITEM_KIND_ALIASES = {
    "ac": "acceptance_criteria",
    "acceptance": "acceptance_criteria",
    "criteria": "acceptance_criteria",
    "check": "checklist",
    "notes": "note",
}

# Only a checklist entry is tickable; criteria are proven by review, notes by
# nothing at all.
TICKABLE_ITEM_KINDS = {"checklist"}

# --------------------------------------------------------------------------- #

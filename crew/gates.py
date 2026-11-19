"""Merge gate: no merge without Reviewer sign-off.

``reviews`` is an iterable of mappings with ``role`` and ``approved``
keys. Merge is allowed only when at least one review has role
"reviewer" and approved true. Builder/Planner/Merger approval, or a
Reviewer review that is not an approval, never opens the gate.
"""

from __future__ import annotations

REVIEWER_ROLE = "reviewer"


def can_merge(reviews) -> bool:
    """True only when a reviewer has approved."""
    return any(
        r.get("role") == REVIEWER_ROLE and r.get("approved") is True
        for r in reviews
    )


__all__ = ["REVIEWER_ROLE", "can_merge"]

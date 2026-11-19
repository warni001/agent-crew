"""Credit / budget guard. Env-based stub for v1 — no live Azure Cost
Management calls, in tests or in the loop.

Stop rules: spent reaches 95% of the limit (~$190 of $200), or the hard
deadline passes. Pure functions, so they are trivially testable offline.
"""

from __future__ import annotations

from datetime import datetime, timezone

STOP_FRACTION = 0.95


def stop_threshold_usd(limit_usd: float) -> float:
    """Spend at or above this stops the crew."""
    return limit_usd * STOP_FRACTION


def remaining_usd(limit_usd: float, spent_usd: float) -> float:
    """Budget left, never below zero."""
    return max(0.0, limit_usd - spent_usd)


def _parse_deadline(deadline_iso: str) -> datetime:
    return datetime.fromisoformat(deadline_iso.replace("Z", "+00:00"))


def should_stop(
    spent_usd: float,
    limit_usd: float,
    deadline_iso: str,
    now: datetime | None = None,
) -> bool:
    """True when the crew must stop: at the 95% spend line or past deadline."""
    now = now or datetime.now(timezone.utc)
    if spent_usd >= stop_threshold_usd(limit_usd):
        return True
    return now >= _parse_deadline(deadline_iso)


def stop_reason(
    spent_usd: float,
    limit_usd: float,
    deadline_iso: str,
    now: datetime | None = None,
) -> str | None:
    """Why the crew must stop, or None when it may continue."""
    now = now or datetime.now(timezone.utc)
    if spent_usd >= stop_threshold_usd(limit_usd):
        return (
            f"spent ${spent_usd:.2f} reached the stop line "
            f"${stop_threshold_usd(limit_usd):.2f} "
            f"(95% of ${limit_usd:.2f} budget)"
        )
    if now >= _parse_deadline(deadline_iso):
        return f"hard deadline {deadline_iso} has passed"
    return None


def status_line(spent_usd: float, limit_usd: float, deadline_iso: str) -> str:
    """One-line budget status for the crew (and for logs)."""
    return (
        f"Budget: ${spent_usd:.2f} spent of ${limit_usd:.2f} "
        f"(${remaining_usd(limit_usd, spent_usd):.2f} left, "
        f"stop at ${stop_threshold_usd(limit_usd):.2f}); "
        f"deadline {deadline_iso}"
    )

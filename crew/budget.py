"""Budget constants and stop check. Thin wrapper over crew.credit.

Stop when spent reaches $190 (95% of the $200 budget) or the hard
deadline passes — Sat Dec 26 2026, 3:08:36pm America/New_York.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from .credit import should_stop as _should_stop

BUDGET_USD = 200.0
STOP_THRESHOLD_USD = 190.0
HARD_DEADLINE = datetime(2026, 12, 26, 15, 8, 36, tzinfo=ZoneInfo("America/New_York"))


def should_stop(spent_usd: float, now: datetime) -> bool:
    """True when the crew must stop: at $190 spent or past the deadline."""
    return _should_stop(spent_usd, BUDGET_USD, "2026-12-26T20:08:36Z", now=now)


__all__ = ["BUDGET_USD", "STOP_THRESHOLD_USD", "HARD_DEADLINE", "should_stop"]

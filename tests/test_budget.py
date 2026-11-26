"""Credit-check stop threshold: stop at >= $190 of $200, or past deadline.

Contract under test (see docs/crew-instructions.md, "Module contract")::

    crew.budget.BUDGET_USD == 200.0
    crew.budget.STOP_THRESHOLD_USD == 190.0   # 95% — the kill-switch budget
    crew.budget.HARD_DEADLINE  # Sat Dec 26 2026, 3:08:36pm America/New_York
    crew.budget.should_stop(spent_usd: float, now: datetime) -> bool

Stop when spent >= $190 OR now >= deadline. The $10 headroom exists
because Cost Management evaluates budgets roughly every 24h on cost
data 8–24h late — the threshold is a backstop, not a real-time cutoff.
The Recurrence trigger at the deadline is the hard stop that does not
depend on cost data at all.

Blocked until ``crew/budget.py`` is merged; these tests skip until then.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from conftest import require_crew

ET = ZoneInfo("America/New_York")
DEADLINE = datetime(2026, 12, 26, 15, 8, 36, tzinfo=ET)
BEFORE = datetime(2026, 11, 19, 12, 0, tzinfo=ET)


@pytest.fixture
def budget_mod():
    return require_crew("budget")


def test_budget_and_threshold_constants(budget_mod):
    assert budget_mod.BUDGET_USD == 200.0
    assert budget_mod.STOP_THRESHOLD_USD == 190.0
    assert budget_mod.STOP_THRESHOLD_USD == 0.95 * budget_mod.BUDGET_USD


def test_hard_deadline_is_sat_dec_26_308pm_et(budget_mod):
    deadline = budget_mod.HARD_DEADLINE
    assert (deadline.year, deadline.month, deadline.day) == (2026, 12, 26)
    assert (deadline.hour, deadline.minute, deadline.second) == (15, 8, 36)
    assert deadline.strftime("%A") == "Saturday"
    assert str(deadline.tzinfo) in ("America/New_York", "EST") or deadline.utcoffset().total_seconds() == -18000


def test_no_stop_well_under_threshold_before_deadline(budget_mod):
    assert budget_mod.should_stop(0.0, BEFORE) is False
    assert budget_mod.should_stop(189.99, BEFORE) is False


def test_stop_at_exactly_190(budget_mod):
    assert budget_mod.should_stop(190.0, BEFORE) is True


def test_stop_above_threshold(budget_mod):
    assert budget_mod.should_stop(199.99, BEFORE) is True
    assert budget_mod.should_stop(250.0, BEFORE) is True


def test_stop_past_deadline_even_at_zero_spend(budget_mod):
    assert budget_mod.should_stop(0.0, datetime(2026, 12, 26, 15, 8, 36, tzinfo=ET)) is True
    assert budget_mod.should_stop(0.0, datetime(2026, 12, 27, 9, 0, tzinfo=ET)) is True


def test_no_stop_one_minute_before_deadline_at_zero_spend(budget_mod):
    assert budget_mod.should_stop(0.0, datetime(2026, 12, 26, 15, 7, 36, tzinfo=ET)) is False


def test_negative_spend_never_stops_on_money_alone(budget_mod):
    # A credit/refund cannot mask a blown deadline.
    assert budget_mod.should_stop(-5.0, BEFORE) is False
    assert budget_mod.should_stop(-5.0, datetime(2026, 12, 26, 15, 8, 36, tzinfo=ET)) is True

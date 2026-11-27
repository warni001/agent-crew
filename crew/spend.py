"""Real spend tracking from API-reported token usage.

This replaces the old env-var stub (crew/credit.py docstring said it:
spent came from CREW_BUDGET_SPENT_USD, default 0, and nothing ever
updated it, so the spend half of the stop rule never fired). Spend is
now counted from the usage the Responses API reports on every model
call — see crew/clients.py CountingChatClient — priced per deployment,
and accumulated in one process-wide tracker.

Prices are $ per 1M tokens, as billed on this subscription:
  gpt-5.4-mini (Data Zone Standard): $0.83 input, $0.09 cached input,
  $4.95 output
  gpt-6.2-sol (Global Standard): $2.00 input, $0.10 cached input,
  $10.00 output (OpenAI pricing page, Nov 17, 2026; verified live
  Nov 27 — Sol deployment provisioned on this sub, 1M TPM pool)
Cached tokens are a subset of input tokens, so they are priced at the
cached rate and the rest of the input at the full rate. Reasoning
tokens are a subset of output tokens — they are NOT added twice.
Cache-write tokens have no separate price here and are counted as
regular input.

The tracker is deliberately small and dependency-free: it reads plain
mappings (the framework's UsageDetails is a dict), so it needs no
agent-framework import and is trivially testable offline.

Persistence: the running total lives in memory for stop decisions and
is written to the run ledger as `spend` events — every $5 crossed and
at the end of each task (see crew/__main__.py). On startup the tracker
restores from the last `spend` event, so a container restart loses at
most the spend since the last $5 milestone. CREW_BUDGET_SPENT_USD
remains as a manual starting offset for spend incurred before this
guard existed; restore takes the larger of the two.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from typing import Any

from .credit import stop_threshold_usd

log = logging.getLogger(__name__)

# $ per 1M tokens. Keyed by deployment name; the Responses API reports
# the model name with dots (gpt-5.4-mini, gpt-6.2-sol), so both spellings
# map here.
_PRICES_USD_PER_MILLION: dict[str, dict[str, float]] = {
    "gpt-5-4-mini": {"input": 0.83, "cached_input": 0.09, "output": 4.95},
    "gpt-6-2-sol": {"input": 2.0, "cached_input": 0.10, "output": 10.0},
}
_ALIASES = {
    "gpt-5.4-mini": "gpt-5-4-mini",
    # The Responses API reports the versioned deployment name.
    "gpt-5.4-mini-2026-03-17": "gpt-5-4-mini",
    "gpt-6.2-sol": "gpt-6-2-sol",
    "gpt-6.2-sol-2026-11-17": "gpt-6-2-sol",
    # Trial deployment name (Nov 27: gpt-6-2-sol-test provisioned and
    # answered on this sub, then deleted; the alias prices any future
    # deployment under that name at Sol rates).
    "gpt-6-2-sol-test": "gpt-6-2-sol",
}
# Unknown deployment: price at the old Astra rates ($8 in / $0.40
# cached / $40 out) and say so, so a new model can only stop the crew
# early, never overshoot.
_FALLBACK_PRICES = {"input": 8.0, "cached_input": 0.40, "output": 40.0}

#: Ledger event carrying the cumulative total; restore reads the last one.
SPEND_EVENT = "spend"
#: Write a ledger event each time the total crosses another $5.
MILESTONE_USD = 5.0


class SpendLimitExceeded(RuntimeError):
    """Raised before a model call when the tracked total hit the stop line."""


def _prices_for(model: str | None) -> dict[str, float]:
    """Price table for a deployment name; old Astra rates for anything unknown."""
    key = _ALIASES.get(model or "", model or "")
    prices = _PRICES_USD_PER_MILLION.get(key)
    if prices is None:
        log.warning("unknown model %r for spend pricing; using Astra rates", model)
        return _FALLBACK_PRICES
    return prices


def price_usd(model: str | None, usage: Mapping[str, Any] | None) -> float:
    """Dollars for one response's reported usage. None usage costs $0.

    usage keys (framework UsageDetails): input_token_count,
    cache_read_input_token_count, output_token_count. Missing keys
    count as zero; cached tokens are capped at the input total.
    """
    if not usage:
        return 0.0
    prices = _prices_for(model)
    total_in = int(usage.get("input_token_count") or 0)
    cached = int(usage.get("cache_read_input_token_count") or 0)
    cached = min(max(cached, 0), total_in)
    output = int(usage.get("output_token_count") or 0)
    return (
        (total_in - cached) * prices["input"]
        + cached * prices["cached_input"]
        + output * prices["output"]
    ) / 1_000_000


class SpendTracker:
    """Accumulated spend for one crew process.

    Single process, no locking — same as the ledger (one writer). The
    tracker never raises on record(): the client wrapper checks the
    stop line *before* the next call instead, so the overshoot past
    the stop line is at most one call's cost.
    """

    def __init__(
        self,
        limit_usd: float = 200.0,
        milestone_usd: float = MILESTONE_USD,
        on_milestone: Callable[[float, dict[str, Any]], None] | None = None,
    ) -> None:
        self.limit_usd = limit_usd
        self.milestone_usd = milestone_usd
        self.on_milestone = on_milestone
        self._total_usd = 0.0
        self._last_milestone_usd = 0.0
        self._tokens: dict[str, dict[str, int]] = {}

    def reset(
        self,
        starting_usd: float = 0.0,
        limit_usd: float | None = None,
        on_milestone: Callable[[float, dict[str, Any]], None] | None = None,
    ) -> None:
        """Zero the totals (tests, process start). Keeps the milestone hook unless replaced."""
        self._total_usd = max(0.0, float(starting_usd))
        self._last_milestone_usd = self._total_usd
        self._tokens = {}
        if limit_usd is not None:
            self.limit_usd = limit_usd
        if on_milestone is not None:
            self.on_milestone = on_milestone

    def record(self, model: str | None, usage: Mapping[str, Any] | None) -> float:
        """Add one response's cost; return the new cumulative total."""
        cost = price_usd(model, usage)
        if usage:
            key = _ALIASES.get(model or "", model or "unknown")
            entry = self._tokens.setdefault(
                key, {"input": 0, "cached": 0, "output": 0}
            )
            total_in = int(usage.get("input_token_count") or 0)
            cached = min(
                max(int(usage.get("cache_read_input_token_count") or 0), 0),
                total_in,
            )
            entry["input"] += total_in - cached
            entry["cached"] += cached
            entry["output"] += int(usage.get("output_token_count") or 0)
        self._total_usd += cost
        self._fire_milestones()
        return self._total_usd

    def _fire_milestones(self) -> None:
        """Call the milestone hook once per $5 crossed since the last one."""
        if self.on_milestone is None:
            return
        while self._total_usd - self._last_milestone_usd >= self.milestone_usd:
            self._last_milestone_usd += self.milestone_usd
            self.on_milestone(self._total_usd, self.snapshot())

    def total_usd(self) -> float:
        """Cumulative spend in dollars."""
        return self._total_usd

    def stop_threshold_usd(self) -> float:
        """Spend at or above this stops the crew (95% of the limit)."""
        return stop_threshold_usd(self.limit_usd)

    def should_stop(self) -> bool:
        """True when the tracked total reached the stop line."""
        return self._total_usd >= self.stop_threshold_usd()

    def snapshot(self) -> dict[str, Any]:
        """Ledger-friendly totals: dollars plus per-model token counts."""
        return {
            "usd": round(self._total_usd, 4),
            "by_model": {
                model: dict(counts) for model, counts in self._tokens.items()
            },
        }

    def restore(self, entries: list[dict[str, Any]]) -> float:
        """Restore the total from the last `spend` ledger event.

        Takes the larger of the current total and the last recorded
        cumulative total, so a manual starting offset is never lowered
        by a stale ledger. Returns the restored total.
        """
        for entry in reversed(entries):
            if entry.get("event") != SPEND_EVENT:
                continue
            recorded = entry.get("usd")
            if isinstance(recorded, (int, float)):
                self._total_usd = max(self._total_usd, float(recorded))
                self._last_milestone_usd = self._total_usd
                break
        return self._total_usd


# One process-wide tracker: the crew runs one loop in one process, and
# every client in the process reports into it. Tests reset it.
TRACKER = SpendTracker()


def record_usage(model: str | None, usage: Mapping[str, Any] | None) -> float:
    """Add one response's cost to the process total; return the new total."""
    return TRACKER.record(model, usage)


def spent_usd() -> float:
    """Cumulative tracked spend in dollars."""
    return TRACKER.total_usd()


def reset_spend(
    starting_usd: float = 0.0,
    limit_usd: float = 200.0,
    on_milestone: Callable[[float, dict[str, Any]], None] | None = None,
) -> None:
    """(Re)start tracking: seed from the manual offset, set the limit."""
    TRACKER.reset(starting_usd, limit_usd, on_milestone)


def restore_spend(entries: list[dict[str, Any]]) -> float:
    """Restore the total from ledger `spend` events; return the total."""
    return TRACKER.restore(entries)


__all__ = [
    "SPEND_EVENT",
    "MILESTONE_USD",
    "SpendLimitExceeded",
    "SpendTracker",
    "TRACKER",
    "price_usd",
    "record_usage",
    "reset_spend",
    "restore_spend",
    "spent_usd",
]

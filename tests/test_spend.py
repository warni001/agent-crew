"""Spend tracking: pricing from API-reported usage, tracker totals,
milestone callbacks, restore from ledger events.

Prices are $ per 1M tokens: Astra $8 in / $0.40 cached / $40 out,
Sol $2 in / $0.10 cached / $10 out. All usage is faked; no network.
"""

from __future__ import annotations

import pytest

from conftest import require_crew


@pytest.fixture
def spend_mod():
    return require_crew("spend")


@pytest.fixture(autouse=True)
def clean_tracker(spend_mod):
    spend_mod.reset_spend()
    spend_mod.TRACKER.on_milestone = None
    yield
    spend_mod.reset_spend()
    spend_mod.TRACKER.on_milestone = None


# --- pricing -----------------------------------------------------------

ASTRA_IN = {"input_token_count": 1_000_000}
ASTRA_OUT = {"output_token_count": 1_000_000}
SOL_IN = {"input_token_count": 1_000_000}
SOL_OUT = {"output_token_count": 1_000_000}


def test_astra_input_price(spend_mod):
    assert spend_mod.price_usd("gpt-6-2-astra", ASTRA_IN) == pytest.approx(8.0)


def test_astra_output_price(spend_mod):
    assert spend_mod.price_usd("gpt-6-2-astra", ASTRA_OUT) == pytest.approx(40.0)


def test_sol_prices(spend_mod):
    assert spend_mod.price_usd("gpt-6-2-sol", SOL_IN) == pytest.approx(2.0)
    assert spend_mod.price_usd("gpt-6-2-sol", SOL_OUT) == pytest.approx(10.0)


def test_cached_input_at_cached_rate(spend_mod):
    usage = {"input_token_count": 1_000_000, "cache_read_input_token_count": 1_000_000}
    assert spend_mod.price_usd("gpt-6-2-astra", usage) == pytest.approx(0.40)


def test_mixed_usage(spend_mod):
    usage = {
        "input_token_count": 1_000_000,
        "cache_read_input_token_count": 500_000,
        "output_token_count": 100_000,
    }
    # (500k x $8 + 500k x $0.40 + 100k x $40) / 1M = 4 + 0.20 + 4
    assert spend_mod.price_usd("gpt-6-2-astra", usage) == pytest.approx(8.20)


def test_reasoning_tokens_not_double_counted(spend_mod):
    # reasoning_output_token_count is a subset of output_token_count.
    usage = {
        "output_token_count": 100_000,
        "reasoning_output_token_count": 90_000,
    }
    assert spend_mod.price_usd("gpt-6-2-astra", usage) == pytest.approx(4.0)


def test_none_and_empty_usage_cost_zero(spend_mod):
    assert spend_mod.price_usd("gpt-6-2-astra", None) == 0.0
    assert spend_mod.price_usd("gpt-6-2-astra", {}) == 0.0
    assert spend_mod.price_usd("gpt-6-2-astra", {"input_token_count": None}) == 0.0


def test_cached_capped_at_input(spend_mod):
    # A malformed report claiming more cached than input: cap it.
    usage = {"input_token_count": 100, "cache_read_input_token_count": 1_000_000}
    assert spend_mod.price_usd("gpt-6-2-astra", usage) == pytest.approx(0.40 * 100 / 1_000_000)


def test_dotted_model_name_maps(spend_mod):
    # The API reports "gpt-6.2-astra"; the deployment is "gpt-6-2-astra".
    assert spend_mod.price_usd("gpt-6.2-astra", ASTRA_IN) == pytest.approx(8.0)
    assert spend_mod.price_usd("gpt-6.2-sol", SOL_IN) == pytest.approx(2.0)


def test_unknown_model_priced_at_astra_rates(spend_mod):
    # A new deployment can only stop the crew early, never overshoot.
    assert spend_mod.price_usd("gpt-9-2-mystery", ASTRA_IN) == pytest.approx(8.0)
    assert spend_mod.price_usd(None, ASTRA_OUT) == pytest.approx(40.0)


# --- tracker -----------------------------------------------------------

def test_tracker_accumulates_and_snapshots(spend_mod):
    tracker = spend_mod.SpendTracker()
    tracker.record("gpt-6-2-astra", ASTRA_IN)
    tracker.record("gpt-6-2-sol", SOL_OUT)
    assert tracker.total_usd() == pytest.approx(18.0)
    snap = tracker.snapshot()
    assert snap["usd"] == pytest.approx(18.0)
    assert snap["by_model"]["gpt-6-2-astra"]["input"] == 1_000_000
    assert snap["by_model"]["gpt-6-2-sol"]["output"] == 1_000_000


def test_stop_line_is_95_percent(spend_mod):
    tracker = spend_mod.SpendTracker(limit_usd=200.0)
    assert tracker.stop_threshold_usd() == pytest.approx(190.0)
    assert tracker.should_stop() is False
    tracker.record("gpt-6-2-astra", {"output_token_count": 4_750_000})
    assert tracker.total_usd() == pytest.approx(190.0)
    assert tracker.should_stop() is True


def test_milestone_callback_fires_per_five_dollars(spend_mod):
    fired: list[float] = []
    tracker = spend_mod.SpendTracker(
        on_milestone=lambda usd, snap: fired.append(usd)
    )
    tracker.record("gpt-6-2-astra", {"output_token_count": 300_000})  # $12.00
    assert fired == [pytest.approx(12.0), pytest.approx(12.0)]
    tracker.record("gpt-6-2-astra", {"output_token_count": 125_000})  # +$5.00
    assert len(fired) == 3  # crossed $15
    assert fired[-1] == pytest.approx(17.0)


def test_milestone_not_fired_below_boundary(spend_mod):
    fired: list[float] = []
    tracker = spend_mod.SpendTracker(
        on_milestone=lambda usd, snap: fired.append(usd)
    )
    tracker.record("gpt-6-2-astra", {"output_token_count": 100_000})  # $4.00
    assert fired == []


def test_reset_seeds_and_clears(spend_mod):
    spend_mod.record_usage("gpt-6-2-astra", ASTRA_IN)
    assert spend_mod.spent_usd() == pytest.approx(8.0)
    spend_mod.reset_spend(starting_usd=25.0)
    assert spend_mod.spent_usd() == pytest.approx(25.0)
    spend_mod.record_usage("gpt-6-2-sol", SOL_OUT)
    assert spend_mod.spent_usd() == pytest.approx(35.0)


# --- restore -----------------------------------------------------------

def test_restore_takes_last_spend_event(spend_mod):
    tracker = spend_mod.SpendTracker()
    entries = [
        {"event": "task_started", "task": "x"},
        {"event": "spend", "usd": 12.5},
        {"event": "task_finished", "task": "x"},
        {"event": "spend", "usd": 47.25},
    ]
    assert tracker.restore(entries) == pytest.approx(47.25)


def test_restore_never_lowers_the_total(spend_mod):
    tracker = spend_mod.SpendTracker()
    tracker.record("gpt-6-2-astra", ASTRA_IN)  # $8.00
    assert tracker.restore([{"event": "spend", "usd": 3.0}]) == pytest.approx(8.0)


def test_restore_ignores_missing_or_bad_events(spend_mod):
    tracker = spend_mod.SpendTracker()
    assert tracker.restore([]) == pytest.approx(0.0)
    assert tracker.restore([{"event": "spend"}]) == pytest.approx(0.0)
    assert tracker.restore([{"event": "spend", "usd": "lots"}]) == pytest.approx(0.0)


def test_module_helpers_round_trip(spend_mod):
    spend_mod.reset_spend(starting_usd=10.0)
    spend_mod.record_usage("gpt-6-2-sol", SOL_IN)  # +$2.00
    assert spend_mod.spent_usd() == pytest.approx(12.0)
    spend_mod.restore_spend([{"event": "spend", "usd": 50.0}])
    assert spend_mod.spent_usd() == pytest.approx(50.0)


# --- wiring: tools and orchestration read the tracker -----------------

def test_run_task_refuses_when_tracked_spend_over_line(spend_mod):
    import asyncio
    from unittest import mock

    orch_mod = require_crew("orchestration")
    config_mod = require_crew("config")
    config = config_mod.CrewConfig(
        foundry_project_endpoint="https://foundry-gpt-vaxvu3c2.services.ai.azure.com/api/projects/crew"
    )
    workflow = mock.AsyncMock()
    result = mock.MagicMock()
    result.get_outputs.return_value = ["done"]
    workflow.run.return_value = result
    # Under the line: runs.
    assert asyncio.run(orch_mod.run_task(workflow, "do it", config)) == ["done"]
    # Over the line: refuses before any model call.
    spend_mod.reset_spend(starting_usd=190.0)
    with pytest.raises(RuntimeError, match="crew stopped before starting"):
        asyncio.run(orch_mod.run_task(workflow, "do it", config))

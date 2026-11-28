"""Spend tracking: pricing from API-reported usage, tracker totals,
milestone callbacks, restore from ledger events.

Prices are $ per 1M tokens: gpt-5.4-mini (Data Zone Standard) $0.83 in /
$0.09 cached / $4.95 out. All usage is faked; no network.
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

MINI_IN = {"input_token_count": 1_000_000}
MINI_OUT = {"output_token_count": 1_000_000}


def test_mini_input_price(spend_mod):
    assert spend_mod.price_usd("gpt-5-4-mini", MINI_IN) == pytest.approx(0.83)


def test_mini_output_price(spend_mod):
    assert spend_mod.price_usd("gpt-5-4-mini", MINI_OUT) == pytest.approx(4.95)


def test_cached_input_at_cached_rate(spend_mod):
    usage = {"input_token_count": 1_000_000, "cache_read_input_token_count": 1_000_000}
    assert spend_mod.price_usd("gpt-5-4-mini", usage) == pytest.approx(0.09)


def test_mixed_usage(spend_mod):
    usage = {
        "input_token_count": 1_000_000,
        "cache_read_input_token_count": 500_000,
        "output_token_count": 100_000,
    }
    # (500k x $0.83 + 500k x $0.09 + 100k x $4.95) / 1M = 0.415 + 0.045 + 0.495
    assert spend_mod.price_usd("gpt-5-4-mini", usage) == pytest.approx(0.955)


def test_reasoning_tokens_not_double_counted(spend_mod):
    # reasoning_output_token_count is a subset of output_token_count.
    usage = {
        "output_token_count": 100_000,
        "reasoning_output_token_count": 90_000,
    }
    assert spend_mod.price_usd("gpt-5-4-mini", usage) == pytest.approx(0.495)


def test_none_and_empty_usage_cost_zero(spend_mod):
    assert spend_mod.price_usd("gpt-5-4-mini", None) == 0.0
    assert spend_mod.price_usd("gpt-5-4-mini", {}) == 0.0
    assert spend_mod.price_usd("gpt-5-4-mini", {"input_token_count": None}) == 0.0


def test_cached_capped_at_input(spend_mod):
    # A malformed report claiming more cached than input: cap it.
    usage = {"input_token_count": 100, "cache_read_input_token_count": 1_000_000}
    assert spend_mod.price_usd("gpt-5-4-mini", usage) == pytest.approx(0.09 * 100 / 1_000_000)


def test_dotted_model_name_maps(spend_mod):
    # The API reports "gpt-5.4-mini"; the deployment is "gpt-5-4-mini".
    assert spend_mod.price_usd("gpt-5.4-mini", MINI_IN) == pytest.approx(0.83)


def test_versioned_model_name_maps(spend_mod):
    # The Responses API reports the versioned deployment name
    # "gpt-5.4-mini-2026-03-17"; it prices at mini rates, not the
    # Astra fallback (Nov 27: the #75 retry logged the Astra warning
    # and overstated spend ~10x).
    assert spend_mod.price_usd("gpt-5.4-mini-2026-03-17", MINI_IN) == pytest.approx(0.83)


SOL_IN = {"input_token_count": 1_000_000}
SOL_OUT = {"output_token_count": 1_000_000}


def test_sol_input_price(spend_mod):
    assert spend_mod.price_usd("gpt-6-2-sol", SOL_IN) == pytest.approx(2.0)


def test_sol_output_price(spend_mod):
    assert spend_mod.price_usd("gpt-6-2-sol", SOL_OUT) == pytest.approx(10.0)


def test_sol_cached_input_price(spend_mod):
    usage = {"input_token_count": 1_000_000, "cache_read_input_token_count": 1_000_000}
    assert spend_mod.price_usd("gpt-6-2-sol", usage) == pytest.approx(0.10)


def test_sol_dotted_and_versioned_names_map(spend_mod):
    # The API reports "gpt-6.2-sol" (and the versioned deployment name);
    # both price at Sol rates, not the Astra fallback.
    assert spend_mod.price_usd("gpt-6.2-sol", SOL_IN) == pytest.approx(2.0)
    assert spend_mod.price_usd("gpt-6.2-sol-2026-11-17", SOL_IN) == pytest.approx(2.0)


def test_sol_test_deployment_name_maps(spend_mod):
    # The Nov 27 trial deployment gpt-6-2-sol-test prices at Sol rates.
    assert spend_mod.price_usd("gpt-6-2-sol-test", SOL_IN) == pytest.approx(2.0)


def test_unknown_model_priced_at_fallback_rates(spend_mod):
    # A new deployment can only stop the crew early, never overshoot:
    # unknown models price at the old Astra rates ($8 in / $40 out).
    assert spend_mod.price_usd("gpt-9-2-mystery", MINI_IN) == pytest.approx(8.0)
    assert spend_mod.price_usd(None, MINI_OUT) == pytest.approx(40.0)


# --- tracker -----------------------------------------------------------

def test_tracker_accumulates_and_snapshots(spend_mod):
    tracker = spend_mod.SpendTracker()
    tracker.record("gpt-5-4-mini", MINI_IN)
    tracker.record("gpt-5-4-mini", MINI_OUT)
    assert tracker.total_usd() == pytest.approx(5.78)
    snap = tracker.snapshot()
    assert snap["usd"] == pytest.approx(5.78)
    assert snap["by_model"]["gpt-5-4-mini"]["input"] == 1_000_000
    assert snap["by_model"]["gpt-5-4-mini"]["output"] == 1_000_000


def test_stop_line_is_95_percent(spend_mod):
    tracker = spend_mod.SpendTracker(limit_usd=200.0)
    assert tracker.stop_threshold_usd() == pytest.approx(190.0)
    assert tracker.should_stop() is False
    # 38,383,839 output tokens x $4.95 / 1M ≈ $190.00
    tracker.record("gpt-5-4-mini", {"output_token_count": 38_383_839})
    assert tracker.total_usd() == pytest.approx(190.0)
    assert tracker.should_stop() is True


def test_milestone_callback_fires_per_five_dollars(spend_mod):
    fired: list[float] = []
    tracker = spend_mod.SpendTracker(
        on_milestone=lambda usd, snap: fired.append(usd)
    )
    # 2,424,243 output tokens x $4.95 / 1M ≈ $12.00
    tracker.record("gpt-5-4-mini", {"output_token_count": 2_424_243})  # $12.00
    assert fired == [pytest.approx(12.0), pytest.approx(12.0)]
    # 1,010,101 output tokens x $4.95 / 1M ≈ $5.00
    tracker.record("gpt-5-4-mini", {"output_token_count": 1_010_101})  # +$5.00
    assert len(fired) == 3  # crossed $15
    assert fired[-1] == pytest.approx(17.0)


def test_milestone_not_fired_below_boundary(spend_mod):
    fired: list[float] = []
    tracker = spend_mod.SpendTracker(
        on_milestone=lambda usd, snap: fired.append(usd)
    )
    tracker.record("gpt-5-4-mini", {"output_token_count": 100_000})  # $0.50
    assert fired == []


def test_reset_seeds_and_clears(spend_mod):
    spend_mod.record_usage("gpt-5-4-mini", MINI_IN)
    assert spend_mod.spent_usd() == pytest.approx(0.83)
    spend_mod.reset_spend(starting_usd=25.0)
    assert spend_mod.spent_usd() == pytest.approx(25.0)
    spend_mod.record_usage("gpt-5-4-mini", MINI_OUT)
    assert spend_mod.spent_usd() == pytest.approx(29.95)


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
    tracker.record("gpt-5-4-mini", MINI_OUT)  # $4.95
    assert tracker.restore([{"event": "spend", "usd": 3.0}]) == pytest.approx(4.95)


def test_restore_ignores_missing_or_bad_events(spend_mod):
    tracker = spend_mod.SpendTracker()
    assert tracker.restore([]) == pytest.approx(0.0)
    assert tracker.restore([{"event": "spend"}]) == pytest.approx(0.0)
    assert tracker.restore([{"event": "spend", "usd": "lots"}]) == pytest.approx(0.0)


def test_module_helpers_round_trip(spend_mod):
    spend_mod.reset_spend(starting_usd=10.0)
    spend_mod.record_usage("gpt-5-4-mini", MINI_IN)  # +$0.83
    assert spend_mod.spent_usd() == pytest.approx(10.83)
    spend_mod.restore_spend([{"event": "spend", "usd": 50.0}])
    assert spend_mod.spent_usd() == pytest.approx(50.0)


# --- wiring: tools and orchestration read the tracker -----------------

def test_run_task_refuses_when_tracked_spend_over_line(spend_mod):
    import asyncio
    from unittest import mock

    from agent_framework import AgentResponse, Content, Message

    orch_mod = require_crew("orchestration")
    config_mod = require_crew("config")
    config = config_mod.CrewConfig(
        foundry_project_endpoint="https://foundry-gpt-vaxvu3c2.services.ai.azure.com/api/projects/crew"
    )

    def _resp(text):
        return AgentResponse(
            messages=[Message(role="assistant", contents=[Content.from_text(text)])],
            response_id="resp-done",
        )

    roles = {}
    for name in ("planner", "builder", "reviewer", "merger"):
        # Plain MagicMock agent: only .run is async. A bare AsyncMock
        # would make .create_session async too, leaving an unawaited
        # coroutine behind.
        agent = mock.MagicMock()
        agent.run = mock.AsyncMock(return_value=_resp("done"))
        roles[name] = agent
    # Under the line: runs.
    outputs = asyncio.run(orch_mod.run_task(roles, "do it", config))
    assert [str(o) for o in outputs] == ["done"] * 4
    # Over the line: refuses before any model call.
    spend_mod.reset_spend(starting_usd=190.0)
    with pytest.raises(RuntimeError, match="crew stopped before starting"):
        asyncio.run(orch_mod.run_task(roles, "do it", config))


@pytest.fixture
def task_runner(tmp_path, monkeypatch):
    main = require_crew("__main__")
    ledger = main.Ledger(tmp_path / "ledger.jsonl")
    monkeypatch.setattr(main, "ensure_task_branch", lambda branch, ledger_path: branch)
    monkeypatch.setattr(main, "report_done", lambda *args: None)
    return main, ledger


@pytest.mark.parametrize("followup_tokens", [200_000, 250_000])
def test_task_cost_stops_inside_sdk_tool_loop(task_runner, monkeypatch, spend_mod, followup_tokens):
    from unittest.mock import Mock

    from agent_framework import Agent, ChatResponse, Content, Message, tool
    from agent_framework_openai import RawOpenAIChatClient

    main, ledger = task_runner
    spend_mod.reset_spend(starting_usd=40)
    calls = []
    tool_calls = []

    @tool
    def read_example() -> str:
        """Read an offline example."""
        tool_calls.append(1)
        return "example"

    async def fake_response(self, messages, **kwargs):
        calls.append(messages)
        first = len(calls) == 1
        return ChatResponse(
            messages=[Message(role="assistant", contents=[Content.from_function_call(
                call_id=f"call-{len(calls)}", name="read_example", arguments={},
            )])],
            model="gpt-6.2-sol",
            usage_details={"output_token_count": 100_000 if first else followup_tokens},
        )

    monkeypatch.setattr(RawOpenAIChatClient, "_inner_get_response", fake_response)
    client = require_crew("clients").CountingChatClient(model="gpt-6-2-sol", api_key="<offline-test>")
    later = Mock()
    roles = {"planner": Agent(client, name="planner", tools=[read_example]), "builder": later}
    monkeypatch.setattr(main, "build_roles", lambda cfg: roles)
    config = main.CrewConfig(ledger_path=str(ledger.path), deadline_iso="2099-01-01T00:00:00Z")

    assert main.run_one_task(config, ledger, "costly task") == 1
    started, failed, checkpoint = ledger.entries()
    assert failed["event"] == "task_failed"
    assert failed["reason"] == "task_cost_limit"
    assert len(calls) == 2
    assert tool_calls == [1]  # the crossing response's tool never runs
    later.create_session.assert_not_called()
    total = 41 + followup_tokens / 100_000
    assert checkpoint["event"] == "spend"
    assert checkpoint["usd"] == pytest.approx(total)
    spend_mod.check_task_cost()  # no failed attempt state leaked

    async def next_workflow(roles, task, config):
        spend_mod.record_usage("gpt-6-2-sol", {"output_token_count": 100_000})
        spend_mod.check_task_cost()
        ledger.record("review_sign_off", pr_number=1, verdict="approved")
        return ["done"]

    monkeypatch.setattr(main, "run_task", next_workflow)
    assert main.run_one_task(config, ledger, "next task") == 0
    assert ledger.entries()[-1]["usd"] == pytest.approx(total + 1)


def test_task_deadline_cancels_pending_workflow(task_runner, monkeypatch, spend_mod):
    import asyncio

    main, ledger = task_runner
    cancelled = []

    async def pending(*args):
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            cancelled.append(True)
            raise

    monkeypatch.setattr(main, "run_task", pending)
    monkeypatch.setattr(main, "build_roles", lambda config: {})
    config = main.CrewConfig(ledger_path=str(ledger.path), task_time_limit_minutes=0.002)
    assert main.run_one_task(config, ledger, "pending task") == 1
    assert cancelled == [True]
    assert ledger.entries()[-2]["reason"] == "task_time_limit"
    assert ledger.entries()[-1]["event"] == "spend"
    assert spend_mod.remaining_task_seconds() is None

    async def next_workflow(*args):
        assert spend_mod.remaining_task_seconds() > 0
        ledger.record("review_sign_off", pr_number=1, verdict="approved")
        return ["done"]

    monkeypatch.setattr(main, "run_task", next_workflow)
    assert main.run_one_task(config, ledger, "next task") == 0


def test_task_deadline_bounds_branch_subprocess(task_runner, monkeypatch, spend_mod):
    from unittest.mock import Mock

    main, ledger = task_runner
    tools = require_crew("tools")
    monkeypatch.setattr(main, "ensure_task_branch", tools.ensure_task_branch)
    build_roles = Mock()
    monkeypatch.setattr(main, "build_roles", build_roles)
    monkeypatch.setattr(spend_mod, "monotonic", lambda: 100.0)

    def timeout(*, timeout):
        assert timeout == pytest.approx(60)
        raise tools.subprocess.TimeoutExpired("git", timeout)

    proc = Mock(pid=1234)
    proc.communicate.side_effect = timeout
    popen = Mock(return_value=proc)
    killpg = Mock()
    monkeypatch.setattr(tools.subprocess, "Popen", popen)
    monkeypatch.setattr(tools.os, "killpg", killpg)
    config = main.CrewConfig(ledger_path=str(ledger.path), task_time_limit_minutes=1)
    assert main.run_one_task(config, ledger, "branch timeout") == 1
    assert ledger.entries()[-2]["reason"] == "task_time_limit"
    build_roles.assert_not_called()
    assert popen.call_args.kwargs["start_new_session"] is True
    killpg.assert_called_once_with(1234, tools.signal.SIGKILL)
    proc.stdout.close.assert_called_once()
    proc.stderr.close.assert_called_once()
    proc.wait.assert_called_once_with(timeout=0.25)


def test_command_timeout_kills_descendant_after_leader_exits(tmp_path):
    import os
    import sys
    import time

    if sys.platform != "linux":
        pytest.skip("uses /proc to distinguish a killed orphan from a live process")
    tools = require_crew("tools")
    pids = tmp_path / "pids"
    # The descendant keeps both pipes open after the leader exits. Limit
    # its lifetime even if _run regresses, and clean up on assertion failure.
    script = (
        "import os, subprocess, sys\n"
        "from pathlib import Path\n"
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(5)'])\n"
        "Path(sys.argv[1]).write_text(f'{os.getpid()} {child.pid}')\n"
    )
    start = time.monotonic()
    try:
        with pytest.raises(RuntimeError, match="command timed out"):
            tools._run([sys.executable, "-c", script, str(pids)], timeout=0.75)
        assert time.monotonic() - start < 2
        leader, child = (int(pid) for pid in pids.read_text().split())
        assert not os.path.exists(f"/proc/{leader}")  # direct child reaped

        def child_running():
            from pathlib import Path
            try:
                return Path(f"/proc/{child}/stat").read_text().rsplit(")", 1)[1].split()[0] != "Z"
            except FileNotFoundError:
                return False

        until = time.monotonic() + 1
        while child_running() and time.monotonic() < until:
            time.sleep(0.01)
        assert not child_running()
    finally:
        if pids.exists():
            try:
                os.killpg(int(pids.read_text().split()[0]), tools.signal.SIGKILL)
            except ProcessLookupError:
                pass

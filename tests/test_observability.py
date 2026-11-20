"""Observability: the Nov 20 #9 run stalled with no PR and no review,
and nothing said why — the workflow returned only the merger's
output, and tool failures surfaced as opaque RuntimeErrors. These
tests pin the fix: detailed tool errors, per-agent response logging,
and settlement summaries that say where a run broke.

All model calls are faked; no network.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from types import SimpleNamespace
from unittest import mock

import pytest
from agent_framework import AgentResponse, Content, Message
from agent_framework.orchestrations import SequentialBuilder

from conftest import require_crew

PROJECT_ENDPOINT = "https://foundry-gpt-vaxvu3c2.cognitiveservices.azure.com/"


def _resp(name, text, extra=None):
    """One agent's response: a text message, plus optional tool traffic."""
    contents = [Content.from_text(text)] + list(extra or [])
    return AgentResponse(
        messages=[
            Message(role="assistant", contents=contents, author_name=name)
        ],
        response_id=f"resp-{name}",
    )


def _tool_traffic(tool, result):
    """A matched call+result pair, as a real agent run returns it."""
    return [
        Content.from_function_call(
            call_id="call_1", name=tool, arguments='{"command": "git push"}'
        ),
        Content.from_function_result(call_id="call_1", result=result),
    ]


def _resp_with_tool(name, text, tool, result):
    """A response whose messages carry one tool call and its result."""
    call, res = _tool_traffic(tool, result)
    return AgentResponse(
        messages=[
            Message(role="assistant", contents=[call], author_name=name),
            Message(role="tool", contents=[res], author_name=name),
            Message(
                role="assistant",
                contents=[Content.from_text(text)],
                author_name=name,
            ),
        ],
        response_id=f"resp-{name}",
    )


class FakeAgent:
    """Stand-in for Agent: returns canned responses, records run inputs."""

    def __init__(self, replies, name="worker"):
        self.replies = list(replies)
        self.calls = []
        self.id = f"fake-{name}"
        self.name = name
        self.description = f"fake {name} agent"

    def create_session(self):
        return SimpleNamespace(state={})

    def get_session(self, *args, **kwargs):
        return None

    async def run(self, messages, *, session=None, **kwargs):
        self.calls.append(list(messages))
        return self.replies.pop(0)


# --- 1. detailed tool errors (crew/clients.py) ---


def test_counting_client_enables_detailed_errors():
    """Every real chat client reports the actual tool error to the
    model instead of the framework's opaque "Error: Function failed."
    """
    clients_mod = require_crew("clients")
    client = clients_mod.CountingChatClient(
        model="gpt-6-2-sol",
        azure_endpoint="https://example.openai.azure.com",
        credential=mock.MagicMock(),
        api_version="preview",
    )
    assert client.function_invocation_configuration["include_detailed_errors"] is True


# --- 1b. the container log shows the failed command and its error ---


def test_run_logs_failed_command_and_error(caplog):
    """A failing command is logged with the command and its output —
    the container log must not show only an opaque RuntimeError."""
    tools_mod = require_crew("tools")
    with caplog.at_level(logging.WARNING, logger="crew.tools"):
        with pytest.raises(RuntimeError):
            tools_mod._run([
                sys.executable, "-c",
                "import sys; sys.stderr.write('boom detail'); sys.exit(3)",
            ])
    assert "boom detail" in caplog.text
    assert "-c" in caplog.text


def test_run_shell_refusal_is_logged(caplog):
    """A refused command is logged with the command itself."""
    tools_mod = require_crew("tools")
    with caplog.at_level(logging.WARNING, logger="crew.tools"):
        with pytest.raises(ValueError):
            tools_mod.run_shell("rm -rf /")
    assert "rm -rf /" in caplog.text


# --- 2. every agent's response is returned and logged ---


def test_run_task_returns_and_logs_every_agent(monkeypatch, caplog):
    """run_task returns all four agents' responses in pipeline order
    (intermediates + final) and logs each one's text at INFO."""
    orch_mod = require_crew("orchestration")
    config_mod = require_crew("config")
    roles = {
        name: FakeAgent([_resp(name, f"{name} did its part")], name=name)
        for name in ("planner", "builder", "reviewer", "merger")
    }
    monkeypatch.setattr(orch_mod, "build_roles", lambda config: roles)
    monkeypatch.setattr(orch_mod, "spent_usd", lambda: 0.0)
    config = config_mod.CrewConfig(foundry_project_endpoint=PROJECT_ENDPOINT)
    workflow = orch_mod.build_workflow(config)
    with caplog.at_level(logging.INFO, logger="crew.orchestration"):
        outputs = asyncio.run(orch_mod.run_task(workflow, "do the thing", config))
    assert [str(o) for o in outputs] == [
        "planner did its part",
        "builder did its part",
        "reviewer did its part",
        "merger did its part",
    ]
    for name in ("planner", "builder", "reviewer", "merger"):
        assert f"{name} response:" in caplog.text
        assert f"{name} did its part" in caplog.text


def test_intermediate_outputs_reach_the_next_agent(monkeypatch):
    """Sanity: intermediate_output_from="all_other" does not change
    what the next agent sees — it only adds events to the result."""
    worker = FakeAgent([_resp("worker", "plan is ready")], name="worker")
    receiver = FakeAgent([_resp("receiver", "got it")], name="receiver")
    workflow = SequentialBuilder(
        participants=[worker, receiver],
        chain_only_agent_responses=True,
        output_from=[receiver],
        intermediate_output_from="all_other",
    ).build()
    result = asyncio.run(workflow.run("do the thing"))
    assert [str(o) for o in result.get_intermediate_outputs()] == ["plan is ready"]
    assert [str(o) for o in result.get_outputs()] == ["got it"]


# --- 3. the run summary: header + narrative ---


def test_format_run_summary_full():
    """Header names the agents, the PR, the verdict with notes, and
    the first tool failure paired with its tool."""
    orch_mod = require_crew("orchestration")
    outputs = [
        _resp("planner", "plan: one small change"),
        _resp_with_tool(
            "builder", "tried to push, it failed",
            "run_shell",
            "Error: Function failed. Exception: git push failed (exit 128): remote hung up",
        ),
        _resp("reviewer", "nothing to review"),
        _resp("merger", "nothing merged"),
    ]
    sign_offs = [
        {"pr_number": 14, "verdict": "changes_requested", "notes": "fix the test"}
    ]
    summary = orch_mod.format_run_summary(outputs, sign_offs)
    assert "Agents ran: planner, builder, reviewer, merger" in summary
    assert "PR: #14" in summary
    assert "Review: changes_requested on PR #14: fix the test" in summary
    assert (
        "First failure: run_shell: Error: Function failed. "
        "Exception: git push failed (exit 128): remote hung up"
    ) in summary
    assert "planner:\nplan: one small change" in summary
    assert "builder:\ntried to push, it failed" in summary


def test_format_run_summary_pr_from_open_pr_url():
    """A PR opened but never reviewed still shows up: the open_pr
    result URL is the source when no sign-off exists."""
    orch_mod = require_crew("orchestration")
    outputs = [
        _resp_with_tool(
            "builder", "opened the PR",
            "open_pr",
            "https://github.com/warni001/agent-crew/pull/14",
        ),
    ]
    summary = orch_mod.format_run_summary(outputs, [])
    assert "PR: #14" in summary
    assert "Review: none recorded" in summary


def test_format_run_summary_plain_strings_and_empty():
    """Plain-string outputs (mocked run_task in other tests) and an
    empty run do not crash; names fall back to pipeline order."""
    orch_mod = require_crew("orchestration")
    summary = orch_mod.format_run_summary(["done"], [])
    assert "Agents ran: planner" in summary
    assert "PR: none recorded" in summary
    assert "Review: none recorded" in summary
    assert "First failure: none" in summary
    assert "planner:\ndone" in summary
    empty = orch_mod.format_run_summary([], [])
    assert "Agents ran: none" in empty


def test_format_run_summary_unknown_when_ledger_unreadable():
    """sign_offs=None means the ledger could not be read: the verdict
    is unknown, not "none recorded"."""
    orch_mod = require_crew("orchestration")
    summary = orch_mod.format_run_summary([_resp("planner", "plan")], None)
    assert "Review: unknown (sign-off ledger unreadable)" in summary


def test_format_run_summary_first_failure_is_first():
    """Only the first tool failure is promoted to the issue; later
    ones stay in the container log (tools._run logs every failure)."""
    orch_mod = require_crew("orchestration")
    outputs = [
        _resp_with_tool(
            "builder", "first broke",
            "run_shell", "Error: Function failed. Exception: first boom",
        ),
        _resp_with_tool(
            "builder", "then this broke",
            "run_pytest", "Error: Function failed. Exception: second boom",
        ),
    ]
    summary = orch_mod.format_run_summary(outputs, [])
    assert "First failure: run_shell: Error: Function failed. Exception: first boom" in summary
    assert "First failure detail:\nrun_shell: Error: Function failed. Exception: first boom" in summary
    assert "second boom" not in summary

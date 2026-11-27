"""Builder action gate: when the builder returns with zero tool calls,
`run_task` re-invokes it on the same session with a forced nudge, up
to 3 attempts total — and if it still makes no call, the run stalls
as `builder_no_action` (see test_main_loop.py). The gate is code,
not prose: the Nov 27 #75 stalls proved the instruction "never
refuse without calling a tool first" cannot make the builder act.

All model calls are faked; no network.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from agent_framework import AgentResponse, Content, Message

from conftest import require_crew

PROJECT_ENDPOINT = "https://foundry-gpt-vaxvu3c2.cognitiveservices.azure.com/"


class FakeAgent:
    """Stand-in for Agent: returns canned responses, records run inputs."""

    def __init__(self, replies, name="worker"):
        self.replies = list(replies)
        self.calls = []
        self.sessions = []
        self.id = f"fake-{name}"
        self.name = name
        self.description = f"fake {name} agent"

    def create_session(self):
        return SimpleNamespace(state={})

    def get_session(self, *args, **kwargs):
        return None

    async def run(self, messages, *, session=None, **kwargs):
        self.calls.append(list(messages))
        self.sessions.append(session)
        return self.replies.pop(0)


def _empty_response():
    """The Nov 27 stall: an agent turn with no text and no tool calls."""
    return AgentResponse(messages=[], response_id="resp-empty")


def _text_response(text):
    return AgentResponse(
        messages=[
            Message(role="assistant", contents=[Content.from_text(text)]),
        ],
        response_id="resp-text",
    )


def _tool_response():
    """A builder turn that acted: one call + result + final text."""
    return AgentResponse(
        messages=[
            Message(role="assistant", contents=[
                Content.from_function_call(
                    call_id="call_1", name="read_file",
                    arguments='{"path": "crew/roles.py"}',
                ),
            ]),
            Message(role="tool", contents=[
                Content.from_function_result(call_id="call_1", result="file contents"),
            ]),
            Message(role="assistant", contents=[Content.from_text("implemented it")]),
        ],
        response_id="resp-tool",
    )


def _contents(messages):
    return [c for m in messages for c in m.contents]


def _roles(builder_replies, monkeypatch):
    orch_mod = require_crew("orchestration")
    config_mod = require_crew("config")
    roles = {
        "planner": FakeAgent([_text_response("plan is ready")], name="planner"),
        "builder": FakeAgent(builder_replies, name="builder"),
        "reviewer": FakeAgent([_text_response("ok")], name="reviewer"),
        "merger": FakeAgent([_text_response("done")], name="merger"),
    }
    monkeypatch.setattr(orch_mod, "spent_usd", lambda: 0.0)
    config = config_mod.CrewConfig(foundry_project_endpoint=PROJECT_ENDPOINT)
    return orch_mod, roles, config


def test_builder_retry_acts_on_second_attempt(monkeypatch):
    """An empty first turn is retried, and the acting retry is the
    response the reviewer sees."""
    orch_mod, roles, config = _roles([_empty_response(), _tool_response()], monkeypatch)
    outputs = asyncio.run(orch_mod.run_task(roles, "do the thing", config))
    assert len(roles["builder"].calls) == 2
    assert [str(o) for o in outputs] == [
        "plan is ready", "implemented it", "ok", "done",
    ]
    # The retry input is the forced nudge, naming a concrete action.
    nudge = _contents(roles["builder"].calls[1])
    assert any("no tool calls" in (c.text or "") for c in nudge)
    assert any("read_file" in (c.text or "") for c in nudge)
    # The reviewer receives the acting response's messages, not the empty one.
    received = _contents(roles["reviewer"].calls[0])
    assert {c.call_id for c in received if c.type == "function_call"} == {"call_1"}
    assert {c.call_id for c in received if c.type == "function_result"} == {"call_1"}


def test_builder_never_acts_stops_after_three_attempts(monkeypatch, caplog):
    """A builder that never calls a tool is retried twice and then
    left alone — the run returns normally with the empty builder
    response, and __main__ stalls it as builder_no_action."""
    import logging

    orch_mod, roles, config = _roles(
        [_empty_response(), _empty_response(), _empty_response()], monkeypatch
    )
    with caplog.at_level(logging.WARNING, logger="crew.orchestration"):
        outputs = asyncio.run(orch_mod.run_task(roles, "do the thing", config))
    assert len(roles["builder"].calls) == 3  # first + 2 retries, then stop
    assert len(outputs) == 4
    assert orch_mod.count_tool_calls(outputs[1]) == 0
    assert "no tool calls after 3 attempts" in caplog.text


def test_builder_retry_reuses_the_same_session(monkeypatch):
    """Retries run on the builder's own session — the task, the plan,
    and the empty turn stay in context — while every other agent
    still gets its own fresh session."""
    orch_mod, roles, config = _roles([_empty_response(), _tool_response()], monkeypatch)
    asyncio.run(orch_mod.run_task(roles, "do the thing", config))
    assert roles["builder"].sessions[0] is roles["builder"].sessions[1]
    sessions = [roles[name].sessions[0] for name in roles]
    assert len(set(map(id, sessions))) == 4


def test_count_tool_calls():
    """The counter sees calls, ignores plain text, and counts an
    empty turn as zero."""
    orch_mod = require_crew("orchestration")
    assert orch_mod.count_tool_calls(_tool_response()) == 1
    assert orch_mod.count_tool_calls(_text_response("just words")) == 0
    assert orch_mod.count_tool_calls(_empty_response()) == 0
    assert orch_mod.count_tool_calls("a plain string") == 0


def test_builder_tool_calls_returns_none_for_plain_strings():
    """Plain-string outputs carry no message structure, so the gate
    cannot tell "no calls" from "unknown" — it returns None and the
    caller falls through to the existing no_review logic."""
    orch_mod = require_crew("orchestration")
    assert orch_mod.builder_tool_calls(["done"]) is None
    assert orch_mod.builder_tool_calls([]) is None
    outputs = [
        _text_response("plan"),
        _tool_response(),
        _text_response("ok"),
        _text_response("done"),
    ]
    assert orch_mod.builder_tool_calls(outputs) == 1
    outputs[1] = _empty_response()
    assert orch_mod.builder_tool_calls(outputs) == 0

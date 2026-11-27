"""Sequential orchestration: planner -> builder -> reviewer -> merger,
one pass, each agent seeing only the previous agent's response.

Contract (see crew/orchestration.py): `run_task` runs the four role
agents directly, in order, each on its own fresh session, each
receiving only the previous agent's response messages. That is the
whole 400 fix — Magentic shared every participant's tool transcript
with every other participant, and under Responses chaining a foreign
function_result with no function_call in the chain 400s. The
explicit sequence passes only the previous response, and each agent
owns a fresh session, so a tool transcript enters the next agent's
chain as a matched call+result pair on its first, stateless call.
Agents run on the framework default store=True; store=False was the
Nov 19 workaround and is gone. No manager agent exists anymore.
The sequence replaced the fixed SequentialBuilder chain (Nov 27,
#75) so a gate can sit after the builder: when the builder returns
with zero tool calls, it is re-invoked on the same session with a
forced nudge, up to 3 attempts total.

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


def _pair_response():
    """A tool-using agent's response: call + result + final text.

    This is what a real Agent returns after its client-side function
    loop (ChatResponse.from_updates aggregates every turn): the
    function_call, its function_result, and the final text message.
    """
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
            Message(role="assistant", contents=[Content.from_text("plan is ready")]),
        ],
        response_id="resp-1",
    )


def _text_response(text):
    return AgentResponse(
        messages=[
            Message(role="assistant", contents=[Content.from_text(text)]),
        ],
        response_id="resp-2",
    )


def _tool_response(text):
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
            Message(role="assistant", contents=[Content.from_text(text)]),
        ],
        response_id="resp-tool",
    )


def _contents(messages):
    return [c for m in messages for c in m.contents]


def test_run_task_sequences_agents_with_only_previous_response(monkeypatch):
    """run_task runs planner->builder->reviewer->merger in order, each
    agent on its own fresh session, each receiving only the previous
    agent's response messages — the explicit sequence that replaced
    the fixed SequentialBuilder chain, so a gate can sit after the
    builder."""
    orch_mod = require_crew("orchestration")
    config_mod = require_crew("config")
    roles = {
        name: FakeAgent([_text_response(f"{name} did its part")], name=name)
        for name in ("planner", "reviewer", "merger")
    }
    # The builder's reply must carry a tool call: a text-only builder
    # turn trips the action gate and is retried (see test_builder_gate).
    roles["builder"] = FakeAgent(
        [_tool_response("builder did its part")], name="builder"
    )
    monkeypatch.setattr(orch_mod, "spent_usd", lambda: 0.0)
    config = config_mod.CrewConfig(foundry_project_endpoint=PROJECT_ENDPOINT)
    outputs = asyncio.run(orch_mod.run_task(roles, "do the thing", config))
    assert [str(o) for o in outputs] == [
        "planner did its part",
        "builder did its part",
        "reviewer did its part",
        "merger did its part",
    ]
    # The planner gets the task as one user message; every later
    # agent gets only the previous response's messages.
    planner_input = roles["planner"].calls[0]
    assert len(planner_input) == 1
    assert planner_input[0].role == "user"
    assert _contents(planner_input)[0].text == "do the thing"
    assert [c.text for c in _contents(roles["builder"].calls[0]) if c.type == "text"] == [
        "planner did its part"
    ]
    assert [c.text for c in _contents(roles["reviewer"].calls[0]) if c.type == "text"] == [
        "builder did its part"
    ]
    assert [c.text for c in _contents(roles["merger"].calls[0]) if c.type == "text"] == [
        "reviewer did its part"
    ]
    # One fresh session per agent — no session is shared across roles.
    sessions = [roles[name].sessions[0] for name in ("planner", "builder", "reviewer", "merger")]
    assert len(set(map(id, sessions))) == 4


def test_next_agent_gets_matched_call_and_result_pair(monkeypatch):
    """The previous agent's tool transcript reaches the next agent as
    a matched call+result pair — never an orphan function_result.

    An orphan output is exactly what 400s under chaining ("No tool
    call found for function call output"). The explicit sequence in
    run_task passes the previous response's messages verbatim; only
    the agents are fake.
    """
    orch_mod = require_crew("orchestration")
    config_mod = require_crew("config")
    roles = {
        "planner": FakeAgent([_pair_response()], name="planner"),
        "builder": FakeAgent([_tool_response("got it")], name="builder"),
        "reviewer": FakeAgent([_text_response("ok")], name="reviewer"),
        "merger": FakeAgent([_text_response("done")], name="merger"),
    }
    monkeypatch.setattr(orch_mod, "spent_usd", lambda: 0.0)
    config = config_mod.CrewConfig(foundry_project_endpoint=PROJECT_ENDPOINT)
    outputs = asyncio.run(orch_mod.run_task(roles, "do the thing", config))
    assert [str(o) for o in outputs] == ["plan is ready", "got it", "ok", "done"]
    assert len(roles["builder"].calls) == 1
    received = _contents(roles["builder"].calls[0])
    calls = {c.call_id for c in received if c.type == "function_call"}
    results = {c.call_id for c in received if c.type == "function_result"}
    assert calls == {"call_1"}
    assert results == {"call_1"}
    assert results <= calls  # no orphan output: the 400 condition
    assert any(c.type == "text" and c.text == "plan is ready" for c in received)


def test_next_agent_gets_only_the_previous_response(monkeypatch):
    """The sequence passes response messages, not the full
    conversation: the receiver never sees the original task."""
    orch_mod = require_crew("orchestration")
    config_mod = require_crew("config")
    roles = {
        "planner": FakeAgent([_pair_response()], name="planner"),
        "builder": FakeAgent([_tool_response("got it")], name="builder"),
        "reviewer": FakeAgent([_text_response("ok")], name="reviewer"),
        "merger": FakeAgent([_text_response("done")], name="merger"),
    }
    monkeypatch.setattr(orch_mod, "spent_usd", lambda: 0.0)
    config = config_mod.CrewConfig(foundry_project_endpoint=PROJECT_ENDPOINT)
    asyncio.run(orch_mod.run_task(roles, "do the thing", config))
    received = roles["builder"].calls[0]
    assert len(received) == 3  # call, result, text — nothing else
    assert not any("do the thing" in (c.text or "") for c in _contents(received))


def test_roles_are_four_chained_agents_no_manager():
    """build_roles returns the four pipeline roles on the framework
    default store — no store=False, no manager agent."""
    roles_mod = require_crew("roles")
    config_mod = require_crew("config")
    roles = roles_mod.build_roles(
        config_mod.CrewConfig(foundry_project_endpoint=PROJECT_ENDPOINT)
    )
    assert set(roles) == {"planner", "builder", "reviewer", "merger"}
    for name, agent in roles.items():
        assert agent.default_options.get("store") is not False, name

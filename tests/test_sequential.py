"""Sequential orchestration: planner -> builder -> reviewer -> merger,
one pass, each agent seeing only the previous agent's response.

Contract (see crew/orchestration.py): SequentialBuilder with
chain_only_agent_responses=True. That is the whole 400 fix — Magentic
shared every participant's tool transcript with every other
participant, and under Responses chaining a foreign function_result
with no function_call in the chain 400s. Sequential passes only the
previous agent's response messages, and each executor owns a fresh
session, so a tool transcript enters the next agent's chain as a
matched call+result pair on its first, stateless call. Agents run on
the framework default store=True; store=False was the Nov 19
workaround and is gone. No manager agent exists anymore.

All model calls are faked; no network.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from agent_framework import AgentResponse, Content, Message
from agent_framework.orchestrations import SequentialBuilder

from conftest import require_crew

PROJECT_ENDPOINT = "https://foundry-gpt-vaxvu3c2.cognitiveservices.azure.com/"


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


def _contents(messages):
    return [c for m in messages for c in m.contents]


def test_build_workflow_is_sequential_with_chain_only_responses(monkeypatch):
    """build_workflow wires planner->builder->reviewer->merger with
    chain_only_agent_responses=True and no manager."""
    orchestration_mod = require_crew("orchestration")
    seen = {}

    class CapturingBuilder:
        def __init__(self, **kwargs):
            seen.update(kwargs)

        def build(self):
            return object()

    roles = {name: FakeAgent([], name=name) for name in ("planner", "builder", "reviewer", "merger")}
    monkeypatch.setattr(orchestration_mod, "build_roles", lambda config: roles)
    monkeypatch.setattr(orchestration_mod, "SequentialBuilder", CapturingBuilder)
    config_mod = require_crew("config")
    config = config_mod.CrewConfig(foundry_project_endpoint=PROJECT_ENDPOINT)
    assert orchestration_mod.build_workflow(config) is not None
    assert [a.name for a in seen["participants"]] == ["planner", "builder", "reviewer", "merger"]
    assert seen["chain_only_agent_responses"] is True
    assert "manager_agent" not in seen
    assert "manager" not in seen


def test_next_agent_gets_matched_call_and_result_pair():
    """The previous agent's tool transcript reaches the next agent as
    a matched call+result pair — never an orphan function_result.

    An orphan output is exactly what 400s under chaining ("No tool
    call found for function call output"). The real SequentialBuilder
    with chain_only_agent_responses=True is exercised end to end;
    only the agents are fake.
    """
    worker = FakeAgent([_pair_response()], name="worker")
    receiver = FakeAgent([_text_response("got it")], name="receiver")
    workflow = SequentialBuilder(
        participants=[worker, receiver], chain_only_agent_responses=True
    ).build()
    outputs = asyncio.run(workflow.run("do the thing")).get_outputs()
    assert [str(o) for o in outputs] == ["got it"]
    assert len(receiver.calls) == 1
    received = _contents(receiver.calls[0])
    calls = {c.call_id for c in received if c.type == "function_call"}
    results = {c.call_id for c in received if c.type == "function_result"}
    assert calls == {"call_1"}
    assert results == {"call_1"}
    assert results <= calls  # no orphan output: the 400 condition
    assert any(c.type == "text" and c.text == "plan is ready" for c in received)


def test_next_agent_gets_only_the_previous_response():
    """chain_only_agent_responses passes response messages, not the
    full conversation: the receiver never sees the original task."""
    worker = FakeAgent([_pair_response()], name="worker")
    receiver = FakeAgent([_text_response("got it")], name="receiver")
    workflow = SequentialBuilder(
        participants=[worker, receiver], chain_only_agent_responses=True
    ).build()
    asyncio.run(workflow.run("do the thing"))
    received = receiver.calls[0]
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

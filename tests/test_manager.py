"""StructuredLedgerManager: the progress ledger is forced to a JSON schema.

Contract (see crew/manager.py): the manager model answers the plain-text
JSON instruction with a reasoning-only response and empty text, so the
parent's ledger parse fails on every attempt. The subclass passes a
json_schema response_format in ``options`` on the ledger call only, and
feeds a parse error back into the next attempt's prompt instead of
re-sending identical input. All model calls are faked; no network.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from agent_framework import Message
from agent_framework.orchestrations import MagenticContext

from conftest import require_crew


@pytest.fixture
def manager_mod():
    return require_crew("manager")


@pytest.fixture
def orchestration_mod():
    return require_crew("orchestration")


@pytest.fixture
def config_mod():
    return require_crew("config")


VALID_LEDGER = {
    "is_request_satisfied": {"reason": "work remains", "answer": False},
    "is_in_loop": {"reason": "first round", "answer": False},
    "is_progress_being_made": {"reason": "just starting", "answer": True},
    "next_speaker": {"reason": "plan first", "answer": "planner"},
    "instruction_or_question": {"reason": "needs a plan", "answer": "Plan the task."},
}


def make_context():
    return MagenticContext(
        task="Do the thing.",
        participant_descriptions={
            "planner": "plans",
            "builder": "builds",
            "reviewer": "reviews",
            "merger": "merges",
        },
    )


class FakeAgent:
    """Stand-in for Agent: returns canned messages, records run kwargs."""

    def __init__(self, replies, name="manager"):
        self.replies = list(replies)
        self.calls = []
        self.id = f"fake-{name}"
        self.name = name
        self.description = f"fake {name} agent"

    def create_session(self):
        return None

    def get_session(self, *args, **kwargs):
        return None

    async def run(self, messages, *, session=None, options=None, **kwargs):
        self.calls.append({"messages": list(messages), "options": options})
        index = min(len(self.calls) - 1, len(self.replies) - 1)
        return SimpleNamespace(messages=[self.replies[index]])


def _run(manager, context):
    import asyncio

    return asyncio.run(manager.create_progress_ledger(context))


def _msg(text):
    return Message(role="assistant", contents=[text])


def _empty_msg():
    return Message(role="assistant", contents=[])


def test_ledger_parses_valid_json(manager_mod):
    manager = manager_mod.StructuredLedgerManager(FakeAgent([_msg(json.dumps(VALID_LEDGER))]))
    ledger = _run(manager, make_context())
    assert ledger.is_request_satisfied.answer is False
    assert ledger.is_in_loop.answer is False
    assert ledger.is_progress_being_made.answer is True
    assert ledger.next_speaker.answer == "planner"
    assert ledger.instruction_or_question.answer == "Plan the task."


def test_ledger_call_passes_response_format(manager_mod):
    agent = FakeAgent([_msg(json.dumps(VALID_LEDGER))])
    manager = manager_mod.StructuredLedgerManager(agent)
    _run(manager, make_context())
    assert len(agent.calls) == 1
    assert agent.calls[0]["options"] == {"response_format": manager_mod.LEDGER_RESPONSE_FORMAT}


def test_response_format_is_strict_json_schema(manager_mod):
    fmt = manager_mod.LEDGER_RESPONSE_FORMAT
    assert fmt["type"] == "json_schema"
    inner = fmt["json_schema"]
    assert inner["name"] == "progress_ledger"
    assert inner["strict"] is True
    schema = inner["schema"]
    assert schema["type"] == "object"
    assert schema["additionalProperties"] is False
    assert sorted(schema["required"]) == sorted(schema["properties"])
    bool_fields = ("is_request_satisfied", "is_in_loop", "is_progress_being_made")
    str_fields = ("next_speaker", "instruction_or_question")
    for field in bool_fields:
        item = schema["properties"][field]
        assert item["properties"]["answer"] == {"type": "boolean"}
        assert item["additionalProperties"] is False
        assert item["required"] == ["reason", "answer"]
    for field in str_fields:
        item = schema["properties"][field]
        assert item["properties"]["answer"] == {"type": "string"}
        assert item["additionalProperties"] is False
        assert item["required"] == ["reason", "answer"]


def test_response_format_converts_to_responses_text_format(manager_mod):
    """The exact dict the manager sends is accepted by the real Responses
    client's translation (_convert_response_format is pure: it reads only
    its argument, so it is called unbound). This is the runtime check
    that the format reaches the API as text.format, not an assumption."""
    import importlib.util

    if importlib.util.find_spec("agent_framework_openai") is None:
        pytest.skip("agent-framework-openai not installed")
    from agent_framework_openai._chat_client import RawOpenAIChatClient

    converted = RawOpenAIChatClient._convert_response_format(None, manager_mod.LEDGER_RESPONSE_FORMAT)
    assert converted == {
        "type": "json_schema",
        "name": "progress_ledger",
        "schema": manager_mod.LEDGER_RESPONSE_FORMAT["json_schema"]["schema"],
        "strict": True,
    }


def test_reasoning_only_response_fails_after_three_attempts(manager_mod):
    agent = FakeAgent([_empty_msg()])
    manager = manager_mod.StructuredLedgerManager(agent)
    with pytest.raises(RuntimeError, match="Progress ledger parse failed after 3 attempt"):
        _run(manager, make_context())
    assert len(agent.calls) == 3
    # Retries carry the parse error; they are not the identical prompt.
    first = agent.calls[0]["messages"][-1].text
    second = agent.calls[1]["messages"][-1].text
    third = agent.calls[2]["messages"][-1].text
    assert "could not be parsed" in second
    assert "could not be parsed" in third
    assert second != first and third != first
    assert all(call["options"] == {"response_format": manager_mod.LEDGER_RESPONSE_FORMAT} for call in agent.calls)


def test_plan_and_replan_calls_stay_unformatted(manager_mod):
    agent = FakeAgent([_msg("some facts"), _msg("some plan")])
    manager = manager_mod.StructuredLedgerManager(agent)
    import asyncio

    asyncio.run(manager.plan(make_context()))
    assert len(agent.calls) == 2
    assert all(call["options"] is None for call in agent.calls)


def test_wiring_builds_workflow_with_structured_manager(orchestration_mod, config_mod, monkeypatch):
    """build_workflow passes a StructuredLedgerManager, not the plain agent."""
    def fake_roles(config):
        roles = {name: FakeAgent([], name=name) for name in ("planner", "builder", "reviewer", "merger")}
        roles["manager"] = FakeAgent([_msg(json.dumps(VALID_LEDGER))])
        return roles

    monkeypatch.setattr(orchestration_mod, "build_roles", fake_roles)
    env = {
        "FOUNDRY_PROJECT_ENDPOINT": "https://foundry-gpt-vaxvu3c2.services.ai.azure.com/api/projects/crew",
        "KEY_VAULT_URL": "https://kv-example.vault.azure.net/",
        "GITHUB_TOKEN": "token",
    }
    config = config_mod.CrewConfig.from_env(env=env)
    workflow = orchestration_mod.build_workflow(config)
    assert workflow is not None

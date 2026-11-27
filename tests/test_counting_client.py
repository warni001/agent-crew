"""CountingChatClient: every non-streaming call is priced from
API-reported usage into the process spend tracker, and a spent
budget makes no further API calls at all.

Async calls run via asyncio.run (repo convention: no pytest-asyncio).

The client is built without __init__ (object.__new__) and the
parent's get_response is faked, so no OpenAI client is ever
constructed and no network is touched. All model calls are faked.
"""

from __future__ import annotations

import asyncio

import pytest
from agent_framework import ChatResponse, Message
from agent_framework_openai import OpenAIChatClient

from conftest import require_crew


@pytest.fixture
def clients_mod():
    return require_crew("clients")


@pytest.fixture
def spend_mod():
    return require_crew("spend")


@pytest.fixture
def roles_mod():
    return require_crew("roles")


@pytest.fixture(autouse=True)
def clean_tracker(spend_mod):
    spend_mod.reset_spend()
    spend_mod.TRACKER.on_milestone = None
    yield
    spend_mod.reset_spend()
    spend_mod.TRACKER.on_milestone = None


def _client(clients_mod, model="gpt-5-4-mini"):
    """CountingChatClient without __init__: only .model is read."""
    client = object.__new__(clients_mod.CountingChatClient)
    client.model = model
    return client


def _run(client, messages):
    """Run one non-streaming call (asyncio.run: repo convention)."""
    return asyncio.run(client.get_response(messages))


def _response(model="gpt-5.4-mini", usage=None):
    return ChatResponse(
        messages=[Message(role="assistant", contents=["done"])],
        model=model,
        usage_details=usage,
    )


def test_call_records_usage(clients_mod, spend_mod, monkeypatch):
    client = _client(clients_mod)
    calls: list = []

    async def fake_get_response(self, messages, **kwargs):
        calls.append(kwargs)
        return _response(usage={"input_token_count": 1_000_000, "output_token_count": 100_000})

    monkeypatch.setattr(OpenAIChatClient, "get_response", fake_get_response)
    result = _run(client, [Message(role="user", contents=["hi"])])
    assert result.text == "done"
    assert len(calls) == 1
    # 1M mini input ($0.83) + 100k mini output ($0.495).
    assert spend_mod.spent_usd() == pytest.approx(1.325)


def test_response_model_prices_the_call(clients_mod, spend_mod, monkeypatch):
    # The client was built for an unknown model (fallback Astra rates);
    # the response says mini served it, so mini rates price the call.
    client = _client(clients_mod, model="gpt-9-2-mystery")

    async def fake_get_response(self, messages, **kwargs):
        return _response(model="gpt-5.4-mini", usage={"input_token_count": 1_000_000})

    monkeypatch.setattr(OpenAIChatClient, "get_response", fake_get_response)
    _run(client, [Message(role="user", contents=["hi"])])
    assert spend_mod.spent_usd() == pytest.approx(0.83)


def test_client_model_is_the_fallback(clients_mod, spend_mod, monkeypatch):
    client = _client(clients_mod, model="gpt-5-4-mini")

    async def fake_get_response(self, messages, **kwargs):
        return _response(model=None, usage={"output_token_count": 1_000_000})

    monkeypatch.setattr(OpenAIChatClient, "get_response", fake_get_response)
    _run(client, [Message(role="user", contents=["hi"])])
    assert spend_mod.spent_usd() == pytest.approx(4.95)


def test_missing_usage_costs_zero(clients_mod, spend_mod, monkeypatch):
    client = _client(clients_mod)

    async def fake_get_response(self, messages, **kwargs):
        return _response(usage=None)

    monkeypatch.setattr(OpenAIChatClient, "get_response", fake_get_response)
    _run(client, [Message(role="user", contents=["hi"])])
    assert spend_mod.spent_usd() == pytest.approx(0.0)


def test_spent_budget_makes_no_api_call(clients_mod, spend_mod, monkeypatch):
    spend_mod.reset_spend(starting_usd=190.0)
    client = _client(clients_mod)
    calls: list = []

    async def fake_get_response(self, messages, **kwargs):
        calls.append(1)
        return _response(usage={"input_token_count": 1_000_000})

    monkeypatch.setattr(OpenAIChatClient, "get_response", fake_get_response)
    with pytest.raises(spend_mod.SpendLimitExceeded, match="stop line"):
        client.get_response([Message(role="user", contents=["hi"])])
    assert calls == []


def test_one_call_may_cross_the_line(clients_mod, spend_mod, monkeypatch):
    # $189.99 tracked: the next call goes through (checked before the
    # call), lands over $190, and only the call after it is refused.
    spend_mod.reset_spend(starting_usd=189.99)
    client = _client(clients_mod)

    async def fake_get_response(self, messages, **kwargs):
        return _response(usage={"input_token_count": 1_000_000})  # +$0.83

    monkeypatch.setattr(OpenAIChatClient, "get_response", fake_get_response)
    _run(client, [Message(role="user", contents=["hi"])])
    assert spend_mod.spent_usd() == pytest.approx(190.82)
    with pytest.raises(spend_mod.SpendLimitExceeded):
        client.get_response([Message(role="user", contents=["hi"])])


def test_streaming_passes_through_uncounted(clients_mod, spend_mod, monkeypatch):
    client = _client(clients_mod)
    sentinel = object()

    def fake_get_response(self, messages, **kwargs):
        assert kwargs.get("stream") is True
        return sentinel

    monkeypatch.setattr(OpenAIChatClient, "get_response", fake_get_response)
    assert client.get_response([Message(role="user", contents=["hi"])], stream=True) is sentinel
    assert spend_mod.spent_usd() == pytest.approx(0.0)


def test_make_chat_client_builds_counting_client(clients_mod, roles_mod, monkeypatch):
    made: dict = {}

    class FakeCounting:
        def __init__(self, **kwargs):
            made.update(kwargs)

    monkeypatch.setattr(clients_mod, "CountingChatClient", FakeCounting)
    config_mod = require_crew("config")
    config = config_mod.CrewConfig(
        foundry_project_endpoint="https://foundry-gpt-vaxvu3c2.services.ai.azure.com/api/projects/crew"
    )
    roles_mod.make_chat_client(config, "gpt-5-4-mini")
    assert made["model"] == "gpt-5-4-mini"
    assert made["azure_endpoint"] == config.resolve_azure_openai_endpoint()
    assert made["api_version"] == "preview"
    assert "credential" in made

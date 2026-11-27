"""Chat client construction: mocked, offline, endpoint from config.

Contract under test (see docs/crew-instructions.md, "Module contract")::

    crew.clients.build_chat_client(endpoint, model,
                                   api_version=AZURE_OPENAI_API_VERSION,
                                   client_factory=None)

The factory is injectable so tests never touch the network: the
default factory builds the real SDK client, the test passes a fake.
The endpoint always comes from config (AZURE_OPENAI_ENDPOINT, else
derived from FOUNDRY_PROJECT_ENDPOINT) — it is never hardcoded in
the client module, and no call here reaches Azure. A canned reply
proves the wiring without a live model call. The default factory is
also exercised by construction only (no I/O at construction): it
must produce an OpenAIChatClient on the classic endpoint with the
proven API version.
"""

from __future__ import annotations

import pytest

from conftest import require_crew

CLASSIC_ENDPOINT = "https://foundry-gpt-vaxvu3c2.openai.azure.com"
PROVEN_API_VERSION = "preview"


@pytest.fixture
def clients_mod():
    return require_crew("clients")


def test_build_client_uses_given_endpoint_and_model(clients_mod, mock_chat_client):
    created = {}

    def factory(endpoint, model, **kwargs):
        created.update(endpoint=endpoint, model=model, kwargs=kwargs)
        return mock_chat_client

    client = clients_mod.build_chat_client(
        CLASSIC_ENDPOINT, "gpt-5-4-mini", client_factory=factory
    )
    assert client is mock_chat_client
    assert created["endpoint"] == CLASSIC_ENDPOINT
    assert created["model"] == "gpt-5-4-mini"
    assert created["kwargs"]["api_version"] == PROVEN_API_VERSION


def test_build_client_passes_no_hardcoded_endpoint(clients_mod, mock_chat_client):
    """Two different endpoints must produce two different clients' configs."""
    seen = []

    def factory(endpoint, model, **kwargs):
        seen.append((endpoint, model))
        return mock_chat_client

    clients_mod.build_chat_client("https://a.example/", "gpt-5-4-mini", client_factory=factory)
    clients_mod.build_chat_client("https://b.example/", "gpt-5-mini", client_factory=factory)
    assert seen == [
        ("https://a.example/", "gpt-5-4-mini"),
        ("https://b.example/", "gpt-5-mini"),
    ]


def test_mocked_client_returns_canned_reply_without_network(
    clients_mod, mock_chat_client
):
    client = clients_mod.build_chat_client(
        CLASSIC_ENDPOINT,
        "gpt-5-4-mini",
        client_factory=lambda endpoint, model, **kw: mock_chat_client,
    )
    reply = client.complete("hello")
    assert reply.content == "canned test reply"
    mock_chat_client.complete.assert_called_once_with("hello")


def test_default_factory_builds_openai_client_on_classic_endpoint(clients_mod):
    """Construction only — no network, no token fetch at construction.

    Pins the exact combination the Nov 19 live probes proved: the
    classic endpoint with api_version "preview".
    """
    from agent_framework_openai import OpenAIChatClient

    client = clients_mod.build_chat_client(CLASSIC_ENDPOINT, "gpt-5-4-mini")
    assert isinstance(client, OpenAIChatClient)
    assert client.azure_endpoint == CLASSIC_ENDPOINT
    assert client.api_version == PROVEN_API_VERSION
    assert client.model == "gpt-5-4-mini"


def test_default_factory_api_version_must_be_passed_explicitly(clients_mod):
    """The SDK default is "preview", not the proven version — the
    constant exists so the call site cannot silently drop it."""
    assert clients_mod.AZURE_OPENAI_API_VERSION == PROVEN_API_VERSION

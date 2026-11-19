"""Foundry client construction: mocked, offline, endpoint from config.

Contract under test (see docs/crew-instructions.md, "Module contract")::

    crew.clients.build_foundry_client(endpoint, client_factory=None)

The factory is injectable so tests never touch the network: the
default factory builds the real SDK client, the test passes a fake.
The endpoint always comes from config (FOUNDRY_GPT_ENDPOINT) — it
is never hardcoded in the client module, and no call here reaches
Azure. A canned reply proves the wiring without a live model call.

Blocked until ``crew/clients.py`` is merged; these tests skip until then.
"""

from __future__ import annotations

import pytest

from conftest import require_crew


@pytest.fixture
def clients_mod():
    return require_crew("clients")


def test_build_client_uses_given_endpoint(clients_mod, mock_foundry_client):
    created = {}

    def factory(endpoint, **kwargs):
        created["endpoint"] = endpoint
        return mock_foundry_client

    client = clients_mod.build_foundry_client(
        "https://foundry-gpt-vaxvu3c2.cognitiveservices.azure.com/",
        client_factory=factory,
    )
    assert client is mock_foundry_client
    assert created["endpoint"] == "https://foundry-gpt-vaxvu3c2.cognitiveservices.azure.com/"


def test_build_client_passes_no_hardcoded_endpoint(clients_mod, mock_foundry_client):
    """Two different endpoints must produce two different clients' configs."""
    seen = []

    def factory(endpoint, **kwargs):
        seen.append(endpoint)
        return mock_foundry_client

    clients_mod.build_foundry_client("https://a.example/", client_factory=factory)
    clients_mod.build_foundry_client("https://b.example/", client_factory=factory)
    assert seen == ["https://a.example/", "https://b.example/"]


def test_mocked_client_returns_canned_reply_without_network(
    clients_mod, mock_foundry_client
):
    client = clients_mod.build_foundry_client(
        "https://foundry-gpt-vaxvu3c2.cognitiveservices.azure.com/",
        client_factory=lambda endpoint, **kw: mock_foundry_client,
    )
    reply = client.complete("hello")
    assert reply.content == "canned test reply"
    mock_foundry_client.complete.assert_called_once_with("hello")

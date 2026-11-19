"""Foundry client construction. Factory-injectable so tests never touch
the network.

The default factory builds the real SDK client; pass ``client_factory``
in tests. The endpoint always comes from the caller (config) — never
hardcoded here. Untested against live Foundry.
"""

from __future__ import annotations


def _default_factory(endpoint, **kwargs):
    from agent_framework.foundry import FoundryChatClient
    from azure.identity import DefaultAzureCredential

    return FoundryChatClient(
        project_endpoint=endpoint, credential=DefaultAzureCredential(), **kwargs
    )


def build_foundry_client(endpoint, client_factory=None):
    """Build a Foundry chat client for ``endpoint`` via ``client_factory``."""
    factory = client_factory or _default_factory
    return factory(endpoint)


__all__ = ["build_foundry_client"]

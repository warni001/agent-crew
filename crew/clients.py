"""Foundry client construction. Factory-injectable so tests never touch
the network.

The default factory builds the real SDK client; pass ``client_factory``
in tests. The endpoint always comes from the caller (config) — never
hardcoded here. Untested against live Foundry.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from agent_framework import Message
from agent_framework.foundry import FoundryChatClient

from .spend import TRACKER, SpendLimitExceeded, record_usage, spent_usd


class CountingChatClient(FoundryChatClient):
    """FoundryChatClient that counts real spend from API-reported usage.

    Every non-streaming call flows through ``get_response`` here: the
    tracked total is checked *before* the call (a spent budget makes
    no further API calls), and the usage the API reports on the
    response is priced and added to the process tracker afterwards
    (see crew/spend.py). The overshoot past the stop line is therefore
    at most one call's cost — the call already in flight when the
    line was crossed.

    Streaming is passed through uncounted: the crew never streams
    (Agent.run is non-streaming everywhere in this repo). Re-check
    this if streaming is ever used.
    """

    def get_response(
        self,
        messages: Sequence[Message],
        *,
        stream: bool = False,
        options: Any = None,
        **kwargs: Any,
    ):
        """Count the call, then delegate to the real client."""
        if stream:
            return super().get_response(
                messages, stream=True, options=options, **kwargs
            )
        if spent_usd() >= TRACKER.stop_threshold_usd():
            raise SpendLimitExceeded(
                f"tracked spend ${spent_usd():.2f} reached the stop line "
                f"${TRACKER.stop_threshold_usd():.2f}; refusing another model call"
            )

        parent_get_response = super().get_response

        async def _counted():
            response = await parent_get_response(
                messages, stream=False, options=options, **kwargs
            )
            record_usage(
                getattr(response, "model", None) or self.model,
                getattr(response, "usage_details", None),
            )
            return response

        return _counted()


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


__all__ = ["CountingChatClient", "build_foundry_client"]

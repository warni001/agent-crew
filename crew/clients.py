"""Chat client construction. Factory-injectable so tests never touch
the network.

The default factory builds the real SDK client; pass ``client_factory``
in tests. The endpoint always comes from the caller (config) — never
hardcoded here. The client is CountingChatClient, an OpenAIChatClient
on the classic Azure OpenAI endpoint that prices every call from
API-reported usage — not FoundryChatClient on the Foundry project
endpoint: proven live Nov 19, the project endpoint returns HTTP 200
with zero usage and no message output even for "Say hello", while
the classic endpoint returns real text. Verified offline against
installed agent-framework-openai 1.18.2; the live call is still to
be verified on the container.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from agent_framework import Message
from agent_framework_openai import OpenAIChatClient

from .spend import TRACKER, SpendLimitExceeded, record_usage, spent_usd

# The exact combination the Nov 19 live probes proved: the classic
# endpoint with api_version "preview". OpenAIChatClient's Azure
# routing always builds the /openai/v1 base URL, and Azure only
# accepts api-version=preview there — the dated 2025-04-01-preview
# 400s ("API version not supported"). That dated version only works
# on the legacy /openai path, which the framework never uses.
# "preview" is also the SDK default
# (DEFAULT_AZURE_OPENAI_RESPONSES_API_VERSION), but pass it
# explicitly so the version is pinned, not inherited — do not drop it.
AZURE_OPENAI_API_VERSION = "preview"


class CountingChatClient(OpenAIChatClient):
    """OpenAIChatClient that counts real spend from API-reported usage.

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


def _default_factory(endpoint, model, api_version=AZURE_OPENAI_API_VERSION, **kwargs):
    from azure.identity import DefaultAzureCredential

    return CountingChatClient(
        model=model,
        azure_endpoint=endpoint,
        credential=DefaultAzureCredential(),
        api_version=api_version,
        **kwargs,
    )


def build_chat_client(endpoint, model, api_version=AZURE_OPENAI_API_VERSION, client_factory=None):
    """Build a chat client for ``endpoint`` + ``model`` via ``client_factory``."""
    factory = client_factory or _default_factory
    return factory(endpoint, model, api_version=api_version)


__all__ = ["AZURE_OPENAI_API_VERSION", "CountingChatClient", "build_chat_client"]

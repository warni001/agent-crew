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

from agent_framework import ChatMiddleware, Message
from agent_framework_openai import OpenAIChatClient

from .spend import (
    TRACKER,
    SpendLimitExceeded,
    check_task_cost,
    record_usage,
    remaining_task_seconds,
    spent_usd,
)

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


def _check_global_spend() -> None:
    if TRACKER.should_stop():
        raise SpendLimitExceeded(
            f"tracked spend ${spent_usd():.2f} reached the stop line "
            f"${TRACKER.stop_threshold_usd():.2f}; refusing another model call"
        )


class _SpendMiddleware(ChatMiddleware):
    """The SDK invokes chat middleware inside its tool-call loop."""

    async def process(self, context, call_next):
        if context.stream:
            await call_next()
            return
        remaining_task_seconds()
        check_task_cost()
        _check_global_spend()
        await call_next()
        response = context.result
        record_usage(
            getattr(response, "model", None) or context.client.model,
            getattr(response, "usage_details", None),
        )
        # Stop before the SDK executes any tools requested by this response.
        check_task_cost()
        remaining_task_seconds()


class CountingChatClient(OpenAIChatClient):
    """Price each non-streaming model response, including tool follow-ups."""

    def __init__(self, *args: Any, middleware=None, **kwargs: Any) -> None:
        super().__init__(
            *args, middleware=[_SpendMiddleware(), *(middleware or ())], **kwargs
        )
        # include_detailed_errors=True: the framework default is False,
        # so a failed tool call reaches the model as the opaque "Error:
        # Function failed." — the Nov 20 #9 run stalled with two
        # run_shell RuntimeErrors nobody could see. The error text
        # comes from the crew's own tools (command + output, capped);
        # no secret ever flows through a tool result, so the
        # framework's sensitive-information caveat does not apply
        # here. The model seeing the real error is also what lets it
        # self-correct instead of guessing.
        self.function_invocation_configuration["include_detailed_errors"] = True

    def get_response(
        self,
        messages: Sequence[Message],
        *,
        stream: bool = False,
        options: Any = None,
        **kwargs: Any,
    ):
        if stream:
            # Never let streaming (unused by the crew) bypass task accounting.
            if remaining_task_seconds() is not None:
                raise ValueError("streaming is not supported under task limits")
        else:
            _check_global_spend()
        return super().get_response(messages, stream=stream, options=options, **kwargs)


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

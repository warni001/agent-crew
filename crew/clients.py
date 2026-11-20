"""Chat client construction. Factory-injectable so tests never touch
the network.

The default factory builds the real SDK client; pass ``client_factory``
in tests. The endpoint always comes from the caller (config) — never
hardcoded here. The client is OpenAIChatClient on the classic Azure
OpenAI endpoint, not FoundryChatClient on the Foundry project
endpoint: proven live Nov 19, the project endpoint returns HTTP 200
with zero usage and no message output even for "Say hello", while
the classic endpoint returns real text. Verified offline against
installed agent-framework-openai 1.18.2; the live call is still to
be verified on the container.
"""

from __future__ import annotations

# The exact combination the Nov 19 live sweep proved: the classic
# endpoint with this API version. OpenAIChatClient's default API
# version is "preview" (agent-framework-openai 1.18.2,
# DEFAULT_AZURE_OPENAI_RESPONSES_API_VERSION), so it must be passed
# explicitly — do not drop it.
AZURE_OPENAI_API_VERSION = "2025-04-01-preview"


def _default_factory(endpoint, model, api_version=AZURE_OPENAI_API_VERSION, **kwargs):
    from agent_framework_openai import OpenAIChatClient
    from azure.identity import DefaultAzureCredential

    return OpenAIChatClient(
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


__all__ = ["AZURE_OPENAI_API_VERSION", "build_chat_client"]

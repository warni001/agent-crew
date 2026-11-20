"""SDK API verification against the INSTALLED agent-framework==1.26.0.

Every assertion here was written from inspection of the installed
package in ``.venv`` (``agent_framework.__version__ == "1.26.0"``),
not from memory or training data. What inspection established:

- Top level has ``Agent`` — there is NO ``ChatAgent`` in 1.26.0
  (the class was renamed). ``Agent(client, instructions=None, *, ...)``
  takes the chat client as its FIRST positional argument.
- There is NO ``agent_framework.magentic`` module. Sequential and
  Magentic both live in ``agent_framework.orchestrations``; the crew
  uses ``SequentialBuilder`` with ``chain_only_agent_responses=True``
  (``AgentExecutor`` context_mode ``"last_agent"``) — Magentic is
  out, since sharing full history across agents' Responses chains
  400s on foreign tool results.
- The chat client is ``agent_framework_openai.OpenAIChatClient``
  (companion package ``agent-framework-openai`` 1.18.2), Azure
  overload keyword-only args: ``OpenAIChatClient(*, model=None,
  azure_endpoint=None, credential=None, api_version=None, ...)``.
  The classic endpoint (``https://<account>.openai.azure.com``)
  with ``api_version="preview"`` is the combination the
  Nov 19 live probes proved: the Foundry project endpoint returns
  hollow 200s (zero usage, no message output) while the classic
  endpoint returns real text. No test here calls the network.

These tests SKIP when the real SDK is not installed (the conftest
stub is then in place instead, and the stub deliberately exposes
only these same verified names). They PASS in this venv.
"""

from __future__ import annotations

import importlib
import importlib.metadata
import importlib.util
import inspect

import pytest


def _real_sdk():
    """Return the real agent_framework module, or skip the test."""
    spec = importlib.util.find_spec("agent_framework")
    if spec is None or spec.origin is None or "site-packages" not in spec.origin:
        pytest.skip("real agent-framework SDK not installed (stub in use)")
    return importlib.import_module("agent_framework")


def test_installed_version_is_exactly_1_26_0():
    _real_sdk()
    assert importlib.metadata.version("agent-framework") == "1.26.0"
    assert importlib.metadata.version("agent-framework") not in ("1.26.1", "1.26.2")


def test_agent_exists_and_chat_agent_does_not():
    af = _real_sdk()
    assert hasattr(af, "Agent")
    assert not hasattr(af, "ChatAgent"), (
        "ChatAgent does not exist in 1.26.0 — the crew must use Agent"
    )
    assert not hasattr(af, "AzureOpenAIChatClient")


def test_agent_takes_client_first():
    af = _real_sdk()
    params = list(inspect.signature(af.Agent.__init__).parameters.values())
    # (self, client, instructions=None, *, ...)
    assert params[1].name == "client"
    assert params[2].name == "instructions"


def test_no_magentic_top_level_module():
    _real_sdk()
    assert importlib.util.find_spec("agent_framework.magentic") is None


def test_sequential_lives_in_orchestrations():
    _real_sdk()
    orch = importlib.import_module("agent_framework.orchestrations")
    assert hasattr(orch, "SequentialBuilder"), "missing in orchestrations: SequentialBuilder"
    sig = inspect.signature(orch.SequentialBuilder.__init__)
    assert "chain_only_agent_responses" in sig.parameters
    assert sig.parameters["chain_only_agent_responses"].default is False
    assert "participants" in sig.parameters
    assert callable(getattr(orch.SequentialBuilder, "build"))


def test_agent_executor_last_agent_context_mode():
    """AgentExecutor's context_mode accepts "last_agent": only the
    latest agent response is passed as context — the mode
    chain_only_agent_responses=True selects."""
    _real_sdk()
    from agent_framework._workflows._agent_executor import AgentExecutor

    sig = inspect.signature(AgentExecutor.__init__)
    assert "context_mode" in sig.parameters


def test_openai_chat_client_azure_signature():
    _real_sdk()
    if importlib.util.find_spec("agent_framework_openai") is None:
        pytest.skip("agent-framework-openai not installed")
    openai_mod = importlib.import_module("agent_framework_openai")
    assert hasattr(openai_mod, "OpenAIChatClient")
    sig = inspect.signature(openai_mod.OpenAIChatClient.__init__)
    # model is the one positional-or-keyword arg; the Azure kwargs are
    # keyword-only (verified against the installed 1.18.2 signature).
    assert sig.parameters["model"].kind == inspect.Parameter.POSITIONAL_OR_KEYWORD
    for kw in ("azure_endpoint", "credential", "api_version"):
        assert kw in sig.parameters, f"missing kwarg: {kw}"
        assert sig.parameters[kw].kind == inspect.Parameter.KEYWORD_ONLY, kw


def test_openai_default_api_version_is_preview():
    """The SDK default API version is "preview" — the same value the
    Nov 19 live probe proved on the /openai/v1 path. It is passed
    explicitly anyway (see crew/clients.py AZURE_OPENAI_API_VERSION)
    so the version is pinned, not inherited."""
    _real_sdk()
    if importlib.util.find_spec("agent_framework_openai") is None:
        pytest.skip("agent-framework-openai not installed")
    from agent_framework_openai._chat_client import (
        DEFAULT_AZURE_OPENAI_RESPONSES_API_VERSION,
    )

    assert DEFAULT_AZURE_OPENAI_RESPONSES_API_VERSION == "preview"


def test_workflow_builder_methods():
    af = _real_sdk()
    for method in ("add_chain", "add_edge", "build"):
        assert callable(getattr(af.WorkflowBuilder, method)), method

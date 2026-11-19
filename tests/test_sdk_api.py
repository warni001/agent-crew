"""SDK API verification against the INSTALLED agent-framework==1.26.0.

Every assertion here was written from inspection of the installed
package in ``.venv`` (``agent_framework.__version__ == "1.26.0"``),
not from memory or training data. What inspection established:

- Top level has ``Agent`` — there is NO ``ChatAgent`` in 1.26.0
  (the class was renamed). ``Agent(client, instructions=None, *, ...)``
  takes the chat client as its FIRST positional argument.
- There is NO ``agent_framework.magentic`` module. Magentic lives in
  ``agent_framework.orchestrations``:
  ``MagenticOrchestrator(manager, participant_registry, *,
  require_plan_signoff=False)``, ``MagenticBuilder`` with
  ``.build()`` / ``.with_checkpointing()`` / ``.with_plan_review()``,
  ``StandardMagenticManager``.
- The Foundry client is ``agent_framework_foundry.FoundryChatClient``
  (companion package ``agent-framework-foundry`` 1.16.2), keyword-only
  args: ``FoundryChatClient(*, project_endpoint=None,
  project_client=None, model=None, credential=None, ...)``.
  NOTE: ``project_endpoint`` is a Foundry *project* endpoint, not
  necessarily the bare account endpoint in FOUNDRY_GPT_ENDPOINT —
  that mapping is UNVERIFIED (see tests/test_clients.py and the
  report). No test here calls the network.

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


def test_magentic_lives_in_orchestrations():
    _real_sdk()
    orch = importlib.import_module("agent_framework.orchestrations")
    for name in (
        "MagenticOrchestrator",
        "MagenticBuilder",
        "StandardMagenticManager",
        "MagenticManagerBase",
    ):
        assert hasattr(orch, name), f"missing in orchestrations: {name}"
    sig = inspect.signature(orch.MagenticOrchestrator.__init__)
    params = list(sig.parameters.values())
    assert params[1].name == "manager"
    assert params[2].name == "participant_registry"
    assert "require_plan_signoff" in sig.parameters


def test_magentic_builder_methods():
    _real_sdk()
    orch = importlib.import_module("agent_framework.orchestrations")
    for method in ("build", "with_checkpointing", "with_plan_review"):
        assert callable(getattr(orch.MagenticBuilder, method)), method


def test_foundry_chat_client_signature():
    _real_sdk()
    foundry = importlib.import_module("agent_framework_foundry")
    assert hasattr(foundry, "FoundryChatClient")
    sig = inspect.signature(foundry.FoundryChatClient.__init__)
    for kw in ("project_endpoint", "model", "credential"):
        assert kw in sig.parameters, f"missing kwarg: {kw}"
        assert sig.parameters[kw].kind == inspect.Parameter.KEYWORD_ONLY, kw
    assert hasattr(foundry, "FoundryAgent")


def test_workflow_builder_methods():
    af = _real_sdk()
    for method in ("add_chain", "add_edge", "build"):
        assert callable(getattr(af.WorkflowBuilder, method)), method

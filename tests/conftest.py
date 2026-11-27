"""Shared fixtures for the agent-crew offline test suite.

The core ``crew/`` package does not exist yet in this worktree (another
agent builds it in parallel). Tests that need it import it through
``require_crew`` and SKIP — they are not failures — until the core files
are merged. Tests that only read repo files / docs run standalone and
must pass now.

Offline rules for the whole suite:

- No network. Chat clients and the ``gh`` CLI are always mocked
  (``unittest.mock``). No live model calls, no live Azure calls.
- ``agent_framework`` is stubbed in ``sys.modules`` so importing crew
  modules never tries to install or reach the real SDK.
- Python 3.14. Run from the repo root: ``.venv/bin/python -m pytest tests/ -v``
"""

from __future__ import annotations

import importlib
import sys
import types
from pathlib import Path
from unittest import mock

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------------------
# agent_framework stub (offline: only when the real SDK is not installed)
# ---------------------------------------------------------------------------
# If agent-framework==1.26.0 is installed in the test environment, the
# REAL package is used — never the stub — so imports and API usage are
# verified against the installed package (see tests/test_sdk_api.py).
# The stub below applies only on machines without the SDK, so importing
# crew modules never tries to install or reach the real SDK.
import importlib.util as _ilu

_REAL_SDK_PRESENT = _ilu.find_spec("agent_framework") is not None


def _stub_agent_framework() -> types.ModuleType:
    """Return a minimal stub module standing in for ``agent_framework``.

    The names here are ONLY names verified to exist in the installed
    agent-framework==1.26.0 (see tests/test_sdk_api.py). There is
    deliberately no catch-all ``__getattr__``: ``from agent_framework
    import ChatAgent`` must fail under the stub exactly as it fails
    against the real 1.26.0 package (the class is ``Agent`` now, and
    there is no ``agent_framework.magentic`` module — Magentic lives
    in ``agent_framework.orchestrations``).
    """
    mod = types.ModuleType("agent_framework")
    mod.__version__ = "1.26.0-stub"
    for name in (
        "Agent",
        "Workflow",
        "WorkflowBuilder",
        "WorkflowAgent",
        "BaseChatClient",
        "Message",
        "Role",
        "ChatResponse",
        "ChatOptions",
    ):
        setattr(mod, name, mock.MagicMock(name=f"agent_framework.{name}"))
    for name in ("tool", "executor", "step", "handler"):
        setattr(mod, name, mock.MagicMock(name=f"agent_framework.{name}"))
    return mod


def _stub_module(name: str, attrs: tuple[str, ...]) -> types.ModuleType:
    mod = types.ModuleType(name)
    for attr in attrs:
        setattr(mod, attr, mock.MagicMock(name=f"{name}.{attr}"))
    return mod


if not _REAL_SDK_PRESENT and "agent_framework" not in sys.modules:
    sys.modules["agent_framework"] = _stub_agent_framework()

# Submodules / companion packages (stubbed only when the real SDK is
# absent). Names verified against the installed 1.26.0 tree.
if not _REAL_SDK_PRESENT:
    _stubs = {
        # Magentic orchestration: agent_framework.orchestrations
        "agent_framework.orchestrations": (
            "MagenticOrchestrator",
            "MagenticBuilder",
            "StandardMagenticManager",
            "MagenticManagerBase",
            "SequentialBuilder",
        ),
        # Foundry chat client: separate agent_framework_foundry package
        "agent_framework_foundry": ("FoundryChatClient", "FoundryAgent"),
        # Classic Azure OpenAI chat client: agent_framework_openai
        "agent_framework_openai": ("OpenAIChatClient",),
        "agent_framework.azure": (),
    }
    for _name, _attrs in _stubs.items():
        if _name not in sys.modules:
            sys.modules[_name] = _stub_module(_name, _attrs)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def require_crew(name: str):
    """Import ``crew.<name>`` or skip the test.

    Skips (never fails) when the core package has not been merged yet.
    The skip message names the blocking module so the report can tell
    "blocked on core merge" apart from a real failure.
    """
    try:
        return importlib.import_module(f"crew.{name}")
    except ModuleNotFoundError as exc:
        pytest.skip(f"blocked: crew.{name} not merged yet ({exc.name})")


@pytest.fixture
def repo_root() -> Path:
    return REPO_ROOT


@pytest.fixture
def clean_env(monkeypatch):
    """Env with only the three config vars the crew is allowed to read.

    Config comes from the environment only: KEY_VAULT_URL,
    FOUNDRY_GPT_ENDPOINT, GITHUB_TOKEN. No secrets in code, no .env read.
    """
    for var in ("KEY_VAULT_URL", "FOUNDRY_GPT_ENDPOINT", "GITHUB_TOKEN"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.delenv("CREW_LEDGER_BLOB_URL", raising=False)
    monkeypatch.setenv("KEY_VAULT_URL", "https://kv-agentcrew-vaxvu3c2.vault.azure.net/")
    monkeypatch.setenv("FOUNDRY_GPT_ENDPOINT", "https://foundry-gpt-vaxvu3c2.cognitiveservices.azure.com/")
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_test-token-placeholder")
    return monkeypatch


@pytest.fixture
def mock_gh(monkeypatch):
    """Mock ``subprocess.Popen`` as the ``gh`` CLI: no network."""
    calls: list[list[str]] = []

    def fake_run(cmd, *args, **kwargs):
        calls.append(list(cmd))
        result = mock.Mock()
        result.returncode = 0
        result.communicate.return_value = ("{}", "")
        return result

    monkeypatch.setattr("subprocess.Popen", fake_run)
    return calls


@pytest.fixture
def mock_chat_client():
    """A fake chat client: records prompts, returns canned text."""
    client = mock.MagicMock(name="chat_client")
    client.complete.return_value = mock.Mock(content="canned test reply")
    return client
